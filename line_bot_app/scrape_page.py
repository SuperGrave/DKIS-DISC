"""Webページ本文の抽出（trafilatura）。"""

from __future__ import annotations

import requests

MAX_RAW_CHARS = 10000


def resolve_news_redirect(url: str, *, timeout: float = 15.0) -> str:
    """Google News のリダイレクトのみ軽く追う。失敗時は元 URL を返す。"""
    if not url or "news.google.com" not in url:
        return url
    try:
        r = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
            allow_redirects=True,
        )
        return r.url or url
    except Exception:
        return url


def scrape_webpage(url: str) -> str:
    try:
        import trafilatura
    except ImportError:
        return "エラー: trafilatura がインストールされていません。"

    if not url or not isinstance(url, str):
        return "エラー: 有効なURLが指定されていません。"

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return "エラー: URLは http:// または https:// で始まる必要があります。"

    try:
        html = trafilatura.fetch_url(url)
        if not html:
            return "エラー: ページの取得に失敗しました（タイムアウトや無効なURLの可能性があります）。"
        text = trafilatura.extract(html, include_comments=False, include_tables=True)
        if not text or not text.strip():
            return "本文が抽出できませんでした。"
        text = text.strip()
        if len(text) > MAX_RAW_CHARS:
            text = text[:MAX_RAW_CHARS] + "\n\n[※文字数制限のため省略]"
        return text
    except Exception as e:
        err_msg = str(e).lower()
        if "403" in err_msg or "forbidden" in err_msg:
            return "エラー: アクセスが拒否されました（403 Forbidden）。"
        if "404" in err_msg or "not found" in err_msg:
            return "エラー: ページが見つかりませんでした（404 Not Found）。"
        if "timeout" in err_msg or "timed out" in err_msg:
            return "エラー: リクエストがタイムアウトしました。"
        return f"エラー: ページの取得・抽出に失敗しました。（{e}）"
