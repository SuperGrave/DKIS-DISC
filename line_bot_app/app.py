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
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from .ai import AIResponder
from .config import load_config
from .line_messages import flatten_reply_parts


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

        with ApiClient(line_configuration) as api_client:
            api = MessagingApi(api_client)
            reply_pending = True

            def send_line(text: str) -> None:
                nonlocal reply_pending
                msgs = [TextMessage(text=text)]
                if reply_pending:
                    api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=msgs,
                        )
                    )
                    reply_pending = False
                elif use_push:
                    api.push_message(PushMessageRequest(to=uid, messages=msgs))

            try:
                if use_push:
                    brain.reply(uid, user_text, on_line_message=send_line)
                else:
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
            except Exception:
                app.logger.exception("OpenAI reply generation failed")
                err = "すみません、今ちょっと返答に失敗しました。少し時間を置いてもう一度話しかけてください。"
                try:
                    msgs = [TextMessage(text=err)]
                    if reply_pending:
                        api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=msgs,
                            )
                        )
                    elif use_push:
                        api.push_message(PushMessageRequest(to=uid, messages=msgs))
                except Exception:
                    app.logger.exception("LINE error notify failed")

    return app
