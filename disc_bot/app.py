"""後方互換エントリ。実行入口は main.py の create_bot。"""

from __future__ import annotations

from main import create_bot


def create_app():
    """後方互換名。Discord bot クライアントを返す。"""
    return create_bot()
