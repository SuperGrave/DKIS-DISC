import os

# config.py
#
# デフォルト設定は dist/settings_default.json で一元管理。
# 本ファイルは API キー・認証情報・固定パス・最終フォールバック定数のみを保持する。
# システムプロンプトと通常のサーバー設定は settings.json / settings_default.json から取得する。

# --- OpenAI API ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# --- AI Model Settings ---
# ※ 実行時の値は settings.json（初期値は settings_default.json）から取得。
# 以下は get_setting のフォールバック用（settings 未読込時のみ）
GPT_MODEL_MAIN = "gpt-4.1-mini"             # メインループ
GPT_MODEL_CHAT_ONLY = "gpt-4.1-mini"        # 省エネ会話モード
GPT_MODEL_SEARCH_SUMMARY = "gpt-5-mini"     # Google検索結果の要約
GPT_MODEL_NEWS_SUMMARY = "gpt-5-mini"       # ニュース検索結果の要約
GPT_MODEL_TEXT_SUMMARY = "gpt-5-mini"      # テキストファイルの要約
GPT_MODEL_WEBPAGE_SUMMARY = "gpt-5-mini"   # Webページ要約
GPT_MODEL_WEATHER_LOCATION = "gpt-4.1-mini" # 天気地名解決
GPT_MODEL_WEATHER_SUMMARY = "gpt-4.1-mini"  # 天気情報の要約（未使用だが拡張用）

# --- Google Custom Search ---
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
GOOGLE_CX = os.environ.get("GOOGLE_CX")
GOOGLE_SEARCH_NUM = 5            # 検索結果の取得数（1-10、AIに入力するサイト数）

# --- VOICEVOX ---
VOICEVOX_URL = "http://127.0.0.1:50021"
SPEAKER_ID = 108  # 四国めたんノーマル2 あまあま0 セクシー4 ささやき 36　東北きりたんノーマル108

# --- VOICEVOX調声設定 ---
VOICEVOX_SPEED = 1.05              # 話速（1.00が標準、大きいほど速く）
VOICEVOX_PITCH = 0.01              # 音高（0.00が標準、正の値で高く、負の値で低く）
VOICEVOX_INTONATION = 1.05         # 抑揚（1.00が標準、大きいほど抑揚が強く）
VOICEVOX_PAUSE_LENGTH = 0.65        # 間の長さ（0.7が標準、大きいほど間が長く）
VOICEVOX_PRE_PHONEME_LENGTH = 0.10 # 開始無音（0.10が標準、音声開始前の無音時間）
VOICEVOX_POST_PHONEME_LENGTH = 0.10 # 終了無音（0.10が標準、音声終了後の無音時間）

# --- システム設定 ---
MAX_LOG_COUNT = 100
MAX_HISTORY = 5
CHAT_ONLY_MAX_HISTORY = 5

# --- 入力付加情報のデフォルト ---
INPUT_FORMAT_DEFAULT = {
    "main": {
        "mode": "all",
        "include": {
            "last_result": True,
            "location": True,
            "time": True
        },
        "labels": {
            "user": "UI：",
            "retry": "RI：",
            "last_result": "LP：",
            "location": "NL：",
            "time": "NT："
        }
    },
    "chat_only": {
        "mode": "all",
        "include": {
            "location": True,
            "time": True
        },
        "labels": {
            "user": "マスター：",
            "location": "現在地：",
            "time": "現在時間："
        }
    }
}

# --- 発話キュー関連 ---
MAX_TTS_CHARS = 200           # 分割上限
CHUNK_GAP_SILENCE_MS = 40     # チャンク間の無音(再生側sleepでOK)
TTS_MAX_RETRIES = 3
TTS_BACKOFF_SEC = [0.3, 0.6, 1.2]
MAX_TTS_PLAY_SEC = 120
TTS_ENABLED_DEFAULT = True    # デフォルトで音声合成を有効にするか（Falseで完全ミュート起動）
ASR_MUTE_DURING_TTS = True    # HTML側でvoice_start/voice_doneに連動

