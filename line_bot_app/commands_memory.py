"""Supabase 記憶コマンド（LIST-FILES / READ-TEXT / WRITE-TEXT / APPEND-TEXT / GET-SETTING / SET-SETTING）。"""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from .chat_models import format_allowed_models_hint, resolve_chat_model
from .tool_notice import (
    ALLOWED_TOOL_NOTICE_DISPLAY_HINT,
    parse_tool_notice_mode,
)
from .commands import CommandServices
from .supabase_store import (
    dumps_memory_index,
    get_db_setting,
    get_notify_worker_restart,
    memory_append,
    memory_list_files,
    memory_read_row,
    memory_write,
    normalize_memory_user_id,
    set_db_setting,
    set_notify_worker_restart,
    validate_memory_filename,
)

_ALLOWED_SETTING_KEYS = frozenset(
    {
        "current_model",
        "show_ri_text",
        "tool_notice_display",
        "text.use_raw_result",
        "notify_worker_restart",
    }
)
_PER_USER_SETTING_KEYS = frozenset({"notify_worker_restart"})
_ALL_SETTING_KEYS_ORDER: tuple[str, ...] = (
    "current_model",
    "tool_notice_display",
    "show_ri_text",
    "text.use_raw_result",
    "notify_worker_restart",
)
_TOOL_NOTICE_DB_VALUES = frozenset({"full", "abbrev", "minimal", "hidden"})

_TOOL_NOTICE_MODE_HELP: dict[str, str] = {
    "full": "リトライ・末尾とも長めの ARGS 要約＋日本語ラベル（例: [R1]検索 … t:…）",
    "abbrev": "リトライ・末尾とも短い ARGS 要約＋日本語ラベル",
    "minimal": "リトライ行のみ（末尾なし）。本文と同じ吹き出しに付与",
    "hidden": "システム行なし（レガシー show_ri_text=false と同等）",
}

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
    rows, err = memory_list_files(user_id or "")
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

    row, err = memory_read_row(fn_raw, user_id or "")
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

    err = memory_write(fn, content, description, user_id or "")
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

    err = memory_append(fn, chunk, user_id or "")
    if err:
        return (TEXT or "").strip(), "APPEND-TEXT 失敗", err, None, None
    ok = f"APPEND-TEXT 完了: {fn}"
    return TEXT or "", ok, ok, ok, None


def _format_setting_block(svc: CommandServices, key: str, user_id: str | None) -> str:
    """単一キーの説明テキスト（GET-SETTING の本文）。"""
    defaults = {
        "current_model": svc.config.openai_model,
        "show_ri_text": "true",
        "tool_notice_display": "",
        "text.use_raw_result": "false",
    }
    if key == "notify_worker_restart":
        uid = normalize_memory_user_id(user_id or "")
        if uid == "anonymous":
            return (
                "参照できません: LINE userId がありません。\n"
                "このキーは known_line_users（この LINE アカウント単位）に保存されます。"
            )
        on = get_notify_worker_restart(uid)
        if svc.config.restart_push_enabled:
            srv = "オン … settings.json の notifications.restart_push_enabled が true で、再起動時の定型 Push を送れます。"
        else:
            srv = (
                "オフ … notifications.restart_push_enabled=false のため、"
                "再起動時の定型 Push は全宛先について送られません（LINE_BOOT_GREETING_USER_IDS も含む）。"
            )
        return (
            f"{key} = {'true' if on else 'false'}（この LINE のオプトイン）\n"
            f"サーバー: {srv}\n"
            "ユーザーがオンでもサーバーがオフなら送信されません。\n"
            "ユーザーがオフなら、このアカウントへの DB 登録による宛先には送られません。\n"
            "※このキーだけ LINE アカウントごとです（user_settings ではありません）。"
        )

    val = get_db_setting(key, defaults.get(key, ""))
    if key == "current_model":
        eff = resolve_chat_model(val, svc.config.openai_model, svc.config.allowed_chat_models)
        hint = format_allowed_models_hint(svc.config.allowed_chat_models)
        line = (
            f"{key} DB値={val!r}\n"
            f"実効モデル={eff!r}\n"
            f"許可リスト（SET-SETTING で指定できる値）: {hint}"
        )
    elif key == "tool_notice_display":
        legacy = get_db_setting("show_ri_text", "true")
        eff = parse_tool_notice_mode(val, legacy_show_ri_text=legacy)
        hint_eff = _TOOL_NOTICE_MODE_HELP.get(eff.value, "")
        line = (
            f"{key} DB値={val!r}\n"
            f"実効モード={eff.value}（{hint_eff}）\n"
            f"legacy show_ri_text={legacy!r}（tool_notice_display が空のときのみ効く）\n"
            f"SET で使える値: {ALLOWED_TOOL_NOTICE_DISPLAY_HINT}"
        )
    else:
        line = f"{key} = {val!r}"
    if key not in _PER_USER_SETTING_KEYS:
        line += "\n※この値はボット全体（すべての LINE ユーザー）で共通です。"
    return line


