import os
from dataclasses import dataclass

from .settings_loader import InputFormatMain, load_json_settings


@dataclass(frozen=True)
class AppConfig:
    openai_api_key: str
    settings_source: str
    system_prompt: str
    openai_model: str
    allowed_chat_models: frozenset[str]
    max_retry_chain: int
    max_history_turns: int
    max_retry_payload_chars: int
    google_search_num: int
    news_max_items: int
    weather_api_timeout: float
    input_main: InputFormatMain
    google_api_key: str | None
    google_cx: str | None
    restart_push_enabled: bool


_OPENAI_PLACEHOLDER = "unused-openai-not-configured"


def load_config(
    *,
    require_discord_credentials: bool = True,
    require_openai: bool = True,
    require_line_credentials: bool | None = None,
) -> AppConfig:
    """Discord 起動時は OpenAI の必須変数をチェックする。開発コンソールだけ試すときは False にできる。
    Render 初回デプロイで ENV が空のときは require_* を False にしプレースホルダで起動できる。"""
    if require_line_credentials is not None:
        require_discord_credentials = require_line_credentials
    _ = require_discord_credentials
    missing: list[str] = []
    if require_openai:
        missing.extend(
            name
            for name in ("OPENAI_API_KEY",)
            if not (os.environ.get(name) or "").strip()
        )
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {joined}")

    js = load_json_settings()

    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not openai_key and not require_openai:
        openai_key = _OPENAI_PLACEHOLDER

    return AppConfig(
        openai_api_key=openai_key,
        settings_source=str(js.path),
        system_prompt=js.system_prompt_main,
        openai_model=js.openai_model,
        allowed_chat_models=js.allowed_chat_models,
        max_retry_chain=js.max_retry_chain,
        max_history_turns=js.max_history_turns,
        max_retry_payload_chars=js.max_retry_payload_chars,
        google_search_num=js.google_search_num,
        news_max_items=js.news_max_items,
        weather_api_timeout=js.weather_api_timeout,
        input_main=js.input_main,
        google_api_key=(os.environ.get("GOOGLE_API_KEY") or "").strip() or None,
        google_cx=(os.environ.get("GOOGLE_CX") or "").strip() or None,
        restart_push_enabled=js.restart_push_enabled,
    )
