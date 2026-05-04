"""コマンド実行・RETRY 連鎖・ユーザー別会話履歴。"""

from __future__ import annotations

from collections import defaultdict

from openai import OpenAI

from .commands import COMMAND_HANDLERS, CommandServices
from .config import AppConfig
from .input_build import build_input_segments
from .parsing import parse_ai_response


class LineBrain:
    def __init__(self, config: AppConfig):
        self._config = config
        self._client = OpenAI(api_key=config.openai_api_key)
        self._svc = CommandServices(config=config)
        self._histories: dict[str, list[dict]] = defaultdict(list)
        self._last_proc_by_user: dict[str, str] = {}

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

    def _complete(self, messages: list[dict]) -> str:
        response = self._client.chat.completions.create(
            model=self._config.openai_model,
            messages=messages,
        )
        return (response.choices[0].message.content or "").strip()

    def _execute(self, parsed: dict):
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

        res = handler(self._svc, args, TEXT, NOTE=NOTE)

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

    def reply(self, user_id: str, user_text: str) -> str:
        text = (user_text or "").strip()
        if not text:
            return "すみません、メッセージが空みたいです。もう一度送ってください。"

        uid = user_id or "anonymous"

        messages = self._ensure_session(uid)
        lp = self._last_proc_by_user.get(uid, "（前回処理なし）")

        payload = build_input_segments(
            text,
            is_retry=False,
            last_proc_result=lp,
            input_main=self._config.input_main,
        )
        formatted = payload["text"]
        messages.append({"role": "user", "content": formatted})
        self._trim(messages)

        ai_raw = self._complete(messages)
        parsed = parse_ai_response(ai_raw)
        TEXT, should_retry, dmis_log, summary, raw_result, _tu = self._execute(parsed)
        self._last_proc_by_user[uid] = dmis_log or "通常会話応答"

        if isinstance(raw_result, dict) and raw_result.get("__suppress_followup_retry__"):
            should_retry = False

        attempt = 0
        while should_retry and attempt < self._config.max_retry_chain:
            attempt += 1
            ri_text = summary or TEXT or ""
            lp_now = self._last_proc_by_user.get(uid, lp)
            retry_payload = build_input_segments(
                ri_text,
                is_retry=True,
                last_proc_result=lp_now,
                input_main=self._config.input_main,
            )
            retry_formatted = retry_payload["text"]

            messages.append({"role": "assistant", "content": ai_raw})
            messages.append({"role": "user", "content": retry_formatted})
            self._trim(messages)

            ai_raw = self._complete(messages)
            parsed_retry = parse_ai_response(ai_raw)
            TEXT, should_retry, dmis_log, summary, raw_result, _tu = self._execute(parsed_retry)
            self._last_proc_by_user[uid] = dmis_log or "通常会話応答"

            if isinstance(raw_result, dict) and raw_result.get("__suppress_followup_retry__"):
                should_retry = False

        messages.append({"role": "assistant", "content": ai_raw})
        self._trim(messages)

        out = (TEXT or "").strip()
        return out or "すみません、うまく返答を組み立てられませんでした。"
