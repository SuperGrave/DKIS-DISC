from __future__ import annotations

from .user_messages import MSG_EMPTY_REPLY

LINE_TEXT_LIMIT = 5000
MAX_REPLY_MESSAGES = 5


def split_line_text(text: str, *, max_chunks: int | None = None) -> list[str]:
    """Split long text into LINE-safe message chunks."""
    chunk_cap = max_chunks if max_chunks is not None else MAX_REPLY_MESSAGES
    chunk_cap = max(1, min(chunk_cap, MAX_REPLY_MESSAGES))

    normalized = (text or "").strip()
    if not normalized:
        return [MSG_EMPTY_REPLY]

    chunks: list[str] = []
    remaining = normalized
    while remaining and len(chunks) < chunk_cap:
        chunk = remaining[:LINE_TEXT_LIMIT]
        if len(remaining) > LINE_TEXT_LIMIT:
            split_at = max(chunk.rfind("\n"), chunk.rfind("。"), chunk.rfind("、"))
            if split_at > LINE_TEXT_LIMIT * 0.5:
                chunk = remaining[: split_at + 1]
        chunks.append(chunk.strip())
        remaining = remaining[len(chunk) :].strip()

    if remaining and chunks:
        chunks[-1] = chunks[-1][: LINE_TEXT_LIMIT - 20].rstrip() + "\n...（省略）"

    return chunks


def flatten_reply_parts(parts: list[str]) -> list[str]:
    """複数パートを LINE の最大バブル数に収める（長文のみ分割）。"""
    cleaned = [(p or "").strip() for p in parts if (p or "").strip()]
    if not cleaned:
        return split_line_text("")

    out: list[str] = []
    n = len(cleaned)
    for i, text in enumerate(cleaned):
        slots = MAX_REPLY_MESSAGES - len(out)
        if slots <= 0:
            break
        if i < n - 1:
            one = split_line_text(text, max_chunks=1)
            if one:
                out.append(one[0])
        else:
            out.extend(split_line_text(text, max_chunks=slots))
    return out if out else split_line_text("")
