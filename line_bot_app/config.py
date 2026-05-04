import os
from dataclasses import dataclass

from .settings_loader import InputFormatMain, load_json_settings


@dataclass(frozen=True)
class AppConfig:
    line_channel_secret: str
    line_channel_access_token: str
    openai_api_key: str
    settings_source: str
    system_prompt: str
    openai_model: str
    max_retry_chain: int
    max_history_turns: int
    google_search_num: int
    news_max_items: int
    weather_api_timeout: float
    input_main: InputFormatMain
    google_api_key: str | None
    google_cx: str | None


def load_config() -> AppConfig:
    missing = [
        name
        for name in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN", "OPENAI_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {joined}")

    js = load_json_settings()

    return AppConfig(
        line_channel_secret=os.environ["LINE_CHANNEL_SECRET"],
        line_channel_access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"],
        openai_api_key=os.environ["OPENAI_API_KEY"],
        settings_source=str(js.path),
        system_prompt=js.system_prompt_main,
        openai_model=js.openai_model,
        max_retry_chain=js.max_retry_chain,
        max_history_turns=js.max_history_turns,
        google_search_num=js.google_search_num,
        news_max_items=js.news_max_items,
        weather_api_timeout=js.weather_api_timeout,
        input_main=js.input_main,
        google_api_key=(os.environ.get("GOOGLE_API_KEY") or "").strip() or None,
        google_cx=(os.environ.get("GOOGLE_CX") or "").strip() or None,
    )
