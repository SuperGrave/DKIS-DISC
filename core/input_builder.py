from __future__ import annotations

import copy
from typing import Dict, List

from core.context_provider import (
    get_current_time,
    get_last_proc_result,
    get_current_location,
)

try:
    from config import INPUT_FORMAT_DEFAULT
except Exception:
    INPUT_FORMAT_DEFAULT = {
        "main": {
            "mode": "all",
            "include": {
                "last_result": True,
                "location": True,
                "time": True
            },
            "labels": {
                "user": "UI：",
                "retry": "RI：",
                "last_result": "LP：",
                "location": "NL：",
                "time": "NT："
            }
        },
        "chat_only": {
            "mode": "none",
            "include": {
                "last_result": False,
                "location": False,
                "time": False
            },
            "labels": {
                "user": "UI：",
                "retry": "RI：",
                "last_result": "LP：",
                "location": "NL：",
                "time": "NT："
            }
        }
    }


DEFAULT_LABELS = {
    "user": "UI：",
    "retry": "RI：",
    "last_result": "LP：",
    "location": "NL：",
    "time": "NT："
}

DEFAULT_CONTEXT_FIELDS = ["last_result", "location", "time"]
CONTEXT_FIELDS_BY_MODE = {
    "main": ["last_result", "location", "time"],
    "chat_only": ["location", "time"]
}


def _load_input_config(mode_key: str) -> Dict[str, any]:
    from core.settings_manager import get_setting

    base = copy.deepcopy(INPUT_FORMAT_DEFAULT.get(mode_key, INPUT_FORMAT_DEFAULT.get("main", {})))
    cfg = get_setting(f"input_format.{mode_key}", None)
    if isinstance(cfg, dict):
        base = _deep_merge(base, cfg)
    return base


def _deep_merge(base: Dict[str, any], override: Dict[str, any]) -> Dict[str, any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _get_fields_for_mode(mode_key: str) -> List[str]:
    return CONTEXT_FIELDS_BY_MODE.get(mode_key, DEFAULT_CONTEXT_FIELDS)


def build_input_segments(mode_key: str, user_text: str, is_retry: bool = False) -> Dict[str, any]:
    """
    入力テキストとログ用のメタ情報を構築する
    """
    cfg = _load_input_config(mode_key)
    mode = (cfg.get("mode") or "all").lower()
    include = cfg.get("include", {})
    labels = cfg.get("labels", {})

    context_values = {
        "user": str(user_text or ""),
        "last_result": get_last_proc_result() or "（前回処理なし）",
        "location": get_current_location() or "（現在地未設定）",
        "time": get_current_time()
    }
    timestamp = context_values["time"]

    if mode == "none":
        lines = [{
            "field": "user",
            "label": "",
            "text": context_values["user"]
        }]
        return {
            "text": context_values["user"],
            "segments": lines,
            "timestamp": timestamp,
            "raw_text": user_text
        }

    segments: List[Dict[str, str]] = []

    user_label = labels.get("user", DEFAULT_LABELS["user"])
    if is_retry:
        user_label = labels.get("retry", labels.get("user", DEFAULT_LABELS["retry"]))
    segments.append({
        "field": "user",
        "label": user_label or "",
        "text": context_values["user"]
    })

    allowed_fields = _get_fields_for_mode(mode_key)

    # メインループのリトライ入力(RI)では、現在地・現在時間は含めない
    # - 初回入力: UI＋LP/NL/NT（設定に従う）
    # - RI入力 : RI＋LP のみ（NL/NTは省略）
    if is_retry and mode_key == "main":
        allowed_fields = [f for f in allowed_fields if f not in ("location", "time")]

    def should_include(field: str) -> bool:
        if field not in allowed_fields:
            return False
        if mode == "all":
            return True
        if mode == "custom":
            return bool(include.get(field, False))
        return False

    for field in allowed_fields:
        if should_include(field):
            label = labels.get(field, DEFAULT_LABELS[field])
            segments.append({
                "field": field,
                "label": label or "",
                "text": context_values[field]
            })

    text_lines = []
    for line in segments:
        prefix = line["label"] or ""
        text_lines.append(f"{prefix}{line['text']}" if prefix else line["text"])

    return {
        "text": "\n".join(text_lines),
        "segments": segments,
        "timestamp": timestamp,
        "raw_text": user_text
    }

