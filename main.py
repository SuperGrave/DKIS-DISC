from flask import Flask, request, jsonify, send_file, Response, send_from_directory, session, redirect, url_for
from flask_cors import CORS
from werkzeug.serving import WSGIRequestHandler
from functools import wraps

# ==== 設定は config.py に集約 ====
from config import (
    VOICEVOX_URL, SPEAKER_ID,
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI,
    # ↓ 追加分
    SPOTIFY_SCOPE,
    SSE_RECENT_KEYS_MAXLEN, MAX_RETRIES,
    MUNICD_CSV_PATH, FRONT_INDEX_PATH,
    SERVER_HOST, SERVER_PORT,
    TTS_CACHE_DIR,
)

from core.gpt_handler import (
    handle_user_input_v2,
    set_global_retry_id_getter,
    set_send_event_func as set_gpt_send_event,
    get_next_conversation_retry_id,
)
from core.chat_loop import (
    handle_chat_only_input,
    reload_chat_loop_settings,
    set_increment_stat_func as set_chat_loop_increment_stat,
    set_error_sender as set_chat_loop_error_sender,
)
from core.voicevox_handler import speak_voicevox
from core.voicevox_handler import enqueue_utterance
from core.voicevox_handler import reload_voicevox_settings
from core.utils import CustomRequestHandler, YELLOW_BRIGHT, RESET
from core.spotify_handler import set_spotify_client, start_spotify_keepalive_loop, set_error_sender
from core.voicevox_handler import set_error_sender as set_voicevox_error_sender
from core.gpt_handler import set_error_sender as set_gpt_error_sender
from core.settings_manager import load_settings, get_setting, get_current_settings, update_settings
from spotipy.oauth2 import SpotifyOAuth
from core.webhook_manager import set_webhook_url
from threading import Lock, Thread
from core.context_provider import set_current_location, get_current_location
from core.utils import load_muniCd_dict, latlon_to_address
from core.weather import warm_primary_area, warm_location_table
from core.youtube_state import get_youtube_state, set_status_notifier as set_youtube_status_notifier
from routes.auth import create_auth_blueprint
from routes.settings import create_settings_blueprint
from routes.sse import create_sse_blueprint
from routes.files import create_files_blueprint
from routes.api import create_api_blueprint
import spotipy
import queue
import json
import logging
from collections import deque  # ← 追加

# import time  # 必要なら

# 予報地点テーブルのウォームアップ
count = warm_primary_area()
print(f"[WEATHER] primary_area.xml をロードしました（{count} 件）")

# 緯度経度テーブルのウォームアップ
try:
    location_count = warm_location_table()
    print(f"[WEATHER] locations.csv をロードしました（{location_count} 件）")
except Exception as e:
    print(f"[WEATHER] ⚠️ locations.csv のロードに失敗しました（スキップ）: {e}")

# 設定マネージャーを初期化（settings.jsonを読み込む）
print("[SETTINGS] ⏳ 設定を読み込んでいます...")
try:
    load_settings()
    # 動的設定を各モジュールに反映
    reload_chat_loop_settings()
    reload_voicevox_settings()
    print("[SETTINGS] ✅ 設定ファイルを読み込みました")
except Exception as e:
    print(f"[SETTINGS] ⚠️ 設定マネージャー初期化エラー（デフォルト設定を使用）: {e}")
    import traceback
    traceback.print_exc()

# ===== Spotify 認証（設定でON/OFF） =====
sp = None
spotify_auto_auth_enabled = bool(get_setting("spotify.auto_auth", True))
spotify_keepalive_enabled = bool(get_setting("spotify.keepalive_enabled", True))

if spotify_auto_auth_enabled:
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=SPOTIFY_SCOPE,
        ))
        print("[SPOTIFY] 認証完了")
    except Exception as e:
        print(f"[SPOTIFY] 認証スキップ/失敗: {e}")

# ===== Spotifyクライアントをcoreへ渡す & KeepAlive起動（設定で制御） =====
if sp is not None:
    set_spotify_client(sp)
    if spotify_keepalive_enabled:
        start_spotify_keepalive_loop()

# ===== グローバルRETRY ID取得関数を定義（後でgpt_handlerに注入） =====
_global_retry_counter = 0  # サーバー起動からのRETRY用インプット総数

def get_next_global_retry_id():
    """次のグローバルRETRY IDを取得"""
    global _global_retry_counter
    _global_retry_counter += 1
    return _global_retry_counter

# ===== 統計情報カウンター =====
import datetime
import time

