import datetime
import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from core.utils import print_color, CYAN
from config import LOG_SAVE_DIR, LOG_FILENAME_FORMAT

# --- 会話ログ管理 ---
conversation_log: List[str] = []   # 1ターン=1ブロック（文字列）を追加
log_max_count: int = 100           # 自動保存の閾値

# 表示用の連番（#1, #2, ...）
_display_no: int = 0
_display_no_date: Optional[str] = None  # 日付が変わったら連番リセット用

# 動的設定管理用のグローバル変数
_current_log_save_dir = LOG_SAVE_DIR
_current_log_filename_format = LOG_FILENAME_FORMAT

def get_current_log_save_dir():
    """現在のログ保存ディレクトリを取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("log.save_dir", _current_log_save_dir)
    except:
        return _current_log_save_dir

def get_current_log_filename_format():
    """現在のログファイル名フォーマットを取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("log.filename_format", _current_log_filename_format)
    except:
        return _current_log_filename_format

def reload_logger_settings():
    """Logger設定をリロード"""
    global _current_log_save_dir, _current_log_filename_format
    try:
        from core.settings_manager import get_setting
        _current_log_save_dir = get_setting("log.save_dir", LOG_SAVE_DIR)
        _current_log_filename_format = get_setting("log.filename_format", LOG_FILENAME_FORMAT)
        print(f"[Logger] 設定をリロードしました: SaveDir={_current_log_save_dir}, Format={_current_log_filename_format}")
    except Exception as e:
        print(f"[Logger] 設定リロードエラー: {e}")


def _next_display_no() -> int:
    """#番号（#1, #2, ...）を払い出す。同日内は通し番号。"""
    global _display_no, _display_no_date
    today = datetime.datetime.now().strftime("%Y%m%d")
    if _display_no_date != today:
        _display_no_date = today
        _display_no = 0
    _display_no += 1
    return _display_no


def _fmt_ai_raw_block(parsed: Dict[str, Any]) -> str:
    """
    AI生出力を所望の体裁に整形。
    想定キー: CMD, ARGS, ARGS_2(またはARGS-2), TEXT, NOTE
    （会話メモリ上の内部表現。保存ファイル用の最終フォーマットではない）
    """
    cmd  = (parsed.get("CMD") or "").strip()
    args = parsed.get("ARGS")
    a2   = parsed.get("ARGS_2") or parsed.get("ARGS-2") or {}
    text = parsed.get("TEXT")
    note = parsed.get("NOTE") or ""

    args_str = "none" if not args else json.dumps(args, ensure_ascii=False)
    a2_str   = json.dumps(a2, ensure_ascii=False)
    text_str = "none" if (text is None or text == "") else str(text)

    lines = []
    lines.append("≪AI RAW OUTPUT≫")
    lines.append(f"[CMD]{cmd}")
    lines.append(f"[ARGS]{args_str}")
    lines.append(f"[ARGS-2]{a2_str}")
    lines.append(f"[TEXT]{text_str}")
    if note:
        lines.append(f"[NOTE]{note}")
    return "\n".join(lines)


def append_log_pretty(
    user_input: Dict[str, Any],
    first_ai_parsed: Dict[str, Any],
    retry_sections: Optional[List[Dict[str, Any]]] = None,
    ts_str: Optional[str] = None
) -> None:
    """
    会話メモリ用の「見やすい整形ブロック」を作る。
    user_input には {"lines": [{"label": str, "text": str}], "timestamp": str, "raw_text": str} を渡す。
    """
    no = _next_display_no()
    resolved_ts = ts_str or user_input.get("timestamp") or datetime.datetime.now().strftime("%Y/%m/%d %H:%M")

    blocks: List[str] = []
    blocks.append(f"#{no} {resolved_ts}")
    blocks.append("")
    blocks.append("≪USER INPUT≫")

    lines = user_input.get("lines") or []
    if lines:
        for line in lines:
            label = (line.get("label") or "").strip()
            text = str(line.get("text") or "")
            blocks.append(f"{label}{text}" if label else text)
    else:
        raw = user_input.get("raw_text") or ""
        blocks.append(str(raw))

    blocks.append("")
    blocks.append(_fmt_ai_raw_block(first_ai_parsed))

    for sec in (retry_sections or []):
        idx = int(sec.get("index", 1))
        total = int(sec.get("total", idx))
        retry_input = sec.get("input") or {}
        retry_lines = retry_input.get("lines") or []
        blocks.append("")
        blocks.append(f"(RETRY発動 #{idx}/{total})")
        blocks.append("")
        blocks.append("≪DKIS RETRY INPUT≫")
        if retry_lines:
            for line in retry_lines:
                label = (line.get("label") or "").strip()
                text = str(line.get("text") or "")
                blocks.append(f"{label}{text}" if label else text)
        else:
            raw_retry = retry_input.get("raw_text")
            if raw_retry:
                blocks.append(str(raw_retry))
        blocks.append("")
        blocks.append(_fmt_ai_raw_block(sec.get("ai_parsed") or {}))

    conversation_log.append("\n".join(blocks))


def append_chat_only_log(user_input: str, ai_reply: str, ai_raw: Optional[str] = None, ts_str: Optional[str] = None) -> None:
    """省エネ会話モード用の簡易ログを保存"""
    no = _next_display_no()
    if not ts_str:
        ts_str = datetime.datetime.now().strftime("%Y/%m/%d %H:%M")

    blocks: List[str] = []
    blocks.append(f"#{no} {ts_str}")
    blocks.append("≪CHAT-ONLY MODE≫")
    blocks.append(f"入力：{(user_input or '').strip()}")
    blocks.append(f"応答：{(ai_reply or '').strip()}")

    if ai_raw and ai_raw.strip() and ai_raw.strip() != (ai_reply or "").strip():
        blocks.append("")
        blocks.append("≪AI RAW OUTPUT≫")
        blocks.append(ai_raw.strip())

    conversation_log.append("\n".join(blocks))


def _transform_block_for_file(block: str) -> str:
    return block


def save_log_to_file() -> None:
    """
    ログをファイルに保存（ファイル名：chatlog_YYYYMMDD_HHMMSS.txt）。
    保存時のみ、保存用フォーマット（和名ラベル＋番号行）へ変換する。
    distフォルダに保存される。
    """
    if not conversation_log:
        return
    
    # 保存先ディレクトリを設定ファイルから取得
    log_dir = get_current_log_save_dir()
    dist_dir = Path(__file__).parent.parent / log_dir
    dist_dir.mkdir(exist_ok=True)  # ディレクトリがなければ作成
    
    # ファイル名フォーマットを設定ファイルから取得
    filename_format = get_current_log_filename_format()
    filename = datetime.datetime.now().strftime(filename_format)
    filepath = dist_dir / filename
    
    with open(filepath, "w", encoding="utf-8") as f:
        for entry in conversation_log:
            f.write(_transform_block_for_file(entry) + "\n\n")
    print_color(f"📝 ログ保存完了：{filepath}", CYAN)
    conversation_log.clear()


def should_save_log() -> bool:
    """ログ保存条件のチェック：件数が閾値を超えたら True"""
    return len(conversation_log) >= log_max_count


def get_recent_conversation_log(n: int = 3) -> str:
    """直近 n ターン分の会話ログ（人間可読の整形テキスト）を返す"""
    if not conversation_log:
        return ""
    recent = conversation_log[-n:]
    return "\n".join(recent)