import os, time, threading
from pathlib import Path
from queue import PriorityQueue
from itertools import count
import requests
# import simpleaudio as sa  # もう使わない
import re

from config import (
    VOICEVOX_URL, SPEAKER_ID,
    MAX_TTS_CHARS, CHUNK_GAP_SILENCE_MS,
    TTS_MAX_RETRIES, TTS_BACKOFF_SEC, MAX_TTS_PLAY_SEC,
    TTS_SYNTH_WORKERS, TTS_CACHE_DIR, TTS_CACHE_TTL_SEC, TTS_PRIME_ON_ENQUEUE,
    VOICEVOX_VERSION_TIMEOUT, WEBHOOK_TIMEOUT,  # タイムアウト設定
    TTS_QUERY_TIMEOUT, TTS_SYNTHESIS_TIMEOUT
)
from core.webhook_manager import get_webhook_url

# エラー送信関数（main.pyから注入される）
_send_error_event = None

def set_error_sender(error_sender_func):
    """main.pyからエラー送信関数を注入する"""
    global _send_error_event
    _send_error_event = error_sender_func

def _send_voicevox_error(error_type: str, summary: str, details: str):
    """VoiceVox関連のエラーを送信"""
    if _send_error_event:
        _send_error_event(error_type, summary, details)
    else:
        print(f"[VoiceVox] エラー送信機能が未初期化: {error_type} - {summary}")

# ====== VOICEVOX状態確認 ======
def check_voicevox_status():
    """VOICEVOXエンジンが起動しているか確認"""
    try:
        url = get_current_voicevox_url()
        version_timeout = get_current_voicevox_version_timeout()
        response = requests.get(f"{url}/version", timeout=version_timeout)
        if response.status_code == 200:
            # /versionエンドポイントは文字列または辞書を返す可能性がある
            version_text = response.text.strip().strip('"')  # JSON文字列の場合の引用符を除去
            
            # JSONとしてパースを試みる
            try:
                version_info = response.json()
                # 辞書の場合
                if isinstance(version_info, dict):
                    version_str = version_info.get("version", version_text)
                # 文字列の場合
                else:
                    version_str = str(version_info)
            except:
                # JSONパースに失敗した場合はテキストをそのまま使用
                version_str = version_text
            
            return {
                "is_running": True,
                "version": version_str or "不明",
                "error": None
            }
        else:
            return {
                "is_running": False,
                "version": None,
                "error": f"HTTPステータス: {response.status_code}"
            }
    except requests.exceptions.ConnectionError:
        return {
            "is_running": False,
            "version": None,
            "error": "接続できません（起動していない可能性）"
        }
    except Exception as e:
        _send_voicevox_error("voicevox_connection", "VoiceVoxエンジンに接続できません", str(e))
        return {
            "is_running": False,
            "version": None,
            "error": str(e)
        }

# ====== 感情タグ除去 ======
def remove_emotion_tag(text: str) -> str:
    """
    テキストの先頭にある感情タグを除去する
    例: (笑)こんにちは → こんにちは
    例: (ふふっ)笑ってる → (ふふっ)笑ってる (削除しない)
    """
    if not text:
        return text
    
    # 先頭の感情タグパターンを検出
    # (文字) の形式で、括弧内に1-3文字程度の感情表現
    emotion_pattern = r'^\([^)]{1,4}\)'
    match = re.match(emotion_pattern, text)
    
    if match:
        # 感情タグを除去
        return text[match.end():].strip()
    
    return text

# ====== 四則演算記号をひらがなに変換 ======
_ARITHMETIC_MAP = {
    "+": "たす",
    "-": "ひく",
    "×": "かける",
    "÷": "わる",
    "／": "わる",  # 全角スラッシュ
    "/": "わる",
    "=": "イコール",
    "＝": "イコール",  # 全角イコール
}

def convert_arithmetic_to_hiragana(text: str) -> str:
    """
    四則演算記号をひらがなに変換する
    VOICEVOXが記号を適切に読まないため
    例: "3+5" → "3たす5"、"10÷2" → "10わる2"
    """
    if not text:
        return text
    result = text
    for symbol, hiragana in _ARITHMETIC_MAP.items():
        result = result.replace(symbol, hiragana)
    return result

