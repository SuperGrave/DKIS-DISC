import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    line_channel_secret: str
    line_channel_access_token: str
    openai_api_key: str
    openai_model: str = "gpt-4.1-mini"
    system_prompt: str = (
        "あなたはLINEでユーザーと会話するAIメイド「DKIS」です。"
        "音声、画像生成、ファイル操作、Web画面操作は行わず、テキストだけで自然に返答してください。"
        "返答はLINEで読みやすい長さにまとめ、必要以上に内部仕様を説明しないでください。"
    )


def load_config() -> AppConfig:
    missing = [
        name
        for name in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN", "OPENAI_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {joined}")

    return AppConfig(
        line_channel_secret=os.environ["LINE_CHANNEL_SECRET"],
        line_channel_access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"],
        openai_api_key=os.environ["OPENAI_API_KEY"],
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
        system_prompt=os.environ.get("DKIS_SYSTEM_PROMPT", AppConfig.system_prompt),
    )
