"""Supabase: user_settings（全ユーザー共通）、memory_files（Discord user_id 別）、known_line_users（中期記憶・起動通知オプトイン等）。

DB の既存テーブル名・列名は LINE 版からの互換名を維持する。
"""

from __future__ import annotations

import json
import os
import re
import time
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
MID_TERM_NOTE_MAX_CHARS = 200
USER_MODEL_CHOICES = {
    "1": "gpt-4.1-mini",
    "2": "gpt-5.4-mini",
    "3": "gpt-4o",
    "4": "gpt-5.2",
    "5": "gpt-5.4",
}
TALKING_MEMORY_TURNS = {"1": 15, "2": 10, "3": 5, "4": 0}
USER_RESPONSE_MODES = {"1": "normal", "2": "mention"}
PROCESS_NOTICE_MODES = {"1": "hidden", "2": "abbrev", "3": "full", "4": "raw"}
USER_ROLES = frozenset({"visitor", "member", "operator"})
ROLE_DAILY_TOKEN_LIMITS = {"visitor": 100, "member": 100_000, "operator": 100_000}
DEFAULT_USER_SETTINGS = {
    "talk_with_kiritan": "true",
    "talking_memory": "1",
    "using_model": "2",
    "personal_memory": "false",
    "response_criteria": "1",
    "user_role": "visitor",
}
CHANNEL_ENABLED_VALUES = frozenset({"on", "off", "enabled", "disabled", "true", "false", "1", "0", "yes", "no"})
CHANNEL_RESPONSE_MODES = frozenset({"inherit", "normal", "mention", "off"})
CHANNEL_TOOL_NOTICE_VALUES = frozenset({"inherit", "full", "abbrev", "minimal", "hidden", "raw"})
CHANNEL_KINDS = frozenset({"normal", "debug"})
DEFAULT_CHANNEL_SETTINGS = {
    "enabled": "off",
    "response_mode": "inherit",
    "tool_notice_display": "abbrev",
    "process_notice": "2",
    "channel_kind": "normal",
}

_client_cache: Any = None
_channel_settings_cache: dict[str, tuple[float, dict[str, str]]] = {}


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


def normalize_channel_id(channel_id: str | int | None) -> str:
    return str(channel_id or "").strip()


