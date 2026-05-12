from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import discord
from dotenv import load_dotenv
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from line_bot_app.ai import AIResponder
from line_bot_app.boot_greeting import maybe_build_worker_boot_greeting
from line_bot_app.config import load_config
from line_bot_app.config import AppConfig
from line_bot_app.commands_memory import build_settings_text, set_setting_value
from line_bot_app.line_messages import split_line_text
from line_bot_app.supabase_store import (
    daily_token_limit_for_role,
    get_discord_user_stats,
    get_discord_user_settings,
    get_channel_settings,
    get_runtime_usage_summary,
    list_debug_channel_ids,
    remember_discord_user_for_push,
    response_mode_from_criteria,
    set_channel_setting,
    user_role_from_value,
)
from line_bot_app.user_messages import MSG_SYSTEM_FAILURE

logger = logging.getLogger("dkis_disc")
DISCORD_HTTP_USER_AGENT = "DKIS-DISC (https://github.com/SuperGrave/DKIS-DISC, 1.2.3)"
DISCORD_ADMINISTRATOR_PERMISSION = 0x8
RECENT_RESPONSE_WINDOW_SECONDS = 10 * 60
USER_SETTING_KEYS = frozenset(
    {"talk_with_kiritan", "talking_memory", "using_model", "personal_memory", "response_criteria", "process_notice"}
)
ADMIN_SETTING_KEYS = frozenset()
CHANNEL_SETTING_KEYS = frozenset({"enabled", "channel_kind"})
_recent_response_channels: dict[int, float] = {}


@dataclass(frozen=True)
class BotRuntime:
    config: AppConfig
    brain: AIResponder
    reply_lock: object = field(default_factory=threading.RLock)


def _discord_user_key(user_id: int) -> str:
    return f"discord:{user_id}"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        logger.warning("%s must be an integer: %r; using %d", name, raw, default)
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _message_mode() -> str:
    raw = (os.environ.get("DISCORD_MESSAGE_MODE") or "hybrid").strip().lower()
    if raw in {"all", "mention", "slash_only", "hybrid"}:
        return raw
    logger.warning("Unknown DISCORD_MESSAGE_MODE=%r; using hybrid", raw)
    return "hybrid"


