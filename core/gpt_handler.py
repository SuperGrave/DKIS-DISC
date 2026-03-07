# core/gpt_handler.py
from openai import OpenAI
from config import (
    OPENAI_API_KEY, MAX_HISTORY,
    GPT_MODEL_MAIN  # メインループ用のモデル
)
from core.settings_manager import get_default_prompt, get_prompt_setting
from core.context_provider import (
    get_current_time, get_last_proc_result, set_last_proc_result, get_current_location,
    set_last_user_input
)
from core.functions import (
    chat_response, save_log,
    search_google, search_news, play_music_from_spotify, play_youtube, get_weather,
    pause_music, resume_music, read_text_file, read_webpage
)
from core.voicevox_handler import speak_voicevox  # 未対応コマンド時の音声用
from core.logger import append_log_pretty  # 新ロガー（#n / USER INPUT / AI RAW / RETRY 対応）
from core.input_builder import build_input_segments

import json
import re

# main.pyからの循環importを避けるため、関数として定義（後でmainから注入）
_get_next_global_retry_id = None

def set_global_retry_id_getter(func):
    """main.pyから get_next_global_retry_id 関数を注入"""
    global _get_next_global_retry_id
    _get_next_global_retry_id = func

# エラー送信関数（main.pyから注入される）
_send_error_event = None

def set_error_sender(error_sender_func):
    """main.pyからエラー送信関数を注入する"""
    global _send_error_event
    _send_error_event = error_sender_func

def _send_openai_error(error_type: str, summary: str, details: str):
    """OpenAI関連のエラーを送信"""
    if _send_error_event:
        _send_error_event(error_type, summary, details)
    else:
        print(f"[OpenAI] エラー送信機能が未初期化: {error_type} - {summary}")

# 統計カウンター関数（main.pyから注入される）
_increment_stat_func = None

# SSE送信関数（webhook未設定時のフォールバック用）
_send_event_func = None

def set_send_event_func(func):
    """SSE送信関数を設定（webhook未設定時にretry_inputを直接配信）"""
    global _send_event_func
    _send_event_func = func


def _dispatch_retry_input(payload: dict):
    from core.webhook_manager import get_webhook_url
    import requests

    try:
        webhook_url = get_webhook_url()
        if webhook_url:
            requests.post(webhook_url + "/retry_input", json=payload, timeout=2)
        elif _send_event_func:
            _send_event_func("retry_input", payload)
    except Exception:
        pass

def set_increment_stat_func(func):
    """統計カウンター関数を設定"""
    global _increment_stat_func
    _increment_stat_func = func

# 会話内RETRYカウンター（会話が終わるたびにリセット）
_conversation_retry_counter = 0

def get_next_conversation_retry_id():
    """次の会話内RETRY IDを取得"""
    global _conversation_retry_counter
    _conversation_retry_counter += 1
    return _conversation_retry_counter

def reset_conversation_retry_counter():
    """会話内RETRYカウンターをリセット"""
    global _conversation_retry_counter
    _conversation_retry_counter = 0

BLUE  = '\033[34m'
RED   = '\033[31m'
BLUE_BRIGHT = "\033[94m"
RED_BRIGHT = "\033[91m"
GREEN_BRIGHT = '\033[92m'
RESET = '\033[0m'

client = OpenAI(api_key=OPENAI_API_KEY)

# --- システムプロンプト管理 ---
_system_prompt = get_prompt_setting("main", get_default_prompt("main"))  # 現在のシステムプロンプト