# ====== 時刻表記（HH:MM）をひらがなに変換 ======
# 時の読み（0-23時用、4時=よじ、7時=しちじ、9時=くじ 等の特殊読みあり）
_JI_READINGS = [
    "れいじ", "いちじ", "にじ", "さんじ", "よじ", "ごじ", "ろくじ", "しちじ", "はちじ", "くじ",
    "じゅうじ", "じゅういちじ", "じゅうにじ", "じゅうさんじ", "じゅうよじ", "じゅうごじ",
    "じゅうろくじ", "じゅうしちじ", "じゅうはちじ", "じゅうくじ",
    "にじゅうじ", "にじゅういちじ", "にじゅうにじ", "にじゅうさんじ",
]
# 分の読み（0-59分用、1分=いっぷん、3分=さんぷん 等の特殊読みあり）
_FUN_READINGS = [
    "れいふん", "いっぷん", "にふん", "さんぷん", "よんぷん", "ごふん", "ろっぷん", "ななふん", "はっぷん", "きゅうふん",
    "じっぷん", "じゅういっぷん", "じゅうにふん", "じゅうさんぷん", "じゅうよんぷん", "じゅうごふん",
    "じゅうろっぷん", "じゅうななふん", "じゅうはっぷん", "じゅうきゅうふん",
    "にじっぷん", "にじゅういっぷん", "にじゅうにふん", "にじゅうさんぷん", "にじゅうよんぷん", "にじゅうごふん",
    "にじゅうろっぷん", "にじゅうななふん", "にじゅうはっぷん", "にじゅうきゅうふん",
    "さんじっぷん", "さんじゅういっぷん", "さんじゅうにふん", "さんじゅうさんぷん", "さんじゅうよんぷん", "さんじゅうごふん",
    "さんじゅうろっぷん", "さんじゅうななふん", "さんじゅうはっぷん", "さんじゅうきゅうふん",
    "よんじっぷん", "よんじゅういっぷん", "よんじゅうにふん", "よんじゅうさんぷん", "よんじゅうよんぷん", "よんじゅうごふん",
    "よんじゅうろっぷん", "よんじゅうななふん", "よんじゅうはっぷん", "よんじゅうきゅうふん",
    "ごじゅっぷん", "ごじゅういっぷん", "ごじゅうにふん", "ごじゅうさんぷん", "ごじゅうよんぷん", "ごじゅうごふん",
    "ごじゅうろっぷん", "ごじゅうななふん", "ごじゅうはっぷん", "ごじゅうきゅうふん",
]

def _minute_to_hiragana(m: int) -> str:
    if 0 <= m <= 59:
        return _FUN_READINGS[m]
    return str(m) + "ふん"

def convert_time_to_hiragana(text: str) -> str:
    """
    時刻表記（HH:MM または H:MM）をひらがなに変換する
    VOICEVOXが「:」や数字を適切に読まないため
    例: "17:50" → "じゅうしちじごじゅっぷん"
    """
    if not text:
        return text
    # 1-2桁:1-2桁 のパターン（例: 9:30, 17:50, 7:05）
    pattern = r'\b(\d{1,2}):(\d{1,2})\b'
    def repl(m):
        h, m_val = int(m.group(1)), int(m.group(2))
        if h < 0 or h > 23 or m_val < 0 or m_val > 59:
            return m.group(0)
        ji = _JI_READINGS[h] if h < len(_JI_READINGS) else str(h) + "じ"
        fun = _minute_to_hiragana(m_val)
        return ji + fun
    return re.sub(pattern, repl, text)

# ====== 読み上げ用：引用符・太字マーク・リテラル\n を除去 ======
def remove_formatting_for_tts(text: str) -> str:
    """
    AI出力の「"」「"」や ** は表示用フォーマットのため、読み上げでは除去する。
    リテラル「\n」は喋らない（実際の改行に変換してポーズ扱いにする）。
    例: ちゃんと "お祭り仕様" です → ちゃんとお祭り仕様です
    例: テーマソングは **「SHIAWASE...」** 。 → テーマソングは「SHIAWASE...」。
    例: 改行\\n\\nテスト → 改行（ポーズ）テスト（\nは読まれない）
    """
    if not text:
        return text
    # リテラル \n を実際の改行に変換（「\n」と喋らないように）
    text = text.replace('\\n', '\n').replace('\\r', '\r')
    # 「"」「"」(U+201C, U+201D) と前後のスペースを除去
    text = re.sub(r'\s*["\u201C\u201D]\s*', '', text)
    # ** と前後のスペースを除去
    text = re.sub(r'\s*\*\*\s*', '', text)
    return text

