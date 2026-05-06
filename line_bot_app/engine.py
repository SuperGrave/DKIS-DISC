"""コマンド実行・RETRY 連鎖・ユーザー別会話履歴。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from openai import OpenAI

from .commands import COMMAND_HANDLERS, CommandServices, ExportHooks
from .config import AppConfig
from .input_build import build_input_segments
from .line_messages import split_line_text
from .parsing import parse_ai_response


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


def _usage_suffix(usage: dict | None) -> str:
    if not usage:
        return "tok=—"
    tt = usage.get("total_tokens")
    if tt is not None:
        return f"tt={tt}"
    pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if pt is not None and ct is not None:
        return f"pt={pt} ct={ct}"
    return "tok=—"


def _rt_arg_summary(command: str, args: object) -> str:
    if not isinstance(args, dict):
        args = {}
    if command == "SEARCH":
        q = str(args.get("query") or "").strip()
        return (q[:120] + "…") if len(q) > 120 else (q or "(queryなし)")
    if command == "NEWS":
        q = str(args.get("query") or "").strip()
        return (q[:120] + "…") if len(q) > 120 else (q or "(queryなし)")
    if command == "WEATHER":
        w = str(args.get("w_location") or "").strip()
        return (w[:120] + "…") if len(w) > 120 else (w or "(地名なし)")
    if command == "READ-PAGE":
        u = str(args.get("url") or "").strip()
        return (u[:120] + "…") if len(u) > 120 else (u or "(urlなし)")
    if command == "LIST-FILES":
        return "(一覧)"
    if command in ("READ-TEXT", "WRITE-TEXT", "APPEND-TEXT", "SAVE-LOG"):
        fn = str(args.get("filename") or "").strip()
        return (fn[:120] + "…") if len(fn) > 120 else (fn or "(filenameなし)")
    if command == "GET-SETTING":
        return str(args.get("key") or "(key)")
    if command == "SET-SETTING":
        k = str(args.get("key") or "")
        return (k[:60] + "…") if len(k) > 60 else k or "(key)"
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


class LineBrain:
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

    def _resolve_openai_model(self) -> str:
        from .supabase_store import get_db_setting

        db = get_db_setting("current_model", "").strip()
        return db if db else self._config.openai_model

    def _resolve_show_ri_text(self) -> bool:
        from .supabase_store import get_db_setting

        v = get_db_setting("show_ri_text", "true").strip().lower()
        return v not in ("0", "false", "no", "off")

    def _ensure_session(self, user_id: str) -> list[dict]:
        key = user_id or "anonymous"
        hist = self._histories[key]
        if not hist:
            hist.append({"role": "system", "content": self._config.system_prompt})
        return hist

    def _trim(self, messages: list[dict]) -> None:
        max_turns = max(1, self._config.max_history_turns)
        while len(messages) > 1 + 2 * max_turns:
            del messages[1:3]

    def _complete(self, messages: list[dict]) -> tuple[str, dict]:
        model = self._resolve_openai_model()
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
            TEXT_out = f"すみません、そのコマンド（{command}）はこの環境では使えません。"
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
    ) -> list[str]:
        """ユーザーへの送信パートの並び。`on_line_message` があるときは各パートを確定次第コールバック（LINE 逐次送信用）。"""
        text = (user_text or "").strip()
        if not text:
            msg = "すみません、メッセージが空みたいです。もう一度送ってください。"
            empty_parts: list[str] = [msg]
            if on_line_message:
                for chunk in split_line_text(msg):
                    on_line_message(chunk)
            return empty_parts

        uid = user_id or "anonymous"

        messages = self._ensure_session(uid)
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
        self._trim(messages)

        ai_raw, usage = self._complete(messages)
        self._append_export_log(uid, "ASSISTANT_RAW", ai_raw)
        parsed = parse_ai_response(ai_raw)
        TEXT, should_retry, dmis_log, summary, raw_result, _tu = self._execute(parsed, uid)
        self._last_proc_by_user[uid] = dmis_log or "通常会話応答"

        if isinstance(raw_result, dict) and raw_result.get("__suppress_followup_retry__"):
            should_retry = False

        out_parts: list[str] = []
        retry_round = 0

        while should_retry and retry_round < self._config.max_retry_chain:
            retry_round += 1
            _emit_chunks(on_line_message, TEXT, out_parts=out_parts)
            cmd = (parsed.get("CMD") or "SPEAK").strip().upper()
            detail = _rt_arg_summary(cmd, parsed.get("ARGS"))
            rt_line = f"[RT#{retry_round}]{cmd}:{detail} {_usage_suffix(usage)}"
            if self._resolve_show_ri_text():
                _emit_chunks(on_line_message, rt_line, out_parts=out_parts)

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
            self._trim(messages)

            ai_raw, usage = self._complete(messages)
            self._append_export_log(uid, "ASSISTANT_RAW", ai_raw)
            parsed_retry = parse_ai_response(ai_raw)
            TEXT, should_retry, dmis_log, summary, raw_result, _tu = self._execute(parsed_retry, uid)
            parsed = parsed_retry
            self._last_proc_by_user[uid] = dmis_log or "通常会話応答"

            if isinstance(raw_result, dict) and raw_result.get("__suppress_followup_retry__"):
                should_retry = False

        messages.append({"role": "assistant", "content": ai_raw})
        self._trim(messages)

        final_text = (TEXT or "").strip()
        if final_text:
            _emit_chunks(on_line_message, final_text, out_parts=out_parts)

        fallback = "すみません、うまく返答を組み立てられませんでした。"
        if not out_parts:
            if on_line_message:
                for chunk in split_line_text(fallback):
                    on_line_message(chunk)
            return [fallback]

        return out_parts
