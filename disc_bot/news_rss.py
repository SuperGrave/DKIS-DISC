"""Google News RSS（APIキー不要）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import quote_plus
from xml.etree import ElementTree
import time

import requests

_NEWS_FETCH_ATTEMPTS = 4
_NEWS_RETRY_STATUS = frozenset({429, 502, 503, 504})


_DEFAULT_HEADERS = {
    # Google News RSS はデフォルトの Python UA でブロック・異常応答になりやすい。
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def _fetch_news_rss_body(url: str, *, timeout: float) -> tuple[bytes | None, str | None]:
    """HTTP GET を試し、(bytes, None) または (None, エラー説明) を返す。429/502/503/504 と接続系は指数バックオフで再試行。"""
    last_err: str | None = None
    for attempt in range(_NEWS_FETCH_ATTEMPTS):
        try:
            r = requests.get(url, timeout=timeout, headers=_DEFAULT_HEADERS)
            if r.status_code in _NEWS_RETRY_STATUS:
                last_err = f"{r.status_code} Server Error"
                if attempt < _NEWS_FETCH_ATTEMPTS - 1:
                    time.sleep(min(8.0, 1.6 * (2**attempt)))
                    continue
                return None, last_err
            r.raise_for_status()
            return r.content, None
        except requests.RequestException as e:
            last_err = str(e)
            if attempt < _NEWS_FETCH_ATTEMPTS - 1:
                time.sleep(min(8.0, 1.6 * (2**attempt)))
                continue
            return None, last_err
    return None, last_err or "リクエストに失敗しました"


def google_news_search(
    query: str,
    *,
    location: str | None = None,
    time_filter: str | None = None,
    max_items: int = 15,
    timeout: float = 15.0,
) -> str:
    q = query.strip()
    if location:
        q = f"{q} {location}"
    encoded = quote_plus(q)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
    try:
        body, fetch_err = _fetch_news_rss_body(url, timeout=timeout)
        if body is None:
            return (
                f"ニュース取得エラー: {fetch_err}。\n"
                "Google News RSS はクラウドホスト（Render 等）の出口 IP から "
                "503／429 を返しやすいことがあります（サーバー側のレート制限や一時障害）。"
                "しばらくしてから再度 NEWS を試すか、GOOGLE_API_KEY と GOOGLE_CX を設定して SEARCH で調べる方法もあります。"
            )
        head_snip = body[:1200].lstrip().lower()
        if b"<rss" not in head_snip and not body.lstrip().startswith(b"<?xml"):
            return (
                "ニュース取得エラー: RSS形式ではない応答でした。"
                "（データセンターからのアクセスが Google 側で制限されている可能性があります。"
                "SEARCH用に GOOGLE_API_KEY と GOOGLE_CX を設定すると別経路で補えます。）"
            )
        root = ElementTree.fromstring(body)
        items = root.findall(".//item") or root.findall("channel/item")
        cutoff = None
        if time_filter:
            now = datetime.utcnow()
            if time_filter == "today":
                cutoff = now - timedelta(days=1)
            elif time_filter == "week":
                cutoff = now - timedelta(days=7)
            elif time_filter == "month":
                cutoff = now - timedelta(days=30)

        output_parts: list[str] = []
        count = 0
        max_items = max(1, min(50, int(max_items)))

        for item in items:
            if count >= max_items:
                break
            title_el = item.find("title")
            link_el = item.find("link")
            pub_el = item.find("pubDate")
            source_el = item.find("source")
            title = (title_el.text or "").strip() if title_el is not None else ""
            link = (link_el.text or "").strip() if link_el is not None else ""
            pub_str = (pub_el.text or "").strip() if pub_el is not None else ""
            source = (source_el.text or "").strip() if source_el is not None else ""

            if cutoff and pub_str:
                try:
                    pub_dt = datetime.strptime(pub_str[:25], "%a, %d %b %Y %H:%M:%S")
                    if pub_dt.replace(tzinfo=None) < cutoff:
                        continue
                except Exception:
                    pass

            if title or link:
                line = f"🔹{title}\n{link}"
                if source:
                    line += f"\n出典: {source}"
                output_parts.append(line)
                count += 1

        if not output_parts:
            return "該当するニュースは見つかりませんでした。"
        return "\n\n".join(output_parts)
    except Exception as e:
        return f"ニュース取得エラー: {e}"