# ====== 英語表記を読み上げ向けに調整 ======
_ENGLISH_LETTER_READINGS = {
    "A": "えー",
    "B": "びー",
    "C": "しー",
    "D": "でぃー",
    "E": "いー",
    "F": "えふ",
    "G": "じー",
    "H": "えいち",
    "I": "あい",
    "J": "じぇい",
    "K": "けー",
    "L": "える",
    "M": "えむ",
    "N": "えぬ",
    "O": "おー",
    "P": "ぴー",
    "Q": "きゅー",
    "R": "あーる",
    "S": "えす",
    "T": "てぃー",
    "U": "ゆー",
    "V": "ぶい",
    "W": "だぶりゅー",
    "X": "えっくす",
    "Y": "わい",
    "Z": "ぜっと",
}


def convert_english_for_tts(text: str) -> str:
    """
    全大文字の英字列を読み上げ向けに調整する。
    - 1〜4文字: アルファベットを1文字ずつ読む
    - 5文字以上: 小文字化して単語として読ませる

    例:
      ASMR -> えーえすえむあーる
      ASEAN -> asean
    """
    if not text:
        return text

    def replace_english(match):
        word = match.group(1)
        if len(word) <= 4:
            return "".join(_ENGLISH_LETTER_READINGS.get(ch, ch.lower()) for ch in word)
        return word.lower()

    return re.sub(r'(?<![A-Za-z])([A-Z]{1,})(?![A-Za-z])', replace_english, text)

# ====== ミュート ======
# is_muted は「現在のTTSミュート状態」を表す。
# 起動時および設定変更時に settings.json の tts.enabled と同期させる。
is_muted = False

def set_mute():
    """手動ミュート（settings.json も別途更新される想定）"""
    global is_muted
    is_muted = True

def set_unmute():
    """手動アンミュート（settings.json も別途更新される想定）"""
    global is_muted
    is_muted = False

def apply_tts_enabled_from_settings():
    """
    settings.json の tts.enabled から is_muted を反映する。
    True: 合成有効 → is_muted=False
    False: 合成無効 → is_muted=True
    """
    global is_muted
    try:
        from core.settings_manager import get_setting
        enabled = bool(get_setting("tts.enabled", True))
    except Exception:
        enabled = True
    is_muted = not enabled
    print(f"[TTS] 設定から音声合成状態を適用: enabled={enabled}, is_muted={is_muted}")

# ====== Webhookユーティリティ ======
def _notify_webhook(payload, timeout=None):
    """
    Webhook URL（＝Flaskサーバー自身）にPOSTリクエストを送信する。
    payload['event'] の値に応じてエンドポイントを切り替える。
    """
    if timeout is None:
        timeout = get_current_webhook_timeout()
    url = get_webhook_url()
    if not url: return

    # ペイロードからイベント名を取得
    event_name = payload.get("event")
    if not event_name: return

    # イベント名に応じてエンドポイントを決定（webhook URLが既に /notify を含んでいるので / のみ）
    endpoint = f"/{event_name}"
    
    try:
        requests.post(url.rstrip('/') + endpoint, json=payload, timeout=timeout)
    except Exception as e:
        # 失敗しても全体は止めない
        pass

def notify_reply(utter_id: int, text: str, ai_raw: str = None, processing_time: float = 0.0, token_usage: dict = None):
    # reply は必ず utter_id 付きで一度だけ
    payload = {"event": "reply", "utter_id": utter_id, "text": text}
    if ai_raw:
        payload["ai_raw"] = ai_raw
    if processing_time > 0:
        payload["processing_time"] = processing_time
    if token_usage and isinstance(token_usage, dict):
        payload["token_usage"] = token_usage
    _notify_webhook(payload)

def notify_synth_start(meta=None):
    payload = {"event": "synth_start"}
    if meta:
        payload.update(meta)
    _notify_webhook(payload)

def notify_synth_done(meta=None):
    payload = {"event": "synth_done"}
    if meta:
        payload.update(meta)
    
    # 音声合成時間を計算（このセグメントが計測対象のutteranceに属する場合のみ）
    global _synthesis_start_time, _synthesis_utter_id
    current_utter_id = meta.get("utter_id") if meta else None
    if (_synthesis_start_time is not None and current_utter_id is not None
            and current_utter_id == _synthesis_utter_id):
        synthesis_time = time.time() - _synthesis_start_time
        payload["synthesis_time"] = synthesis_time
        payload["synthesis_utter_id"] = _synthesis_utter_id
        
        # 合成完了後はフラグをリセット
        _synthesis_start_time = None
        _synthesis_utter_id = None
    
    _notify_webhook(payload)

