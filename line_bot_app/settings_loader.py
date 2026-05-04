"""dist/settings.json を読み込み、LINE ボット実行パラメータを組み立てる。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InputFormatMain:
    mode: str
    include_last_result: bool
    include_location: bool
    include_time: bool
    labels: dict[str, str]
    nl_static: str


@dataclass(frozen=True)
class JsonSettings:
    path: Path
    system_prompt_main: str
    openai_model: str
    max_retry_chain: int
    max_history_turns: int
    google_search_num: int
    news_max_items: int
    weather_api_timeout: float
    input_main: InputFormatMain


def resolve_settings_path() -> Path:
    env = os.environ.get("DKIS_SETTINGS_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / "dist" / "settings.json").resolve()


def load_json_settings() -> JsonSettings:
    path = resolve_settings_path()
    if not path.is_file():
        raise RuntimeError(f"設定ファイルが見つかりません: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))

    ai_models = raw.get("ai_models") or {}
    control = raw.get("control") or {}
    search = raw.get("search") or {}
    news = raw.get("news") or {}
    weather = raw.get("weather") or {}
    system_prompts = raw.get("system_prompts") or {}
    input_fmt = raw.get("input_format") or {}
    main_if = input_fmt.get("main") or {}

    labels = dict(main_if.get("labels") or {})
    include = main_if.get("include") or {}

    nl_static = str(main_if.get("nl_static") or "").strip()
    if not nl_static:
        nl_static = (
            "LINEのためサーバーはGPSを取得できません。"
            "天気は WEATHER の w_location に、マスターの発話または文脈から特定した具体的地名を必ず入れてください。"
            "地名が断定できないときは WEATHER を実行せず、SPEAK で地名を伺ってください。"
        )

    input_main = InputFormatMain(
        mode=str(main_if.get("mode") or "all").lower(),
        include_last_result=bool(include.get("last_result", True)),
        include_location=bool(include.get("location", True)),
        include_time=bool(include.get("time", True)),
        labels=labels,
        nl_static=nl_static,
    )

    return JsonSettings(
        path=path,
        system_prompt_main=str(system_prompts.get("main") or "").strip()
        or "システムプロンプト（system_prompts.main）が空です。dist/settings.json を確認してください。",
        openai_model=str(ai_models.get("main") or "gpt-4.1-mini"),
        max_retry_chain=max(1, int(control.get("max_retries", 5))),
        max_history_turns=max(1, int(control.get("max_history", 8))),
        google_search_num=max(1, min(10, int(search.get("result_count", 5)))),
        news_max_items=max(1, min(50, int(news.get("max_items", 10)))),
        weather_api_timeout=float(weather.get("api_timeout", 12)),
        input_main=input_main,
    )