def cmd_get_setting(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    """常に全設定キーをまとめて返す（ARGS の key は無視）。"""
    blocks = [_format_setting_block(svc, k, user_id) for k in _ALL_SETTING_KEYS_ORDER]
    headings = [f"【{k}】" for k in _ALL_SETTING_KEYS_ORDER]
    inner = "\n\n---\n\n".join(f"{h}\n{b}" for h, b in zip(headings, blocks, strict=True))
    line = "（GET-SETTING 全項目）\n\n" + inner
    return TEXT or "", "GET-SETTING (all)", line, None, None


def cmd_set_setting(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    if not isinstance(args, dict):
        args = {}
    key = str(args.get("key") or "").strip()
    val = str(args.get("value") if args.get("value") is not None else "")
    if key not in _ALLOWED_SETTING_KEYS:
        msg = f"key は {_ALLOWED_SETTING_KEYS} のいずれかです。"
        return (TEXT or "").strip(), "SET-SETTING 検証", msg, None, None
    if key == "notify_worker_restart":
        uid = normalize_memory_user_id(user_id or "")
        if uid == "anonymous":
            msg = "LINE userId が無いため notify_worker_restart は設定できません。"
            return (TEXT or "").strip(), "SET-SETTING 拒否", msg, None, None
        vm = val.strip().lower()
        if vm not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            msg = "notify_worker_restart の value は true/false（または 1/0、yes/no、on/off）です。"
            return (TEXT or "").strip(), "SET-SETTING 拒否", msg, None, None
        enabled = vm in ("true", "1", "yes", "on")
        ok, err = set_notify_worker_restart(uid, enabled)
        if not ok:
            return (TEXT or "").strip(), "SET-SETTING 失敗", err or "不明なエラー", None, None
        line = (
            f"notify_worker_restart を {'オン' if enabled else 'オフ'} にしました。\n"
            "※この LINE アカウントだけに適用されます（known_line_users）。"
        )
        if not svc.config.restart_push_enabled:
            line += (
                "\n（注: notifications.restart_push_enabled=false のため、"
                "再起動時の定型 Push はサーバーから送られません。）"
            )
        return TEXT or "", f"SET-SETTING {key}", line, None, None

    if key == "current_model":
        vm = val.strip()
        if vm and vm not in svc.config.allowed_chat_models:
            hint = format_allowed_models_hint(svc.config.allowed_chat_models)
            msg = f"このモデルは許可リストにありません: {vm!r}。許可: {hint}"
            return (TEXT or "").strip(), "SET-SETTING 拒否", msg, None, None
    if key == "tool_notice_display":
        vm = val.strip().lower()
        if vm and vm not in _TOOL_NOTICE_DB_VALUES:
            msg = (
                f"tool_notice_display は {sorted(_TOOL_NOTICE_DB_VALUES)} のいずれか、"
                f"または空（レガシーの show_ri_text に従う）です。"
            )
            return (TEXT or "").strip(), "SET-SETTING 拒否", msg, None, None
    ok, err = set_db_setting(key, val)
    if not ok:
        return (TEXT or "").strip(), "SET-SETTING 失敗", err or "不明なエラー", None, None
    line = f"{key} を保存しました。"
    if key == "current_model":
        line += f" 実効モデルは {resolve_chat_model(val, svc.config.openai_model, svc.config.allowed_chat_models)!r} です。"
    elif key == "tool_notice_display":
        eff = parse_tool_notice_mode(val, legacy_show_ri_text=get_db_setting("show_ri_text", "true"))
        line += f" 実効モードは {eff.value} です。"
    else:
        line += "（値の妥当性はモデル側・運用で確認してください）。"
    if key not in _PER_USER_SETTING_KEYS:
        line += "\n※変更はボット全体（すべての LINE ユーザー）に反映されます。"
    return TEXT or "", f"SET-SETTING {key}", line, None, None


MEMORY_COMMAND_HANDLERS: dict[str, Any] = {
    "LIST-FILES": cmd_list_files,
    "READ-TEXT": cmd_read_text,
    "WRITE-TEXT": cmd_write_text,
    "APPEND-TEXT": cmd_append_text,
    "GET-SETTING": cmd_get_setting,
    "SET-SETTING": cmd_set_setting,
}