# ====== 分割 ======
PRIMARY = "。！？\n"; SECOND = "、・，"
def chunk_text_for_tts(text: str, limit: int = None):
    if limit is None:
        limit = get_current_max_tts_chars()
    parts, buf = [], ""
    for ch in text:
        buf += ch
        if len(buf) >= limit:
            cut = -1
            for i in range(len(buf)-1, -1, -1):
                if buf[i] in PRIMARY: cut = i+1; break
            if cut == -1:
                for i in range(len(buf)-1, -1, -1):
                    if buf[i] in SECOND: cut = i+1; break
            if cut == -1: cut = len(buf)
            parts.append(buf[:cut].strip()); buf = buf[cut:]
    if buf.strip(): parts.append(buf.strip())
    return parts

# ====== TTS 合成（リトライ付き） ======
def _synth_wav_bytes(text: str, speaker: int, volume: float):
    # VoiceVox用のテキスト前処理
    text = remove_formatting_for_tts(text)        # 「"」「**」と前後のスペースを除去
    text = convert_english_for_tts(text)          # 英語: 4文字以下はスペル読み、5文字以上は小文字化
    text = convert_arithmetic_to_hiragana(text)   # 四則演算記号 → ひらがな
    text = convert_time_to_hiragana(text)         # 時刻表記（17:50等） → ひらがな
    
    url = get_current_voicevox_url()
    query_timeout = get_current_tts_query_timeout()
    q = requests.post(f"{url}/audio_query",
                      params={"speaker": speaker, "text": text}, timeout=query_timeout)
    q.raise_for_status()
    query = q.json()
    
    # 調声設定を適用
    query["speedScale"] = get_current_voicevox_speed()
    query["pitchScale"] = get_current_voicevox_pitch()
    query["intonationScale"] = get_current_voicevox_intonation()
    query["pauseLength"] = get_current_voicevox_pause_length()
    query["prePhonemeLength"] = get_current_voicevox_pre_phoneme_length()
    query["postPhonemeLength"] = get_current_voicevox_post_phoneme_length()
    query["volumeScale"] = float(volume)
    
    url = get_current_voicevox_url()
    synthesis_timeout = get_current_tts_synthesis_timeout()
    s = requests.post(f"{url}/synthesis",
                      params={"speaker": speaker, "enable_interrogative_upspeak": True},
                      json=query, timeout=synthesis_timeout)
    s.raise_for_status()
    return s.content

def synth_with_retry(text: str, volume: float):
    last = None
    speaker_id = get_current_speaker_id()
    max_retries = get_current_tts_max_retries()
    backoff_sec = get_current_tts_backoff_sec()
    for i in range(max_retries):
        try:
            return _synth_wav_bytes(text, speaker_id, volume)
        except Exception as e:
            last = e
            if i == max_retries - 1:  # 最後のリトライでエラー送信
                _send_voicevox_error("voicevox_synthesis", "VoiceVox音声合成に失敗しました", str(e))
            time.sleep(backoff_sec[min(i, len(backoff_sec)-1)])
    raise last if last else RuntimeError("TTS unknown error")

# ====== キャッシュ管理 ======
CACHE_DIR = Path(TTS_CACHE_DIR)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _cache_path(utter_id: int, seg_no: int) -> Path:
    return CACHE_DIR / f"utt{utter_id:06d}_seg{seg_no:03d}.wav"

def _write_wav(path: Path, data: bytes):
    path.write_bytes(data)

# _play_wav は使わなくなったのでコメントアウト
# def _play_wav(path: Path, timeout: float = MAX_TTS_PLAY_SEC):
#     obj = sa.WaveObject.from_wave_file(str(path))
#     play = obj.play()
#     start = time.time()
#     while play.is_playing():
#         if time.time() - start > timeout:
#             play.stop(); break
#         time.sleep(0.02)

# ====== 先読み合成用の状態 ======
#   segment_status[(utter_id, seg_no)] = {"state":"pending|working|ready|error", "path":Path|None, "err":str|None}
segment_status = {}
segment_cv = threading.Condition()
_utter_seq = count(1)
_utter_synth_start_fired = set()   # そのUtteranceで「最初の合成開始」を送ったか

