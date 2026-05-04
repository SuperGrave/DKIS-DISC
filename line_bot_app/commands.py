"""SEARCH / NEWS / WEATHER / READ-PAGE / SPEAK などコマンド実装（音声なし）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from .config import AppConfig
from .google_cse import google_custom_search
from .news_rss import google_news_search
from .scrape_page import resolve_news_redirect, scrape_webpage
from .summaries import summarize_news_results, summarize_search_results, summarize_webpage
from .weather_openmeteo import weather_for_place


@dataclass
class CommandServices:
    config: AppConfig
    client: OpenAI
    last_user_input: str = ""


def cmd_speak(_svc: CommandServices, _args: dict, TEXT: str, NOTE: str | None = None):
    return ((TEXT or "").strip(), "通常会話応答")


def cmd_save_log(_svc: CommandServices, _args: dict, TEXT: str, NOTE: str | None = None):
    return (
        TEXT or "クラウド版では会話ファイルへの保存はできませんが、必要な内容はこのチャットでお伝えします。",
        "SAVE-LOG は無効（ホスト保存なし）",
    )


def cmd_search(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None):
    query = args.get("query", "").strip() if isinstance(args, dict) else ""
    if not query:
        return (TEXT or "").strip(), "SEARCH: query なし", "検索語が空です。もう一度お願いします。", None, None

    if not svc.config.google_api_key or not svc.config.google_cx:
        msg = (
            "検索機能を使うには、サーバーに GOOGLE_API_KEY と GOOGLE_CX（Programmable Search の CX）"
            "が設定されている必要があります。"
        )
        return (TEXT or "").strip(), "SEARCH: API 未設定", msg, None, None

    num = svc.config.google_search_num
    if isinstance(args, dict) and "result_count" in args:
        try:
            num = int(args["result_count"])
        except (TypeError, ValueError):
            pass

    blob = google_custom_search(svc.config.google_api_key, svc.config.google_cx, query, num=num)
    note = NOTE or args.get("note", "") if isinstance(args, dict) else ""

    if svc.config.search_use_raw_result:
        summary = blob
    else:
        summary = summarize_search_results(
            svc.client,
            svc.config.summary_model,
            query,
            note,
            blob,
        )

    dmis_log = f"Google検索「{query}」"
    return TEXT or "", dmis_log, summary, blob, None


def cmd_news(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None):
    query = args.get("query", "").strip() if isinstance(args, dict) else ""
    if not query:
        return (TEXT or "").strip(), "NEWS: query なし", "ニュースのキーワードが空です。", None, None

    location = args.get("location", "").strip() if isinstance(args, dict) else ""
    time_filter = args.get("time_filter", "").strip().lower() if isinstance(args, dict) else ""
    if time_filter not in ("today", "week", "month"):
        time_filter = None

    max_items = svc.config.news_max_items
    if isinstance(args, dict) and "max_items" in args:
        try:
            max_items = int(args["max_items"])
        except (TypeError, ValueError):
            pass

    blob = google_news_search(query, location=location or None, time_filter=time_filter, max_items=max_items)
    note = NOTE or args.get("note", "") if isinstance(args, dict) else ""

    if svc.config.news_use_raw_result:
        summary = blob
    else:
        summary = summarize_news_results(svc.client, svc.config.summary_model, query, note, blob)

    dmis_log = f"ニュース検索「{query}」"
    return TEXT or "", dmis_log, summary, blob, None


def cmd_weather(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None):
    place = ""
    if isinstance(args, dict):
        place = (args.get("w_location") or "").strip()

    blob = weather_for_place(place, default_place=svc.config.default_weather_location)
    dmis_log = f"天気: {place or '（既定地点）'}"
    return TEXT or "", dmis_log, blob, blob, None


def cmd_read_page(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None):
    url = args.get("url", "").strip() if isinstance(args, dict) else ""
    if not url:
        return (
            (TEXT or "").strip(),
            "READ-PAGE: url なし",
            "ページ URL が空です。https:// から始まる URL を指定してください。",
            None,
            None,
        )

    target = resolve_news_redirect(url)
    raw_text = scrape_webpage(target)
    if raw_text.startswith("エラー:") or raw_text.startswith("本文が抽出できませんでした"):
        return TEXT or "", f"READ-PAGE 失敗: {target}", raw_text, raw_text, None

    note = NOTE or args.get("note", "") if isinstance(args, dict) else ""
    user_q = svc.last_user_input or ""

    if svc.config.webpage_use_raw_result:
        summary = raw_text
    else:
        summary = summarize_webpage(svc.client, svc.config.summary_model, target, user_q, note, raw_text)

    dmis_log = f"READ-PAGE: {target}"
    return TEXT or "", dmis_log, summary, raw_text, None


COMMAND_HANDLERS: dict[str, Any] = {
    "SPEAK": cmd_speak,
    "SAVE-LOG": cmd_save_log,
    "SEARCH": cmd_search,
    "NEWS": cmd_news,
    "WEATHER": cmd_weather,
    "READ-PAGE": cmd_read_page,
}
