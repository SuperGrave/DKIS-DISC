"""チャット補完で使用するモデル ID の許可リストと解決ロジック。"""

from __future__ import annotations

# Chat Completions でよく使う ID（OpenAI アカウント・プランにより利用可否は異なる）
DEFAULT_ALLOWED_CHAT_MODELS: frozenset[str] = frozenset(
    {
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
        "o4-mini",
        "o3-mini",
    }
)


def resolve_chat_model(db_override: str, settings_default: str, allowed: frozenset[str]) -> str:
    """Supabase の current_model と settings の既定を許可リストで検証し、不正なら既定へフォールバック。"""
    db = (db_override or "").strip()
    if db and db in allowed:
        return db
    base = (settings_default or "").strip()
    if base and base in allowed:
        return base
    for preferred in ("gpt-4o-mini", "gpt-4.1-mini", "gpt-4o", "gpt-4.1"):
        if preferred in allowed:
            return preferred
    if allowed:
        return sorted(allowed)[0]
    return "gpt-4o-mini"


def format_allowed_models_hint(allowed: frozenset[str]) -> str:
    return ", ".join(sorted(allowed))
