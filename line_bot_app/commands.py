"""SEARCH / NEWS / WEATHER / READ-PAGE / SPEAK（ツール結果は要約せずそのまま RI へ）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import AppConfig
from .google_cse import google_custom_search
from .news_rss import google_news_search
from .scrape_page import resolve_news_redirect, scrape_webpage
from .weather_openmeteo import fetch_weather_report


@dataclass
class CommandServices:
    config: AppConfig


def cmd_speak(_svc: CommandServices, _args: dict, TEXT: str, NOTE: str | None = None):
    return ((TEXT or "").strip(), "通常会話応答")


def cmd_save_log(_svc: CommandServices, _args: dict, TEXT: str, NOTE: str | None = None):
    return (
        TEXT or "クラウド版では会話ログのファイル保存は行えません。",
        "SAVE-LOG は無効",
    )


def cmd_search(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None):
    query = args.get("query", "").strip() if isinstance(args, dict) else ""
    if not query:
        return (TEXT or "").strip(), "SEARCH: query なし", "検索語が空です。", None, None

    if not svc.config.google_api_key or not svc.config.google_cx:
        msg = (
            "検索を使うにはサーバーに GOOGLE_API_KEY と GOOGLE_CX（Programmable Search の CX）が必要です。"
        )
        return (TEXT or "").strip(), "SEARCH: API 未設定", msg, None, None

    num = svc.config.google_search_num
    if isinstance(args, dict) and "result_count" in args:
        try:
            num = int(args["result_count"])
        except (TypeError, ValueError):
            pass

    blob = google_custom_search(svc.config.google_api_key, svc.config.google_cx, query, num=num)
    dmis_log = f"Google検索「{query}」"
    return TEXT or "", dmis_log, blob, blob, None


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
    dmis_log = f"ニュース検索「{query}」"
    return TEXT or "", dmis_log, blob, blob, None


def cmd_weather(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None):
    place = ""
    if isinstance(args, dict):
        place = (args.get("w_location") or "").strip()

    gps_aliases = ("現在地", "いまの場所", "ここ", "current", "here")
    if place.lower() in {x.lower() for x in gps_aliases}:
        place = ""

    if not place:
        msg = "(困) どこの天気が知りたいか、地名を教えてください、マスター。"
        return msg, "WEATHER: 地名なし", msg, {"__suppress_followup_retry__": True}, None

    blob = fetch_weather_report(place, timeout=svc.config.weather_api_timeout)
    dmis_log = f"天気: {place}"
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

    dmis_log = f"READ-PAGE: {target}"
    return TEXT or "", dmis_log, raw_text, raw_text, None


COMMAND_HANDLERS: dict[str, Any] = {
    "SPEAK": cmd_speak,
    "SAVE-LOG": cmd_save_log,
    "SEARCH": cmd_search,
    "NEWS": cmd_news,
    "WEATHER": cmd_weather,
    "READ-PAGE": cmd_read_page,
}
