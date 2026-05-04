import os
from dataclasses import dataclass

from .system_instruction import BUILTIN_SYSTEM_PROMPT


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AppConfig:
    line_channel_secret: str
    line_channel_access_token: str
    openai_api_key: str
    openai_model: str = "gpt-4.1-mini"
    summary_model: str = "gpt-4.1-mini"
    system_prompt: str = BUILTIN_SYSTEM_PROMPT
    google_api_key: str | None = None
    google_cx: str | None = None
    google_search_num: int = 5
    search_use_raw_result: bool = False
    news_use_raw_result: bool = False
    news_max_items: int = 12
    webpage_use_raw_result: bool = False
    default_weather_location: str = "東京"
    max_retry_chain: int = 5
    max_history_turns: int = 8


def load_config() -> AppConfig:
    missing = [
        name
        for name in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN", "OPENAI_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {joined}")

    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    summary_model = os.environ.get("OPENAI_SUMMARY_MODEL") or model

    return AppConfig(
        line_channel_secret=os.environ["LINE_CHANNEL_SECRET"],
        line_channel_access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"],
        openai_api_key=os.environ["OPENAI_API_KEY"],
        openai_model=model,
        summary_model=summary_model,
        system_prompt=os.environ.get("DKIS_SYSTEM_PROMPT") or BUILTIN_SYSTEM_PROMPT,
        google_api_key=(os.environ.get("GOOGLE_API_KEY") or "").strip() or None,
        google_cx=(os.environ.get("GOOGLE_CX") or "").strip() or None,
        google_search_num=int(os.environ.get("GOOGLE_SEARCH_NUM", "5")),
        search_use_raw_result=_env_bool("SEARCH_USE_RAW_RESULT", False),
        news_use_raw_result=_env_bool("NEWS_USE_RAW_RESULT", False),
        news_max_items=int(os.environ.get("NEWS_MAX_ITEMS", "12")),
        webpage_use_raw_result=_env_bool("WEBPAGE_USE_RAW_RESULT", False),
        default_weather_location=os.environ.get("DEFAULT_WEATHER_LOCATION", "東京"),
        max_retry_chain=int(os.environ.get("MAX_RETRY_CHAIN", "5")),
        max_history_turns=int(os.environ.get("MAX_HISTORY_TURNS", "8")),
    )
