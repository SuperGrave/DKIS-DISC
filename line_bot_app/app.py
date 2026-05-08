"""旧 Flask Webhook 入口の互換モジュール。

Discord 版では `main.create_bot` が実行入口です。
"""

from __future__ import annotations

from main import create_bot


def create_app():
    """後方互換名。Discord bot クライアントを返す。"""
    return create_bot()
