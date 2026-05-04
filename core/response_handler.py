"""Text-only response delivery for DKIS-LINE."""

from __future__ import annotations

import itertools


_send_event_func = None
_increment_stat_func = None
_error_sender = None
_utter_id_counter = itertools.count(1)


def set_send_event_func(func):
    """Register the in-process SSE sender used by the Flask app."""
    global _send_event_func
    _send_event_func = func


def set_increment_stat_func(func):
    """Register the statistics increment callback."""
    global _increment_stat_func
    _increment_stat_func = func


def set_error_sender(func):
    """Keep the same injection shape as the removed voice handler."""
    global _error_sender
    _error_sender = func


def _next_utter_id() -> str:
    return str(next(_utter_id_counter))


def enqueue_utterance(
    text: str,
    turn_id: str = "0",
    emotion: str = "(無)",
    priority: int = 0,
    volume: float = 1.0,
    ai_raw: str | None = None,
    processing_time: float = 0.0,
    token_usage: dict | None = None,
    silent: bool = False,
):
    """Send a text-only reply event; no audio synthesis is performed."""
    if not text:
        return None

    utter_id = turn_id if turn_id and turn_id != "0" else _next_utter_id()
    payload = {
        "utter_id": utter_id,
        "text": text,
        "ai_raw": ai_raw,
        "processing_time": processing_time,
        "token_usage": token_usage,
        "text_only": True,
    }

    if _increment_stat_func:
        _increment_stat_func("kiritan_replies")

    if _send_event_func:
        _send_event_func("reply", payload)
    elif _error_sender:
        _error_sender("response_delivery", "返答の送信先が未初期化です", "send_event が登録されていません")
    else:
        print(f"[RESPONSE] {text}")

    return utter_id


def speak_response(text, silent=False, ai_raw=None, processing_time=0.0, token_usage=None):
    """Compatibility wrapper for call sites that previously triggered TTS."""
    return enqueue_utterance(
        text,
        turn_id="0",
        emotion="(無)",
        ai_raw=ai_raw,
        processing_time=processing_time,
        token_usage=token_usage,
        silent=silent,
    )
