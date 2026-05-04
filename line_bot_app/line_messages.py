from __future__ import annotations


LINE_TEXT_LIMIT = 5000
MAX_REPLY_MESSAGES = 5


def split_line_text(text: str) -> list[str]:
    """Split long text into LINE-safe message chunks."""
    normalized = (text or "").strip()
    if not normalized:
        return ["すみません、返答が空になってしまいました。"]

    chunks: list[str] = []
    remaining = normalized
    while remaining and len(chunks) < MAX_REPLY_MESSAGES:
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
