"""チャット補完で使用するモデル ID の許可リストと解決ロジック。"""

from __future__ import annotations

# プロセス再起動時や DB 値が無効なときに返す既定（運用で main と揃える）
DEFAULT_CHAT_MODEL = "gpt-5.4-mini"

# Chat Completions ID（OpenAI の公開モデル一覧に準拠。アカウントにより利用可否は異なる）
DEFAULT_ALLOWED_CHAT_MODELS: frozenset[str] = frozenset(
    {
        "gpt-5.5",
        "gpt-5.5-pro",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.4-pro",
        "gpt-5.2",
        "gpt-5.2-pro",
        "gpt-5.1",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5-pro",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
    }
)

# DB / 設定が空または許可外のとき、許可リストから選ぶ優先順（軽めを先に）
_PREFERRED_FALLBACK_ORDER: tuple[str, ...] = (
    DEFAULT_CHAT_MODEL,
    "gpt-5-nano",
    "gpt-5-mini",
    "gpt-4.1-nano",
    "gpt-4.1-mini",
    "gpt-5.4-nano",
    "gpt-5.4",
    "gpt-5.1",
    "gpt-5",
    "gpt-5.2",
    "gpt-5-pro",
    "gpt-5.2-pro",
    "gpt-5.4-pro",
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-4.1",
)


def resolve_chat_model(db_override: str, settings_default: str, allowed: frozenset[str]) -> str:
    """Supabase の current_model と settings の既定を許可リストで検証し、不正なら既定へフォールバック。"""
    db = (db_override or "").strip()
    if db and db in allowed:
        return db
    base = (settings_default or "").strip()
    if base and base in allowed:
        return base
    for preferred in _PREFERRED_FALLBACK_ORDER:
        if preferred in allowed:
            return preferred
    if allowed:
        return sorted(allowed)[0]
    return DEFAULT_CHAT_MODEL


def format_allowed_models_hint(allowed: frozenset[str]) -> str:
    return ", ".join(sorted(allowed))
