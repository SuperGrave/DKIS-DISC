from datetime import datetime, timezone, timedelta
import re
import threading
from typing import Optional, Tuple

_loc_lock = threading.Lock()
_current_location = "（現在地未設定）"
_current_latitude: Optional[float] = None
_current_longitude: Optional[float] = None

def get_current_time() -> str:
    """
    現在時刻をJST(UTC+9)で 'YYYY/MM/DD HH:MM' 形式にして返す。
    time_test.get_jst_time() があれば優先して使う。
    """
    # 既存の time-test を優先
    try:
        import time_test  # マスターの既存モジュール名に合わせて
        ts = time_test.get_jst_time()  # 'YYYY/MM/DD HH:MM' を想定
        if isinstance(ts, str) and ts.strip():
            return ts.strip()
    except Exception:
        pass

    # フォールバック：PythonのJST生成
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).strftime("%Y/%m/%d %H:%M")

# ★ 追加：前回の処理内容（次ターン入力に混ぜる用）
_last_proc_result = "（前回処理なし）"

def set_last_proc_result(text: str):
    """DMIS-LOGなどの処理説明を"1行に正規化"して保存"""
    global _last_proc_result
    if not text:
        _last_proc_result = "（このターンで実行した処理はありません）"
        return
    s = re.sub(r"\s+", " ", str(text)).strip()          # 改行や連続空白を1スペースへ
    _last_proc_result = (s[:180] + "…") if len(s) > 180 else s  # 長すぎる時は省略

def get_last_proc_result() -> str:
    return _last_proc_result

# ★ 追加：直近のユーザー入力（テキストファイル要約用）
_last_user_input = ""

def set_last_user_input(text: str):
    """直近のユーザー入力を保存"""
    global _last_user_input
    _last_user_input = text or ""

def get_last_user_input() -> str:
    """直近のユーザー入力を取得"""
    return _last_user_input


def set_current_location(text: str, lat: Optional[float] = None, lon: Optional[float] = None) -> None:
    """現在地（市区町村などの短い文字列）と緯度経度を更新。"""
    global _current_location, _current_latitude, _current_longitude
    s = "（現在地未設定）" if text is None else re.sub(r"\s+", " ", str(text)).strip()
    if not s:
        s = "（現在地未設定）"
    if len(s) > 100:
        s = s[:100]  # 過度に長いのは切る
    with _loc_lock:
        _current_location = s
        if lat is not None:
            _current_latitude = float(lat)
        if lon is not None:
            _current_longitude = float(lon)

def get_current_location() -> str:
    """現在地の文字列を取得。"""
    with _loc_lock:
        return _current_location

def get_current_coordinates() -> Tuple[Optional[float], Optional[float]]:
    """現在地の緯度経度を取得。戻り値: (latitude, longitude)"""
    with _loc_lock:
        return (_current_latitude, _current_longitude)

def has_coordinates() -> bool:
    """緯度経度が設定されているかどうかを返す。"""
    with _loc_lock:
        return _current_latitude is not None and _current_longitude is not None
