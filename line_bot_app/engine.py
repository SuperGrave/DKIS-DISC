"""コマンド実行・RETRY 連鎖・ユーザー別会話履歴。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from openai import OpenAI

from .boot_greeting import take_worker_boot_push_for_history
from .commands import COMMAND_HANDLERS, CommandServices, ExportHooks
from .config import AppConfig
from .input_build import build_input_segments
from .line_messages import split_line_text
from .parsing import format_model_response_block, parse_ai_response
from .supabase_store import (
    get_discord_user_settings,
    get_mid_term_note,
    record_discord_user_usage,
    talking_memory_turns,
    user_model_from_choice,
)
from .tool_notice import (
    ToolNoticeMode,
    format_normal_footer_line,
    format_retry_notice_line,
    parse_tool_notice_mode,
    retry_line_abbreviated,
    show_normal_footer,
    show_retry_notice,
)
from .user_messages import (
    MSG_AI_OUTPUT_PARSE_FAILED,
    MSG_REPLY_ASSEMBLY_FAILED,
    MSG_UNKNOWN_COMMAND,
)


def _usage_from_response(response: object) -> dict:
    u = getattr(response, "usage", None)
    if not u:
        return {}
    out: dict = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        v = getattr(u, k, None)
        if v is not None:
            out[k] = v
    return out


def _keep_detail(text: str, *, limit: int) -> str:
    s = text.strip()
    if limit <= 0 or len(s) <= limit:
        return s
    return s[:limit] + "…"


def _rt_arg_summary(command: str, args: object, *, limit: int = 120) -> str:
    if not isinstance(args, dict):
        args = {}
    if command == "SEARCH":
        q = str(args.get("query") or "").strip()
        return _keep_detail(q, limit=limit) if q else "(queryなし)"
    if command == "NEWS":
        q = str(args.get("query") or "").strip()
        return _keep_detail(q, limit=limit) if q else "(queryなし)"
    if command == "WEATHER":
        w = str(args.get("w_location") or "").strip()
        return _keep_detail(w, limit=limit) if w else "(地名なし)"
    if command == "READ-PAGE":
        u = str(args.get("url") or "").strip()
        return _keep_detail(u, limit=limit) if u else "(urlなし)"
    if command == "LIST-FILES":
        return "(一覧)"
    if command in ("READ-TEXT", "WRITE-TEXT", "APPEND-TEXT", "DELETE-TEXT"):
        fn = str(args.get("filename") or "").strip()
        return _keep_detail(fn, limit=limit) if fn else "(filenameなし)"
    if command == "GET-SETTING":
        return "(全設定)"
    if command == "SET-SETTING":
        k = str(args.get("key") or "")
        return _keep_detail(k, limit=60 if limit > 0 else 0) if k else "(key)"
    if command == "MID-MEMORY-APPEND":
        frag = str(args.get("content") or "").strip()
        return _keep_detail(frag, limit=30 if limit > 0 else 0) if frag else "(contentなし)"
    if command == "MID-MEMORY-CLEAR":
        return "(中期記憶リセット)"
    return "-"


def _truncate_tool_payload(text: str, limit: int) -> str:
    """ツール生結果を LLM に再投入するときの上限（低メモリ環境での OOM 回避）。"""
    if limit <= 0:
        return text
    s = text or ""
    if len(s) <= limit:
        return s
    note = "\n\n…（以下省略。サーバー負荷・メモリ上限のため切り詰めました）"
    keep = max(500, limit - len(note))
    return s[:keep] + note


def _emit_chunks(on_line_message: Callable[[str], None] | None, part: str, *, out_parts: list[str]) -> None:
    """論理パートを out_parts に積み、指定があれば分割済みチャンクをその場でコールバックする。"""
    p = (part or "").strip()
    if not p:
        return
    out_parts.append(p)
    if not on_line_message:
        return
    for chunk in split_line_text(p):
        on_line_message(chunk)


class DiscordBrain:
    def __init__(self, config: AppConfig):
        self._config = config
        self._client = OpenAI(api_key=config.openai_api_key)
        self._export_log_lines: dict[str, list[str]] = defaultdict(list)
        self._svc = CommandServices(
            config=config,
            client=self._client,
            hooks=ExportHooks(
                append_log=self._append_export_log,
                take_clear_log=self._take_clear_export_log,
            ),
        )
        self._histories: dict[str, list[dict]] = defaultdict(list)
        self._last_proc_by_user: dict[str, str] = {}

    def _append_export_log(self, uid: str, tag: str, body: str) -> None:
        body = (body or "").strip()
        if not body:
            return
        cap = 120_000
        if len(body) > cap:
            body = body[:cap] + "\n…（ログ行省略）"
        self._export_log_lines[uid or "anonymous"].append(f"=== {tag} ===\n{body}\n")

    def _take_clear_export_log(self, uid: str) -> str:
        key = uid or "anonymous"
        chunks = self._export_log_lines.pop(key, [])
        return "\n".join(chunks).strip()

    def _resolve_openai_model(self, user_id: str | None = None) -> str:
        from .chat_models import resolve_chat_model

        user_settings = get_discord_user_settings(user_id or "")
        return resolve_chat_model(
            user_model_from_choice(user_settings.get("using_model")),
            self._config.openai_model,
            self._config.allowed_chat_models | frozenset({"gpt-4o"}),
        )

    def _resolve_tool_notice_mode(self, override: str | None = None) -> ToolNoticeMode:
        raw_override = (override or "").strip().lower()
        if raw_override and raw_override != "inherit":
            return parse_tool_notice_mode(
                raw_override,
                legacy_show_ri_text="true",
            )
        return parse_tool_notice_mode("abbrev", legacy_show_ri_text="true")

    def _build_system_prompt_for_user(self, user_id: str) -> str:
        base = self._config.system_prompt
        user_settings = get_discord_user_settings(user_id or "")
        if user_settings.get("personal_memory") != "true":
            return base
        note = get_mid_term_note(user_id or "")
        if not note.strip():
            return base
        suffix = (
            "\n\n▼中期記憶（このマスター専用・1本・最大200字。サーバーが毎ターンここへ最新文を差し込みます）\n"
            f"{note.strip()}"
        )
        return base + suffix

    def _maybe_refresh_system_for_mid_memory(self, messages: list[dict], uid: str, parsed: dict) -> None:
        cmd = (parsed.get("CMD") or "").strip().upper()
        if cmd not in ("MID-MEMORY-APPEND", "MID-MEMORY-CLEAR"):
            return
        if not messages or messages[0].get("role") != "system":
            return
        messages[0] = {"role": "system", "content": self._build_system_prompt_for_user(uid)}

    def _ensure_session(self, user_id: str) -> list[dict]:
        key = user_id or "anonymous"
        hist = self._histories[key]
        content = self._build_system_prompt_for_user(user_id or "anonymous")
        if not hist:
            hist.append({"role": "system", "content": content})
        else:
            hist[0] = {"role": "system", "content": content}
        return hist

    def _trim(self, messages: list[dict], *, max_turns: int | None = None) -> None:
        if max_turns is None:
            max_turns = max(1, self._config.max_history_turns)
        if max_turns <= 0:
            del messages[1:]
            return
        while len(messages) > 1 + 2 * max_turns:
            del messages[1:3]

    def _complete(self, messages: list[dict], *, user_id: str | None = None) -> tuple[str, dict]:
        model = self._resolve_openai_model(user_id)
        response = self._client.chat.completions.create(
            model=model,
            messages=messages,
        )
        text = (response.choices[0].message.content or "").strip()
        return text, _usage_from_response(response)

    def _execute(self, parsed: dict, uid: str):
        command = (parsed.get("CMD") or "SPEAK").strip().upper()
        args = parsed.get("ARGS") or {}
        if not isinstance(args, dict):
            args = {}
        TEXT = parsed.get("TEXT", "")
        NOTE = parsed.get("NOTE", "")

        handler = COMMAND_HANDLERS.get(command)
        if not handler:
            TEXT_out = MSG_UNKNOWN_COMMAND.format(command=command)
            return TEXT_out, False, TEXT_out, TEXT_out, None, None

        res = handler(self._svc, args, TEXT, NOTE=NOTE, user_id=uid)

        summary_token_usage = None
        if isinstance(res, tuple) and len(res) >= 5:
            TEXT_out, dmis_log, summary, raw_result, summary_token_usage = res[:5]
        elif isinstance(res, tuple) and len(res) >= 4:
            TEXT_out, dmis_log, summary, raw_result = res[:4]
        elif isinstance(res, tuple) and len(res) == 3:
            TEXT_out, dmis_log, summary = res
            raw_result = None
        else:
            TEXT_out, dmis_log = res
            summary = TEXT_out
            raw_result = None

        a2 = parsed.get("ARGS_2") or {}
        should_retry = bool(isinstance(a2, dict) and a2.get("retry"))

        return TEXT_out, should_retry, dmis_log, summary, raw_result, summary_token_usage

    def reply(
        self,
        user_id: str,
        user_text: str,
        *,
        on_line_message: Callable[[str], None] | None = None,
        tool_notice_display_override: str | None = None,
    ) -> list[str]:
        """ユーザーへの送信パートの並び。`on_line_message` があるときは各パートを確定次第コールバックする。"""
        text = (user_text or "").strip()
        if not text:
            msg = "メッセージが空のようです。入力内容をご確認ください。"
            empty_parts: list[str] = [msg]
            if on_line_message:
                for chunk in split_line_text(msg):
                    on_line_message(chunk)
            return empty_parts

        uid = user_id or "anonymous"
        user_settings = get_discord_user_settings(uid)
        max_history_turns = talking_memory_turns(user_settings.get("talking_memory"))

        notice_mode = self._resolve_tool_notice_mode(tool_notice_display_override)

        messages = self._ensure_session(uid)
        boot_line = take_worker_boot_push_for_history(uid)
        if boot_line:
            messages.append({"role": "assistant", "content": boot_line})
            self._append_export_log(uid, "WORKER_BOOT_PUSH", boot_line)

        lp = self._last_proc_by_user.get(uid, "（前回処理なし）")

        self._append_export_log(uid, "USER", text)

        payload = build_input_segments(
            text,
            is_retry=False,
            last_proc_result=lp,
            input_main=self._config.input_main,
        )
        formatted = payload["text"]
        messages.append({"role": "user", "content": formatted})
        self._trim(messages, max_turns=max_history_turns)

        ai_raw, usage = self._complete(messages, user_id=uid)
        total_tokens_used = int((usage or {}).get("total_tokens") or 0)
        self._append_export_log(uid, "ASSISTANT_RAW", ai_raw)
        if notice_mode is ToolNoticeMode.RAW:
            raw_parts: list[str] = []
            messages.append({"role": "assistant", "content": ai_raw})
            self._trim(messages, max_turns=max_history_turns)
            _emit_chunks(on_line_message, ai_raw, out_parts=raw_parts)
            record_discord_user_usage(uid, tokens=total_tokens_used)
            return raw_parts
        parsed = parse_ai_response(ai_raw)
        parse_ok = bool(parsed.pop("__dkis_parse_ok__", False))
        if not parse_ok:
            TEXT = MSG_AI_OUTPUT_PARSE_FAILED.strip()
            should_retry = False
            dmis_log = "応答フォーマット異常"
            summary = TEXT
            raw_result = None
            _tu = None
            parsed = {
                "CMD": "SPEAK",
                "ARGS": {},
                "ARGS_2": {"retry": False},
                "TEXT": TEXT,
                "NOTE": "",
            }
        else:
            TEXT, should_retry, dmis_log, summary, raw_result, _tu = self._execute(parsed, uid)
        self._maybe_refresh_system_for_mid_memory(messages, uid, parsed)
        self._last_proc_by_user[uid] = dmis_log or "通常会話応答"

        if isinstance(raw_result, dict) and raw_result.get("__suppress_followup_retry__"):
            should_retry = False

        out_parts: list[str] = []
        retry_round = 0

        while should_retry and parse_ok and retry_round < self._config.max_retry_chain:
            retry_round += 1
            cmd = (parsed.get("CMD") or "SPEAK").strip().upper()
            detail = _rt_arg_summary(cmd, parsed.get("ARGS"), limit=0 if notice_mode is ToolNoticeMode.FULL else 120)
            parts_body = (TEXT or "").strip()
            notice = ""
            if show_retry_notice(notice_mode):
                notice = format_retry_notice_line(
                    retry_round,
                    cmd,
                    detail,
                    usage,
                    abbreviated=retry_line_abbreviated(notice_mode),
                )
            combined = parts_body
            if notice:
                combined = f"{parts_body}\n{notice}" if parts_body else notice
            _emit_chunks(on_line_message, combined, out_parts=out_parts)

            ri_raw = summary if isinstance(summary, str) else ""
            if not ri_raw:
                ri_raw = TEXT or ""
            ri_text = _truncate_tool_payload(ri_raw, self._config.max_retry_payload_chars)
            lp_now = self._last_proc_by_user.get(uid, lp)
            retry_payload = build_input_segments(
                ri_text,
                is_retry=True,
                last_proc_result=lp_now,
                input_main=self._config.input_main,
            )
            retry_formatted = retry_payload["text"]

            messages.append(
                {"role": "assistant", "content": _truncate_tool_payload(ai_raw, self._config.max_retry_payload_chars)}
            )
            messages.append({"role": "user", "content": retry_formatted})
            self._trim(messages, max_turns=max_history_turns)

            ai_raw, usage = self._complete(messages, user_id=uid)
            total_tokens_used += int((usage or {}).get("total_tokens") or 0)
            self._append_export_log(uid, "ASSISTANT_RAW", ai_raw)
            parsed_retry = parse_ai_response(ai_raw)
            parse_ok = bool(parsed_retry.pop("__dkis_parse_ok__", False))
            if not parse_ok:
                TEXT = MSG_AI_OUTPUT_PARSE_FAILED.strip()
                should_retry = False
                dmis_log = "応答フォーマット異常"
                summary = TEXT
                raw_result = None
                _tu = None
                parsed = {
                    "CMD": "SPEAK",
                    "ARGS": {},
                    "ARGS_2": {"retry": False},
                    "TEXT": TEXT,
                    "NOTE": "",
                }
            else:
                TEXT, should_retry, dmis_log, summary, raw_result, _tu = self._execute(parsed_retry, uid)
                parsed = parsed_retry
            self._maybe_refresh_system_for_mid_memory(messages, uid, parsed)
            self._last_proc_by_user[uid] = dmis_log or "通常会話応答"

            if isinstance(raw_result, dict) and raw_result.get("__suppress_followup_retry__"):
                should_retry = False

        assistant_hist = (
            format_model_response_block(
                (TEXT or "").strip(),
                note="（応答ブロックの解析に失敗したため定型文を返しています）",
            )
            if not parse_ok
            else ai_raw
        )
        messages.append({"role": "assistant", "content": assistant_hist})
        self._trim(messages, max_turns=max_history_turns)

        final_text = (TEXT or "").strip()
        footer_line = ""
        if show_normal_footer(notice_mode):
            cmd_last = (parsed.get("CMD") or "SPEAK").strip().upper()
            detail_last = _rt_arg_summary(cmd_last, parsed.get("ARGS"), limit=0 if notice_mode is ToolNoticeMode.FULL else 120)
            footer_line = format_normal_footer_line(
                cmd_last,
                detail_last,
                usage,
                abbreviated=(notice_mode is ToolNoticeMode.ABBREV),
            )
        if final_text:
            combined = final_text + (("\n" + footer_line) if footer_line else "")
            _emit_chunks(on_line_message, combined, out_parts=out_parts)
        elif footer_line:
            _emit_chunks(on_line_message, footer_line, out_parts=out_parts)

        fallback = MSG_REPLY_ASSEMBLY_FAILED
        if not out_parts:
            if on_line_message:
                for chunk in split_line_text(fallback):
                    on_line_message(chunk)
            return [fallback]

        record_discord_user_usage(uid, tokens=total_tokens_used)
        return out_parts


# 旧 import 名の互換。実体は Discord 向けの応答エンジン。
LineBrain = DiscordBrain
