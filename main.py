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
    CHANNEL_RESPONSE_MODES,
    CHANNEL_TOOL_NOTICE_VALUES,
    get_channel_settings,
    remember_discord_user_for_push,
    set_channel_setting,
)
from line_bot_app.user_messages import MSG_SYSTEM_FAILURE

logger = logging.getLogger("dkis_disc")
DISCORD_HTTP_USER_AGENT = "DKIS-DISC (https://github.com/SuperGrave/DKIS-DISC, 1.0.0)"
DISCORD_ADMINISTRATOR_PERMISSION = 0x8
USER_SETTING_KEYS = frozenset({"notify_worker_restart"})
ADMIN_SETTING_KEYS = frozenset({"current_model", "show_ri_text", "tool_notice_display", "text.use_raw_result"})
CHANNEL_SETTING_KEYS = frozenset({"enabled", "response_mode", "tool_notice_display"})


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
        return "RAM --%"
    pct = max(0, min(999, round(current / limit * 100)))
    return f"RAM {pct}%"


def _channel_is_enabled(channel_id: str | int | None) -> bool:
    return get_channel_settings(channel_id).get("enabled", "on") == "on"


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
    enabled_channel_count, channel_count = _connected_channel_counts(client)
    mode = _status_mode(enabled_channel_count)
    if mode == "sleep":
        await client.change_presence(
            status=discord.Status.idle,
            activity=discord.Activity(type=discord.ActivityType.watching, name="sleep"),
        )
        return

    label = f"run in {enabled_channel_count}/{channel_count}ch・{_ram_usage_label()}"
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
    config = load_config(require_line_credentials=False, require_openai=openai_ready)
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