server_start_time = datetime.datetime.now()  # サーバー起動時刻
server_start_timestamp = time.time()  # サーバー起動時のタイムスタンプ

statistics = {
    "user_inputs": 0,          # ユーザー入力回数
    "retry_inputs": 0,         # RETRY処理回数
    "kiritan_replies": 0,      # きりたん返答回数
    "ai_requests": 0,          # AI処理要求回数（GPT、地名解決、天気、検索など）
    "commands": {}             # コマンド実行回数 {コマンド名: 回数}
}
statistics_lock = Lock()

def increment_stat(key, subkey=None):
    """統計情報をインクリメント"""
    with statistics_lock:
        if subkey:
            if key not in statistics:
                statistics[key] = {}
            statistics[key][subkey] = statistics[key].get(subkey, 0) + 1
            print(f"[STAT] {key}.{subkey} = {statistics[key][subkey]}")
        else:
            statistics[key] = statistics.get(key, 0) + 1
            print(f"[STAT] {key} = {statistics[key]}")

# グローバル統計カウンター（他モジュールから使用可能）
_global_increment_stat = None

def set_global_increment_stat(func):
    """統計カウンター関数をグローバルに設定"""
    global _global_increment_stat
    _global_increment_stat = func

def get_global_increment_stat():
    """統計カウンター関数を取得"""
    return _global_increment_stat

# 自分自身の increment_stat を設定
set_global_increment_stat(increment_stat)

# gpt_handlerに関数を注入（循環importを回避）
set_global_retry_id_getter(get_next_global_retry_id)

# 統計カウンター関数を各モジュールに注入
from core.gpt_handler import set_increment_stat_func as set_gpt_increment_stat
from core.voicevox_handler import set_increment_stat_func as set_voicevox_increment_stat
from core.functions import set_increment_stat_func as set_functions_increment_stat
set_gpt_increment_stat(increment_stat)
set_voicevox_increment_stat(increment_stat)
set_functions_increment_stat(increment_stat)
set_chat_loop_increment_stat(increment_stat)
print("[STAT] 統計カウンター関数を注入しました")


def get_statistics():
    """統計情報のコピーを取得"""
    # 経過時間を計算
    elapsed_seconds = int(time.time() - server_start_timestamp)
    
    with statistics_lock:
        return {
            "user_inputs": statistics.get("user_inputs", 0),
            "retry_inputs": statistics.get("retry_inputs", 0),
            "kiritan_replies": statistics.get("kiritan_replies", 0),
            "ai_requests": statistics.get("ai_requests", 0),
            "commands": statistics.get("commands", {}).copy(),
            "server_start_time": server_start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "uptime_seconds": elapsed_seconds
        }

# ===== クライアントリスト・システム状態の定期配信スレッドを起動 =====
def start_clients_broadcast():
    """クライアントリスト・システム状態配信スレッドを起動"""
    from config import SYSTEM_STATUS_BROADCAST_INTERVAL
    broadcast_thread = Thread(target=broadcast_clients_periodically, daemon=True)
    broadcast_thread.start()
    print(f"[SSE] クライアントリスト・システム状態の定期配信を開始（{SYSTEM_STATUS_BROADCAST_INTERVAL}秒間隔）")

# ===== Flask 準備 =====
app = Flask(__name__)

# セッション管理のための秘密鍵を設定
import secrets
app.secret_key = secrets.token_hex(32)  # ランダムな秘密鍵を生成
app.config['SESSION_COOKIE_SECURE'] = False  # 開発環境用（本番環境ではTrueに）
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# 複数クライアント対応：各接続ごとにキューを管理
event_queues = []  # 各クライアントのキューのリスト
client_info_list = []  # 各クライアントの情報（IPアドレス、ブラウザなど）
event_queues_lock = Lock()
app.logger.setLevel(logging.INFO)
logging.getLogger("werkzeug").setLevel(logging.INFO)


class _SuppressHeartbeatAccessLogFilter(logging.Filter):
    """heartbeat系のアクセスログだけを抑制する"""
    def filter(self, record):
        message = record.getMessage()
        return "/notify/client_heartbeat" not in message


_werkzeug_logger = logging.getLogger("werkzeug")
_werkzeug_logger.addFilter(_SuppressHeartbeatAccessLogFilter())

CORS(app)

# 認証システムの初期化
from core import auth_manager

# 住所辞書の読み込み（パスはconfig化）
muniCd_dict = load_muniCd_dict(MUNICD_CSV_PATH)

# =========================
#  統一ログ & SSEユーティリティ
# =========================
_recent_keys = deque(maxlen=SSE_RECENT_KEYS_MAXLEN)  # 同一イベントの氾濫抑止