def reload_system_prompt():
    """システムプロンプトを再読み込み。設定により会話履歴をリセットするか、先頭のsystemだけ差し替えるか選択可能"""
    global _system_prompt, chat_history
    try:
        from core.settings_manager import get_setting
        _system_prompt = get_prompt_setting("main", get_default_prompt("main"))

        reset_history = get_setting("control.reset_history_on_prompt_change", True)
        if reset_history:
            chat_history = [{"role": "system", "content": _system_prompt}]
            print(GREEN_BRIGHT + "[GPT] システムプロンプトをリロードしました（履歴リセット）" + RESET)
        else:
            if len(chat_history) > 0 and chat_history[0].get("role") == "system":
                chat_history[0]["content"] = _system_prompt
            else:
                chat_history.insert(0, {"role": "system", "content": _system_prompt})
            print(GREEN_BRIGHT + "[GPT] システムプロンプトをリロードしました（履歴保持）" + RESET)
        return _system_prompt
    except Exception as e:
        print(f"[GPT] システムプロンプトリロードエラー: {e}")
        _send_openai_error("openai_config", "システムプロンプトの読み込みに失敗しました", str(e))
        # エラー時は現在のプロンプトを維持
        return _system_prompt

def get_current_system_prompt():
    """現在のシステムプロンプトを取得"""
    return _system_prompt

# --- チャット履歴管理 ---
chat_history = [{"role": "system", "content": _system_prompt}]

# 動的設定管理用のグローバル変数
_current_max_history = MAX_HISTORY

