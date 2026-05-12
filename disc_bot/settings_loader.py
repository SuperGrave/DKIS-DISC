"""dist/settings.json を読み込み、Discord ボット実行パラメータを組み立てる。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .chat_models import DEFAULT_ALLOWED_CHAT_MODELS, DEFAULT_CHAT_MODEL, resolve_chat_model


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
    allowed_chat_models: frozenset[str]
    max_retry_chain: int
    max_history_turns: int
    max_retry_payload_chars: int
    google_search_num: int
    news_max_items: int
    weather_api_timeout: float
    input_main: InputFormatMain
    restart_push_enabled: bool


def resolve_settings_path() -> Path:
    env = os.environ.get("DKIS_SETTINGS_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / "dist" / "settings.json").resolve()


def _load_system_prompt_main(system_prompts: dict, settings_path: Path) -> str:
    """main_file（settings と同じディレクトリを基準とする相対パス）を優先。無ければ main（JSON 内文字列）。"""
    base = settings_path.parent
    main_file = str(system_prompts.get("main_file") or "").strip()
    inline = str(system_prompts.get("main") or "").strip()

    if main_file:
        file_path = (base / main_file).resolve()
        if not file_path.is_file():
            raise RuntimeError(
                f"system_prompts.main_file が見つかりません: {file_path} "
                f"（settings.json と同じフォルダを基準にしています）"
            )
        text = file_path.read_text(encoding="utf-8").strip()
        if not text:
            raise RuntimeError(f"システムプロンプトファイルが空です: {file_path}")
        return text

    return inline


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
    notifications = raw.get("notifications") or {}
    system_prompts = raw.get("system_prompts") or {}
    input_fmt = raw.get("input_format") or {}
    main_if = input_fmt.get("main") or {}

    labels = dict(main_if.get("labels") or {})
    include = main_if.get("include") or {}

    nl_static = str(main_if.get("nl_static") or "").strip()
    if not nl_static:
        nl_static = (
            "DiscordのためサーバーはGPSを取得できません。"
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

    prompt_main = _load_system_prompt_main(system_prompts, path).strip()
    if not prompt_main:
        prompt_main = (
            "システムプロンプトが空です。"
            "dist/settings.json の system_prompts.main_file または main を確認してください。"
        )

    main_raw = str(ai_models.get("main") or DEFAULT_CHAT_MODEL).strip()
    raw_allow = ai_models.get("allowed_chat_models")
    if isinstance(raw_allow, list) and raw_allow:
        allowed_chat_models = frozenset(str(x).strip() for x in raw_allow if str(x).strip())
    else:
        allowed_chat_models = DEFAULT_ALLOWED_CHAT_MODELS
    main_model = resolve_chat_model(main_raw, DEFAULT_CHAT_MODEL, allowed_chat_models)
    allowed_chat_models = allowed_chat_models | {main_model}

    restart_push_enabled = bool(notifications.get("restart_push_enabled", True))

    return JsonSettings(
        path=path,
        system_prompt_main=prompt_main,
        openai_model=main_model,
        allowed_chat_models=allowed_chat_models,
        max_retry_chain=max(1, int(control.get("max_retries", 10))),
        max_history_turns=max(1, int(control.get("max_history", 24))),
        max_retry_payload_chars=max(2000, int(control.get("max_retry_payload_chars", 10000))),
        google_search_num=max(1, min(10, int(search.get("result_count", 5)))),
        news_max_items=max(1, min(50, int(news.get("max_items", 10)))),
        weather_api_timeout=float(weather.get("api_timeout", 12)),
        input_main=input_main,
        restart_push_enabled=restart_push_enabled,
    )