def _mk_key(ev, uid, reason, data=None):
    # retry_inputイベントの場合はグローバル・会話内IDも含める
    if ev == "retry_input" and data:
        global_retry_id = data.get("global_retry_id", 0)
        conversation_retry_id = data.get("conversation_retry_id", 0)
        retry_index = data.get("retry_index", 0)
        key = f"{ev}:{global_retry_id}:{conversation_retry_id}:{retry_index}"
        return key
    
    # synth_startとsynth_doneイベントの場合はsegment_indexも含める
    if ev in ("synth_start", "synth_done") and data:
        segment_index = data.get("segment_index", 0)
        return f"{ev}:{uid}:{segment_index}:{reason or '-'}"
    
    # tts_enabled_changedイベントの場合はenabled値を含める（重複チェック回避）
    if ev == "tts_enabled_changed" and data:
        enabled = data.get("enabled", False)
        return f"{ev}:{enabled}"
    
    return f"{ev}:{uid}:{reason or '-'}"

def log_event(ev: str, data: dict):
    uid = data.get("utter_id")
    reason = data.get("reason")
    txt = data.get("text")
    if isinstance(txt, str):
        preview = (txt[:40] + "…") if len(txt) > 41 else txt
    else:
        preview = ""
    app.logger.info(f"[SSE] event={ev} uid={uid} reason={reason} text='{preview}'")

def send_event(ev: str, data: dict):
    """event: 名前＆payloadを全クライアントのSSEキューへ。重複は軽く抑止。"""
    # 設定変更系イベントとエラーイベント、およびユーザー入力確定イベントは重複チェックをスキップ
    # （user_input_committed は毎ターン必ず飛ばしたいので、dedup対象外にする）
    if ev not in ("tts_enabled_changed", "settings_updated", "settings_sync", "error_occurred", "user_input_committed"):
        uid = data.get("utter_id")
        reason = data.get("reason")
        key = _mk_key(ev, uid, reason, data)  # dataを渡す
        if key in _recent_keys:
            app.logger.debug(f"[SSE:dedup] {key}")
            return
        _recent_keys.append(key)

    payload = {"type": ev, **data}  # ← payloadにも type を入れておく
    log_event(ev, payload)
    
    # 全クライアントのキューにイベントを送信
    with event_queues_lock:
        event_data = (ev, json.dumps(payload))
        app.logger.info(f"[SSE] 送信準備: {ev}, クライアント数: {len(event_queues)}")
        for i, q in enumerate(event_queues):
            try:
                q.put(event_data, block=False)
                app.logger.info(f"[SSE] クライアント{i+1}に送信成功: {ev}")
            except queue.Full:
                app.logger.warning(f"[SSE] クライアント{i+1}キューが満杯: {ev}")
                pass

def send_error_event(error_type: str, summary: str, details: str):
    """
    エラーイベントを全クライアントに送信
    
    Args:
        error_type: エラーの種類（例: "spotify_auth", "api_key_expired", "parse_error"）
        summary: エラーの要約文（例: "SpotifyのAPIが認証できません"）
        details: エラーの詳細文（例: "Access token has expired"）
    """
    import time
    
    error_data = {
        "error_type": error_type,
        "summary": summary,
        "details": details,
        "timestamp": time.time(),
        "timestamp_str": time.strftime("%H:%M:%S")
    }
    
    app.logger.error(f"[ERROR_EVENT] {error_type}: {summary}")
    app.logger.info(f"[ERROR_EVENT] 送信データ: {error_data}")
    send_event("error_occurred", error_data)

# gpt_handlerにSSE送信関数を注入（webhook未設定時のretry_inputフォールバック用）
set_gpt_send_event(send_event)

# エラー送信関数を各ハンドラーに注入（関数定義後に実行）
if sp is not None:
    set_error_sender(send_error_event)
set_voicevox_error_sender(send_error_event)
set_gpt_error_sender(send_error_event)
set_chat_loop_error_sender(send_error_event)

def prune_stale_clients():
    """heartbeatが途絶えたクライアントを削除する"""
    try:
        from config import SSE_CLIENT_STALE_TIMEOUT
        stale_timeout = max(3, int(SSE_CLIENT_STALE_TIMEOUT))
    except Exception:
        stale_timeout = 12

    now = time.time()
    removed = []
    with event_queues_lock:
        for i in range(len(client_info_list) - 1, -1, -1):
            info = client_info_list[i]
            last_seen_ts = info.get("last_seen_ts", now)
            if now - last_seen_ts <= stale_timeout:
                continue

            removed_info = client_info_list.pop(i)
            if i < len(event_queues):
                event_queues.pop(i)
            removed.append(removed_info)

    for info in removed:
        app.logger.info(
            f"[SSE] ♻️ stale client removed: {info.get('ip')}:{info.get('port')} "
            f"id={info.get('id')} (last_seen={info.get('last_seen_ts')})"
        )
    return len(removed)