# --- TTS 先読み合成とキャッシュ ---
TTS_SYNTH_WORKERS = 3          # 先読み合成ワーカー数（CPUと相談）
TTS_CACHE_DIR = "tts_cache"      # 一時WAVの保管先
TTS_CACHE_TTL_SEC = 600          # 使い残し掃除のしきい値(秒)
TTS_PRIME_ON_ENQUEUE = True      # キュー投入時に即合成を始める

# --- 色設定（ログ表示用） ---
COLOR_GREEN = "\033[32m"
COLOR_CYAN = "\033[36m"
COLOR_RESET = "\033[0m"

# --- Server / Routing --------------------------------------------------------
SERVER_HOST = "127.0.0.1"       # Flaskのbindアドレス（LAN公開時は "0.0.0.0" など）
SERVER_PORT = 5000              # Flaskのポート番号
FRONT_INDEX_PATH = "templates/index.html" # フロントのトップページの実体パス
MUNICD_CSV_PATH = "core/muniCd.csv"  # 住所辞書CSVの相対/絶対パス

# --- Retry / Control ---------------------------------------------------------
MAX_RETRIES = 5                # 多段処理のリトライ上限（GPT呼び出し等）
SSE_RECENT_KEYS_MAXLEN = 256    # SSE重複抑止用のdeque長（イベントの取りこぼし/重複対策）

# --- Spotify ---------------------------------------------------------------
SPOTIFY_SCOPE = (
    "user-modify-playback-state "
    "user-read-playback-state "
    "user-read-currently-playing "
    "app-remote-control streaming"
)                               # OAuthスコープ（必要権限を空白区切りで列挙）
SPOTIFY_KEEPALIVE_SEC = 120     # KeepAliveの周期（秒）= 2分
SILENT_TRACK_URI = "spotify:track:2bNCdW4rLnCTzgqUXTTDO1"
# ↑ John Cageの「4'33"」（4分33秒の無音曲）
SILENT_TRACK_ID = "2bNCdW4rLnCTzgqUXTTDO1"  # 4'33"のトラックID（曲判定用）
SPOTIFY_SEARCH_LIMIT = 1        # 検索APIのlimit（先頭のみ再生なら1で充分）

# --- 天気API設定 ---
WEATHER_SERVICE = "jma"  # 気象サービス選択: "jma" (気象庁API), "open-meteo" (Open-Meteo API), または "both" (両方)
WEATHER_API_URL = "https://weather.tsukumijima.net/api/forecast/city/"  # 気象庁API URL
WEATHER_API_TIMEOUT = 10  # 天気API タイムアウト（秒）
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"  # Open-Meteo API URL

# --- ログ保存設定 ---
LOG_SAVE_DIR = "dist"  # ログ保存ディレクトリ
LOG_FILENAME_FORMAT = "chatlog_%Y%m%d_%H%M%S.txt"  # ログファイル名フォーマット（strftime形式）

# --- システム状態配信設定 ---
SYSTEM_STATUS_BROADCAST_INTERVAL = 10  # システム状態配信間隔（秒）
SSE_KEEPALIVE_INTERVAL = 3             # SSE keep-alive送信間隔（秒）

# --- タイムアウト設定 ---
VOICEVOX_VERSION_TIMEOUT = 2   # VOICEVOX起動確認タイムアウト（秒）
WEBHOOK_TIMEOUT = 2             # Webhook送信タイムアウト（秒）
TTS_QUERY_TIMEOUT = 10          # TTS音声クエリ生成タイムアウト（秒）
TTS_SYNTHESIS_TIMEOUT = 60      # TTS音声合成タイムアウト（秒）
GPS_TIMEOUT = 5                 # GPS逆ジオコーディングタイムアウト（秒）

# --- Spotify 認証 ---
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback")

