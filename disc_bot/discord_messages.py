from __future__ import annotations

from .user_messages import MSG_EMPTY_REPLY

DISCORD_TEXT_LIMIT = 2000
# 旧 import 名の互換。Discord でも同じ 2,000 文字制限を使う。
LINE_TEXT_LIMIT = DISCORD_TEXT_LIMIT
MAX_REPLY_MESSAGES = 5


def split_line_text(text: str, *, max_chunks: int | None = None) -> list[str]:
    """Split long text into Discord-safe message chunks."""
    chunk_cap = max(1, max_chunks) if max_chunks is not None else None

    normalized = (text or "").strip()
    if not normalized:
        return [MSG_EMPTY_REPLY]

    chunks: list[str] = []
    remaining = normalized
    while remaining and (chunk_cap is None or len(chunks) < chunk_cap):
        chunk = remaining[:DISCORD_TEXT_LIMIT]
        if len(remaining) > DISCORD_TEXT_LIMIT:
            split_at = max(chunk.rfind("\n"), chunk.rfind("。"), chunk.rfind("、"))
            if split_at > DISCORD_TEXT_LIMIT * 0.5:
                chunk = remaining[: split_at + 1]
        chunks.append(chunk.strip())
        remaining = remaining[len(chunk) :].strip()

    if remaining and chunks:
        chunks[-1] = chunks[-1][: DISCORD_TEXT_LIMIT - 20].rstrip() + "\n...（省略）"

    return chunks


def flatten_reply_parts(parts: list[str]) -> list[str]:
    """旧入口向け互換。Discord 入口では split_line_text を直接使う。"""
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
