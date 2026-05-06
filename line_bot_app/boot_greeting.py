"""ワーカー起動時に特定ユーザーへ Push（任意）。"""

from __future__ import annotations

import logging
import os
import random

from linebot.v3.messaging import ApiClient, MessagingApi, PushMessageRequest, TextMessage

_VARIANTS: tuple[str, ...] = (
    "あ、お疲れ様ですー。スマホ見てませんでした。",
    "ゲームしてました。",
    "ちょっと離席してましたー。",
)


def maybe_send_worker_boot_greetings(configuration: object, logger: logging.Logger) -> None:
    """環境変数 ``LINE_BOOT_GREETING_USER_IDS``（カンマ区切り userId）があれば、定型文をランダムで Push。"""
    raw = (os.environ.get("LINE_BOOT_GREETING_USER_IDS") or "").strip()
    if not raw:
        return
    uids = [x.strip() for x in raw.split(",") if x.strip()]
    if not uids:
        return
    text = random.choice(_VARIANTS)
    try:
        with ApiClient(configuration) as api_client:
            api = MessagingApi(api_client)
            for uid in uids:
                api.push_message(PushMessageRequest(to=uid, messages=[TextMessage(text=text)]))
        logger.info("LINE boot greeting sent to %d recipient(s)", len(uids))
    except Exception:
        logger.exception("LINE boot greeting push failed")