def _discord_channel_id() -> int | None:
    raw = (os.environ.get("DISCORD_CHANNEL_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.error("DISCORD_CHANNEL_ID must be an integer: %r", raw)
        return None


def _admin_user_ids() -> set[str]:
    raw = ",".join(
        [
            os.environ.get("DISCORD_ADMIN_USER_IDS") or "",
            os.environ.get("DISCORD_RESTART_ALLOWED_USER_IDS") or "",
        ]
    )
    return {x.strip() for x in raw.split(",") if x.strip()}


def _operator_user_ids() -> set[str]:
    raw = ",".join(
        [
            os.environ.get("DISCORD_OPERATOR_USER_IDS") or "",
            os.environ.get("DISCORD_ADMIN_USER_IDS") or "",
        ]
    )
    return {x.strip() for x in raw.split(",") if x.strip()}


def _is_restart_command(content: str) -> bool:
    normalized = (content or "").strip().lower()
    return normalized in {"サーバー再起動", "再起動", "restart", "/restart"}


def _can_restart(message: discord.Message) -> bool:
    allowed_raw = (os.environ.get("DISCORD_RESTART_ALLOWED_USER_IDS") or "").strip()
    allowed_ids = {x.strip() for x in allowed_raw.split(",") if x.strip()}
    if allowed_ids and str(message.author.id) in allowed_ids:
        return True
    if isinstance(message.author, discord.Member):
        return bool(message.author.guild_permissions.administrator)
    return False


def _strip_bot_mention(client: discord.Client, text: str) -> str:
    if client.user is None:
        return text.strip()
    return text.replace(client.user.mention, "").strip()


def _message_targets_bot(
    client: discord.Client,
    message: discord.Message,
    mode: str,
    *,
    response_mode_override: str = "inherit",
) -> tuple[bool, str]:
    text = (message.content or "").strip()
    if not text:
        return False, ""
    response_mode = (response_mode_override or "inherit").strip().lower()
    if response_mode == "off":
        return False, ""
    if response_mode == "normal":
        return True, text
    if response_mode == "mention":
        if client.user not in message.mentions:
            return False, ""
        text = _strip_bot_mention(client, text)
        return bool(text), text
    if mode == "slash_only":
        return False, ""
    if mode == "all":
        return True, text
    target_channel_id = _discord_channel_id()
    if mode == "hybrid" and target_channel_id is not None and message.channel.id == target_channel_id:
        return True, text
    if client.user not in message.mentions:
        return False, ""
    text = _strip_bot_mention(client, text)
    return bool(text), text


def restart_process() -> None:
    """現在の Python プロセスを同じ引数で置き換える。"""
    python = sys.executable
    os.execv(python, [python, *sys.argv])


async def _send_discord_chunks(channel: discord.abc.Messageable, text: str) -> None:
    for chunk in split_line_text(text):
        await channel.send(chunk)


async def _reply_with_streaming(
    runtime: BotRuntime,
    *,
    uid: str,
    user_text: str,
    channel: discord.abc.Messageable,
    tool_notice_display_override: str | None = None,
) -> None:
    """AIが確定した応答パートをDiscordへ逐次送信する。"""
    loop = asyncio.get_running_loop()

    def on_line_message(chunk: str) -> None:
        future = asyncio.run_coroutine_threadsafe(channel.send(chunk), loop)
        future.result(timeout=60)

    def reply() -> None:
        with runtime.reply_lock:
            runtime.brain.reply(
                uid,
                user_text,
                on_line_message=on_line_message,
                tool_notice_display_override=tool_notice_display_override,
            )

    await asyncio.to_thread(
        reply,
    )


def _read_int_file(path: str) -> int | None:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw or raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _ram_usage_label() -> str:
    current = _read_int_file("/sys/fs/cgroup/memory.current")
    limit = _read_int_file("/sys/fs/cgroup/memory.max")
    if current is None or limit is None or limit <= 0:
        return "RAM:--%"
    pct = max(0, min(999, round(current / limit * 100)))
    return f"RAM:{pct}%"


def _remember_response_channel(channel_id: int | str | None) -> None:
    try:
        cid = int(channel_id or 0)
    except (TypeError, ValueError):
        return
    if cid <= 0:
        return
    _recent_response_channels[cid] = time.monotonic()


def _recent_response_channel_count() -> int:
    now = time.monotonic()
    expired = [
        cid
        for cid, last_at in _recent_response_channels.items()
        if now - last_at > RECENT_RESPONSE_WINDOW_SECONDS
    ]
    for cid in expired:
        _recent_response_channels.pop(cid, None)
    return len(_recent_response_channels)


def _channel_is_enabled(channel_id: str | int | None) -> bool:
    return get_channel_settings(channel_id).get("enabled", "off") == "on"


def _connected_channel_counts(client: discord.Client) -> tuple[int, int]:
    enabled = 0
    total = 0
    for guild in client.guilds:
        me = guild.me
        if me is None:
            continue
        for channel in guild.text_channels:
            permissions = channel.permissions_for(me)
            if permissions.view_channel and permissions.send_messages and permissions.read_message_history:
                total += 1
                if _channel_is_enabled(channel.id):
                    enabled += 1
    return enabled, total


def _status_mode(channel_count: int) -> str:
    raw = (os.environ.get("DISCORD_STATUS_MODE") or "auto").strip().lower()
    if raw in {"run", "running"}:
        return "run"
    if raw in {"sleep", "idle"}:
        return "sleep"
    if raw != "auto":
        logger.warning("Unknown DISCORD_STATUS_MODE=%r; using auto", raw)
    return "run" if channel_count > 0 else "sleep"


async def _update_presence_once(client: discord.Client) -> None:
    guild_count = len(client.guilds)
    enabled_channel_count, configurable_channel_count = _connected_channel_counts(client)
    recent_response_channel_count = _recent_response_channel_count()
    mode = _status_mode(enabled_channel_count)
    if mode == "sleep":
        await client.change_presence(
            status=discord.Status.idle,
            activity=discord.Activity(type=discord.ActivityType.watching, name="sleep"),
        )
        return

    label = (
        f"run in {guild_count}sb "
        f"({recent_response_channel_count}/{enabled_channel_count}/{configurable_channel_count}ch) "
        f"{_ram_usage_label()}"
    )
    await client.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(type=discord.ActivityType.watching, name=label),
    )


async def _presence_loop(client: discord.Client) -> None:
    interval = _env_int("DISCORD_STATUS_INTERVAL_SECONDS", 60, minimum=30)
    while not client.is_closed():
        try:
            await _update_presence_once(client)
        except Exception:
            logger.exception("Discord presence update failed")
        await asyncio.sleep(interval)


def _build_runtime() -> BotRuntime:
    openai_ready = bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    config = load_config(require_discord_credentials=False, require_openai=openai_ready)
    return BotRuntime(config=config, brain=AIResponder(config))


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _verify_discord_interaction(body: bytes, headers) -> bool:
    public_key_hex = (os.environ.get("DISCORD_PUBLIC_KEY") or "").strip()
    if not public_key_hex:
        logger.warning("DISCORD_PUBLIC_KEY is not set; interaction endpoint is disabled")
        return False
    signature = (headers.get("X-Signature-Ed25519") or "").strip()
    timestamp = (headers.get("X-Signature-Timestamp") or "").strip()
    if not signature or not timestamp:
        return False
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(bytes.fromhex(signature), timestamp.encode("utf-8") + body)
        return True
    except (ValueError, InvalidSignature):
        return False


def _interaction_options(data: dict) -> dict[str, str]:
    return {str(option.get("name") or ""): str(option.get("value") or "") for option in data.get("options") or []}


def _interaction_user_id(payload: dict) -> str:
    member = payload.get("member") or {}
    user = member.get("user") or payload.get("user") or {}
    raw = str(user.get("id") or "").strip()
    return _discord_user_key(int(raw)) if raw.isdigit() else "discord:interaction"


def _interaction_raw_user_id(payload: dict) -> str:
    member = payload.get("member") or {}
    user = member.get("user") or payload.get("user") or {}
    return str(user.get("id") or "").strip()


def _interaction_channel_id(payload: dict) -> str:
    return str(payload.get("channel_id") or "").strip()


def _interaction_is_admin(payload: dict) -> bool:
    raw_id = _interaction_raw_user_id(payload)
    if raw_id and raw_id in _admin_user_ids():
        return True
    raw_permissions = str((payload.get("member") or {}).get("permissions") or "0")
    try:
        permissions = int(raw_permissions)
    except ValueError:
        permissions = 0
    return bool(permissions & DISCORD_ADMINISTRATOR_PERMISSION)


def _raw_discord_id_from_user_key(user_id: str | None) -> str:
    raw = str(user_id or "").strip()
    return raw.removeprefix("discord:") if raw.startswith("discord:") else raw


def _user_role(user_id: str | None) -> str:
    raw_id = _raw_discord_id_from_user_key(user_id)
    if raw_id and raw_id in _operator_user_ids():
        return "operator"
    settings = get_discord_user_settings(user_id or "")
    return user_role_from_value(settings.get("user_role"))


def _is_operator_user(user_id: str | None) -> bool:
    return _user_role(user_id) == "operator"


def _interaction_is_operator(payload: dict, uid: str | None = None) -> bool:
    raw_id = _interaction_raw_user_id(payload)
    if raw_id and raw_id in _operator_user_ids():
        return True
    return _is_operator_user(uid or _interaction_user_id(payload))


def _discord_json_headers(*, token: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "User-Agent": DISCORD_HTTP_USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bot {token}"
    return headers


def _discord_command_guild_ids() -> list[str]:
    raw = ",".join(
        [
            os.environ.get("DISCORD_GUILD_ID") or "",
            os.environ.get("DISCORD_GUILD_IDS") or "",
        ]
    )
    seen: set[str] = set()
    guild_ids: list[str] = []
    for item in raw.split(","):
        guild_id = item.strip()
        if not guild_id or guild_id in seen:
            continue
        seen.add(guild_id)
        guild_ids.append(guild_id)
    return guild_ids


def _discord_command_registration_scope() -> str:
    raw = (os.environ.get("DISCORD_COMMAND_REGISTRATION_SCOPE") or "global").strip().lower()
    if raw in {"global", "guild", "both"}:
        return raw
    logger.warning("Unknown DISCORD_COMMAND_REGISTRATION_SCOPE=%r; using global", raw)
    return "global"


def _setting_requires_admin(key: str) -> bool:
    return key in ADMIN_SETTING_KEYS


def _format_setting_permission_error(key: str) -> str:
    return (
        f"`{key}` はボット全体に影響する設定なので、Discord管理者または "
        "`DISCORD_ADMIN_USER_IDS` に登録されたユーザーだけ変更できます。"
    )


def _channel_setting_key_choices() -> list[dict]:
    return [{"name": "利用ON/OFF", "value": "enabled"}]


def _channel_setting_value_choices() -> list[dict]:
    return [
        {"name": "true（有効）", "value": "true"},
        {"name": "false（無効）", "value": "false"},
        {"name": "debug（デバッグルーム化/operator）", "value": "debug"},
        {"name": "normal（通常チャンネルへ戻す/operator）", "value": "normal"},
    ]


def _resolve_interaction_channel_option(payload: dict, options: dict[str, str]) -> str:
    return (options.get("channel") or _interaction_channel_id(payload)).strip()


def _format_channel_settings_text(channel_id: str) -> str:
    settings = get_channel_settings(channel_id)
    return (
        f"channel_id: {channel_id or '(unknown)'}\n"
        f"enabled: {settings.get('enabled', 'off')}\n"
        f"process_notice: {settings.get('process_notice', '2')}\n"
        f"channel_kind: {settings.get('channel_kind', 'normal')}"
    )


def _format_settings_text(config: AppConfig, user_id: str | None, channel_id: str) -> str:
    return build_settings_text(config, user_id, channel_id)


def _is_channel_enable_command(command: str, options: dict[str, str]) -> bool:
    value = (options.get("value") or "").strip().lower()
    return command == "channel_setting" and value in {
        "true",
        "1",
        "yes",
        "debug",
        "normal",
    }


def _channel_disabled_response(channel_id: str) -> dict:
    return _interaction_message_response(
        "このチャンネルはDKIS-DISCの利用設定が無効です。\n"
        f"管理者が `/channel_setting value:true` をこのチャンネルで実行すると有効に戻せます。channel_id={channel_id}"
    )


def _interaction_message_response(content: str, *, ephemeral: bool = True) -> dict:
    chunks = split_line_text(content, max_chunks=1)
    data: dict = {
        "content": chunks[0] if chunks else MSG_SYSTEM_FAILURE,
        "allowed_mentions": {"parse": []},
    }
    if ephemeral:
        data["flags"] = 64
    return {"type": 4, "data": data}


def _build_interaction_command_response(runtime: BotRuntime, payload: dict) -> dict:
    data = payload.get("data") or {}
    command = str(data.get("name") or "").lower()
    options = _interaction_options(data)
    uid = _interaction_user_id(payload)

    try:
        if command == "get_setting":
            return _interaction_message_response(
                _format_settings_text(runtime.config, uid, _interaction_channel_id(payload))
            )

        if command == "set_setting":
            key = (options.get("key") or "").strip()
            value = options.get("value") or ""
            role = _user_role(uid)
            if role == "visitor" and key == "using_model":
                return _interaction_message_response("visitor は using_model を変更できません。member 以上が必要です。")
            if _setting_requires_admin(key) and not _interaction_is_admin(payload):
                return _interaction_message_response(_format_setting_permission_error(key))
            ok, line = set_setting_value(runtime.config, key, value, uid, _interaction_channel_id(payload))
            return _interaction_message_response(line)

        if command == "channel_setting":
            channel_value = (options.get("value") or "").strip().lower()
            if channel_value in {"debug", "normal"}:
                if not _interaction_is_operator(payload, uid):
                    return _interaction_message_response(
                        "デバッグルーム設定は operator または `DISCORD_OPERATOR_USER_IDS` のユーザーだけ変更できます。"
                    )
                channel_id = _interaction_channel_id(payload)
                kind = "debug" if channel_value == "debug" else "normal"
                ok, line = set_channel_setting(channel_id, "channel_kind", kind)
                if ok and kind == "debug":
                    line += "\nこのチャンネルはデバッグルームです。通常会話は無効で、専用コマンドは後続実装で追加されます。"
                return _interaction_message_response(line)
            if not _interaction_is_admin(payload):
                return _interaction_message_response(
                    "チャンネル設定はDiscord管理者または `DISCORD_ADMIN_USER_IDS` のユーザーだけ変更できます。"
                )
            channel_id = _interaction_channel_id(payload)
            value = (options.get("value") or "").strip()
            ok, line = set_channel_setting(channel_id, "enabled", value)
            return _interaction_message_response(line)

        return _interaction_message_response("未対応のコマンドです。")
    except Exception:
        logger.exception("Discord interaction command failed")
        return _interaction_message_response(MSG_SYSTEM_FAILURE)


def _handle_discord_interaction(runtime: BotRuntime, payload: dict) -> dict:
    interaction_type = payload.get("type")
    if interaction_type == 1:
        return {"type": 1}

    if interaction_type != 2:
        return _interaction_message_response("未対応のInteractionです。")

    data = payload.get("data") or {}
    name = str(data.get("name") or "").lower()
    if name not in {"get_setting", "set_setting", "channel_setting"}:
        return _interaction_message_response("未対応のコマンドです。")
    options = _interaction_options(data)
    channel_id = _resolve_interaction_channel_option(payload, options)
    channel_settings = get_channel_settings(channel_id)
    if channel_settings.get("channel_kind", "normal") == "debug":
        return _build_interaction_command_response(runtime, payload)
    if channel_settings.get("enabled", "off") != "on" and not _is_channel_enable_command(name, options):
        return _channel_disabled_response(channel_id)

    return _build_interaction_command_response(runtime, payload)


def _start_health_server(runtime: BotRuntime) -> None:
    port = int(os.environ.get("PORT", "5000"))

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            _json_response(self, 200, {"ok": True, "service": "dkis-disc-bot"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/interactions":
                _json_response(self, 404, {"ok": False, "error": "not_found"})
                return

            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length)
            if not _verify_discord_interaction(body, self.headers):
                _json_response(self, 401, {"ok": False, "error": "invalid_signature"})
                return
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                _json_response(self, 400, {"ok": False, "error": "invalid_json"})
                return

            _json_response(self, 200, _handle_discord_interaction(runtime, payload))

        def log_message(self, format: str, *args: object) -> None:
            logger.debug("health server: " + format, *args)

    def serve() -> None:
        try:
            server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
            logger.info("Health server listening on port %d", port)
            server.serve_forever()
        except Exception:
            logger.exception("Health server failed")

    threading.Thread(target=serve, name="health-server", daemon=True).start()


def _start_self_ping() -> None:
    if _env_flag("DISABLE_KEEPALIVE"):
        return
    url = (os.environ.get("KEEPALIVE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip()
    if not url:
        return
    interval = max(60, int(os.environ.get("KEEPALIVE_INTERVAL_SECONDS", "300")))

    def ping_loop() -> None:
        while True:
            time.sleep(interval)
            try:
                with urllib.request.urlopen(url, timeout=15) as response:
                    logger.info("Keepalive ping: %s -> %s", url, response.status)
            except urllib.error.HTTPError as exc:
                logger.warning("Keepalive ping returned HTTP %s: %s", exc.code, url)
            except urllib.error.URLError as exc:
                logger.warning("Keepalive ping could not reach %s: %s", url, exc.reason)
            except Exception:
                logger.exception("Keepalive ping failed")

    threading.Thread(target=ping_loop, name="keepalive-ping", daemon=True).start()


def _setting_key_choices() -> list[dict]:
    return [
        {"name": "talk with kiritan（きりたんとの会話設定）", "value": "talk_with_kiritan"},
        {"name": "talking memory（会話履歴の保持）", "value": "talking_memory"},
        {"name": "using model（使用するモデル）", "value": "using_model"},
        {"name": "personal memory（個人用中期記憶）", "value": "personal_memory"},
        {"name": "response criteria（botの応答モード）", "value": "response_criteria"},
        {"name": "process notice（処理内容の付加表示）", "value": "process_notice"},
    ]


def _setting_value_choices() -> list[dict]:
    return [
        {"name": "true", "value": "true"},
        {"name": "false", "value": "false"},
        {"name": "1", "value": "1"},
        {"name": "2", "value": "2"},
        {"name": "3", "value": "3"},
        {"name": "4", "value": "4"},
        {"name": "5", "value": "5"},
    ]


def _discord_command_definitions(config: AppConfig) -> list[dict]:
    return [
        {
            "name": "get_setting",
            "description": "DKIS-DISCの現在設定を表示します。",
            "type": 1,
        },
        {
            "name": "set_setting",
            "description": "DKIS-DISCの設定を変更します。",
            "type": 1,
            "options": [
                {
                    "type": 3,
                    "name": "key",
                    "description": "変更する設定キー",
                    "required": True,
                    "choices": _setting_key_choices(),
                },
                {
                    "type": 3,
                    "name": "value",
                    "description": "保存する値（true/false/1/2/3/4/5）",
                    "required": True,
                    "choices": _setting_value_choices(),
                },
            ],
        },
        {
            "name": "channel_setting",
            "description": "このチャンネルで bot の有効/無効を設定します（operator は debug/normal も可）。",
            "type": 1,
            "options": [
                {
                    "type": 3,
                    "name": "value",
                    "description": "true/false/debug/normal",
                    "required": True,
                    "choices": _channel_setting_value_choices(),
                },
            ],
        },
    ]


def _register_discord_commands(config: AppConfig) -> None:
    if not _env_flag("DISCORD_AUTO_REGISTER_COMMANDS", True):
        return
    application_id = (os.environ.get("DISCORD_APPLICATION_ID") or "").strip()
    token = (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    if not application_id or not token:
        logger.info("Discord command registration skipped; DISCORD_APPLICATION_ID or token is not set")
        return

    commands_data = json.dumps(_discord_command_definitions(config), ensure_ascii=False).encode("utf-8")
    empty_data = b"[]"
    global_url = f"https://discord.com/api/v10/applications/{application_id}/commands"
    guild_urls = [
        (
            f"guild:{guild_id}",
            f"https://discord.com/api/v10/applications/{application_id}/guilds/{guild_id}/commands",
        )
        for guild_id in _discord_command_guild_ids()
    ]
    scope = _discord_command_registration_scope()
    requests: list[tuple[str, str, bytes]] = []
    if scope in {"global", "both"}:
        requests.append(("global", global_url, commands_data))
    if scope in {"guild", "both"}:
        if guild_urls:
            requests.extend((label, url, commands_data) for label, url in guild_urls)
        else:
            logger.warning("DISCORD_COMMAND_REGISTRATION_SCOPE=%s but DISCORD_GUILD_ID(S) is not set", scope)
    if scope == "global":
        requests.extend((f"{label}:clear", url, empty_data) for label, url in guild_urls)
    elif scope == "guild" and guild_urls:
        requests.append(("global:clear", global_url, empty_data))

    for label, url, payload in requests:
        request = urllib.request.Request(
            url,
            data=payload,
            headers=_discord_json_headers(token=token),
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                logger.info("Discord command bulk registration (%s): HTTP %s", label, response.status)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            logger.error("Discord command registration failed (%s): HTTP %s %s", label, exc.code, body)
        except Exception:
            logger.exception("Discord command registration failed (%s)", label)


def _build_boot_status_text(client: discord.Client, greeting: str | None) -> str:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    enabled_channel_count, channel_count = _connected_channel_counts(client)
    usage = get_runtime_usage_summary()
    lines = [
        "[DKIS-DISC 起動通知]",
        f"起動時刻: {now_utc}",
        f"接続サーバー数: {len(client.guilds)}",
        f"会話可能チャンネル: {enabled_channel_count}/{channel_count}",
        f"登録ユーザー数: {usage['registered_users']}",
        f"本日のアクティブユーザー数: {usage['active_users_today']}",
        f"本日の消費トークン数(UTC): {usage['daily_token_count']}",
    ]
    if greeting:
        lines.extend(["", "[起動あいさつ]", greeting])
    return "\n".join(lines)


async def _send_boot_status_to_debug_rooms(client: discord.Client, text: str, fallback_channel_id: int) -> None:
    raw_debug_ids = list_debug_channel_ids()
    target_ids = [int(x) for x in raw_debug_ids if str(x).isdigit()]
    if not target_ids:
        target_ids = [fallback_channel_id]
    seen: set[int] = set()
    for channel_id in target_ids:
        if channel_id in seen:
            continue
        seen.add(channel_id)
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        except discord.Forbidden:
            logger.exception("Boot status channel is not accessible: %s", channel_id)
            continue
        except Exception:
            logger.exception("Boot status channel fetch failed: %s", channel_id)
            continue
        if not isinstance(channel, discord.abc.Messageable):
            logger.error("Boot status channel is not messageable: %s", channel_id)
            continue
        await _send_discord_chunks(channel, text)


def create_bot(runtime: BotRuntime | None = None) -> discord.Client:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())

    token_ready = bool((os.environ.get("DISCORD_BOT_TOKEN") or "").strip())
    if runtime is None:
        runtime = _build_runtime()
    config = runtime.config
    message_mode = _message_mode()

    intents = discord.Intents.default()
    intents.message_content = message_mode != "slash_only"
    client = discord.Client(intents=intents)
    boot_greeting_sent = False
    presence_task: asyncio.Task | None = None

    @client.event
    async def on_ready() -> None:
        nonlocal boot_greeting_sent, presence_task
        logger.info("Logged in as %s (%s)", client.user, getattr(client.user, "id", "-"))
        if presence_task is None or presence_task.done():
            presence_task = asyncio.create_task(_presence_loop(client))
        if boot_greeting_sent:
            return
        boot_greeting_sent = True

        channel_id = _discord_channel_id()
        if channel_id is None:
            logger.info("DISCORD_CHANNEL_ID is not set; boot greeting skipped")
            return

        greeting = maybe_build_worker_boot_greeting(
            logger,
            restart_push_enabled=config.restart_push_enabled,
        )
        status_text = _build_boot_status_text(client, greeting)
        await _send_boot_status_to_debug_rooms(client, status_text, channel_id)

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        channel_settings = get_channel_settings(message.channel.id)
        if channel_settings.get("channel_kind", "normal") == "debug":
            text = (message.content or "").strip()
            mentions_bot = client.user in message.mentions if client.user is not None else False
            if text.lower().startswith("k:") or mentions_bot:
                await message.channel.send(
                    "ここはデバッグルームです。通常会話は無効です。専用コマンドは後続実装で追加されます。"
                )
            return
        if channel_settings.get("enabled", "off") != "on":
            return
        uid = _discord_user_key(message.author.id)
        user_settings = get_discord_user_settings(uid)
        role = _user_role(uid)
        if user_settings.get("talk_with_kiritan", "true") != "true":
            return
        should_reply, user_text = _message_targets_bot(
            client,
            message,
            message_mode,
            response_mode_override=response_mode_from_criteria(user_settings.get("response_criteria")),
        )
        if not should_reply:
            return

        if _is_restart_command(user_text):
            if not _can_restart(message):
                await message.channel.send("再起動できるのは管理者だけです。")
                return
            await message.channel.send("サーバーを再起動します。")
            await client.close()
            restart_process()
            return

        remember_discord_user_for_push(uid)
        current_stats = get_discord_user_stats(uid)
        daily_limit = daily_token_limit_for_role(role)
        if int(current_stats.get("daily_token_count") or 0) >= daily_limit:
            await message.channel.send(
                f"{role} の本日トークン上限（{daily_limit}）に達しています。member 以上への変更は operator に依頼してください。"
            )
            return
        try:
            async with message.channel.typing():
                await _reply_with_streaming(
                    runtime,
                    uid=uid,
                    user_text=user_text,
                    channel=message.channel,
                    tool_notice_display_override=channel_settings.get("tool_notice_display", "abbrev"),
                )
                _remember_response_channel(message.channel.id)
        except Exception:
            logger.exception("AI reply generation or Discord send failed")
            await message.channel.send(MSG_SYSTEM_FAILURE)

    if not token_ready:
        logger.warning("DISCORD_BOT_TOKEN is not set; bot.run will fail until it is configured")
    return client


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    runtime = _build_runtime()
    _register_discord_commands(runtime.config)
    _start_health_server(runtime)
    _start_self_ping()
    bot = create_bot(runtime)
    TOKEN = (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    bot.run(TOKEN)
