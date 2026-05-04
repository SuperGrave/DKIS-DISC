"""AI応答テキストから DKIS 形式のコマンドブロックを抽出する。"""

from __future__ import annotations

import json
import re


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
        else:
            parsed[key] = first.strip().upper() if first else None

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

    return parsed
