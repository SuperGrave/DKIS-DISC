"""Google News RSS（APIキー不要）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests


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
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        root = ElementTree.fromstring(r.content)
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
