# core/scraper.py
# Webページ本文抽出（trafilatura使用）
# pip install trafilatura が必要

"""
指定URLから本文を抽出するスクレイピングモジュール。
trafilatura を使用してHTMLからメインコンテンツを取得する。
"""

# 文字数制限（Rawモード時、GPTコンテキスト考慮）
MAX_RAW_CHARS = 10000


def scrape_webpage(url: str, use_summary: bool = True) -> str:
    """
    指定URLからWebページの本文を抽出する。

    Args:
        url (str): 対象のURL
        use_summary (bool): Trueなら要約AIを通す。Falseなら抽出した生テキストを返す（文字数制限あり）。

    Returns:
        str: 最終的なテキスト（エラー時はエラーメッセージ）
    """
    try:
        import trafilatura
    except ImportError:
        return "エラー: trafilatura がインストールされていません。pip install trafilatura を実行してください。"

    if not url or not isinstance(url, str):
        return "エラー: 有効なURLが指定されていません。"

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return "エラー: URLは http:// または https:// で始まる必要があります。"

    try:
        # HTML取得（trafilatura内蔵のfetch）
        html = trafilatura.fetch_url(url)
        if not html:
            return "エラー: ページの取得に失敗しました（タイムアウト、接続エラー、または無効なURLの可能性があります）。"

        # 本文抽出
        text = trafilatura.extract(html, include_comments=False, include_tables=True)
        if not text or not text.strip():
            return "本文が抽出できませんでした。"

        text = text.strip()

        # use_summary=False の場合: 文字数制限で切り詰め（GPTコンテキスト考慮）
        if not use_summary:
            if len(text) > MAX_RAW_CHARS:
                text = text[:MAX_RAW_CHARS] + "\n\n[※文字数制限のため、以降は省略されています]"
        # use_summary=True の場合は生テキストをそのまま返す（要約は read_webpage 側で実施）
        return text

    except Exception as e:
        err_msg = str(e).lower()
        if "403" in err_msg or "forbidden" in err_msg:
            return "エラー: アクセスが拒否されました（403 Forbidden）。"
        if "404" in err_msg or "not found" in err_msg:
            return "エラー: ページが見つかりませんでした（404 Not Found）。"
        if "timeout" in err_msg or "timed out" in err_msg:
            return "エラー: リクエストがタイムアウトしました。"
        if "connection" in err_msg or "connect" in err_msg:
            return "エラー: サーバーに接続できませんでした。"
        return f"エラー: ページの取得・抽出に失敗しました。（{e}）"