# 統計カウンター関数（mainから注入される）
_increment_stat_func = None

def set_increment_stat_func(func):
    """統計カウンター関数を設定"""
    global _increment_stat_func
    _increment_stat_func = func

# 動的設定管理用のグローバル変数
_current_voicevox_url = VOICEVOX_URL
_current_speaker_id = SPEAKER_ID
_current_max_tts_chars = MAX_TTS_CHARS
_current_chunk_gap_silence_ms = CHUNK_GAP_SILENCE_MS
_current_tts_max_retries = TTS_MAX_RETRIES
_current_tts_backoff_sec = TTS_BACKOFF_SEC
_current_voicevox_version_timeout = VOICEVOX_VERSION_TIMEOUT
_current_webhook_timeout = WEBHOOK_TIMEOUT
_current_tts_query_timeout = TTS_QUERY_TIMEOUT
_current_tts_synthesis_timeout = TTS_SYNTHESIS_TIMEOUT

def reload_voicevox_settings():
    """設定ファイルから最新の設定を読み込む"""
    global _current_voicevox_url, _current_speaker_id
    global _current_max_tts_chars, _current_chunk_gap_silence_ms
    global _current_tts_max_retries, _current_tts_backoff_sec
    global _current_voicevox_version_timeout, _current_webhook_timeout
    global _current_tts_query_timeout, _current_tts_synthesis_timeout
    try:
        from core.settings_manager import get_setting
        _current_voicevox_url = get_setting("voicevox.url", VOICEVOX_URL)
        _current_speaker_id = get_setting("voicevox.speaker_id", SPEAKER_ID)
        _current_max_tts_chars = get_setting("tts.max_chars", MAX_TTS_CHARS)
        _current_chunk_gap_silence_ms = get_setting("tts.chunk_gap_silence_ms", CHUNK_GAP_SILENCE_MS)
        _current_tts_max_retries = get_setting("tts.max_retries", TTS_MAX_RETRIES)
        _current_tts_backoff_sec = get_setting("tts.backoff_sec", TTS_BACKOFF_SEC)
        _current_voicevox_version_timeout = get_setting("timeouts.voicevox_version", VOICEVOX_VERSION_TIMEOUT)
        _current_webhook_timeout = get_setting("timeouts.webhook", WEBHOOK_TIMEOUT)
        _current_tts_query_timeout = get_setting("timeouts.tts_query", TTS_QUERY_TIMEOUT)
        _current_tts_synthesis_timeout = get_setting("timeouts.tts_synthesis", TTS_SYNTHESIS_TIMEOUT)
        # TTS有効状態をsettings.jsonから反映
        apply_tts_enabled_from_settings()
        print(f"[VOICEVOX] 設定をリロードしました: URL={_current_voicevox_url}, Speaker={_current_speaker_id}, MaxChars={_current_max_tts_chars}")
    except Exception as e:
        print(f"[VOICEVOX] 設定リロードエラー: {e}")

def get_current_voicevox_url():
    """現在のVOICEVOX URLを取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("voicevox.url", _current_voicevox_url)
    except:
        return _current_voicevox_url

def get_current_speaker_id():
    """現在の話者IDを取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("voicevox.speaker_id", _current_speaker_id)
    except:
        return _current_speaker_id

def get_current_max_tts_chars():
    """現在の最大TTS文字数を取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("tts.max_chars", _current_max_tts_chars)
    except:
        return _current_max_tts_chars

def get_current_chunk_gap_silence_ms():
    """現在のチャンク間無音時間を取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("tts.chunk_gap_silence_ms", _current_chunk_gap_silence_ms)
    except:
        return _current_chunk_gap_silence_ms

def get_current_tts_max_retries():
    """現在のTTS最大リトライ回数を取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("tts.max_retries", _current_tts_max_retries)
    except:
        return _current_tts_max_retries

def get_current_tts_backoff_sec():
    """現在のTTSバックオフ秒数を取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("tts.backoff_sec", _current_tts_backoff_sec)
    except:
        return _current_tts_backoff_sec

def get_current_voicevox_version_timeout():
    """現在のVOICEVOXバージョン確認タイムアウトを取得"""
    try:
        from core.settings_manager import get_setting
        return get_setting("timeouts.voicevox_version", _current_voicevox_version_timeout)
    except:
        return _current_voicevox_version_timeout

