"""Supabase: user_settings（全ユーザー共通）と memory_files（LINE user_id 別）。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

MAX_LINE_USER_ID_LEN = 128


def normalize_memory_user_id(uid: str | None) -> str:
    """LIST/READ/WRITE 系で memory_files.line_user_id に格納する値。"""
    u = (uid or "").strip()
    if not u:
        return "anonymous"
    if len(u) > MAX_LINE_USER_ID_LEN:
        u = u[:MAX_LINE_USER_ID_LEN]
    return u


_filename_safe_re = re.compile(r"^[\w\-\.\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf]+$")
MAX_FILENAME_LEN = 200
MAX_MEMORY_CONTENT_CHARS = 500_000

_client_cache: Any = None


def validate_memory_filename(name: str) -> str | None:
    """ディレクトリトラバーサル等を拒否し、許可文字のみ。"""
    n = (name or "").strip()
    if not n or len(n) > MAX_FILENAME_LEN:
        return None
    if ".." in n or "/" in n or "\\" in n or n.startswith("."):
        return None
    if not _filename_safe_re.match(n):
        return None
    return n


def supabase_configured() -> bool:
    return bool((os.environ.get("SUPABASE_URL") or "").strip() and (os.environ.get("SUPABASE_KEY") or "").strip())


def _client():
    global _client_cache
    if _client_cache is not None:
        return _client_cache
    if not supabase_configured():
        return None
    from supabase import create_client

    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    _client_cache = create_client(url, key)
    return _client_cache


def get_db_setting(key: str, default: str = "") -> str:
    """user_settings から取得。値は setting_key につき 1 つだけ（ボット全体・全ユーザー共通）。"""
    k = (key or "").strip()
    if not k:
        return default
    sb = _client()
    if sb is None:
        return default
    try:
        r = sb.table("user_settings").select("setting_value").eq("setting_key", k).limit(1).execute()
        rows = r.data or []
        if not rows:
            return default
        v = rows[0].get("setting_value")
        if v is None:
            return default
        s = str(v).strip()
        return s if s else default
    except Exception:
        return default


def set_db_setting(key: str, value: str) -> tuple[bool, str]:
    """user_settings に保存。変更はボット全体（すべての LINE ユーザー）に反映されます。"""
    k = (key or "").strip()
    if not k:
        return False, "setting_key が空です。"
    sb = _client()
    if sb is None:
        return False, "Supabase が未設定です（SUPABASE_URL / SUPABASE_KEY）。"
    try:
        sb.table("user_settings").upsert(
            {"setting_key": k, "setting_value": (value or "")},
            on_conflict="setting_key",
        ).execute()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_list_row(row: dict[str, Any]) -> dict[str, Any]:
    cc_raw = row.get("char_count")
    if cc_raw is None:
        cc_raw = row.get("content_chars")
    try:
        char_count = int(cc_raw) if cc_raw is not None else 0
    except (TypeError, ValueError):
        char_count = 0
    return {
        "filename": row.get("filename"),
        "description": row.get("description") or "",
        "updated_at": row.get("updated_at"),
        "char_count": max(0, char_count),
    }


def memory_list_files(line_user_id: str) -> tuple[list[dict[str, Any]], str]:
    uid = normalize_memory_user_id(line_user_id)
    sb = _client()
    if sb is None:
        return [], "Supabase が未設定です。"
    try:
        r = (
            sb.table("memory_files")
            .select("filename,description,updated_at,content_chars")
            .eq("line_user_id", uid)
            .order("filename")
            .execute()
        )
        rows = [_normalize_list_row(dict(row)) for row in (r.data or [])]
        return rows, ""
    except Exception:
        try:
            r = (
                sb.table("memory_files")
                .select("filename,description,updated_at,content")
                .eq("line_user_id", uid)
                .order("filename")
                .execute()
            )
            out: list[dict[str, Any]] = []
            for row in r.data or []:
                body = str(row.get("content") or "")
                slim = {
                    "filename": row.get("filename"),
                    "description": row.get("description") or "",
                    "updated_at": row.get("updated_at"),
                    "char_count": len(body),
                }
                out.append(slim)
            return out, ""
        except Exception as exc:
            return [], str(exc)


def memory_read_row(filename: str, line_user_id: str) -> tuple[dict[str, Any] | None, str]:
    fn = validate_memory_filename(filename)
    if fn is None:
        return None, "不正なファイル名です。"
    uid = normalize_memory_user_id(line_user_id)
    sb = _client()
    if sb is None:
        return None, "Supabase が未設定です。"
    try:
        r = (
            sb.table("memory_files")
            .select("filename,content,description,updated_at")
            .eq("filename", fn)
            .eq("line_user_id", uid)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        if not rows:
            return None, ""
        return rows[0], ""
    except Exception as exc:
        return None, str(exc)


def memory_write(filename: str, content: str, description: str, line_user_id: str) -> str:
    fn = validate_memory_filename(filename)
    if fn is None:
        return "不正なファイル名です。"
    uid = normalize_memory_user_id(line_user_id)
    body = content if isinstance(content, str) else ""
    if len(body) > MAX_MEMORY_CONTENT_CHARS:
        body = body[: MAX_MEMORY_CONTENT_CHARS - 80] + "\n…（content が長すぎるため切り詰めました）"
    sb = _client()
    if sb is None:
        return "Supabase が未設定です。"
    desc = (description or "").strip()[:5000]
    base_row = {
        "line_user_id": uid,
        "filename": fn,
        "content": body,
        "description": desc,
        "updated_at": _now_iso(),
    }
    try:
        sb.table("memory_files").upsert(
            {**base_row, "content_chars": len(body)},
            on_conflict="line_user_id,filename",
        ).execute()
        return ""
    except Exception:
        try:
            sb.table("memory_files").upsert(base_row, on_conflict="line_user_id,filename").execute()
            return ""
        except Exception as exc:
            return str(exc)


def memory_append(filename: str, append_content: str, line_user_id: str) -> str:
    fn = validate_memory_filename(filename)
    if fn is None:
        return "不正なファイル名です。"
    uid = normalize_memory_user_id(line_user_id)
    chunk = append_content if isinstance(append_content, str) else ""
    sb = _client()
    if sb is None:
        return "Supabase が未設定です。"
    try:
        r = (
            sb.table("memory_files")
            .select("content,description")
            .eq("filename", fn)
            .eq("line_user_id", uid)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        old = ""
        desc = ""
        if rows:
            old = str(rows[0].get("content") or "")
            desc = str(rows[0].get("description") or "")
        sep = "" if not old or old.endswith("\n") else "\n"
        merged = old + sep + chunk
        if len(merged) > MAX_MEMORY_CONTENT_CHARS:
            merged = merged[: MAX_MEMORY_CONTENT_CHARS - 80] + "\n…（結合後に長すぎるため切り詰めました）"
        base_row = {
            "line_user_id": uid,
            "filename": fn,
            "content": merged,
            "description": desc,
            "updated_at": _now_iso(),
        }
        try:
            sb.table("memory_files").upsert(
                {**base_row, "content_chars": len(merged)},
                on_conflict="line_user_id,filename",
            ).execute()
        except Exception:
            sb.table("memory_files").upsert(base_row, on_conflict="line_user_id,filename").execute()
        return ""
    except Exception as exc:
        return str(exc)


def dumps_memory_index(rows: list[dict[str, Any]]) -> str:
    slim = []
    for row in rows:
        cc = row.get("char_count")
        try:
            char_count = int(cc) if cc is not None else 0
        except (TypeError, ValueError):
            char_count = 0
        slim.append(
            {
                "filename": row.get("filename"),
                "description": row.get("description") or "",
                "updated_at": row.get("updated_at"),
                "char_count": max(0, char_count),
            }
        )
    return json.dumps(slim, ensure_ascii=False)