def _bool_text(value: object, *, default: bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    raw = str(value if value is not None else "").strip().lower()
    if raw in {"true", "1", "yes", "on"}:
        return "true"
    if raw in {"false", "0", "no", "off"}:
        return "false"
    return "true" if default else "false"


def _as_choice(value: object, allowed: set[str] | frozenset[str] | dict[str, object], default: str) -> str:
    raw = str(value if value is not None else "").strip()
    return raw if raw in allowed else default


def user_model_from_choice(choice: str | int | None) -> str:
    return USER_MODEL_CHOICES.get(str(choice or "").strip(), USER_MODEL_CHOICES[DEFAULT_USER_SETTINGS["using_model"]])


def talking_memory_turns(choice: str | int | None) -> int:
    return TALKING_MEMORY_TURNS.get(str(choice or "").strip(), TALKING_MEMORY_TURNS[DEFAULT_USER_SETTINGS["talking_memory"]])


def response_mode_from_criteria(choice: str | int | None) -> str:
    return USER_RESPONSE_MODES.get(str(choice or "").strip(), USER_RESPONSE_MODES[DEFAULT_USER_SETTINGS["response_criteria"]])


def process_notice_mode_from_choice(choice: str | int | None) -> str:
    return PROCESS_NOTICE_MODES.get(str(choice or "").strip(), PROCESS_NOTICE_MODES[DEFAULT_CHANNEL_SETTINGS["process_notice"]])


def user_role_from_value(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in USER_ROLES else DEFAULT_USER_SETTINGS["user_role"]


def daily_token_limit_for_role(role: str | None) -> int:
    return ROLE_DAILY_TOKEN_LIMITS.get(user_role_from_value(role), ROLE_DAILY_TOKEN_LIMITS["visitor"])


def _normalize_user_settings(row: dict[str, Any] | None = None) -> dict[str, str]:
    row = row or {}
    out = dict(DEFAULT_USER_SETTINGS)
    out["talk_with_kiritan"] = _bool_text(row.get("talk_with_kiritan"), default=True)
    out["talking_memory"] = _as_choice(row.get("talking_memory"), TALKING_MEMORY_TURNS, out["talking_memory"])
    out["using_model"] = _as_choice(row.get("using_model"), USER_MODEL_CHOICES, out["using_model"])
    out["personal_memory"] = _bool_text(row.get("personal_memory"), default=False)
    out["response_criteria"] = _as_choice(row.get("response_criteria"), USER_RESPONSE_MODES, out["response_criteria"])
    out["user_role"] = user_role_from_value(row.get("user_role"))
    return out


def get_discord_user_settings(uid: str | None) -> dict[str, str]:
    u = normalize_memory_user_id(uid)
    if u == "anonymous":
        return dict(DEFAULT_USER_SETTINGS)
    sb = _client()
    if sb is None:
        return dict(DEFAULT_USER_SETTINGS)
    try:
        r = (
            sb.table("known_line_users")
            .select("talk_with_kiritan,talking_memory,using_model,personal_memory,response_criteria,user_role")
            .eq("line_user_id", u)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        return _normalize_user_settings(rows[0] if rows else None)
    except Exception:
        try:
            r = (
                sb.table("known_line_users")
                .select("talk_with_kiritan,talking_memory,using_model,personal_memory,response_criteria")
                .eq("line_user_id", u)
                .limit(1)
                .execute()
            )
            rows = r.data or []
            return _normalize_user_settings(rows[0] if rows else None)
        except Exception:
            return dict(DEFAULT_USER_SETTINGS)


def set_discord_user_setting(uid: str | None, key: str, value: str) -> tuple[bool, str]:
    u = normalize_memory_user_id(uid)
    if u == "anonymous":
        return False, "Discord userId が無いため設定できません。"
    k = str(key or "").strip()
    v = str(value if value is not None else "").strip().lower()
    current = get_discord_user_settings(u)
    if k in {"talk_with_kiritan", "personal_memory"}:
        if v not in {"true", "false"}:
            return False, f"{k} の value は true/false です。"
    elif k == "talking_memory":
        if v not in TALKING_MEMORY_TURNS:
            return False, "talking_memory の value は 1/2/3/4 です。"
    elif k == "using_model":
        if v not in USER_MODEL_CHOICES:
            return False, "using_model の value は 1/2/3/4/5 です。"
    elif k == "response_criteria":
        if v not in USER_RESPONSE_MODES:
            return False, "response_criteria の value は 1/2 です。"
    elif k == "user_role":
        if v not in USER_ROLES:
            return False, "user_role の value は visitor/member/operator です。"
    else:
        return False, "key は talk_with_kiritan, talking_memory, using_model, personal_memory, response_criteria, user_role のいずれかです。"
    current[k] = v
    sb = _client()
    if sb is None:
        return False, "Supabase が未設定です（SUPABASE_URL / SUPABASE_KEY）。"
    try:
        sb.table("known_line_users").upsert(
            {
                "line_user_id": u,
                "last_seen_at": _now_iso(),
                **current,
            },
            on_conflict="line_user_id",
        ).execute()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def get_discord_user_stats(uid: str | None) -> dict[str, int | str]:
    u = normalize_memory_user_id(uid)
    out: dict[str, int | str] = {
        "user_id": "" if u == "anonymous" else u,
        "conversation_count": 0,
        "mid_term_note_chars": 0,
        "daily_token_count": 0,
    }
    if u == "anonymous":
        return out
    sb = _client()
    if sb is None:
        return out
    try:
        r = (
            sb.table("known_line_users")
            .select("conversation_count,mid_term_note,daily_token_date,daily_token_count")
            .eq("line_user_id", u)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        if not rows:
            return out
        row = rows[0]
        out["conversation_count"] = int(row.get("conversation_count") or 0)
        out["mid_term_note_chars"] = len(str(row.get("mid_term_note") or ""))
        today = datetime.now(timezone.utc).date().isoformat()
        out["daily_token_count"] = int(row.get("daily_token_count") or 0) if row.get("daily_token_date") == today else 0
        return out
    except Exception:
        return out


def get_runtime_usage_summary() -> dict[str, int]:
    """起動通知用の概況。列が未移行でも通知自体は落とさない。"""
    out = {
        "registered_users": 0,
        "active_users_today": 0,
        "daily_token_count": 0,
    }
    sb = _client()
    if sb is None:
        return out
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        r = sb.table("known_line_users").select("line_user_id,daily_token_date,daily_token_count").execute()
        rows = r.data or []
        out["registered_users"] = len(rows)
        for row in rows:
            if row.get("daily_token_date") != today:
                continue
            count = int(row.get("daily_token_count") or 0)
            if count > 0:
                out["active_users_today"] += 1
                out["daily_token_count"] += count
        return out
    except Exception:
        return out


def record_discord_user_usage(uid: str | None, *, tokens: int = 0) -> None:
    u = normalize_memory_user_id(uid)
    if u == "anonymous":
        return
    sb = _client()
    if sb is None:
        return
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        r = (
            sb.table("known_line_users")
            .select("conversation_count,daily_token_date,daily_token_count")
            .eq("line_user_id", u)
            .limit(1)
            .execute()
        )
        row = (r.data or [{}])[0]
        conversation_count = int(row.get("conversation_count") or 0) + 1
        old_date = str(row.get("daily_token_date") or "")
        daily_count = int(row.get("daily_token_count") or 0) if old_date == today else 0
        daily_count += max(0, int(tokens or 0))
        sb.table("known_line_users").upsert(
            {
                "line_user_id": u,
                "last_seen_at": _now_iso(),
                "conversation_count": conversation_count,
                "daily_token_date": today,
                "daily_token_count": daily_count,
            },
            on_conflict="line_user_id",
        ).execute()
    except Exception:
        pass


def _channel_settings_cache_seconds() -> int:
    raw = (os.environ.get("CHANNEL_SETTINGS_CACHE_SECONDS") or "300").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def _remember_channel_settings_cache(cid: str, settings: dict[str, str]) -> None:
    ttl = _channel_settings_cache_seconds()
    if ttl <= 0:
        _channel_settings_cache.pop(cid, None)
        return
    _channel_settings_cache[cid] = (time.monotonic() + ttl, dict(settings))


def get_channel_settings(channel_id: str | int | None, *, use_cache: bool = True) -> dict[str, str]:
    """Discord channel_id ごとの会話入口・通知表示設定。未設定時は inherit。"""
    cid = normalize_channel_id(channel_id)
    out = dict(DEFAULT_CHANNEL_SETTINGS)
    if not cid:
        return out
    if use_cache:
        cached = _channel_settings_cache.get(cid)
        if cached is not None:
            expires_at, settings = cached
            if expires_at > time.monotonic():
                return dict(settings)
            _channel_settings_cache.pop(cid, None)
    sb = _client()
    if sb is None:
        return out
    try:
        r = (
            sb.table("channel_settings")
            .select("*")
            .eq("channel_id", cid)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        if not rows:
            _remember_channel_settings_cache(cid, out)
            return out
        row = rows[0]
        enabled = row.get("enabled")
        response_mode = str(row.get("response_mode") or "inherit").strip().lower()
        tool_notice_display = str(row.get("tool_notice_display") or "inherit").strip().lower()
        process_notice = str(row.get("process_notice") or "").strip()
        if isinstance(enabled, bool):
            out["enabled"] = "on" if enabled else "off"
        else:
            enabled_text = str(enabled if enabled is not None else "off").strip().lower()
            if enabled_text in {"false", "0", "no", "off"}:
                out["enabled"] = "off"
            elif enabled_text in {"true", "1", "yes", "on"}:
                out["enabled"] = "on"
        if response_mode in CHANNEL_RESPONSE_MODES:
            out["response_mode"] = response_mode
        if tool_notice_display in CHANNEL_TOOL_NOTICE_VALUES:
            out["tool_notice_display"] = tool_notice_display
        channel_kind = str(row.get("channel_kind") or "normal").strip().lower()
        if channel_kind in CHANNEL_KINDS:
            out["channel_kind"] = channel_kind
        if process_notice in PROCESS_NOTICE_MODES:
            out["process_notice"] = process_notice
            out["tool_notice_display"] = process_notice_mode_from_choice(process_notice)
        elif tool_notice_display in {"hidden", "abbrev", "minimal", "full", "raw"}:
            out["process_notice"] = {
                "hidden": "1",
                "abbrev": "2",
                "minimal": "2",
                "full": "3",
                "raw": "4",
            }[tool_notice_display]
        _remember_channel_settings_cache(cid, out)
        return out
    except Exception:
        return out


def list_debug_channel_ids() -> list[str]:
    sb = _client()
    if sb is None:
        return []
    try:
        r = (
            sb.table("channel_settings")
            .select("channel_id")
            .eq("channel_kind", "debug")
            .order("updated_at", desc=True)
            .execute()
        )
        out: list[str] = []
        for row in r.data or []:
            cid = normalize_channel_id(row.get("channel_id"))
            if cid:
                out.append(cid)
        return out
    except Exception:
        return []


def set_channel_setting(channel_id: str | int | None, key: str, value: str) -> tuple[bool, str]:
    """channel_settings の単一キーを保存する。"""
    cid = normalize_channel_id(channel_id)
    if not cid:
        return False, "channel_id が空です。"
    k = (key or "").strip()
    v = (value or "").strip().lower()
    if k == "enabled":
        if v not in CHANNEL_ENABLED_VALUES:
            return False, "enabled は on/off、enabled/disabled（または true/false、1/0、yes/no）です。"
        v = "off" if v in {"off", "disabled", "false", "0", "no"} else "on"
    elif k == "process_notice":
        if v not in PROCESS_NOTICE_MODES:
            return False, "process_notice の value は 1/2/3/4 です。"
    elif k == "response_mode":
        if v not in CHANNEL_RESPONSE_MODES:
            return False, f"response_mode は {sorted(CHANNEL_RESPONSE_MODES)} のいずれかです。"
    elif k == "tool_notice_display":
        if v not in CHANNEL_TOOL_NOTICE_VALUES:
            return False, f"tool_notice_display は {sorted(CHANNEL_TOOL_NOTICE_VALUES)} のいずれかです。"
    elif k == "channel_kind":
        if v not in CHANNEL_KINDS:
            return False, "channel_kind は normal/debug のいずれかです。"
    else:
        return False, "key は enabled / process_notice / response_mode / tool_notice_display / channel_kind のいずれかです。"
    sb = _client()
    if sb is None:
        return False, "Supabase が未設定です（SUPABASE_URL / SUPABASE_KEY）。"
    current = get_channel_settings(cid, use_cache=False)
    current[k] = v
    if k == "process_notice":
        current["tool_notice_display"] = process_notice_mode_from_choice(v)
    if k == "channel_kind" and v == "debug":
        current["enabled"] = "off"
        current["response_mode"] = "off"
    try:
        sb.table("channel_settings").upsert(
            {
                "channel_id": cid,
                "enabled": current["enabled"] == "on",
                "response_mode": current["response_mode"],
                "tool_notice_display": current["tool_notice_display"],
                "process_notice": current["process_notice"],
                "channel_kind": current["channel_kind"],
                "updated_at": _now_iso(),
            },
            on_conflict="channel_id",
        ).execute()
        _remember_channel_settings_cache(cid, current)
        return True, (
            f"channel {cid} の {k} を {v} にしました。\n"
            f"enabled={current['enabled']} / process_notice={current['process_notice']} / channel_kind={current['channel_kind']}"
        )
    except Exception as exc:
        return False, str(exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def remember_line_user_for_push(uid: str | None) -> None:
    """互換名。Discord 入口では remember_discord_user_for_push を使う。"""
    remember_discord_user_for_push(uid)


def remember_discord_user_for_push(uid: str | None) -> None:
    """Discord で観測した userId を保存。notify_on_restart は既存行があれば維持する。"""
    u = normalize_memory_user_id(uid)
    if u == "anonymous":
        return
    sb = _client()
    if sb is None:
        return
    notify = False
    try:
        r = sb.table("known_line_users").select("notify_on_restart").eq("line_user_id", u).limit(1).execute()
        rows = r.data or []
        if rows:
            v = rows[0].get("notify_on_restart")
            notify = bool(v) if v is not None else False
    except Exception:
        pass
    try:
        sb.table("known_line_users").upsert(
            {"line_user_id": u, "last_seen_at": _now_iso(), "notify_on_restart": notify},
            on_conflict="line_user_id",
        ).execute()
    except Exception:
        pass


def _boot_push_store_limit() -> int:
    raw = (os.environ.get("DISCORD_BOOT_GREETING_PUSH_STORE_LIMIT") or "50").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 50
    return max(1, min(n, 2000))


def list_boot_notification_recipient_ids() -> list[str]:
    """notify_on_restart が true の userId を最近アクティブ順で返す（上限・列なし時は空）。"""
    sb = _client()
    if sb is None:
        return []
    lim = _boot_push_store_limit()
    try:
        r = (
            sb.table("known_line_users")
            .select("line_user_id")
            .eq("notify_on_restart", True)
            .order("last_seen_at", desc=True)
            .limit(lim)
            .execute()
        )
        out: list[str] = []
        for row in r.data or []:
            lid = str(row.get("line_user_id") or "").strip()
            if lid and lid != "anonymous":
                out.append(lid)
        return out
    except Exception:
        return []


def get_notify_worker_restart(uid: str | None) -> bool:
    """この LINE userId がワーカー起動 Push を受け取るか（known_line_users.notify_on_restart）。"""
    u = normalize_memory_user_id(uid)
    if u == "anonymous":
        return False
    sb = _client()
    if sb is None:
        return False
    try:
        r = sb.table("known_line_users").select("notify_on_restart").eq("line_user_id", u).limit(1).execute()
        rows = r.data or []
        if not rows:
            return False
        v = rows[0].get("notify_on_restart")
        return bool(v) if v is not None else False
    except Exception:
        return False


def get_mid_term_note(uid: str | None) -> str:
    """known_line_users.mid_term_note。1 本・短文のユーザー別中期記憶。"""
    u = normalize_memory_user_id(uid)
    if u == "anonymous":
        return ""
    sb = _client()
    if sb is None:
        return ""
    try:
        r = sb.table("known_line_users").select("mid_term_note").eq("line_user_id", u).limit(1).execute()
        rows = r.data or []
        if not rows:
            return ""
        v = rows[0].get("mid_term_note")
        return str(v).strip() if v is not None else ""
    except Exception:
        return ""


def append_mid_term_note(uid: str | None, chunk: str) -> tuple[bool, str]:
    """既存に空白区切りで結合し、長さ MID_TERM_NOTE_MAX_CHARS を超えたら末尾優先で切り詰める。"""
    u = normalize_memory_user_id(uid)
    if u == "anonymous":
        return False, "Discord userId が無いため中期記憶は使えません。"
    piece = (chunk or "").strip()
    if not piece:
        return False, "content が空です。"
    sb = _client()
    if sb is None:
        return False, "Supabase が未設定です。"
    notify = False
    cur = ""
    try:
        r = (
            sb.table("known_line_users")
            .select("notify_on_restart,mid_term_note")
            .eq("line_user_id", u)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        if rows:
            v = rows[0].get("notify_on_restart")
            notify = bool(v) if v is not None else False
            m = rows[0].get("mid_term_note")
            cur = str(m).strip() if m is not None else ""
    except Exception:
        pass
    merged = f"{cur} {piece}" if cur else piece
    merged = merged.strip()
    if len(merged) > MID_TERM_NOTE_MAX_CHARS:
        merged = merged[-MID_TERM_NOTE_MAX_CHARS:]
    try:
        sb.table("known_line_users").upsert(
            {
                "line_user_id": u,
                "last_seen_at": _now_iso(),
                "notify_on_restart": notify,
                "mid_term_note": merged,
            },
            on_conflict="line_user_id",
        ).execute()
        return True, (
            f"MID-MEMORY-APPEND 完了（現在 {len(merged)} 字・上限 {MID_TERM_NOTE_MAX_CHARS}）。\n{merged}"
        )
    except Exception as exc:
        return False, str(exc)


def clear_mid_term_note(uid: str | None) -> tuple[bool, str]:
    u = normalize_memory_user_id(uid)
    if u == "anonymous":
        return False, "Discord userId が無いため中期記憶は使えません。"
    sb = _client()
    if sb is None:
        return False, "Supabase が未設定です。"
    notify = False
    try:
        r = sb.table("known_line_users").select("notify_on_restart").eq("line_user_id", u).limit(1).execute()
        rows = r.data or []
        if rows:
            v = rows[0].get("notify_on_restart")
            notify = bool(v) if v is not None else False
    except Exception:
        pass
    try:
        sb.table("known_line_users").upsert(
            {
                "line_user_id": u,
                "last_seen_at": _now_iso(),
                "notify_on_restart": notify,
                "mid_term_note": "",
            },
            on_conflict="line_user_id",
        ).execute()
        return True, "MID-MEMORY-CLEAR 完了（中期記憶を空にしました）。"
    except Exception as exc:
        return False, str(exc)


_MAX_PENDING_BOOT_HISTORY_CHARS = 12_000


def set_pending_worker_boot_history(uid: str | None, assistant_history_block: str) -> None:
    """起動 Push 直後、そのユーザー次回応答用に assistant 履歴ブロックを保存（ワーカー間共有）。"""
    u = normalize_memory_user_id(uid)
    if u == "anonymous":
        return
    block = (assistant_history_block or "").strip()
    if not block:
        return
    if len(block) > _MAX_PENDING_BOOT_HISTORY_CHARS:
        block = block[: _MAX_PENDING_BOOT_HISTORY_CHARS - 40] + "\n…（省略）"
    sb = _client()
    if sb is None:
        return
    notify = False
    try:
        r = sb.table("known_line_users").select("notify_on_restart").eq("line_user_id", u).limit(1).execute()
        rows = r.data or []
        if rows:
            v = rows[0].get("notify_on_restart")
            notify = bool(v) if v is not None else False
    except Exception:
        pass
    try:
        sb.table("known_line_users").upsert(
            {
                "line_user_id": u,
                "last_seen_at": _now_iso(),
                "notify_on_restart": notify,
                "pending_worker_boot_history": block,
            },
            on_conflict="line_user_id",
        ).execute()
    except Exception:
        pass


def take_pending_worker_boot_history(uid: str | None) -> str | None:
    """保存済みブロックがあれば返し、列を空にする（1 回限り）。"""
    u = normalize_memory_user_id(uid)
    if u == "anonymous":
        return None
    sb = _client()
    if sb is None:
        return None
    try:
        r = (
            sb.table("known_line_users")
            .select("pending_worker_boot_history")
            .eq("line_user_id", u)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        if not rows:
            return None
        val = rows[0].get("pending_worker_boot_history")
        if val is None:
            return None
        s = str(val).strip()
        if not s:
            try:
                sb.table("known_line_users").update({"pending_worker_boot_history": None}).eq(
                    "line_user_id", u
                ).execute()
            except Exception:
                pass
            return None
        sb.table("known_line_users").update({"pending_worker_boot_history": None}).eq("line_user_id", u).execute()
        return s
    except Exception:
        return None


def set_notify_worker_restart(uid: str | None, enabled: bool) -> tuple[bool, str]:
    """known_line_users に upsert し、notify_on_restart を設定する。"""
    u = normalize_memory_user_id(uid)
    if u == "anonymous":
        return False, "Discord userId が無いため設定できません。"
    sb = _client()
    if sb is None:
        return False, "Supabase が未設定です（SUPABASE_URL / SUPABASE_KEY）。"
    try:
        sb.table("known_line_users").upsert(
            {"line_user_id": u, "last_seen_at": _now_iso(), "notify_on_restart": bool(enabled)},
            on_conflict="line_user_id",
        ).execute()
        return True, ""
    except Exception as exc:
        return False, str(exc)


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


def memory_delete(filename: str, line_user_id: str) -> str:
    fn = validate_memory_filename(filename)
    if fn is None:
        return "不正なファイル名です。"
    uid = normalize_memory_user_id(line_user_id)
    sb = _client()
    if sb is None:
        return "Supabase が未設定です。"
    row, err = memory_read_row(fn, uid)
    if err:
        return err
    if row is None:
        return "DELETE-TEXT: ファイルが見つかりません。"
    try:
        sb.table("memory_files").delete().eq("filename", fn).eq("line_user_id", uid).execute()
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
