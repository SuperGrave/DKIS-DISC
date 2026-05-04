"""パッケージ直下では LINE SDK（Flask アプリ）を即時 import しない。
`from line_bot_app.ai import ...` など軽いモジュールだけ使う経路を軽くする。"""

from __future__ import annotations

__all__ = ["create_app"]


def __getattr__(name: str):
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
