# config.example.py
#
# このファイルを config.py にコピーし、APIキー等を設定してください。
#   cp config.example.py config.py
# config.py は .gitignore に含まれるため、リポジトリには含まれません。
#
# デフォルト設定は dist/settings_default.json で一元管理。
# 本ファイルは API キー・認証情報・固定パス・最終フォールバック定数のみを保持する。

# --- OpenAI API ---
OPENAI_API_KEY = "sk-your-openai-api-key-here"  # ← GPTのAPIキー

# --- AI Model Settings ---
GPT_MODEL_MAIN = "gpt-4.1-mini"
GPT_MODEL_CHAT_ONLY = "gpt-4.1-mini"
GPT_MODEL_SEARCH_SUMMARY = "gpt-5-mini"
GPT_MODEL_NEWS_SUMMARY = "gpt-5-mini"
GPT_MODEL_TEXT_SUMMARY = "gpt-5-mini"
GPT_MODEL_WEBPAGE_SUMMARY = "gpt-5-mini"
GPT_MODEL_WEATHER_LOCATION = "gpt-4.1-mini"
GPT_MODEL_WEATHER_SUMMARY = "gpt-4.1-mini"

# --- Google Custom Search ---
GOOGLE_API_KEY = "your-google-api-key"  # ← Google APIキー
GOOGLE_CX = "your-custom-search-engine-id"  # カスタム検索エンジンID
GOOGLE_SEARCH_NUM = 5

# --- VOICEVOX ---
VOICEVOX_URL = "http://127.0.0.1:50021"
SPEAKER_ID = 108  # 東北きりたんノーマル

# --- VOICEVOX調声設定 ---
VOICEVOX_SPEED = 1.05
VOICEVOX_PITCH = 0.01
VOICEVOX_INTONATION = 1.05
VOICEVOX_PAUSE_LENGTH = 0.65
VOICEVOX_PRE_PHONEME_LENGTH = 0.10
VOICEVOX_POST_PHONEME_LENGTH = 0.10

# --- システム設定 ---
MAX_LOG_COUNT = 100
MAX_HISTORY = 5
CHAT_ONLY_MAX_HISTORY = 5

# --- 入力付加情報のデフォルト ---
INPUT_FORMAT_DEFAULT = {
    "main": {
        "mode": "all",
        "include": {"last_result": True, "location": True, "time": True},
        "labels": {"user": "UI：", "retry": "RI：", "last_result": "LP：", "location": "NL：", "time": "NT："}
    },
    "chat_only": {
        "mode": "all",
        "include": {"location": True, "time": True},
        "labels": {"user": "マスター：", "location": "現在地：", "time": "現在時間："}
    }
}

# --- 発話キュー関連 ---
MAX_TTS_CHARS = 200
CHUNK_GAP_SILENCE_MS = 40
TTS_MAX_RETRIES = 3
TTS_BACKOFF_SEC = [0.3, 0.6, 1.2]
MAX_TTS_PLAY_SEC = 120
TTS_ENABLED_DEFAULT = True
ASR_MUTE_DURING_TTS = True

# --- TTS 先読み合成とキャッシュ ---
TTS_SYNTH_WORKERS = 3
TTS_CACHE_DIR = "tts_cache"
TTS_CACHE_TTL_SEC = 600
TTS_PRIME_ON_ENQUEUE = True

# --- 色設定（ログ表示用） ---
COLOR_GREEN = "\033[32m"
COLOR_CYAN = "\033[36m"
COLOR_RESET = "\033[0m"

# --- Server / Routing ---
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 5000
FRONT_INDEX_PATH = "templates/index.html"
MUNICD_CSV_PATH = "core/muniCd.csv"

# --- Retry / Control ---
MAX_RETRIES = 5
SSE_RECENT_KEYS_MAXLEN = 256

# --- Spotify ---
SPOTIFY_SCOPE = (
    "user-modify-playback-state "
    "user-read-playback-state "
    "user-read-currently-playing "
    "app-remote-control streaming"
)
SPOTIFY_KEEPALIVE_SEC = 120
SILENT_TRACK_URI = "spotify:track:2bNCdW4rLnCTzgqUXTTDO1"
SILENT_TRACK_ID = "2bNCdW4rLnCTzgqUXTTDO1"
SPOTIFY_SEARCH_LIMIT = 1

# --- 天気API設定 ---
WEATHER_SERVICE = "jma"
WEATHER_API_URL = "https://weather.tsukumijima.net/api/forecast/city/"
WEATHER_API_TIMEOUT = 10
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# --- ログ保存設定 ---
LOG_SAVE_DIR = "dist"
LOG_FILENAME_FORMAT = "chatlog_%Y%m%d_%H%M%S.txt"

# --- システム状態配信設定 ---
SYSTEM_STATUS_BROADCAST_INTERVAL = 60

# --- タイムアウト設定 ---
VOICEVOX_VERSION_TIMEOUT = 2
WEBHOOK_TIMEOUT = 2
TTS_QUERY_TIMEOUT = 10
TTS_SYNTHESIS_TIMEOUT = 60
GPS_TIMEOUT = 5

# --- Spotify 認証（Spotify Developer でアプリ登録して取得） ---
SPOTIFY_CLIENT_ID = "your-spotify-client-id"
SPOTIFY_CLIENT_SECRET = "your-spotify-client-secret"
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8080/callback"
