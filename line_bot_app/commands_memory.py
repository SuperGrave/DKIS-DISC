"""Supabase 記憶コマンド（LIST-FILES / READ-TEXT / WRITE-TEXT / APPEND-TEXT / SAVE-LOG / GET-SETTING / SET-SETTING）。"""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from .chat_models import format_allowed_models_hint, resolve_chat_model
from .commands import CommandServices
from .supabase_store import (
    dumps_memory_index,
    get_db_setting,
    memory_append,
    memory_list_files,
    memory_read_row,
    memory_write,
    set_db_setting,
    validate_memory_filename,
)

_ALLOWED_SETTING_KEYS = frozenset({"current_model", "show_ri_text", "text.use_raw_result"})

_READ_SUMMARY_INPUT_CAP = 14_000


def _effective_openai_model(svc: CommandServices) -> str:
    return resolve_chat_model(
        get_db_setting("current_model", ""),
        svc.config.openai_model,
        svc.config.allowed_chat_models,
    )


def _summarize_long_text(client: OpenAI, model: str, raw: str) -> tuple[str, str]:
    raw_clip = (raw or "").strip()
    if len(raw_clip) > _READ_SUMMARY_INPUT_CAP:
        raw_clip = raw_clip[: _READ_SUMMARY_INPUT_CAP - 60] + "\n…（要約入力として省略）"
    if not raw_clip:
        return "", ""
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "あなたは編集補助です。与えられたテキストを日本語で箇条書き中心に簡潔に要約してください。推測で足さないでください。",
                },
                {"role": "user", "content": raw_clip},
            ],
        )
        out = (r.choices[0].message.content or "").strip()
        return out, ""
    except Exception as exc:
        return "", str(exc)


