"""検索・ニュース・Webページの中間要約（2段目 RETRY 用）。"""

from __future__ import annotations

from openai import OpenAI

_SEARCH_SUMMARY_PROMPT = """あなたは検索結果を整理するアシスタントです。
与えられた検索スニペットと URL を踏まえ、事実関係を崩さずに要約してください。
・憶測やハルシネーションは禁止
・ユーザーへの挨拶やキャラクター演技は不要（情報のみ）
・箇条書きか短い段落で、LINE で読みやすい長さに収める
"""

_NEWS_SUMMARY_PROMPT = """あなたはニュース一覧を整理するアシスタントです。
重要度が高そうな順に、見出し・要点・リンクを読みやすくまとめてください。
憶測はせず、入力にない情報は書かないでください。
"""

_WEB_SUMMARY_PROMPT = """あなたは Web ページ本文から情報を抜き出すアシスタントです。
ユーザーの質問意図に役立つ事実だけを、過不足なく日本語でまとめてください。
ページにない内容は書かないでください。
"""


def _one_shot(client: OpenAI, model: str, system: str, user: str) -> str:
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def summarize_search_results(
    client: OpenAI,
    model: str,
    query: str,
    note: str,
    search_blob: str,
) -> str:
    user = (
        f"目的・メモ: {note or '一般的な検索'}\n"
        f"検索ワード: {query}\n---\n【検索結果】\n{search_blob}"
    )
    return _one_shot(client, model, _SEARCH_SUMMARY_PROMPT, user)


def summarize_news_results(
    client: OpenAI,
    model: str,
    query: str,
    note: str,
    news_blob: str,
) -> str:
    user = (
        f"目的・メモ: {note or 'ニュースまとめ'}\n"
        f"検索ワード: {query}\n---\n【ニュース一覧】\n{news_blob}"
    )
    return _one_shot(client, model, _NEWS_SUMMARY_PROMPT, user)


def summarize_webpage(
    client: OpenAI,
    model: str,
    url: str,
    user_question: str,
    note: str,
    page_text: str,
) -> str:
    user = (
        f"ページURL: {url}\n"
        f"ユーザー発話（質問のヒント）: {user_question}\n"
        f"メモ: {note}\n---\n【ページ本文】\n{page_text}"
    )
    return _one_shot(client, model, _WEB_SUMMARY_PROMPT, user)
