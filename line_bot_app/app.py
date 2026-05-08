from __future__ import annotations

import os

from dotenv import load_dotenv
from flask import Flask, abort, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.messaging.exceptions import ApiException
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from .ai import AIResponder
from .boot_greeting import maybe_send_worker_boot_greetings
from .config import load_config
from .line_messages import flatten_reply_parts
from .supabase_store import remember_line_user_for_push
from .user_messages import MSG_SYSTEM_FAILURE


def create_app() -> Flask:
    load_dotenv()
    line_ready = bool(
        (os.environ.get("LINE_CHANNEL_SECRET") or "").strip()
        and (os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or "").strip()
    )
    openai_ready = bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    config = load_config(
        require_line_credentials=line_ready,
        require_openai=openai_ready,
    )

    app = Flask(__name__)
    handler = WebhookHandler(config.line_channel_secret)
    line_configuration = Configuration(access_token=config.line_channel_access_token)
    brain = AIResponder(config)

    if line_ready:
        maybe_send_worker_boot_greetings(
            line_configuration,
            app.logger,
            restart_push_enabled=config.restart_push_enabled,
        )

    @app.get("/")
    def health_check():
        return {
            "ok": True,
            "service": "dkis-ll-bot",
            "settings": config.settings_source,
            "credentials_ready": {"line": line_ready, "openai": openai_ready},
        }

    @app.post("/webhook")
    def webhook():
        signature = request.headers.get("X-Line-Signature", "")
        body = request.get_data(as_text=True)

        try:
            handler.handle(body, signature)
        except InvalidSignatureError:
            abort(400)

        return "OK"

    @handler.add(MessageEvent, message=TextMessageContent)
    def handle_text_message(event: MessageEvent):
        user_text = event.message.text
        uid = getattr(event.source, "user_id", None) or "anonymous"
        use_push = uid != "anonymous"
        if use_push:
            remember_line_user_for_push(uid)

        with ApiClient(line_configuration) as api_client:
            api = MessagingApi(api_client)
            try:
                # reply_message は1リクエストで最大5通まで。逐次 push にすると Push 無料枠を枯渇しやすい。
                reply_parts = brain.reply(uid, user_text)
                messages = [
                    TextMessage(text=chunk) for chunk in flatten_reply_parts(reply_parts)
                ]
                api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=messages,
                    )
                )
            except ApiException as exc:
                app.logger.error("LINE Messaging API error: %s", exc, exc_info=True)
                try:
                    api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=MSG_SYSTEM_FAILURE)],
                        )
                    )
                except Exception:
                    app.logger.exception("LINE error reply (fallback) failed")
            except Exception:
                app.logger.exception("AI reply generation or LINE reply failed")
                try:
                    api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=MSG_SYSTEM_FAILURE)],
                        )
                    )
                except Exception:
                    app.logger.exception("LINE error reply (fallback) failed")

    return app
