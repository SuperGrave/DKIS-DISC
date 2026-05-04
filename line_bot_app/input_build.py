"""DKIS 互換の入力整形（UI:/RI: と LP: の付与）。LINE では現在地はダミー文字列。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _jst_now_str() -> str:
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).strftime("%Y/%m/%d %H:%M")


def build_input_segments(
    user_text: str,
    *,
    is_retry: bool = False,
    last_proc_result: str = "（前回処理なし）",
) -> dict:
    """
    Returns:
        dict with keys: text, segments, timestamp, raw_text
    """
    timestamp = _jst_now_str()
    lp = last_proc_result.strip() if last_proc_result else "（前回処理なし）"
    nl = "（LINEのためサーバー側でGPS連携していません。地名はユーザー発話から判断してください）"

    if is_retry:
        lines = [
            {"field": "user", "label": "RI：", "text": user_text},
            {"field": "last_result", "label": "LP：", "text": lp},
        ]
    else:
        lines = [
            {"field": "user", "label": "UI：", "text": user_text},
            {"field": "last_result", "label": "LP：", "text": lp},
            {"field": "location", "label": "NL：", "text": nl},
            {"field": "time", "label": "NT：", "text": timestamp},
        ]

    text = "\n".join(
        f'{seg["label"]}{seg["text"]}' if seg.get("label") else seg["text"] for seg in lines
    )

    return {
        "text": text,
        "segments": lines,
        "timestamp": timestamp,
        "raw_text": user_text,
    }
