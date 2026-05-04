"""Google Programmable Search Engine (Custom Search JSON API)."""

from __future__ import annotations

import requests


def google_custom_search(
    api_key: str,
    cx: str,
    query: str,
    num: int = 5,
    timeout: float = 15.0,
) -> str:
    num = max(1, min(10, int(num)))
    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "num": num,
        "hl": "ja",
    }
    try:
        res = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
            timeout=timeout,
        )
        res.raise_for_status()
        results = res.json().get("items", []) or []
        output = ""
        for item in results:
            title = item.get("title")
            snippet = item.get("snippet")
            link = item.get("link")
            output += f"🔹{title}\n{snippet}\n{link}\n\n"
        return output.strip() if output else "検索結果は見つかりませんでした。"
    except Exception as e:
        return f"検索エラー: {e}"
