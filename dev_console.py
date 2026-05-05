"""開発用: LINE Webhook と同じ AIResponder(LineBrain).reply 経路をコンソールで試す。

本番デプロイ前に削除してください。
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from line_bot_app.ai import AIResponder
from line_bot_app.config import load_config

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
        config = load_config(require_line_credentials=False)
    except RuntimeError as exc:
        print(exc)
        print()
        print("対処:")
        print(f"  1. 次のフォルダで実行しているか確認（ここに pyproject.toml と .env がある想定）:")
        print(f"     {_PROJECT_ROOT}")
        print("  2. .env に OPENAI_API_KEY=（イコールの右にキーを貼り付け）を記入。")
        print("     （開発コンソールでは LINE の値は不要。Webhook / main.py では必須。）")
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
            first_bubble = True

            def on_line(text: str) -> None:
                nonlocal first_bubble
                prefix = "ボット> " if first_bubble else "      … "
                first_bubble = False
                print(f"{prefix}{text}")

            brain.reply(_CONSOLE_USER_ID, line, on_line_message=on_line)
        except Exception as exc:
            print(f"ボット> （エラー）{exc}")
        print()


if __name__ == "__main__":
    main()
