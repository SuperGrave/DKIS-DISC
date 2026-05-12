"""AI応答テキストから DKIS 形式のコマンドブロックを抽出する。"""

from __future__ import annotations

import json
import re


def format_model_response_block(text: str, *, note: str = "雑談と判断。発話のみ。") -> str:
    """履歴用など、モデル出力と同型の SPEAK ブロックを組み立てる。"""
    t = text or ""
    return (
        "[CMD]SPEAK\n"
        "[ARGS]none\n"
        '[ARGS-2]{"retry": false}\n'
        f"[NOTE]{note}\n"
        f"[TEXT]{t}"
    )


def parse_ai_response(text: str) -> dict:
    """
    生テキストからタグを抽出する。
    - [CMD] / [COMMAND] をどちらも受理
    - [ARGS-2] は JSON 推奨（例: {"retry": true}）
    - [TEXT], [NOTE] は複数行対応
    """
    pattern = re.compile(r"^\[(CMD|COMMAND|ARGS|ARGS-2|TEXT|NOTE)\](.*)$")
    parsed: dict = {
        "CMD": None,
        "ARGS": {},
        "ARGS_2": {"retry": False},
        "TEXT": "",
        "NOTE": "",
    }

    explicit_cmd = False
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = pattern.match(lines[i].strip())
        if not m:
            i += 1
            continue

        key, first = m.groups()
        key = key.replace("-", "_")
        first = first.lstrip()

        if key in ("TEXT", "NOTE"):
            buf = [first] if first else []
            i += 1
            while i < len(lines) and not pattern.match(lines[i].strip()):
                buf.append(lines[i])
                i += 1
            parsed[key] = "\n".join(buf).strip()
            continue

        if key in ("ARGS", "ARGS_2"):
            if not first or first.lower() == "none":
                parsed[key] = {}
            else:
                try:
                    parsed[key] = json.loads(first)
                except Exception:
                    parsed[key] = {}
            i += 1
            continue

        val = first.strip().upper() if first else None
        parsed[key] = val
        if key in ("CMD", "COMMAND") and val:
            explicit_cmd = True
        i += 1

    if not parsed.get("CMD") and parsed.get("COMMAND"):
        parsed["CMD"] = parsed["COMMAND"]

    a2 = parsed.get("ARGS_2")
    if isinstance(a2, dict):
        a2.setdefault("retry", False)
    else:
        parsed["ARGS_2"] = {"retry": False}

    if parsed.get("TEXT"):
        parsed["TEXT"] = re.sub(r"\[\s*\]$", "", parsed["TEXT"]).strip()
    if parsed.get("NOTE"):
        parsed["NOTE"] = re.sub(r"\[\s*\]$", "", parsed["NOTE"]).strip()

    parsed["__dkis_parse_ok__"] = explicit_cmd
    return parsed