def get_current_max_history():
    """現在の最大履歴数を取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("control.max_history", _current_max_history)
    except:
        return _current_max_history

def reload_gpt_settings():
    """GPT設定をリロード"""
    global _current_max_history
    try:
        from core.settings_manager import get_setting
        _current_max_history = get_setting("control.max_history", MAX_HISTORY)
        print(f"[GPT] 設定をリロードしました: MaxHistory={_current_max_history}")
    except Exception as e:
        print(f"[GPT] 設定リロードエラー: {e}")

# --- コマンド関数マップ（大文字キー想定）---
command_handlers = {
    "SPEAK": chat_response,
    "SAVE-LOG": save_log,
    "SEARCH": search_google,
    "NEWS": search_news,
    "PLAY-MUSIC": play_music_from_spotify,
    "PLAY-YOUTUBE": play_youtube,
    "PAUSE-MUSIC": pause_music,
    "RESUME-MUSIC": resume_music,
    "WEATHER": get_weather,
    "READ-TEXT": read_text_file,
    "READ-PAGE": read_webpage,
}

def ask_chatgpt(user_text: str) -> tuple[str, dict | None]:
    """モデル呼び出し。履歴のスリム化は MAX_HISTORY に準拠。
    Returns:
        (reply, usage_dict): usage_dict は {"prompt_tokens", "completion_tokens", "total_tokens"} または None
    """
    global chat_history
    
    # 統計：GPT呼び出し回数をカウント
    if _increment_stat_func:
        _increment_stat_func("ai_requests")
    
    # モデル名を設定ファイルから取得（なければconfig.pyのデフォルト）
    try:
        from core.settings_manager import get_setting
        model = get_setting("ai_models.main", GPT_MODEL_MAIN)
    except:
        model = GPT_MODEL_MAIN
    
    chat_history.append({"role": "user", "content": user_text})
    try:
        response = client.chat.completions.create(
            model=model,  # 設定ファイルまたはconfig.pyから
            messages=chat_history
        )
        reply = response.choices[0].message.content or ""
        chat_history.append({"role": "assistant", "content": reply})
        # トークン使用量を取得（OpenAI APIのusageオブジェクト）
        usage = None
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
            }
    except Exception as e:
        _send_openai_error("openai_api", "OpenAI API呼び出しに失敗しました", str(e))
        raise

    # 履歴を上限に収める（system + user/assistant × MAX_HISTORY）
    max_history = get_current_max_history()
    while len(chat_history) > max_history * 2 + 1:
        del chat_history[1:3]

    return reply, usage

def parse_ai_response(text: str) -> dict:
    """
    生成テキストからタグを抽出。
    - [CMD] / [COMMAND] をどちらも受理
    - [ARGS-2] は JSON 推奨（例: {"retry": true}）
    - [TEXT], [NOTE] は複数行対応
    """
    pattern = re.compile(r"^\[(CMD|COMMAND|ARGS|ARGS-2|TEXT|NOTE)\](.*)$")
    parsed = {
        "CMD": None,
        "ARGS": {},
        "ARGS_2": {"retry": False},
        "TEXT": "",
        "NOTE": ""
    }

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = pattern.match(lines[i].strip())
        if not m:
            i += 1
            continue

        key, first = m.groups()
        key = key.replace("-", "_")  # ARGS-2 → ARGS_2
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
                    parsed[key] = first
        else:
            # CMD / COMMAND
            parsed[key] = first.strip().upper() if first else None

        i += 1

    # COMMAND の別名を取り込み
    if not parsed.get("CMD") and parsed.get("COMMAND"):
        parsed["CMD"] = parsed["COMMAND"]

    # ARGS_2 の retry 正規化
    a2 = parsed.get("ARGS_2")
    if isinstance(a2, dict):
        a2.setdefault("retry", False)
    else:
        parsed["ARGS_2"] = {"retry": False}
    
    # GPT-5対策：TEXTとNOTEの末尾に余分な [] がある場合は除去
    if parsed.get("TEXT"):
        # 末尾の [] を除去（複数ある場合も対応）
        parsed["TEXT"] = re.sub(r'\[\s*\]$', '', parsed["TEXT"]).strip()
    if parsed.get("NOTE"):
        parsed["NOTE"] = re.sub(r'\[\s*\]$', '', parsed["NOTE"]).strip()

    return parsed

def execute_command(parsed: dict, user_input: str, ai_raw: str = None, processing_time: float = 0.0, token_usage: dict = None):
    """
    ここでは一切 SSE を送らない。
    - SPEAK/SEARCH/WEATHER 等の実コマンドは、関数内で speak_voicevox() を呼ぶ実装にしておく。
    - 未対応コマンドのみ、ここでフォールバック発話を行う。
    """
    command = (parsed.get("CMD") or "SPEAK").strip().upper()
    args = parsed.get("ARGS", {})
    TEXT = parsed.get("TEXT", "")

    if not isinstance(args, dict):
        args = {}

    handler = command_handlers.get(command)
    if handler:
        # 統計：コマンド実行回数をカウント
        if _increment_stat_func:
            _increment_stat_func("commands", command)
        
        res = handler(args, TEXT, NOTE=parsed.get("NOTE"), ai_raw=ai_raw, processing_time=processing_time, token_usage=token_usage)
        summary_token_usage = None
        if isinstance(res, tuple) and len(res) >= 5:
            TEXT, dmis_log, summary, raw_result, summary_token_usage = res[0], res[1], res[2], res[3], res[4]
        elif isinstance(res, tuple) and len(res) >= 4:
            TEXT, dmis_log, summary, raw_result = res[0], res[1], res[2], res[3]
        elif isinstance(res, tuple) and len(res) == 3:
            TEXT, dmis_log, summary = res
            raw_result = None
        else:
            TEXT, dmis_log = res
            summary = TEXT
            raw_result = None
    else:
        # 未対応コマンドはエラー文面を音声で返す（utter_id付き reply は voicevox 側で送られる）
        TEXT = f"システムは、使用不可能なコマンド『{command}』を使用しました。"
        dmis_log = f"未対応のコマンドを指定：{command}"
        summary = TEXT
        raw_result = None
        summary_token_usage = None
        speak_voicevox(TEXT, ai_raw=ai_raw, token_usage=token_usage)

    # 6値返し（TEXT, should_retry, dmis_log, summary, raw_result, summary_token_usage）
    should_retry = False
    a2 = parsed.get("ARGS_2", {})
    if isinstance(a2, dict):
        should_retry = bool(a2.get("retry", False))
    return TEXT, should_retry, dmis_log, summary, raw_result, summary_token_usage

def _get_command_from_parsed(parsed):
    """パース結果からコマンド名を取得（raw_resultの出所表示用）"""
    return (parsed.get("CMD") or "SPEAK").strip().upper()

# 1ターンぶんを整形ログにまとめて保存（リトライ連鎖も同梱）
def handle_user_input_v2(
    user_text: str,
    is_retry: bool = False,
    retry_count: int = 0,
    max_retries: int = 5,
    user_id: str = None,
    client_id: str = None,
):
    """
    上位（main.py）から呼ばれるエントリ。SSEは送らない。
    印字もログも「投下直前スナップショット」を使うので、LP/NL/NTのズレが発生しない。
    """
    import time
    
    # 処理開始時刻を記録
    start_time = time.time()
    
    # ユーザー入力を保存（テキストファイル要約用）
    set_last_user_input(user_text)
    
    # --- 初回：投下直前をスナップ（印字・ログで共有）
    # is_retry=True で呼ばれた場合も、ここでは「そのターン内の初回入力」として扱う
    input_payload = build_input_segments("main", user_text, is_retry=is_retry)
    formatted_input = input_payload["text"]
    structured_input = {
        "lines": input_payload.get("segments", []),
        "timestamp": input_payload.get("timestamp"),
        "raw_text": user_text
    }
    print(BLUE_BRIGHT + "======== [USER INPUT] =========" + RESET)
    print(formatted_input)
    print(BLUE_BRIGHT + "===============================" + RESET)

    # サーバー側で付加された最終入力内容を他デバイスにも共有するため、SSE用に通知
    # ※ 外側RETRYループから is_retry=True で呼ばれた場合は、ここからは配信しない
    if not is_retry:
        try:
            from core.webhook_manager import get_webhook_url
            import requests

            webhook_url = get_webhook_url()
            if webhook_url:
                payload = {
                    "mode": "main",
                    "input_type": "UI",
                    "text": formatted_input,
                    "segments": input_payload.get("segments", []),
                    "timestamp": input_payload.get("timestamp"),
                    "raw_text": user_text,
                    "user_id": user_id,
                    "client_id": client_id,
                    "retry": False,
                    "retry_index": retry_count,
                    "retry_total": max_retries,
                }
                requests.post(webhook_url + "/user_input", json=payload, timeout=2)
        except Exception:
            # ログ出力のみ（会話フローは継続）
            print("[Webhook] /user_input 通知に失敗しました（初回入力）")

    # モデル→パース→実行
    ai_raw, token_usage = ask_chatgpt(formatted_input)
    
    print(RED_BRIGHT + "\n======= [AI RAW OUTPUT] =======" + RESET)
    print(ai_raw)
    print(RED_BRIGHT + "===============================" + RESET)

    parsed = parse_ai_response(ai_raw)
    
    # 処理時間を計算（execute_command実行前）
    processing_time = time.time() - start_time
    
    TEXT, should_retry, dmis_log, summary, raw_result, summary_token_usage = execute_command(parsed, user_text, ai_raw, processing_time, token_usage)

    if isinstance(raw_result, dict) and raw_result.get("__suppress_followup_retry__"):
        should_retry = False

    if isinstance(raw_result, dict) and raw_result.get("__emit_retry_log__") and not should_retry:
        payload = {
            "retry_index": 1,
            "retry_total": 1,
            "retry_content": raw_result.get("__retry_content__", f"RI：{summary or TEXT or ''}"),
            "global_retry_id": _get_next_global_retry_id() if _get_next_global_retry_id else 0,
            "conversation_retry_id": get_next_conversation_retry_id(),
            "raw_result": raw_result,
            "raw_result_source": _get_command_from_parsed(parsed),
        }
        if summary_token_usage is not None and isinstance(summary_token_usage, dict):
            payload["token_usage"] = summary_token_usage
        _dispatch_retry_input(payload)

    # 実行後：次ターン用に LP を更新（スナップは既に固定済み）
    set_last_proc_result(dmis_log if dmis_log else "通常の会話応答を実行。")

    # --- リトライ連鎖（必要なときだけ）
    retry_sections = []
    attempt = 0
    current_should_retry = should_retry  # 初回のshould_retryを保存
    parsed_for_source = parsed  # raw_resultの出所（各RETRYで更新）

    while current_should_retry and attempt < max_retries:
        attempt += 1

        ri_text = summary or TEXT
        retry_payload = build_input_segments("main", ri_text, is_retry=True)
        retry_formatted = retry_payload["text"]
        print(GREEN_BRIGHT + "====== [DKIS RETRY INPUT] =====" + RESET)
        print(retry_formatted)
        print(GREEN_BRIGHT + "===============================" + RESET)
        
        # RETRY内容をHTML側に送信（raw_result はAI整形前の検索結果・読み込んだページの生データなど）
        global_retry_id = _get_next_global_retry_id() if _get_next_global_retry_id else 0
        conversation_retry_id = get_next_conversation_retry_id()

        retry_content = retry_formatted
        if isinstance(raw_result, dict):
            retry_content = raw_result.get("__retry_content__", retry_formatted)

        payload = {
            "retry_index": attempt,
            "retry_total": max_retries,
            "retry_content": retry_content,
            "global_retry_id": global_retry_id,
            "conversation_retry_id": conversation_retry_id
        }
        if raw_result is not None:
            payload["raw_result"] = raw_result
            payload["raw_result_source"] = _get_command_from_parsed(parsed_for_source)
        if summary_token_usage is not None and isinstance(summary_token_usage, dict):
            payload["token_usage"] = summary_token_usage
        _dispatch_retry_input(payload)

        ai_raw_retry, token_usage_retry = ask_chatgpt(retry_formatted)
        print(RED_BRIGHT + "\n======= [AI RAW OUTPUT] =======" + RESET)
        print(ai_raw_retry)
        print(RED_BRIGHT + "===============================" + RESET)

        parsed_retry = parse_ai_response(ai_raw_retry)
        
        # リトライ時の処理時間を計算
        retry_processing_time = time.time() - start_time
        
        TEXT, should_retry, dmis_log, summary, raw_result, summary_token_usage_retry = execute_command(parsed_retry, user_text, ai_raw_retry, retry_processing_time, token_usage_retry)
        parsed_for_source = parsed_retry  # 次ターンのraw_result_source用
        summary_token_usage = summary_token_usage_retry  # 次イテレでretry_inputに使う（要約AI使用時のみ）

        # 実行後：次のループ用に LP を更新
        set_last_proc_result(dmis_log if dmis_log else "通常の会話応答を実行。")

        # ログには"投下直前スナップ"を保存（＝印字と完全一致）
        retry_sections.append({
            "index": attempt,
            "total": max_retries,
            "input": {
                "lines": retry_payload.get("segments", []),
                "timestamp": retry_payload.get("timestamp"),
                "raw_text": ri_text
            },
            "ai_parsed": parsed_retry
        })
        
        # 次のループの条件を更新
        current_should_retry = should_retry

    # --- 1ターン分の整形ログを出力（初回＋リトライ連鎖）
    append_log_pretty(
        user_input=structured_input,
        first_ai_parsed=parsed,
        retry_sections=retry_sections,
        ts_str=structured_input.get("timestamp")
    )

    # 会話終了時にカウンターをリセット
    reset_conversation_retry_counter()
    
    # 最終的な処理時間を計算（リトライも含む）
    final_processing_time = time.time() - start_time
    
    # 呼び出し元へ（処理時間・raw_result・raw_result_sourceも含める）
    raw_result_source = _get_command_from_parsed(parsed) if raw_result is not None else None
    return TEXT, should_retry, (summary or TEXT), final_processing_time, raw_result, raw_result_source