_last_clients_signature = None
_last_clients_log_ts = 0.0
_CLIENTS_STABLE_LOG_INTERVAL_SEC = 30


def _log_clients_state(clients):
    """クライアント状態ログを抑制しつつ可読性を保つ"""
    global _last_clients_signature, _last_clients_log_ts

    signature = tuple(client.get("id", "unknown") for client in clients)
    now = time.time()
    is_changed = signature != _last_clients_signature

    if is_changed:
        app.logger.info(f"[SSE] クライアント状態更新: {len(clients)}台")
        for i, client in enumerate(clients):
            app.logger.info(f"[SSE] クライアント{i+1}: {client}")
        _last_clients_signature = signature
        _last_clients_log_ts = now
        return

    if now - _last_clients_log_ts >= _CLIENTS_STABLE_LOG_INTERVAL_SEC:
        app.logger.info(f"[SSE] クライアント監視中: 変化なし（{len(clients)}台接続）")
        _last_clients_log_ts = now

def send_clients_list():
    """接続中のクライアント一覧を全クライアントに送信"""
    prune_stale_clients()
    with event_queues_lock:
        clients = []
        for i, info in enumerate(client_info_list):
            clients.append({
                "index": i + 1,
                "id": info.get("id", "unknown"),
                "ip": info["ip"],
                "port": info.get("port", "unknown"),
                "browser": info["browser"],
                "os": info.get("os", "Unknown"),
                "device_type": info.get("device_type", "Unknown"),
                "connected_at": info["connected_at"],
                "user_id": info.get("user_id", "ゲスト")
            })
        
        # clients_listイベントを送信（重複チェックなし）
        payload = {"type": "clients_list", "clients": clients, "total": len(clients)}

        _log_clients_state(clients)
        
        event_data = ("clients_list", json.dumps(payload))
        
        for q in event_queues:
            try:
                q.put(event_data, block=False)
            except queue.Full:
                pass

def _build_system_status_payload(force_update=False):
    """システム状態のペイロードを構築"""
    from core.spotify_handler import get_keepalive_status, update_keepalive_status_now, get_current_track_info
    from core.voicevox_handler import check_voicevox_status

    prune_stale_clients()
    with event_queues_lock:
        clients = []
        for i, info in enumerate(client_info_list):
            client_data = {
                "index": i + 1,
                "id": info.get("id", "unknown"),
                "ip": info["ip"],
                "port": info.get("port", "unknown"),
                "browser": info["browser"],
                "os": info.get("os", "Unknown"),
                "device_type": info.get("device_type", "Unknown"),
                "connected_at": info["connected_at"],
                "user_id": info.get("user_id", "ゲスト")
            }
            clients.append(client_data)
    
    # Spotify状態を取得
    spotify_keepalive_enabled = bool(get_setting("spotify.keepalive_enabled", True))
    spotify_info = {
        "enabled": spotify_keepalive_enabled,
        "keepalive": (update_keepalive_status_now() if force_update else get_keepalive_status()) if spotify_keepalive_enabled else None,
        "current_track": get_current_track_info() if spotify_keepalive_enabled else None
    }
    
    # VOICEVOX状態を取得
    voicevox_info = check_voicevox_status()
    
    # TTS有効/無効状態を取得（settings.json の tts.enabled を参照）
    from core.settings_manager import get_setting as _get_setting_for_status
    tts_enabled = bool(_get_setting_for_status("tts.enabled", True))
    
    # 統計情報を取得
    stats = get_statistics()

    ai_loop_mode = get_setting("control.ai_loop_mode", "main")
    chat_only_history_limit = get_setting("control.chat_only_max_history", 3)
    youtube_info = get_youtube_state()
    
    return {
        "type": "system_status",
        "clients": {
            "list": clients,
            "total": len(clients)
        },
        "spotify": spotify_info,
        "youtube": youtube_info,
        "voicevox": voicevox_info,
        "tts_enabled": tts_enabled,
        "statistics": stats,
        "ai_loop": {
            "mode": ai_loop_mode,
            "chat_only_history_limit": chat_only_history_limit
        }
    }

