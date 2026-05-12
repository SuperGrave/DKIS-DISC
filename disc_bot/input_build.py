"""DKIS 互換の入力整形（UI:/RI: と LP: の付与）。labels は settings.json 由来。"""

from __future__ import annotations

from datetime import datetime, timezone

from .settings_loader import InputFormatMain


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M UTC")


_DEFAULT_LABELS = {
    "user": "UI：",
    "retry": "RI：",
    "last_result": "LP：",
    "location": "NL：",
    "time": "NT：",
}


def build_input_segments(
    user_text: str,
    *,
    is_retry: bool = False,
    last_proc_result: str = "（前回処理なし）",
    input_main: InputFormatMain | None = None,
) -> dict:
    """
    Returns:
        dict with keys: text, segments, timestamp, raw_text
    """
    fmt = input_main
    timestamp = _utc_now_str()
    lp = last_proc_result.strip() if last_proc_result else "（前回処理なし）"
    labels = {**_DEFAULT_LABELS, **(fmt.labels if fmt else {})}

    mode = (fmt.mode if fmt else "all").lower()
    inc_lr = fmt.include_last_result if fmt else True
    inc_loc = fmt.include_location if fmt else True
    inc_time = fmt.include_time if fmt else True
    nl_body = fmt.nl_static if fmt else ""

    def should_include(field: str) -> bool:
        if is_retry and field in ("location", "time"):
            return False
        if mode == "none":
            return False
        if mode == "all":
            return True
        if mode == "custom":
            return bool(
                (field == "last_result" and inc_lr)
                or (field == "location" and inc_loc)
                or (field == "time" and inc_time)
            )
        return True

    segments: list[dict[str, str]] = []

    user_key = "retry" if is_retry else "user"
    ulab = labels.get(user_key) or labels.get("user") or ("RI：" if is_retry else "UI：")
    segments.append({"field": "user", "label": ulab, "text": user_text})

    if should_include("last_result"):
        segments.append(
            {
                "field": "last_result",
                "label": labels.get("last_result", "LP："),
                "text": lp,
            }
        )

    if should_include("location"):
        segments.append(
            {
                "field": "location",
                "label": labels.get("location", "NL："),
                "text": nl_body,
            }
        )

    if should_include("time"):
        segments.append(
            {
                "field": "time",
                "label": labels.get("time", "NT："),
                "text": timestamp,
            }
        )

    text = "\n".join(
        f'{seg["label"]}{seg["text"]}' if seg.get("label") else seg["text"] for seg in segments
    )

    return {
        "text": text,
        "segments": segments,
        "timestamp": timestamp,
        "raw_text": user_text,
    }