def _discord_json_headers(*, token: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "User-Agent": DISCORD_HTTP_USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bot {token}"
    return headers


def _setting_requires_admin(key: str) -> bool:
    return key in ADMIN_SETTING_KEYS


def _format_setting_permission_error(key: str) -> str:
    return (
        f"`{key}` はボット全体に影響する設定なので、Discord管理者または "
        "`DISCORD_ADMIN_USER_IDS` に登録されたユーザーだけ変更できます。"
    )


def _channel_setting_key_choices() -> list[dict]:
    return [
        {"name": "利用ON/OFF（無効にすると復帰操作以外を拒否）", "value": "enabled"},
        {"name": "応答モード", "value": "response_mode"},
        {"name": "ツール通知表示", "value": "tool_notice_display"},
    ]


def _channel_setting_value_choices() -> list[dict]:
    return [
        {"name": "有効（ON）", "value": "on"},
        {"name": "無効（復帰操作以外拒否）", "value": "disabled"},
        *[{"name": value, "value": value} for value in sorted(CHANNEL_RESPONSE_MODES)],
        *[{"name": value, "value": value} for value in sorted(CHANNEL_TOOL_NOTICE_VALUES)],
    ]


def _resolve_interaction_channel_option(payload: dict, options: dict[str, str]) -> str:
    return (options.get("channel") or _interaction_channel_id(payload)).strip()


def _format_channel_settings_text(channel_id: str) -> str:
    settings = get_channel_settings(channel_id)
    return (
        f"channel_id: {channel_id or '(unknown)'}\n"
        f"enabled: {settings.get('enabled', 'on')}\n"
        f"response_mode: {settings.get('response_mode', 'inherit')}\n"
        f"tool_notice_display: {settings.get('tool_notice_display', 'inherit')}"
    )


def _format_settings_text(config: AppConfig, user_id: str | None, channel_id: str) -> str:
    return (
        build_settings_text(config, user_id)
        + "\n\n---\n\n【current_channel】\n"
        + _format_channel_settings_text(channel_id)
    )


def _is_channel_enable_command(command: str, options: dict[str, str]) -> bool:
    value = (options.get("value") or "").strip().lower()
    return command == "channel_setting_set" and (options.get("key") or "").strip() == "enabled" and value in {
        "on",
        "true",
        "1",
        "yes",
    }


def _channel_disabled_response(channel_id: str) -> dict:
    return _interaction_message_response(
        "このチャンネルはDKIS-DISCの利用設定が無効です。\n"
        f"管理者が `/channel_setting_set key:利用ON/OFF value:有効 channel:{channel_id}` を実行すると有効に戻せます。"
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
        if command == "setting_get":
            return _interaction_message_response(
                _format_settings_text(runtime.config, uid, _interaction_channel_id(payload))
            )

        if command == "setting_model":
            if not _interaction_is_admin(payload):
                return _interaction_message_response(_format_setting_permission_error("current_model"))
            ok, line = set_setting_value(runtime.config, "current_model", options.get("model", ""), uid)
            return _interaction_message_response(line)

        if command == "setting_set":
            key = (options.get("key") or "").strip()
            value = options.get("value") or ""
            if _setting_requires_admin(key) and not _interaction_is_admin(payload):
                return _interaction_message_response(_format_setting_permission_error(key))
            ok, line = set_setting_value(runtime.config, key, value, uid)
            return _interaction_message_response(line)

        if command == "channel_setting_get":
            channel_id = _resolve_interaction_channel_option(payload, options)
            return _interaction_message_response(_format_channel_settings_text(channel_id))

        if command == "channel_setting_set":
            if not _interaction_is_admin(payload):
                return _interaction_message_response(
                    "チャンネル設定はDiscord管理者または `DISCORD_ADMIN_USER_IDS` のユーザーだけ変更できます。"
                )
            channel_id = _resolve_interaction_channel_option(payload, options)
            key = (options.get("key") or "").strip()
            value = (options.get("value") or "").strip()
            ok, line = set_channel_setting(channel_id, key, value)
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
    if name not in {"setting_get", "setting_set", "setting_model", "channel_setting_get", "channel_setting_set"}:
        return _interaction_message_response("未対応のコマンドです。")
    options = _interaction_options(data)
    channel_id = _resolve_interaction_channel_option(payload, options)
    if not _channel_is_enabled(channel_id) and not _is_channel_enable_command(name, options):
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
    interval = max(60, int(os.environ.get("KEEPALIVE_INTERVAL_SECONDS", "900")))

    def ping_loop() -> None:
        while True:
            time.sleep(interval)
            try:
                with urllib.request.urlopen(url, timeout=15) as response:
                    logger.info("Keepalive ping: %s -> %s", url, response.status)
            except Exception:
                logger.exception("Keepalive ping failed")

    threading.Thread(target=ping_loop, name="keepalive-ping", daemon=True).start()


def _setting_key_choices() -> list[dict]:
    choices: list[dict] = []
    for key in sorted(USER_SETTING_KEYS | ADMIN_SETTING_KEYS):
        choices.append({"name": key, "value": key})
    return choices


def _model_choices(config: AppConfig) -> list[dict]:
    return [{"name": model, "value": model} for model in sorted(config.allowed_chat_models)[:25]]


def _discord_command_definitions(config: AppConfig) -> list[dict]:
    return [
        {
            "name": "setting_get",
            "description": "DKIS-DISCの現在設定を表示します。",
            "type": 1,
        },
        {
            "name": "setting_set",
            "description": "DKIS-DISCの設定を変更します。重要設定は管理者のみです。",
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
                    "description": "保存する値",
                    "required": True,
                },
            ],
        },
        {
            "name": "setting_model",
            "description": "使用するOpenAIチャットモデルを変更します（管理者のみ）。",
            "type": 1,
            "options": [
                {
                    "type": 3,
                    "name": "model",
                    "description": "使用するモデル",
                    "required": True,
                    "choices": _model_choices(config),
                }
            ],
        },
        {
            "name": "channel_setting_get",
            "description": "現在または指定チャンネルのDKIS-DISCチャンネル設定を表示します。",
            "type": 1,
            "options": [
                {
                    "type": 7,
                    "name": "channel",
                    "description": "確認するチャンネル（未指定なら現在チャンネル）",
                    "required": False,
                }
            ],
        },
        {
            "name": "channel_setting_set",
            "description": "チャンネルごとの会話入口・通知表示を変更します（管理者のみ）。",
            "type": 1,
            "options": [
                {
                    "type": 3,
                    "name": "key",
                    "description": "変更するチャンネル設定キー",
                    "required": True,
                    "choices": _channel_setting_key_choices(),
                },
                {
                    "type": 3,
                    "name": "value",
                    "description": "保存する値",
                    "required": True,
                    "choices": _channel_setting_value_choices(),
                },
                {
                    "type": 7,
                    "name": "channel",
                    "description": "変更するチャンネル（未指定なら現在チャンネル）",
                    "required": False,
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

    guild_id = (os.environ.get("DISCORD_GUILD_ID") or "").strip()
    if guild_id:
        url = f"https://discord.com/api/v10/applications/{application_id}/guilds/{guild_id}/commands"
    else:
        url = f"https://discord.com/api/v10/applications/{application_id}/commands"

    data = json.dumps(_discord_command_definitions(config), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers=_discord_json_headers(token=token),
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            logger.info("Discord command bulk registration: HTTP %s", response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.error("Discord command registration failed: HTTP %s %s", exc.code, body)
    except Exception:
        logger.exception("Discord command registration failed")


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

        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        except discord.Forbidden:
            logger.exception("DISCORD_CHANNEL_ID is not accessible; boot greeting skipped")
            return
        if not isinstance(channel, discord.abc.Messageable):
            logger.error("DISCORD_CHANNEL_ID is not a messageable channel: %s", channel_id)
            return

        greeting = maybe_build_worker_boot_greeting(
            logger,
            restart_push_enabled=config.restart_push_enabled,
        )
        if greeting:
            await _send_discord_chunks(channel, greeting)

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        channel_settings = get_channel_settings(message.channel.id)
        if channel_settings.get("enabled", "on") != "on":
            return
        should_reply, user_text = _message_targets_bot(
            client,
            message,
            message_mode,
            response_mode_override=channel_settings.get("response_mode", "inherit"),
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

        uid = _discord_user_key(message.author.id)
        remember_discord_user_for_push(uid)
        try:
            async with message.channel.typing():
                await _reply_with_streaming(
                    runtime,
                    uid=uid,
                    user_text=user_text,
                    channel=message.channel,
                    tool_notice_display_override=channel_settings.get("tool_notice_display", "inherit"),
                )
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
