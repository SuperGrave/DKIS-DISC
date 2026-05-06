"""ワーカー起動時に特定ユーザーへ Push（任意）。"""

from __future__ import annotations

import logging
import os
import random

from linebot.v3.messaging import ApiClient, MessagingApi, PushMessageRequest, TextMessage

from .supabase_store import list_boot_notification_recipient_ids

_VARIANTS: tuple[str, ...] = (
    "あ、お疲れ様ですー。スマホ見てませんでした。",
    "ゲームしてました。",
    "ちょっと離席してましたー。",
)


def _skip_stored_ids_for_boot_push() -> bool:
    return (os.environ.get("LINE_BOOT_GREETING_SKIP_STORED_IDS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _merged_boot_recipient_ids() -> list[str]:
    env_raw = (os.environ.get("LINE_BOOT_GREETING_USER_IDS") or "").strip()
    env_ids = [x.strip() for x in env_raw.split(",") if x.strip()]
    extra = [] if _skip_stored_ids_for_boot_push() else list_boot_notification_recipient_ids()
    seen: set[str] = set()
    merged: list[str] = []
    for uid in env_ids + extra:
        if uid in seen:
            continue
        seen.add(uid)
        merged.append(uid)
    return merged


def maybe_send_worker_boot_greetings(configuration: object, logger: logging.Logger) -> None:
    """起動時に定型 Push。
    - ``LINE_BOOT_GREETING_USER_IDS`` …カンマ区切りで明示（運用者向け・任意）
    - Supabase の ``known_line_users.notify_on_restart=true`` …ユーザーが **SET-SETTING** でオプトインした Id のみ（任意・上限あり）

    LINE が過去ユーザ一覧を返すことはないため **Webhook での記録が前提**。``notify_worker_restart`` をオンにしたユーザーだけ DB 経由で届きます。環境変数の Id はそのままマージされます。
    """
    uids = _merged_boot_recipient_ids()
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
