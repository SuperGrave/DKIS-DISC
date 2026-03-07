from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from threading import Lock


_state_lock = Lock()
_status_notifier = None


def _base_state() -> dict:
    return {
        "visible": False,
        "video_id": "",
        "title": "",
        "url": "",
        "thumbnail": "",
        "embed_url": "",
        "manual_embed_url": "",
        "autoplay": False,
        "muted": False,
        "needs_user_action": False,
        "reason": "",
        "source_query": "",
        "mode": "",
        "selection_mode": "",
        "selection_source": "",
        "pending_selection": False,
        "selected_index": None,
        "candidates": [],
        "updated_at": None,
    }


_youtube_state = _base_state()


def set_status_notifier(func):
    """状態更新後に system_status を配信するコールバックを設定する。"""
    global _status_notifier
    _status_notifier = func


def _notify():
    if callable(_status_notifier):
        try:
            _status_notifier()
        except Exception as e:
            print(f"[YOUTUBE] 状態通知エラー: {e}")


def get_youtube_state() -> dict:
    with _state_lock:
        return deepcopy(_youtube_state)


def set_youtube_state(payload: dict):
    with _state_lock:
        _youtube_state.update(payload or {})
        _youtube_state["visible"] = bool(_youtube_state.get("visible", False))
        _youtube_state["pending_selection"] = bool(_youtube_state.get("pending_selection", False))
        _youtube_state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _notify()


def clear_youtube_state():
    with _state_lock:
        _youtube_state.clear()
        _youtube_state.update(_base_state())
        _youtube_state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _notify()


def has_pending_selection() -> bool:
    with _state_lock:
        return bool(_youtube_state.get("pending_selection")) and bool(_youtube_state.get("candidates"))


def set_pending_youtube_selection(query: str, candidates: list[dict], selection_mode: str):
    candidate_list = [dict(candidate) for candidate in (candidates or [])]
    set_youtube_state({
        "visible": True,
        "video_id": "",
        "title": "YouTube候補を選択してください",
        "url": "",
        "thumbnail": "",
        "embed_url": "",
        "manual_embed_url": "",
        "autoplay": False,
        "muted": False,
        "needs_user_action": True,
        "reason": "候補を表示中です。番号入力またはタップで選択できます。",
        "source_query": query,
        "mode": "pending_selection",
        "selection_mode": selection_mode,
        "selection_source": "",
        "pending_selection": True,
        "selected_index": None,
        "candidates": candidate_list,
    })


def activate_youtube_candidate(index: int, *, selection_source: str = "manual", reason: str = "", autoplay: bool = True, muted: bool = False):
    from core.utils import build_youtube_embed_url

    with _state_lock:
        candidates = _youtube_state.get("candidates") or []
        normalized_index = int(index)
        if normalized_index < 1 or normalized_index > len(candidates):
            return None

        selected = dict(candidates[normalized_index - 1])
        video_id = selected.get("video_id", "")
        if not video_id:
            return None

        _youtube_state.update({
            "visible": True,
            "video_id": video_id,
            "title": selected.get("title", f"YouTube動画 {video_id}"),
            "url": selected.get("url", ""),
            "thumbnail": selected.get("thumbnail", ""),
            "embed_url": build_youtube_embed_url(video_id, autoplay=autoplay, mute=muted),
            "manual_embed_url": build_youtube_embed_url(video_id, autoplay=True, mute=False),
            "autoplay": bool(autoplay),
            "muted": bool(muted),
            "needs_user_action": False,
            "reason": reason or selected.get("selection_reason", ""),
            "mode": "player",
            "selection_source": selection_source,
            "pending_selection": False,
            "selected_index": normalized_index,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    _notify()
    return selected
