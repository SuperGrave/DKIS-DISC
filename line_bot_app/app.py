from __future__ import annotations

from dotenv import load_dotenv
from flask import Flask, abort, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import ApiClient, Configuration, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from .ai import AIResponder
from .config import load_config
from .line_messages import flatten_reply_parts


def create_app() -> Flask:
    load_dotenv()
    config = load_config()

    app = Flask(__name__)
    handler = WebhookHandler(config.line_channel_secret)
    line_configuration = Configuration(access_token=config.line_channel_access_token)
    brain = AIResponder(config)

    @app.get("/")
    def health_check():
        return {"ok": True, "service": "dkis-ll-bot", "settings": config.settings_source}

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

        try:
            reply_parts = brain.reply(uid, user_text)
        except Exception:
            app.logger.exception("OpenAI reply generation failed")
            reply_parts = [
                "すみません、今ちょっと返答に失敗しました。少し時間を置いてもう一度話しかけてください。"
            ]

        messages = [TextMessage(text=chunk) for chunk in flatten_reply_parts(reply_parts)]
        with ApiClient(line_configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=messages,
                )
            )

    return app
