import spotipy
from spotipy.oauth2 import SpotifyOAuth
from config import (
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI,
    SPOTIFY_SCOPE,               # 認証時のスコープ（config側で管理）
    SPOTIFY_KEEPALIVE_SEC,       # KeepAlive周期（秒）
    SPOTIFY_SEARCH_LIMIT,        # 楽曲検索のlimit（先頭だけ取るなら1）
    SILENT_TRACK_URI,            # 無音トラックURI（接続維持用）
    SILENT_TRACK_ID,             # 無音トラックのID（曲判定用）
)
from core.utils import print_color, CYAN, RED_BRIGHT
from core.voicevox_handler import speak_voicevox  # 現状未使用でも互換維持
import threading

# エラー送信関数（main.pyから注入される）
_send_error_event = None

def set_error_sender(error_sender_func):
    """main.pyからエラー送信関数を注入する"""
    global _send_error_event
    _send_error_event = error_sender_func

def _send_spotify_error(error_type: str, summary: str, details: str):
    """Spotify関連のエラーを送信"""
    if _send_error_event:
        _send_error_event(error_type, summary, details)


# Spotifyクライアント（グローバルに保持）
sp = None

def set_spotify_client(spotify_client):
    """main側で認証したSpotifyクライアントを注入する"""
    global sp
    sp = spotify_client

# 動的設定管理用のグローバル変数
_current_keepalive_sec = SPOTIFY_KEEPALIVE_SEC
_current_search_limit = SPOTIFY_SEARCH_LIMIT

def get_current_spotify_keepalive_sec():
    """現在のKeepAlive周期を取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("spotify.keepalive_sec", _current_keepalive_sec)
    except:
        return _current_keepalive_sec

def get_current_spotify_search_limit():
    """現在のSpotify検索Limitを取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("spotify.search_limit", _current_search_limit)
    except:
        return _current_search_limit

def reload_spotify_settings():
    """Spotify設定をリロード"""
    global _current_keepalive_sec, _current_search_limit
    try:
        from core.settings_manager import get_setting
        _current_keepalive_sec = get_setting("spotify.keepalive_sec", SPOTIFY_KEEPALIVE_SEC)
        _current_search_limit = get_setting("spotify.search_limit", SPOTIFY_SEARCH_LIMIT)
    except Exception as e:
        pass


# 認証（mainでやるなら未使用でもOK）
def init_spotify():
    """Spotify APIとの認証を行う（必要ならmainから呼ぶ）"""
    global sp
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=SPOTIFY_SCOPE,
        ))
        print_color("✅ Spotify認証完了", CYAN)
    except Exception as e:
        print_color(f"❌ Spotify認証エラー: {e}", RED_BRIGHT)
        _send_spotify_error("spotify_auth", "SpotifyのAPIが認証できません", str(e))


# 再生中か確認
def is_spotify_playing() -> bool:
    if sp is None:
        return False
    try:
        playback = sp.current_playback()
        return bool(playback and playback.get("is_playing", False))
    except Exception as e:
        return False


# 現在再生中のトラックIDを取得
def get_current_track_id() -> str:
    """現在再生中のトラックIDを取得。再生していない場合はNoneを返す"""
    if sp is None:
        return None
    try:
        playback = sp.current_playback()
        if playback and playback.get("item"):
            track = playback["item"]
            track_id = track.get("id")
            return track_id
        return None
    except Exception as e:
        _send_spotify_error("spotify_api", "SpotifyのAPI呼び出しに失敗しました", str(e))
        return None


# 現在再生中の曲情報を取得
def get_current_track_info() -> dict:
    """現在再生中の曲の詳細情報を取得"""
    if sp is None:
        return None
    try:
        playback = sp.current_playback()
        if playback and playback.get("item"):
            track = playback["item"]
            
            # タイトル
            title = track.get("name", "不明")
            
            # アーティスト（複数の場合はカンマ区切り）
            artists = track.get("artists", [])
            artist = ", ".join([a.get("name", "") for a in artists]) if artists else "不明"
            
            # アルバム名
            album = track.get("album", {}).get("name", "不明")
            
            # Spotify URL
            url = track.get("external_urls", {}).get("spotify", "")
            
            # アルバムアート
            album_images = track.get("album", {}).get("images", [])
            album_art = album_images[0].get("url") if album_images else None
            
            # 再生状態
            is_playing = playback.get("is_playing", False)
            
            return {
                "title": title,
                "artist": artist,
                "album": album,
                "url": url,
                "album_art": album_art,
                "is_playing": is_playing
            }
        return None
    except Exception as e:
        _send_spotify_error("spotify_api", "Spotifyの曲情報取得に失敗しました", str(e))
        return None


