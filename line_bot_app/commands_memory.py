"""Supabase 記憶コマンド（LIST-FILES / READ-TEXT / WRITE-TEXT / APPEND-TEXT / DELETE-TEXT / GET-SETTING / SET-SETTING / 中期記憶）。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from openai import OpenAI

from .chat_models import resolve_chat_model
from .config import AppConfig
from .supabase_store import (
    append_mid_term_note,
    clear_mid_term_note,
    daily_token_limit_for_role,
    dumps_memory_index,
    get_channel_settings,
    get_db_setting,
    get_discord_user_settings,
    get_discord_user_stats,
    get_notify_worker_restart,
    memory_append,
    memory_delete,
    memory_list_files,
    memory_read_row,
    memory_write,
    normalize_memory_user_id,
    process_notice_mode_from_choice,
    response_mode_from_criteria,
    set_channel_setting,
    set_discord_user_setting,
    set_notify_worker_restart,
    talking_memory_turns,
    user_role_from_value,
    user_model_from_choice,
    validate_memory_filename,
)

if TYPE_CHECKING:
    from .commands import CommandServices

_ALLOWED_SETTING_KEYS = frozenset(
    {
        "talk_with_kiritan",
        "talking_memory",
        "using_model",
        "personal_memory",
        "response_criteria",
        "process_notice",
        "notify_worker_restart",
    }
)
_READ_SUMMARY_INPUT_CAP = 14_000


def _effective_openai_model(svc: CommandServices, user_id: str | None = None) -> str:
    user_settings = get_discord_user_settings(user_id or "")
    return resolve_chat_model(
        user_model_from_choice(user_settings.get("using_model")),
        svc.config.openai_model,
        svc.config.allowed_chat_models | frozenset({"gpt-4o"}),
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

    summary, s_err = _summarize_long_text(svc.client, _effective_openai_model(svc, user_id), content)
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


def cmd_delete_text(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    if not isinstance(args, dict):
        args = {}
    fn = str(args.get("filename") or "").strip()
    if validate_memory_filename(fn) is None:
        msg = "filename が不正か空です。"
        return (TEXT or "").strip(), "DELETE-TEXT 検証エラー", msg, None, None

    err = memory_delete(fn, user_id or "")
    if err:
        return (TEXT or "").strip(), "DELETE-TEXT 失敗", err, None, None
    ok = f"DELETE-TEXT 完了: {fn}"
    return TEXT or "", ok, ok, ok, None


def cmd_mid_memory_append(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    if not isinstance(args, dict):
        args = {}
    chunk = str(args.get("content") if args.get("content") is not None else "")
    ok, line = append_mid_term_note(user_id or "", chunk)
    if not ok:
        return (TEXT or "").strip(), "MID-MEMORY-APPEND 失敗", line, None, None
    return TEXT or "", "MID-MEMORY-APPEND", line, line, None


def cmd_mid_memory_clear(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    ok, line = clear_mid_term_note(user_id or "")
    if not ok:
        return (TEXT or "").strip(), "MID-MEMORY-CLEAR 失敗", line, None, None
    return TEXT or "", "MID-MEMORY-CLEAR", line, line, None


def _setting_line(name: str, label: str, value: str, value_label: str, desc: str, choices: str) -> str:
    return f"{name:<20}<{label}>\n ={value} ({value_label})\n  →{desc}\n  [{choices}]"


def cmd_get_setting(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    """常に全設定キーをまとめて返す（ARGS の key は無視）。"""
    line = build_settings_text(svc.config, user_id)
    return TEXT or "", "GET-SETTING (all)", line, None, None


def build_settings_text(config: AppConfig, user_id: str | None, channel_id: str | None = None) -> str:
    uid = normalize_memory_user_id(user_id or "")
    user_settings = get_discord_user_settings(uid)
    channel_settings = get_channel_settings(channel_id or "")
    stats = get_discord_user_stats(uid)
    talk = user_settings["talk_with_kiritan"]
    memory = user_settings["talking_memory"]
    model = user_settings["using_model"]
    personal = user_settings["personal_memory"]
    response = user_settings["response_criteria"]
    role = user_role_from_value(user_settings.get("user_role"))
    notice = channel_settings.get("process_notice", "2")
    channel_kind = channel_settings.get("channel_kind", "normal")
    memory_labels = {"1": "15ターン保持", "2": "10ターン保持", "3": "5ターン保持", "4": "保持しない(非推奨)"}
    model_labels = {"1": "gpt-4.1-mini", "2": "gpt-5.4-mini", "3": "gpt-4o", "4": "gpt-5.2", "5": "gpt-5.4"}
    response_labels = {"1": "通常メッセージ", "2": "メンション付きメッセージ"}
    notice_labels = {"1": "非表示", "2": "最小", "3": "表示", "4": "開発者用"}
    blocks = [
        "[ユーザー別設定]",
        "",
        _setting_line(
            "talk with kiritan",
            "きりたんとの会話設定",
            talk,
            "会話する" if talk == "true" else "会話しない",
            "きりたんと会話するか否かを設定します。falseに設定してあるユーザーのメッセージは無視します。",
            "true : 会話する  false : 会話しない",
        ),
        "",
        _setting_line(
            "talking memory",
            "会話履歴の保持",
            memory,
            memory_labels.get(memory, "15ターン保持"),
            "ユーザーとの会話内容を履歴として参照します。保持ターンを増やすことでより過去の会話まで覚えられますが、消費トークン数が増大し、モデルによっては応答精度が低下します。",
            "1 : 15ターン保持  2 : 10ターン保持  3 : 5ターン保持  4 : 保持しない(非推奨)",
        ),
        "",
        "**" + _setting_line(
            "using model",
            "使用するモデル",
            model,
            model_labels.get(model, "gpt-5.4-mini"),
            "きりたんの応答に使用するモデルを変更します。",
            "1 : gpt-4.1-mini  2 : gpt-5.4-mini  3 : gpt-4o   4 : gpt-5.2  5 : gpt-5.4",
        ) + "**",
        "",
        _setting_line(
            "personal memory",
            "個人用中期記憶",
            personal,
            "利用する" if personal == "true" else "利用しない",
            "会話履歴のリセットで消去されない、ユーザーごとの記憶を覚えておけるスペースをきりたんに与えます。",
            "true : 利用する false : 利用しない",
        ),
        "",
        _setting_line(
            "response criteria",
            "botの応答モード",
            response,
            response_labels.get(response, "通常メッセージ"),
            "きりたんにメッセージを送る方法を変更します。",
            "1 : 通常メッセージ  2 : メンション付きメッセージ",
        ),
        "",
        "",
        "[チャンネル別設定]",
        "",
        _setting_line(
            "process notice",
            "処理内容の付加表示",
            notice,
            notice_labels.get(notice, "最小"),
            "きりたんの内部処理結果(使用したコマンド・消費トークン数)を応答文の最後に付加します。",
            "1 : 非表示  2 : 最小  3 : 表示  4 : 開発者用",
        ),
        "",
        "",
        f"ユーザーID          :{'' if uid == 'anonymous' else uid}",
        "",
        f"チャンネルID        :{channel_id or ''}",
        "",
        f"チャンネル種別      :{channel_kind}",
        "",
        f"権限                :{role}（本日上限 {daily_token_limit_for_role(role)} トークン）",
        "",
        f"きりたんとの会話回数:{stats['conversation_count']}回",
        "",
        f"個人用中期記憶文字数:{stats['mid_term_note_chars']}文字(max{200}文字)",
        "",
        f"本日の消費トークン数:{stats['daily_token_count']}トークン",
    ]
    return "\n".join(blocks)


def set_setting_value(config: AppConfig, key: str, val: str, user_id: str | None, channel_id: str | None = None) -> tuple[bool, str]:
    key = str(key or "").strip()
    val = str(val if val is not None else "").strip().lower()
    key_aliases = {
        "talk with kiritan": "talk_with_kiritan",
        "talking memory": "talking_memory",
        "using model": "using_model",
        "personal memory": "personal_memory",
        "response criteria": "response_criteria",
        "process notice": "process_notice",
    }
    key = key_aliases.get(key, key)
    if key not in _ALLOWED_SETTING_KEYS:
        return False, "key は talk_with_kiritan / talking_memory / using_model / personal_memory / response_criteria / process_notice です。"
    if key == "notify_worker_restart":
        uid = normalize_memory_user_id(user_id or "")
        if uid == "anonymous":
            return False, "Discord userId が無いため notify_worker_restart は設定できません。"
        vm = val.strip().lower()
        if vm not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            return False, "notify_worker_restart の value は true/false（または 1/0、yes/no、on/off）です。"
        enabled = vm in ("true", "1", "yes", "on")
        ok, err = set_notify_worker_restart(uid, enabled)
        if not ok:
            return False, err or "不明なエラー"
        line = (
            f"notify_worker_restart を {'オン' if enabled else 'オフ'} にしました。\n"
            "※この Discord アカウントだけに適用されます（known_line_users）。"
        )
        if not config.restart_push_enabled:
            line += (
                "\n（注: notifications.restart_push_enabled=false のため、"
                "再起動時の定型 Push はサーバーから送られません。）"
            )
        return True, line

    if key == "process_notice":
        ok, err = set_channel_setting(channel_id or "", "process_notice", val)
        if not ok:
            return False, err or "不明なエラー"
        line = f"process_notice を {val}（{process_notice_mode_from_choice(val)}）にしました。"
        return True, line

    current_role = user_role_from_value(get_discord_user_settings(user_id or "").get("user_role"))
    if current_role == "visitor" and key == "using_model":
        return False, "visitor は using_model を変更できません。member 以上が必要です。"

    ok, err = set_discord_user_setting(user_id or "", key, val)
    if not ok:
        return False, err or "不明なエラー"
    line = f"{key} を {val} にしました。"
    if key == "talking_memory":
        line += f" 会話履歴は {talking_memory_turns(val)} ターン保持です。"
    elif key == "using_model":
        line += f" 実効モデルは {user_model_from_choice(val)!r} です。"
    elif key == "response_criteria":
        line += f" 応答モードは {response_mode_from_criteria(val)} です。"
    else:
        line += " このDiscordアカウントだけに適用されます。"
    return True, line


def cmd_set_setting(svc: CommandServices, args: dict, TEXT: str, NOTE: str | None = None, *, user_id=None):
    if not isinstance(args, dict):
        args = {}
    key = str(args.get("key") or "").strip()
    val = str(args.get("value") if args.get("value") is not None else "")
    ok, line = set_setting_value(svc.config, key, val, user_id)
    if not ok:
        return (TEXT or "").strip(), "SET-SETTING 拒否", line, None, None
    return TEXT or "", f"SET-SETTING {key}", line, None, None


MEMORY_COMMAND_HANDLERS: dict[str, Any] = {
    "LIST-FILES": cmd_list_files,
    "READ-TEXT": cmd_read_text,
    "WRITE-TEXT": cmd_write_text,
    "APPEND-TEXT": cmd_append_text,
    "DELETE-TEXT": cmd_delete_text,
    "GET-SETTING": cmd_get_setting,
    "SET-SETTING": cmd_set_setting,
    "MID-MEMORY-APPEND": cmd_mid_memory_append,
    "MID-MEMORY-CLEAR": cmd_mid_memory_clear,
}
