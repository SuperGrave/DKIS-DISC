from openai import OpenAI
from config import (
    OPENAI_API_KEY,
    GPT_MODEL_CHAT_ONLY,
)
from core.settings_manager import get_default_prompt, get_prompt_setting
from core.context_provider import set_last_user_input, set_last_proc_result
from core.response_handler import speak_response
from core.logger import append_chat_only_log
from core.input_builder import build_input_segments

import time

BLUE_BRIGHT = "\033[94m"
RED_BRIGHT = "\033[91m"
RESET = "\033[0m"

client = OpenAI(api_key=OPENAI_API_KEY)

_system_prompt = get_prompt_setting("chat_only", get_default_prompt("chat_only"))
_chat_only_history = []
_chat_only_history_limit = 3
_chat_only_model = GPT_MODEL_CHAT_ONLY

_increment_stat_func = None
_error_sender = None


def set_increment_stat_func(func):
    """統計カウンター関数を設定"""
    global _increment_stat_func
    _increment_stat_func = func


def set_error_sender(func):
    """エラー送信関数を設定"""
    global _error_sender
    _error_sender = func


def _notify_error(error_type: str, summary: str, details: str):
    if _error_sender:
        _error_sender(error_type, summary, details)
    else:
        print(f"[chat_loop] {error_type}: {summary} / {details}")


def reload_chat_loop_settings():
    """設定ファイルから最新の値を取得"""
    global _system_prompt, _chat_only_model, _chat_only_history_limit, _chat_only_history
    try:
        from core.settings_manager import get_setting

        prompt = get_prompt_setting("chat_only", get_default_prompt("chat_only"))
        model = get_setting("ai_models.chat_only", GPT_MODEL_CHAT_ONLY) or GPT_MODEL_CHAT_ONLY
        history_limit = get_setting("control.chat_only_max_history", 3)
        if history_limit is None:
            history_limit = 3
    except Exception as e:
        print(f"[CLP] ⚠️ 設定の取得に失敗したためデフォルト値を使用します: {e}")
        prompt = get_prompt_setting("chat_only", get_default_prompt("chat_only"))
        model = GPT_MODEL_CHAT_ONLY
        history_limit = 3

    _system_prompt = prompt
    _chat_only_model = model
    try:
        _chat_only_history_limit = max(0, int(history_limit))
    except Exception:
        _chat_only_history_limit = 3

    # プロンプト変更時の履歴リセットは設定で制御
    reset_history = True
    try:
        reset_history = get_setting("control.reset_history_on_prompt_change", True)
    except Exception:
        pass
    if reset_history:
        _chat_only_history = []
        print(f"[CLP] 設定: model={_chat_only_model}, history_limit={_chat_only_history_limit}")
    else:
        print(f"[CLP] 設定: model={_chat_only_model}, history_limit={_chat_only_history_limit}")


def _trim_history():
    if _chat_only_history_limit <= 0:
        _chat_only_history.clear()
        return
    max_items = _chat_only_history_limit * 2
    if len(_chat_only_history) > max_items:
        del _chat_only_history[:-max_items]


def _build_messages(user_text: str):
    messages = [{"role": "system", "content": _system_prompt}]
    if _chat_only_history_limit > 0 and _chat_only_history:
        max_items = _chat_only_history_limit * 2
        messages.extend(_chat_only_history[-max_items:])
    messages.append({"role": "user", "content": user_text})
    return messages


def handle_chat_only_input(user_text: str, user_id: str = None, client_id: str = None):
    """省エネ会話モードのハンドラー"""
    global _chat_only_history
    set_last_user_input(user_text)

    start_time = time.time()
    input_payload = build_input_segments("chat_only", user_text, is_retry=False)
    formatted_input = input_payload["text"]

    print(BLUE_BRIGHT + "======== [USER INPUT] =========" + RESET)
    print(formatted_input)
    print(BLUE_BRIGHT + "===============================" + RESET)

    # メインループと同様に、「きりたんに実際に送った最終入力」を全クライアントへ通知
    try:
        from core.webhook_manager import get_webhook_url
        import requests

        webhook_url = get_webhook_url()
        if webhook_url:
            payload = {
                "mode": "chat_only",
                "input_type": "UI",
                "text": formatted_input,
                "segments": input_payload.get("segments", []),
                "timestamp": input_payload.get("timestamp"),
                "raw_text": user_text,
                "user_id": user_id,
                "client_id": client_id,
                "retry": False,
            }
            requests.post(webhook_url + "/user_input", json=payload, timeout=2)
    except Exception:
        print("[Webhook] /user_input 通知に失敗しました（chat_only）")

    messages = _build_messages(formatted_input)

    if _increment_stat_func:
        _increment_stat_func("ai_requests")

    token_usage = None
    try:
        response = client.chat.completions.create(
            model=_chat_only_model,
            messages=messages
        )
        reply = response.choices[0].message.content or ""
        token_usage = None
        if hasattr(response, "usage") and response.usage:
            token_usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
            }

        print(RED_BRIGHT + "\n======= [AI RAW OUTPUT] =======" + RESET)
        print(reply)
        print(RED_BRIGHT + "===============================" + RESET)
    except Exception as e:
        _notify_error("chat_only_openai", "会話だけモードのOpenAI呼び出しに失敗しました", str(e))
        raise

    _chat_only_history.extend([
        {"role": "user", "content": formatted_input},
        {"role": "assistant", "content": reply}
    ])
    _trim_history()

    processing_time = time.time() - start_time
    speak_response(reply, ai_raw=reply, processing_time=processing_time, token_usage=token_usage)
    append_chat_only_log(formatted_input, reply, ai_raw=reply, ts_str=input_payload.get("timestamp"))
    set_last_proc_result("省エネ会話モードで通常応答。")

    return reply, False, reply, processing_time