def get_current_webhook_timeout():
    """現在のWebhookタイムアウトを取得"""
    try:
        from core.settings_manager import get_setting
        return get_setting("timeouts.webhook", _current_webhook_timeout)
    except:
        return _current_webhook_timeout

def get_current_tts_query_timeout():
    """現在のTTSクエリタイムアウトを取得"""
    try:
        from core.settings_manager import get_setting
        return get_setting("timeouts.tts_query", _current_tts_query_timeout)
    except:
        return _current_tts_query_timeout

def get_current_tts_synthesis_timeout():
    """現在のTTS合成タイムアウトを取得"""
    try:
        from core.settings_manager import get_setting
        return get_setting("timeouts.tts_synthesis", _current_tts_synthesis_timeout)
    except:
        return _current_tts_synthesis_timeout

def get_current_voicevox_speed():
    """現在のVoiceVox話速を取得"""
    try:
        from core.settings_manager import get_setting
        return get_setting("voicevox.speed", 1.00)
    except:
        return 1.00

def get_current_voicevox_pitch():
    """現在のVoiceVox音高を取得"""
    try:
        from core.settings_manager import get_setting
        return get_setting("voicevox.pitch", 0.00)
    except:
        return 0.00

def get_current_voicevox_intonation():
    """現在のVoiceVox抑揚を取得"""
    try:
        from core.settings_manager import get_setting
        return get_setting("voicevox.intonation", 1.00)
    except:
        return 1.00

def get_current_voicevox_pause_length():
    """現在のVoiceVox間の長さを取得"""
    try:
        from core.settings_manager import get_setting
        return get_setting("voicevox.pause_length", 0.7)
    except:
        return 0.7

def get_current_voicevox_pre_phoneme_length():
    """現在のVoiceVox開始無音長を取得"""
    try:
        from core.settings_manager import get_setting
        return get_setting("voicevox.pre_phoneme_length", 0.10)
    except:
        return 0.10

def get_current_voicevox_post_phoneme_length():
    """現在のVoiceVox終了無音長を取得"""
    try:
        from core.settings_manager import get_setting
        return get_setting("voicevox.post_phoneme_length", 0.10)
    except:
        return 0.10

# 再生順（Utterance単位）と合成順（Segment単位）の別キュー
play_q  = PriorityQueue()   # (priority, enqueue_time, job_dict)
synth_q = PriorityQueue()   # (priority, enqueue_time, (utter_id, seg_no, text, volume))

# 合成時間管理用のグローバル変数
_synthesis_start_time = None
_synthesis_utter_id = None

def enqueue_utterance(text: str, turn_id: str = "0", emotion="(無)", priority=0, volume=1.0, ai_raw: str = None, processing_time: float = 0.0, token_usage: dict = None, silent: bool = False):
    """発話をキュー投入し、即座に reply(utter_id付) を送る。silent=Trueなら音声合成はスキップ。"""
    utter_id = next(_utter_seq)
    
    # 音声合成開始時刻を記録（最初のutteranceのみ）
    global _synthesis_start_time, _synthesis_utter_id
    if _synthesis_start_time is None and not silent:
        _synthesis_start_time = time.time()
        _synthesis_utter_id = utter_id

    # まず reply を utter_id 付きで通知（ミュート時も送信）
    notify_reply(utter_id, text, ai_raw, processing_time, token_usage)
    
    # 統計：きりたん返答回数をカウント
    if _increment_stat_func:
        _increment_stat_func("kiritan_replies")
    
    # silent=True または ミュート時はreplyのみ送信して音声合成はスキップ
    if silent or is_muted: 
        return
    
    segs = chunk_text_for_tts(text)  # 動的に設定から取得

    # Segment状態を初期化し、必要なら先読み合成へ
    with segment_cv:
        for idx, seg in enumerate(segs, start=1):
            segment_status[(utter_id, idx)] = {"state":"pending", "path":None, "err":None, "text":seg}
            if TTS_PRIME_ON_ENQUEUE:
                synth_q.put((priority, time.time(), (utter_id, idx, seg, float(volume))))
    job = {
        "turn_id": str(turn_id), "utter_id": utter_id,
        "segments": list(range(1, len(segs)+1)),
        "meta": {"emotion": emotion, "volume": float(volume)}
    }
    play_q.put((priority, time.time(), job))

