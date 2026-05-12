from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

from disc_bot.engine import DiscordBrain
from disc_bot.boot_greeting import maybe_build_worker_boot_greeting
from disc_bot.config import load_config
from disc_bot.config import AppConfig
from disc_bot.commands_memory import build_settings_text, set_setting_value
from disc_bot.discord_messages import split_line_text
from disc_bot.supabase_store import (
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
from disc_bot.user_messages import MSG_SYSTEM_FAILURE

logger = logging.getLogger("dkis_disc")
DISCORD_ADMINISTRATOR_PERMISSION = 0x8
RECENT_RESPONSE_WINDOW_SECONDS = 10 * 60
USER_SETTING_KEYS = frozenset(
    {"talk_with_kiritan", "talking_memory", "using_model", "personal_memory", "response_criteria", "process_notice"}
)
CHANNEL_SETTING_KEYS = frozenset({"enabled", "channel_kind"})
_recent_response_channels: dict[int, float] = {}


@dataclass(frozen=True)
class BotRuntime:
    config: AppConfig
    brain: DiscordBrain
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

    await asyncio.to_thread(reply)


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
    enabled_channel_count, configurable_channel_count = await asyncio.to_thread(_connected_channel_counts, client)
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
    return BotRuntime(config=config, brain=DiscordBrain(config))


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


def _is_interaction_admin(interaction: discord.Interaction) -> bool:
    raw_id = str(interaction.user.id)
    if raw_id in _admin_user_ids():
        return True
    if isinstance(interaction.user, discord.Member):
        return bool(interaction.user.guild_permissions.administrator)
    return False


def _is_interaction_operator(interaction: discord.Interaction) -> bool:
    raw_id = str(interaction.user.id)
    if raw_id in _operator_user_ids():
        return True
    uid = _discord_user_key(interaction.user.id)
    return _is_operator_user(uid)


def _format_settings_text(config: AppConfig, user_id: str | None, channel_id: str) -> str:
    return build_settings_text(config, user_id, channel_id)


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


async def _sync_app_commands(tree: app_commands.CommandTree) -> None:
    scope = _discord_command_registration_scope()
    guild_ids = _discord_command_guild_ids()
    if scope in ("guild", "both") and guild_ids:
        for gid in guild_ids:
            try:
                synced = await tree.sync(guild=discord.Object(id=int(gid)))
                logger.info("App commands synced to guild %s: %d command(s)", gid, len(synced))
            except Exception:
                logger.exception("App command sync failed for guild %s", gid)
    if scope in ("global", "both"):
        try:
            synced = await tree.sync()
            logger.info("App commands synced globally: %d command(s)", len(synced))
        except Exception:
            logger.exception("Global app command sync failed")


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _start_health_server() -> None:
    port = int(os.environ.get("PORT", "5000"))

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            _json_response(self, 200, {"ok": True, "service": "dkis-disc-bot"})

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


async def _send_to_debug_rooms(
    client: discord.Client,
    text: str,
    fallback_channel_id: int | None,
) -> None:
    raw_debug_ids = list_debug_channel_ids()
    target_ids = [int(x) for x in raw_debug_ids if str(x).isdigit()]
    if not target_ids and fallback_channel_id:
        target_ids = [fallback_channel_id]
    seen: set[int] = set()
    for channel_id in target_ids:
        if channel_id in seen:
            continue
        seen.add(channel_id)
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        except discord.Forbidden:
            logger.exception("Debug channel is not accessible: %s", channel_id)
            continue
        except Exception:
            logger.exception("Debug channel fetch failed: %s", channel_id)
            continue
        if not isinstance(channel, discord.abc.Messageable):
            logger.error("Debug channel is not messageable: %s", channel_id)
            continue
        await _send_discord_chunks(channel, text)


async def _send_error_to_debug_rooms(
    client: discord.Client,
    title: str,
    detail: str,
    fallback_channel_id: int | None = None,
) -> None:
    """エラー詳細をデバッグルームへ通知する。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    tb_clipped = detail[:1800] if len(detail) > 1800 else detail
    text = f"[エラー通知] {now}\n{title}\n```\n{tb_clipped}\n```"
    await _send_to_debug_rooms(client, text, fallback_channel_id)


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
    tree = app_commands.CommandTree(client)
    boot_greeting_sent = False
    presence_task: asyncio.Task | None = None

    # -------------------------------------------------------
    # スラッシュコマンド定義
    # -------------------------------------------------------

    @tree.command(name="get_setting", description="DKIS-DISCの現在設定を表示します。")
    async def slash_get_setting(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        channel_id = str(interaction.channel_id or "")
        channel_settings = get_channel_settings(channel_id)
        if channel_settings.get("channel_kind", "normal") != "debug" and channel_settings.get("enabled", "off") != "on":
            await interaction.followup.send(
                f"このチャンネルはDKIS-DISCの利用設定が無効です。\n"
                f"管理者が `/channel_setting value:true` をこのチャンネルで実行すると有効に戻せます。channel_id={channel_id}",
            )
            return
        uid = _discord_user_key(interaction.user.id)
        text = _format_settings_text(config, uid, channel_id)
        chunks = split_line_text(text, max_chunks=1)
        await interaction.followup.send(chunks[0] if chunks else MSG_SYSTEM_FAILURE)

    @tree.command(name="set_setting", description="DKIS-DISCの設定を変更します。")
    @app_commands.describe(key="変更する設定キー", value="保存する値（true/false/1/2/3/4/5）")
    @app_commands.choices(
        key=[
            app_commands.Choice(name="talk with kiritan（きりたんとの会話設定）", value="talk_with_kiritan"),
            app_commands.Choice(name="talking memory（会話履歴の保持）", value="talking_memory"),
            app_commands.Choice(name="using model（使用するモデル）", value="using_model"),
            app_commands.Choice(name="personal memory（個人用中期記憶）", value="personal_memory"),
            app_commands.Choice(name="response criteria（botの応答モード）", value="response_criteria"),
            app_commands.Choice(name="process notice（処理内容の付加表示）", value="process_notice"),
        ],
        value=[
            app_commands.Choice(name="true", value="true"),
            app_commands.Choice(name="false", value="false"),
            app_commands.Choice(name="1", value="1"),
            app_commands.Choice(name="2", value="2"),
            app_commands.Choice(name="3", value="3"),
            app_commands.Choice(name="4", value="4"),
            app_commands.Choice(name="5", value="5"),
        ],
    )
    async def slash_set_setting(interaction: discord.Interaction, key: str, value: str) -> None:
        await interaction.response.defer(ephemeral=True)
        channel_id = str(interaction.channel_id or "")
        channel_settings = get_channel_settings(channel_id)
        if channel_settings.get("channel_kind", "normal") != "debug" and channel_settings.get("enabled", "off") != "on":
            await interaction.followup.send(
                f"このチャンネルはDKIS-DISCの利用設定が無効です。channel_id={channel_id}",
            )
            return
        uid = _discord_user_key(interaction.user.id)
        role = _user_role(uid)
        if role == "visitor" and key == "using_model":
            await interaction.followup.send(
                "visitor は using_model を変更できません。member 以上が必要です。",
            )
            return
        ok, line = set_setting_value(config, key, value, uid, channel_id)
        await interaction.followup.send(line)

    @tree.command(
        name="channel_setting",
        description="このチャンネルで bot の有効/無効を設定します（operator は debug/normal も可）。",
    )
    @app_commands.describe(value="true（有効）/ false（無効）/ debug（デバッグルーム化・operator）/ normal（通常チャンネルに戻す・operator）")
    @app_commands.choices(
        value=[
            app_commands.Choice(name="true（有効）", value="true"),
            app_commands.Choice(name="false（無効）", value="false"),
            app_commands.Choice(name="debug（デバッグルーム化/operator）", value="debug"),
            app_commands.Choice(name="normal（通常チャンネルへ戻す/operator）", value="normal"),
        ]
    )
    async def slash_channel_setting(interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)
        channel_id = str(interaction.channel_id or "")
        value_lower = value.strip().lower()
        if value_lower in {"debug", "normal"}:
            if not _is_interaction_operator(interaction):
                await interaction.followup.send(
                    "デバッグルーム設定は operator または `DISCORD_OPERATOR_USER_IDS` のユーザーだけ変更できます。",
                )
                return
            kind = "debug" if value_lower == "debug" else "normal"
            ok, line = set_channel_setting(channel_id, "channel_kind", kind)
            if ok and kind == "debug":
                line += "\nこのチャンネルはデバッグルームです。通常会話は無効で、起動通知などの専用通知が届きます。"
            await interaction.followup.send(line)
            return
        if not _is_interaction_admin(interaction):
            await interaction.followup.send(
                "チャンネル設定はDiscord管理者または `DISCORD_ADMIN_USER_IDS` のユーザーだけ変更できます。",
            )
            return
        ok, line = set_channel_setting(channel_id, "enabled", value)
        await interaction.followup.send(line)

    # -------------------------------------------------------
    # イベントハンドラ
    # -------------------------------------------------------

    @client.event
    async def on_ready() -> None:
        nonlocal boot_greeting_sent, presence_task
        logger.info("Logged in as %s (%s)", client.user, getattr(client.user, "id", "-"))
        if presence_task is None or presence_task.done():
            presence_task = asyncio.create_task(_presence_loop(client))

        await _sync_app_commands(tree)

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
        status_text = await asyncio.to_thread(_build_boot_status_text, client, greeting)
        await _send_to_debug_rooms(client, status_text, channel_id)

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        channel_settings = await asyncio.to_thread(get_channel_settings, message.channel.id)
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
        user_settings = await asyncio.to_thread(get_discord_user_settings, uid)
        role = await asyncio.to_thread(_user_role, uid)
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
            tb = traceback.format_exc()
            logger.exception("AI reply generation or Discord send failed")
            await message.channel.send(MSG_SYSTEM_FAILURE)
            asyncio.create_task(
                _send_error_to_debug_rooms(
                    client,
                    f"on_message エラー uid={uid} channel={message.channel.id}",
                    tb,
                    _discord_channel_id(),
                )
            )

    if not token_ready:
        logger.warning("DISCORD_BOT_TOKEN is not set; bot.run will fail until it is configured")
    return client


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    runtime = _build_runtime()
    _start_health_server()
    _start_self_ping()
    bot = create_bot(runtime)
    TOKEN = (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    bot.run(TOKEN)
