from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import discord
from dotenv import load_dotenv

from line_bot_app.ai import AIResponder
from line_bot_app.boot_greeting import maybe_build_worker_boot_greeting
from line_bot_app.config import load_config
from line_bot_app.line_messages import split_line_text
from line_bot_app.supabase_store import remember_discord_user_for_push
from line_bot_app.user_messages import MSG_SYSTEM_FAILURE

logger = logging.getLogger("dkis_disc")


def _discord_user_key(user_id: int) -> str:
    return f"discord:{user_id}"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


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


def restart_process() -> None:
    """現在の Python プロセスを同じ引数で置き換える。"""
    python = sys.executable
    os.execv(python, [python, *sys.argv])


async def _send_discord_chunks(channel: discord.abc.Messageable, text: str) -> None:
    for chunk in split_line_text(text):
        await channel.send(chunk)


async def _reply_with_streaming(
    brain: AIResponder,
    *,
    uid: str,
    user_text: str,
    channel: discord.abc.Messageable,
) -> None:
    """AIが確定した応答パートをDiscordへ逐次送信する。"""
    loop = asyncio.get_running_loop()

    def on_line_message(chunk: str) -> None:
        future = asyncio.run_coroutine_threadsafe(channel.send(chunk), loop)
        future.result(timeout=60)

    await asyncio.to_thread(
        brain.reply,
        uid,
        user_text,
        on_line_message=on_line_message,
    )


def _start_health_server() -> None:
    port = int(os.environ.get("PORT", "5000"))

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps({"ok": True, "service": "dkis-disc-bot"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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


def create_bot() -> discord.Client:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())

    token_ready = bool((os.environ.get("DISCORD_BOT_TOKEN") or "").strip())
    openai_ready = bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    config = load_config(require_line_credentials=False, require_openai=openai_ready)
    brain = AIResponder(config)

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    boot_greeting_sent = False

    @client.event
    async def on_ready() -> None:
        nonlocal boot_greeting_sent
        logger.info("Logged in as %s (%s)", client.user, getattr(client.user, "id", "-"))
        if boot_greeting_sent:
            return
        boot_greeting_sent = True

        channel_id_raw = (os.environ.get("DISCORD_CHANNEL_ID") or "").strip()
        if not channel_id_raw:
            logger.info("DISCORD_CHANNEL_ID is not set; boot greeting skipped")
            return
        try:
            channel_id = int(channel_id_raw)
        except ValueError:
            logger.error("DISCORD_CHANNEL_ID must be an integer: %r", channel_id_raw)
            return

        channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
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
        user_text = (message.content or "").strip()
        if not user_text:
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
                    brain,
                    uid=uid,
                    user_text=user_text,
                    channel=message.channel,
                )
        except Exception:
            logger.exception("AI reply generation or Discord send failed")
            await message.channel.send(MSG_SYSTEM_FAILURE)

    if not token_ready:
        logger.warning("DISCORD_BOT_TOKEN is not set; bot.run will fail until it is configured")
    return client


if __name__ == "__main__":
    load_dotenv()
    _start_health_server()
    _start_self_ping()
    bot = create_bot()
    TOKEN = (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    bot.run(TOKEN)