# ====== 合成ワーカー（先読み） ======
class SynthesisWorker(threading.Thread):
    daemon = True
    def run(self):
        while True:
            _, _, task = synth_q.get()
            utter_id, seg_no, text, volume = task
            key = (utter_id, seg_no)

            # 合成開始通知
            notify_synth_start({
                "utter_id": utter_id,
                "segment_index": seg_no,
                "text": text
            })

            # 合成
            with segment_cv:
                st = segment_status.get(key)
                if not st or st["state"] == "ready":
                    continue
                st["state"] = "working"
            try:
                wav = synth_with_retry(text, volume)
                path = _cache_path(utter_id, seg_no)
                _write_wav(path, wav)
                with segment_cv:
                    segment_status[key]["state"] = "ready"
                    segment_status[key]["path"] = path
                    segment_cv.notify_all()
                
                # 合成完了通知
                notify_synth_done({
                    "utter_id": utter_id,
                    "segment_index": seg_no,
                    "url": f"/audio/{path.name}",
                    "text": text
                })
            except Exception as e:
                _send_voicevox_error("voicevox_segment", "VoiceVoxセグメント合成に失敗しました", str(e))
                # 合成失敗時は合成時間管理をリセット（次発話の合成時間が旧発話に紐づくのを防ぐ）
                global _synthesis_start_time, _synthesis_utter_id
                _synthesis_start_time = None
                _synthesis_utter_id = None
                with segment_cv:
                    segment_status[key]["state"] = "error"
                    segment_status[key]["err"] = str(e)
                    segment_cv.notify_all()

# ====== 再生ワーカー（順次再生） ======
class SpeechWorker(threading.Thread):
    daemon = True
    def run(self):
        while True:
            _, _, job = play_q.get()
            utter_id = job["utter_id"]
            seg_ids = job["segments"]
            volume = job["meta"]["volume"]

            ok, err = True, None
            has_play_started = False
            last_err_kind = None  # "synth_error" / "play_error"

            try:
                has_play_started = True
                
                # 各セグメントが合成完了するまで待機（synth_doneで通知済み）
                for seg_no in seg_ids:
                    key = (utter_id, seg_no)
                    with segment_cv:
                        while True:
                            st = segment_status.get(key)
                            if st and st["state"] == "ready":
                                break
                            if st and st["state"] == "error":
                                last_err_kind = "synth_error"
                                raise RuntimeError(st["err"] or "synth error")
                            segment_cv.wait(timeout=0.2)

            except Exception as e:
                ok, err = False, str(e)
            finally:
                # キャッシュ掃除（クライアント側で再生が終わるまで削除しない方が良いが、
                # 今回は簡単のためにすぐ削除はせず、Janitorに任せる）
                for seg_no in seg_ids:
                    key = (utter_id, seg_no)
                    st = segment_status.pop(key, None)
                    # すぐには削除しない - クライアント側でまだ再生中の可能性がある
                    # try:
                    #     if st and st.get("path"):
                    #         Path(st["path"]).unlink(missing_ok=True)
                    # except Exception:
                    #     pass

                # 終了通知は削除（クライアント側で管理）
                pass

# ====== 掃除スレッド（TTLで孤児WAVを消す） ======
class Janitor(threading.Thread):
    daemon = True
    def run(self):
        while True:
            try:
                now = time.time()
                for p in CACHE_DIR.glob("utt*_seg*.wav"):
                    try:
                        if now - p.stat().st_mtime > TTS_CACHE_TTL_SEC:
                            p.unlink()
                    except Exception:
                        pass
            finally:
                time.sleep(60)

# ====== 起動と公開API ======
_workers_started = False
def ensure_workers():
    global _workers_started
    if _workers_started: return
    # 先読み合成ワーカー
    for _ in range(max(1, int(TTS_SYNTH_WORKERS))):
        SynthesisWorker().start()
    # 再生ワーカー（単一）
    SpeechWorker().start()
    # 掃除
    Janitor().start()
    _workers_started = True

def speak_voicevox(text, silent=False, ai_raw=None, processing_time=0.0, token_usage=None):
    if not text: return
    
    # 感情タグを除去
    clean_text = remove_emotion_tag(text)
    
    ensure_workers()
    # ミュート時もenqueue_utteranceは呼ぶ（内部でreplyを送信してから合成をスキップする）
    enqueue_utterance(clean_text, turn_id="0", emotion="(無)", priority=0, volume=1.0, ai_raw=ai_raw, processing_time=processing_time, token_usage=token_usage, silent=silent)