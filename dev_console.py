"""開発用: LINE Webhook と同じ AIResponder(LineBrain).reply 経路をコンソールで試す。

本番デプロイ前に削除してください。
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from line_bot_app.ai import AIResponder
from line_bot_app.config import load_config
from line_bot_app.line_messages import split_line_text

# LINE 上の user_id に相当（履歴はこの ID で区切られる）
_CONSOLE_USER_ID = "console-dev"
_PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    load_dotenv(_PROJECT_ROOT / ".env")
    try:
        config = load_config()
    except RuntimeError as exc:
        print(exc)
        print()
        print("対処:")
        print(f"  1. 次のフォルダで実行しているか確認（ここに pyproject.toml と .env がある想定）:")
        print(f"     {_PROJECT_ROOT}")
        print("  2. .env が無い場合: .env.example をコピーし、LINE_* と OPENAI_API_KEY を埋める。")
        sys.exit(1)
    brain = AIResponder(config)

    print("DKIS-LL 開発コンソール（LINE と同じ応答生成経路）")
    print("終了: quit / exit / q / Ctrl+D / Ctrl+C")
    print("-" * 52)

    while True:
        try:
            line = input("あなた> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if line.lower() in {"quit", "exit", "q"}:
            break

        try:
            reply = brain.reply(_CONSOLE_USER_ID, line)
        except Exception as exc:
            print(f"ボット> （エラー）{exc}")
            print()
            continue

        chunks = split_line_text(reply)
        for i, chunk in enumerate(chunks):
            prefix = "ボット> " if i == 0 else "      … "
            print(f"{prefix}{chunk}")
        print()


if __name__ == "__main__":
    main()