# 無音トラックを再生（KeepAliveで使用）
def play_silent_track() -> bool:
    if sp is None:
        return False
    try:
        sp.start_playback(uris=[SILENT_TRACK_URI])
        return True
    except Exception as e:
        return False


# KeepAliveループと状態管理
_keepalive_timer = None
_keepalive_lock = threading.Lock()
_keepalive_status = {
    "last_check": None,
    "state": "idle",  # idle | playing | restarted | error
    "error_message": None,
    "check_count": 0
}

def _keepalive_tick():
    """2分ごとに再生状態を点検し、4'33"を再生（再生中でなければ4'33"を流す）"""
    global _keepalive_timer, _keepalive_status
    import datetime
    
    try:
        _keepalive_status["last_check"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _keepalive_status["check_count"] += 1
        
        # 現在再生中のトラックIDと再生状態を取得
        current_track_id = get_current_track_id()
        is_playing = is_spotify_playing()
        
        # 何も再生していない場合 → 4'33"を再生
        if current_track_id is None:
            if play_silent_track():
                _keepalive_status["state"] = "restarted"
                _keepalive_status["error_message"] = None
            else:
                _keepalive_status["state"] = "error"
                _keepalive_status["error_message"] = "4'33\"再生失敗"
        # 4'33"を再生中の場合 → 上書きして再生継続
        elif current_track_id == SILENT_TRACK_ID:
            if play_silent_track():
                _keepalive_status["state"] = "playing"
                _keepalive_status["error_message"] = None
            else:
                _keepalive_status["state"] = "error"
                _keepalive_status["error_message"] = "4'33\"継続再生失敗"
        # 他の曲が選ばれているが再生中でない場合 → 4'33"を再生
        elif not is_playing:
            if play_silent_track():
                _keepalive_status["state"] = "restarted"
                _keepalive_status["error_message"] = None
            else:
                _keepalive_status["state"] = "error"
                _keepalive_status["error_message"] = "4'33\"再生失敗"
        # ユーザーが他の曲を再生中 → 何もしない
        else:
            _keepalive_status["state"] = "playing"
            _keepalive_status["error_message"] = None
            
    except Exception as e:
        _keepalive_status["state"] = "error"
        _keepalive_status["error_message"] = str(e)
    finally:
        with _keepalive_lock:
            keepalive_sec = get_current_spotify_keepalive_sec()
            _keepalive_timer = threading.Timer(keepalive_sec, _keepalive_tick)
            _keepalive_timer.daemon = True
            _keepalive_timer.start()

def get_keepalive_status():
    """KeepAliveループの現在の状態を取得"""
    return _keepalive_status.copy()

def update_keepalive_status_now():
    """今すぐSpotifyの状態を確認して更新（手動更新用）"""
    global _keepalive_status
    import datetime
    
    if sp is None:
        return {
            "last_check": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "state": "error",
            "error_message": "Spotifyクライアントが初期化されていません",
            "check_count": _keepalive_status.get("check_count", 0)
        }
    
    try:
        _keepalive_status["last_check"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _keepalive_status["check_count"] += 1
        
        # 現在再生中のトラックIDと再生状態を取得
        current_track_id = get_current_track_id()
        is_playing = is_spotify_playing()
        
        # 何も再生していない場合 → 4'33"を再生
        if current_track_id is None:
            if play_silent_track():
                _keepalive_status["state"] = "restarted"
                _keepalive_status["error_message"] = None
            else:
                _keepalive_status["state"] = "error"
                _keepalive_status["error_message"] = "4'33\"再生失敗"
        # 4'33"を再生中の場合 → 上書きして再生継続
        elif current_track_id == SILENT_TRACK_ID:
            if play_silent_track():
                _keepalive_status["state"] = "playing"
                _keepalive_status["error_message"] = None
            else:
                _keepalive_status["state"] = "error"
                _keepalive_status["error_message"] = "4'33\"継続再生失敗"
        # 他の曲が選ばれているが再生中でない場合 → 4'33"を再生
        elif not is_playing:
            if play_silent_track():
                _keepalive_status["state"] = "restarted"
                _keepalive_status["error_message"] = None
            else:
                _keepalive_status["state"] = "error"
                _keepalive_status["error_message"] = "4'33\"再生失敗"
        # ユーザーが他の曲を再生中 → 何もしない
        else:
            _keepalive_status["state"] = "playing"
            _keepalive_status["error_message"] = None
            
    except Exception as e:
        _keepalive_status["state"] = "error"
        _keepalive_status["error_message"] = str(e)
        _send_spotify_error("spotify_keepalive", "Spotifyの接続維持に失敗しました", str(e))
    
    return _keepalive_status.copy()

def start_spotify_keepalive_loop():
    """初期化時に呼び出してKeepAliveタイマーを起動"""
    global _keepalive_timer
    with _keepalive_lock:
        if _keepalive_timer is None:
            keepalive_sec = get_current_spotify_keepalive_sec()
            _keepalive_timer = threading.Timer(keepalive_sec, _keepalive_tick)
            _keepalive_timer.daemon = True
            _keepalive_timer.start()


# 検索して最初の曲を再生（辞書を返す）
def search_and_play(query: str):
    """指定したキーワードで曲を検索し、先頭結果を再生→{'title','artist'}を返す"""
    if not sp:
        print_color("Spotifyが初期化されていません", RED_BRIGHT)
        return None

    try:
        search_limit = get_current_spotify_search_limit()
        results = sp.search(q=query, type="track", limit=search_limit)
    except Exception as e:
        print_color(f"Spotify検索エラー: {e}", RED_BRIGHT)
        _send_spotify_error("spotify_search", "Spotifyの楽曲検索に失敗しました", str(e))
        return None

    tracks = results.get("tracks", {}).get("items", [])
    if not tracks:
        print_color(f"曲が見つかりませんでした: {query}", RED_BRIGHT)
        return None

    track = tracks[0]
    uri = track["uri"]
    name = track["name"]
    artist = track["artists"][0]["name"]

    try:
        sp.start_playback(uris=[uri])
        return {"title": name, "artist": artist}
    except Exception as e:
        print_color(f"再生に失敗: {e}", RED_BRIGHT)
        _send_spotify_error("spotify_playback", "Spotifyの楽曲再生に失敗しました", str(e))
        return None


# 再生を一時停止
def pause_playback() -> bool:
    """現在再生中の曲を一時停止する"""
    if not sp:
        print_color("Spotifyが初期化されていません", RED_BRIGHT)
        return False
    
    try:
        sp.pause_playback()
        return True
    except Exception as e:
        print_color(f"一時停止に失敗: {e}", RED_BRIGHT)
        _send_spotify_error("spotify_playback", "Spotifyの一時停止に失敗しました", str(e))
        return False


# 再生を再開
def resume_playback() -> bool:
    """一時停止中の曲を再開する"""
    if not sp:
        print_color("Spotifyが初期化されていません", RED_BRIGHT)
        return False
    
    try:
        sp.start_playback()
        return True
    except Exception as e:
        print_color(f"再生再開に失敗: {e}", RED_BRIGHT)
        _send_spotify_error("spotify_playback", "Spotifyの再生再開に失敗しました", str(e))
        return False


# Function Calling 用の薄いラッパ
def play_music_from_spotify(args: dict) -> str:
    """自然言語で指定されたクエリからSpotify曲を再生（ユーザー返答文を返す）"""
    query = args.get("query", "").strip()
    if not sp:
        _send_spotify_error("spotify_auth", "Spotifyが初期化されていません", "Spotify client is not initialized")
        return "すみません、音楽を再生できませんでした。"

    try:
        search_limit = get_current_spotify_search_limit()
        results = sp.search(q=query, type="track", limit=search_limit)
    except Exception as e:
        _send_spotify_error("spotify_search", "Spotifyの楽曲検索に失敗しました", str(e))
        return "すみません、音楽を再生できませんでした。"

    tracks = results.get("tracks", {}).get("items", [])
    if not tracks:
        return f"Spotifyで『{query}』に一致する曲が見つからなかったわ。"

    track = tracks[0]
    uri = track["uri"]
    name = track["name"]
    artist = track["artists"][0]["name"]

    try:
        sp.start_playback(uris=[uri])
        return f"『{name}』（{artist}）を再生するわね。"
    except Exception as e:
        _send_spotify_error("spotify_playback", "Spotifyの楽曲再生に失敗しました", str(e))
        return "すみません、音楽を再生できませんでした。"