def send_system_status():
    """システム状態を全クライアントに送信"""
    payload = _build_system_status_payload(force_update=False)
    event_data = ("system_status", json.dumps(payload))
    
    with event_queues_lock:
        for q in event_queues:
            try:
                q.put(event_data, block=False)
            except queue.Full:
                pass


set_youtube_status_notifier(send_system_status)

def broadcast_clients_periodically():
    """定期的にクライアントリストとシステム状態を配信"""
    import time
    from config import SYSTEM_STATUS_BROADCAST_INTERVAL
    while True:
        time.sleep(SYSTEM_STATUS_BROADCAST_INTERVAL)  # config.pyで設定可能
        send_clients_list()
        send_system_status()

# ===== 認証デコレーター =====
def login_required(f):
    """ログインが必要なページに適用するデコレーター"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth_routes.login_page'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(required_role: str):
    """
    権限チェックデコレーター
    
    Args:
        required_role: 必要な権限（"operator" または "member"）
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # ログインチェック
            if 'user_id' not in session:
                return redirect(url_for('auth_routes.login_page'))
            
            # 権限チェック
            from core.auth_manager import has_permission
            user_id = session.get('user_id')
            
            if not has_permission(user_id, required_role):
                app.logger.warning(f"[Auth] 権限不足: ユーザー {user_id} が {required_role} 権限を要求")
                return jsonify({"ok": False, "error": "権限がありません"}), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ルート本体は routes/*.py へ移行
# ==============
# Blueprint登録
# ==============
app.register_blueprint(create_auth_blueprint({
    "app": app,
    "auth_manager": auth_manager,
    "login_required": login_required,
    "role_required": role_required,
}))

app.register_blueprint(create_settings_blueprint({
    "app": app,
    "role_required": role_required,
    "send_event": send_event,
}))

app.register_blueprint(create_sse_blueprint({
    "app": app,
    "login_required": login_required,
    "send_event": send_event,
    "send_clients_list": send_clients_list,
    "send_system_status": send_system_status,
    "_build_system_status_payload": _build_system_status_payload,
    "increment_stat": increment_stat,
    "event_queues": event_queues,
    "client_info_list": client_info_list,
    "event_queues_lock": event_queues_lock,
}))

app.register_blueprint(create_files_blueprint({
    "app": app,
    "role_required": role_required,
}))

app.register_blueprint(create_api_blueprint({
    "app": app,
    "login_required": login_required,
    "role_required": role_required,
    "FRONT_INDEX_PATH": FRONT_INDEX_PATH,
    "MAX_RETRIES": MAX_RETRIES,
    "TTS_CACHE_DIR": TTS_CACHE_DIR,
    "YELLOW_BRIGHT": YELLOW_BRIGHT,
    "RESET": RESET,
    "get_setting": get_setting,
    "get_current_settings": get_current_settings,
    "update_settings": update_settings,
    "increment_stat": increment_stat,
    "send_event": send_event,
    "handle_chat_only_input": handle_chat_only_input,
    "handle_user_input_v2": handle_user_input_v2,
    "set_webhook_url": set_webhook_url,
    "get_next_global_retry_id": get_next_global_retry_id,
    "get_next_conversation_retry_id": get_next_conversation_retry_id,
    "set_current_location": set_current_location,
    "get_current_location": get_current_location,
    "latlon_to_address": latlon_to_address,
    "muniCd_dict": muniCd_dict,
    "enqueue_utterance": enqueue_utterance,
}))

if __name__ == "__main__":
    # クライアントリスト定期配信を開始
    start_clients_broadcast()
    
    # ポート再利用を有効化（プロセス再起動時の TIME_WAIT 対策）
    import socket
    import werkzeug.serving
    
    # ThreadedWSGIServerをモンキーパッチして SO_REUSEADDR を有効化
    _original_server_bind = werkzeug.serving.ThreadedWSGIServer.server_bind
    
    def patched_server_bind(self):
        """ポートの即座再バインドを可能にするパッチ"""
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _original_server_bind(self)
    
    werkzeug.serving.ThreadedWSGIServer.server_bind = patched_server_bind
    
    # サーバーを起動
    server_host = get_setting("server.host", SERVER_HOST)
    try:
        server_port = int(get_setting("server.port", SERVER_PORT))
    except Exception:
        server_port = SERVER_PORT

    print(f"[DKIS] サーバーを起動します: {server_host}:{server_port}")
    print(f"[DKIS] ポート再利用設定: 有効（再起動時の TIME_WAIT 対策）")
    
    try:
        app.run(host=server_host, port=server_port, threaded=True)
    except KeyboardInterrupt:
        print("\n[DKIS] サーバーを停止します...")