def cmd_list_files(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    rows, err = memory_list_files()
    if err:
        msg = f"LIST-FILES エラー: {err}"
        return (TEXT or "").strip(), "LIST-FILES 失敗", msg, None, None
    blob = dumps_memory_index(rows)
    dmis_log = f"LIST-FILES ({len(rows)} 件)"
    return TEXT or "", dmis_log, blob, blob, None


def cmd_read_text(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    fn_raw = args.get("filename", "").strip() if isinstance(args, dict) else ""
    if not fn_raw:
        return (TEXT or "").strip(), "READ-TEXT: filename なし", "filename を ARGS に指定してください。", None, None

    row, err = memory_read_row(fn_raw)
    if err and not row:
        return (TEXT or "").strip(), "READ-TEXT 失敗", err, None, None
    if row is None and not err:
        return (TEXT or "").strip(), f"READ-TEXT: なし ({fn_raw})", "ファイルが見つかりません。", None, None

    assert row is not None
    content = str(row.get("content") or "")
    description = str(row.get("description") or "")
    meta = json.dumps(
        {"filename": row.get("filename"), "description": description, "updated_at": row.get("updated_at")},
        ensure_ascii=False,
    )

    use_raw = get_db_setting("text.use_raw_result", "false").strip().lower() in ("1", "true", "yes", "on")
    if use_raw:
        blob = f"{meta}\n--- content ---\n{content}"
        dmis_log = f"READ-TEXT raw {fn_raw}"
        return TEXT or "", dmis_log, blob, blob, None

    summary, s_err = _summarize_long_text(svc.client, _effective_openai_model(svc), content)
    if s_err:
        fallback = f"{meta}\n--- content（要約失敗） ---\n{content[:8000]}"
        return TEXT or "", f"READ-TEXT 要約エラー {fn_raw}", fallback, fallback, None

    blob = f"{meta}\n--- summary ---\n{summary}"
    dmis_log = f"READ-TEXT summary {fn_raw}"
    return TEXT or "", dmis_log, blob, blob, None


def cmd_write_text(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    if not isinstance(args, dict):
        args = {}
    fn = str(args.get("filename") or "").strip()
    content = str(args.get("content") if args.get("content") is not None else "")
    description = str(args.get("description") or "").strip()
    if validate_memory_filename(fn) is None:
        msg = "filename が不正か空です。"
        return (TEXT or "").strip(), "WRITE-TEXT 検証エラー", msg, None, None

    err = memory_write(fn, content, description)
    if err:
        return (TEXT or "").strip(), "WRITE-TEXT 失敗", err, None, None
    ok = f"WRITE-TEXT 完了: {fn}"
    return TEXT or "", ok, ok, ok, None


def cmd_append_text(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    if not isinstance(args, dict):
        args = {}
    fn = str(args.get("filename") or "").strip()
    chunk = str(args.get("content") if args.get("content") is not None else "")
    if validate_memory_filename(fn) is None:
        msg = "filename が不正か空です。"
        return (TEXT or "").strip(), "APPEND-TEXT 検証エラー", msg, None, None
    if not chunk:
        return (TEXT or "").strip(), "APPEND-TEXT: content なし", "追記する content が空です。", None, None

    err = memory_append(fn, chunk)
    if err:
        return (TEXT or "").strip(), "APPEND-TEXT 失敗", err, None, None
    ok = f"APPEND-TEXT 完了: {fn}"
    return TEXT or "", ok, ok, ok, None


def cmd_save_log_supabase(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    uid = user_id or "anonymous"
    if not isinstance(args, dict):
        args = {}
    fn = str(args.get("filename") or "").strip()
    description = str(args.get("description") or "会話ログのエクスポート").strip()
    if validate_memory_filename(fn) is None:
        msg = "filename が不正か空です。"
        return (TEXT or "").strip(), "SAVE-LOG 検証エラー", msg, None, None

    body = svc.hooks.take_clear_log(uid)
    if not body.strip():
        msg = "保存する会話ログが空です（このユーザーでまだメッセージが蓄積されていません）。"
        return (TEXT or "").strip(), "SAVE-LOG: 空", msg, None, None

    err = memory_write(fn, body, description)
    if err:
        return (TEXT or "").strip(), "SAVE-LOG 失敗", err, None, None
    ok = f"SAVE-LOG 完了: {fn}（ログバッファをクリアしました）"
    return TEXT or "", ok, ok, ok, None


def cmd_get_setting(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    if not isinstance(args, dict):
        args = {}
    key = str(args.get("key") or "").strip()
    if key not in _ALLOWED_SETTING_KEYS:
        msg = f"key は {_ALLOWED_SETTING_KEYS} のいずれかです。"
        return (TEXT or "").strip(), "GET-SETTING 検証", msg, None, None
    defaults = {
        "current_model": svc.config.openai_model,
        "show_ri_text": "true",
        "text.use_raw_result": "false",
    }
    val = get_db_setting(key, defaults.get(key, ""))
    if key == "current_model":
        eff = resolve_chat_model(val, svc.config.openai_model, svc.config.allowed_chat_models)
        hint = format_allowed_models_hint(svc.config.allowed_chat_models)
        line = (
            f"{key} DB値={val!r}\n"
            f"実効モデル={eff!r}\n"
            f"許可リスト（SET-SETTING で指定できる値）: {hint}"
        )
    else:
        line = f"{key} = {val!r}"
    return TEXT or "", f"GET-SETTING {key}", line, None, None


def cmd_set_setting(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    if not isinstance(args, dict):
        args = {}
    key = str(args.get("key") or "").strip()
    val = str(args.get("value") if args.get("value") is not None else "")
    if key not in _ALLOWED_SETTING_KEYS:
        msg = f"key は {_ALLOWED_SETTING_KEYS} のいずれかです。"
        return (TEXT or "").strip(), "SET-SETTING 検証", msg, None, None
    if key == "current_model":
        vm = val.strip()
        if vm and vm not in svc.config.allowed_chat_models:
            hint = format_allowed_models_hint(svc.config.allowed_chat_models)
            msg = f"このモデルは許可リストにありません: {vm!r}。許可: {hint}"
            return (TEXT or "").strip(), "SET-SETTING 拒否", msg, None, None
    ok, err = set_db_setting(key, val)
    if not ok:
        return (TEXT or "").strip(), "SET-SETTING 失敗", err or "不明なエラー", None, None
    line = f"{key} を保存しました。"
    if key == "current_model":
        line += f" 実効モデルは {resolve_chat_model(val, svc.config.openai_model, svc.config.allowed_chat_models)!r} です。"
    else:
        line += "（値の妥当性はモデル側・運用で確認してください）。"
    return TEXT or "", f"SET-SETTING {key}", line, None, None


MEMORY_COMMAND_HANDLERS: dict[str, Any] = {
    "LIST-FILES": cmd_list_files,
    "READ-TEXT": cmd_read_text,
    "WRITE-TEXT": cmd_write_text,
    "APPEND-TEXT": cmd_append_text,
    "SAVE-LOG": cmd_save_log_supabase,
    "GET-SETTING": cmd_get_setting,
    "SET-SETTING": cmd_set_setting,
}
