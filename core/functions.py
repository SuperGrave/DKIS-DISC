from core.logger import save_log_to_file
from core.response_handler import speak_response
from core.utils import (
    google_search,
    google_news_search,
    resolve_google_news_url,
    normalize_location_text,
)
from core.scraper import scrape_webpage
from core.logger import get_recent_conversation_log  # 履歴取得用
from core.context_provider import get_last_user_input  # ユーザー入力取得用
from openai import OpenAI
from config import (
    OPENAI_API_KEY,
    GPT_MODEL_SEARCH_SUMMARY,  # 検索要約用モデル
    GPT_MODEL_NEWS_SUMMARY,    # ニュース要約用モデル
    GPT_MODEL_WEBPAGE_SUMMARY, # Webページ要約用モデル
    WEATHER_API_URL,           # 天気API URL
    WEATHER_API_TIMEOUT        # 天気API タイムアウト
)
from core.settings_manager import get_prompt_setting
from core.weather import (
    ai_resolve_city_id_simple, load_rows, detect_pref_from_text, best_city_in_pref, get_weather_open_meteo,
    ai_resolve_location_coordinates, load_location_rows, detect_pref_from_text_for_location, best_location_in_pref
)
from core.context_provider import get_current_location, get_current_coordinates, has_coordinates, get_current_time
import requests
import os
import json
from datetime import datetime

# 統計カウンター関数（main.pyから注入される）
_increment_stat_func = None

def set_increment_stat_func(func):
    """統計カウンター関数を設定"""
    global _increment_stat_func
    _increment_stat_func = func

# 動的設定管理用のグローバル変数
_current_weather_api_url = WEATHER_API_URL
_current_weather_api_timeout = WEATHER_API_TIMEOUT

def get_current_weather_api_url():
    """現在の天気API URLを取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("weather.api_url", _current_weather_api_url)
    except:
        return _current_weather_api_url

def get_current_weather_api_timeout():
    """現在の天気API タイムアウトを取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("weather.api_timeout", _current_weather_api_timeout)
    except:
        return _current_weather_api_timeout

def reload_functions_settings():
    """Functions設定をリロード"""
    global _current_weather_api_url, _current_weather_api_timeout
    try:
        from core.settings_manager import get_setting
        _current_weather_api_url = get_setting("weather.api_url", WEATHER_API_URL)
        _current_weather_api_timeout = get_setting("weather.api_timeout", WEATHER_API_TIMEOUT)
        print(f"[Functions] 設定をリロードしました: WeatherAPI={_current_weather_api_url}, Timeout={_current_weather_api_timeout}")
    except Exception as e:
        print(f"[Functions] 設定リロードエラー: {e}")


def _wdebug(message: str):
    """天気コマンド用の簡易デバッグ出力"""
    print(f"[WEATHER] {message}")

_openai_client = None
def _get_openai():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client

# 通常会話
def chat_response(args, TEXT, NOTE=None, ai_raw=None, processing_time=0.0, token_usage=None):
    user_text = args.get("text", "") if isinstance(args, dict) else ""
    # ここでask_gptして多重出力エラーになったこと、ありまーす（なので喋るだけ）
    if TEXT and TEXT != "none":
        speak_response(TEXT, ai_raw=ai_raw, processing_time=processing_time, token_usage=token_usage)
    return TEXT, "通常の会話応答を実行。"

# ログ保存
def save_log(args, TEXT, NOTE=None, ai_raw=None, processing_time=0.0, token_usage=None):
    save_log_to_file()
    # AIが生成したTEXTをそのまま使用
    if TEXT and TEXT != "none":
        speak_response(TEXT, ai_raw=ai_raw, processing_time=processing_time, token_usage=token_usage)
    return TEXT, "テキストファイルとして会話ログを保存。"



def google_search_summary(query, note, history_text, search_result):
    base_prompt = get_prompt_setting("search_summary")
    
    # 検索固有の情報を追加
    system_prompt = (
        f"{base_prompt}\n\n"
        f"この後にAIはあなたの出力を受けて、情報を再度まとめてユーザーへ送ります。あなたは、まず情報をAIに伝える体で出力してください。キャラクター性は必要ありません。\n"
        f"この検索は「{note}」という目的で行われます。\n"
        f"【検索ワード】\n{query}\n"
        f"【検索結果全文】\n{search_result}\n"
        f"---\n"
        f"【重要】\n"
        f"・あなたは検索結果を要約するAIです。挨拶やきりたんへの問いかけは必要ありませんので、情報だけを出力するようにしてください。\n"
        f"・小説作りや詳細な解説など、長い文章が要求される場合にはその通りにできるだけ長い文章を生成してください。\n"
        f"・曲名や簡単な答えが求められる時は端的に簡潔に要約してください。\n"
    )
    
    # 統計：GPT呼び出し回数をカウント
    if _increment_stat_func:
        _increment_stat_func("ai_requests")
    
    # モデル名を設定ファイルから取得（なければconfig.pyのデフォルト）
    try:
        from core.settings_manager import get_setting
        model = get_setting("ai_models.search_summary", GPT_MODEL_SEARCH_SUMMARY)
    except:
        model = GPT_MODEL_SEARCH_SUMMARY
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=model,  # 設定ファイルまたはconfig.pyから
        messages=[{"role": "system", "content": system_prompt}]
    )
    content = response.choices[0].message.content
    usage = None
    if hasattr(response, "usage") and response.usage:
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
        }
    return content, usage

def google_news_summary(query, note, history_text, news_result):
    """ニュース検索結果を要約する（news_summary専用プロンプト・モデル）"""
    base_prompt = get_prompt_setting("news_summary")

    system_prompt = (
        f"{base_prompt}\n\n"
        f"この後にAIはあなたの出力を受けて、情報を再度まとめてユーザーへ送ります。あなたは、まず情報をAIに伝える体で出力してください。キャラクター性は必要ありません。\n"
        f"このニュース検索は「{note}」という目的で行われます。\n"
        f"【検索ワード】\n{query}\n"
        f"【ニュース結果全文】\n{news_result}\n"
        f"---\n"
        f"【重要】\n"
        f"・あなたはニュース結果を要約するAIです。挨拶やきりたんへの問いかけは必要ありませんので、情報だけを出力するようにしてください。\n"
        f"・複数のニュースがある場合は、重要なニュースを優先し、時系列やトピックごとに整理してください。\n"
        f"・小説作りや詳細な解説など、長い文章が要求される場合にはその通りにできるだけ長い文章を生成してください。\n"
        f"・簡単な答えが求められる時は端的に簡潔に要約してください。\n"
    )

    if _increment_stat_func:
        _increment_stat_func("ai_requests")

    try:
        from core.settings_manager import get_setting
        model = get_setting("ai_models.news_summary", GPT_MODEL_NEWS_SUMMARY)
    except Exception:
        model = GPT_MODEL_NEWS_SUMMARY

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system_prompt}]
    )
    content = response.choices[0].message.content
    usage = None
    if hasattr(response, "usage") and response.usage:
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
        }
    return content, usage

# --- ここが本体 ---
def search_google(args, TEXT, NOTE=None, ai_raw=None, processing_time=0.0, token_usage=None):
    query = args.get("query", "").strip() if isinstance(args, dict) else ""
    if not query:
        dmis_log = "google検索コマンドが実行されたが、検索クエリが指定されず。"
        return TEXT, dmis_log, None, None, None

    # 進捗は"発話だけ"で先行。notify_reply は使わない（utter_id整合のため）
    if TEXT and TEXT != "none":
        speak_response(TEXT, ai_raw=ai_raw, processing_time=processing_time, token_usage=token_usage)

    # 参照数: サーバー優先時は設定値、そうでなければARGSのresult_countを優先
    num = None
    try:
        from core.settings_manager import get_setting
        server_priority = get_setting("search.server_priority", False)
        if server_priority:
            num = get_setting("search.result_count", 5)
            num = max(1, min(10, int(num)))
        elif isinstance(args, dict) and "result_count" in args:
            try:
                arg_num = int(args["result_count"])
                if 1 <= arg_num <= 10:
                    num = arg_num
            except (TypeError, ValueError):
                pass
    except Exception:
        pass

    # 重い処理
    result = google_search(query, num=num)
    history_text = get_recent_conversation_log(3)
    note = NOTE or args.get("note", "")

    use_raw = False
    try:
        from core.settings_manager import get_setting
        use_raw = get_setting("search.use_raw_result", False)
    except Exception:
        pass

    if use_raw:
        summary = result  # AI要約をスキップし、生の検索結果をそのままメインループへ
        dmis_log = f"『{query}』でGoogle検索（生データをメインループへ）。"
        summary_token_usage = None
    else:
        summary, summary_token_usage = google_search_summary(query, note, history_text, result)
        dmis_log = f"『{query}』でGoogle検索。"
    # 次ターン（RETRY）で summary を本返答にする。raw_result はRETRYモーダルで確認用
    return TEXT, dmis_log, summary, result, summary_token_usage

# --- NEWSコマンド（queryベースのニュース検索）---
def search_news(args, TEXT, NOTE=None, ai_raw=None, processing_time=0.0, token_usage=None):
    """queryベースのニュース検索。Google News RSS使用（APIキー不要）"""
    query = args.get("query", "").strip() if isinstance(args, dict) else ""
    if not query:
        dmis_log = "ニュース検索コマンドが実行されたが、検索クエリが指定されず。"
        return TEXT, dmis_log, None, None, None

    if TEXT and TEXT != "none":
        speak_response(TEXT, ai_raw=ai_raw, processing_time=processing_time, token_usage=token_usage)

    # 参照記事数: サーバー優先時は設定値のみ、そうでなければARGSのmax_itemsを優先
    max_items = 10
    try:
        from core.settings_manager import get_setting
        server_priority = get_setting("news.server_priority", False)
        if server_priority:
            max_items = get_setting("news.max_items", 10)
            max_items = max(1, min(50, int(max_items)))
        else:
            max_items = get_setting("news.max_items", 10)
            max_items = max(1, min(50, int(max_items)))
            if isinstance(args, dict) and "max_items" in args:
                try:
                    arg_max = int(args["max_items"])
                    if 1 <= arg_max <= 50:
                        max_items = arg_max
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    result = google_news_search(query, max_items=max_items)
    history_text = get_recent_conversation_log(3)
    note = NOTE or args.get("note", "")

    use_raw = False
    try:
        from core.settings_manager import get_setting
        use_raw = get_setting("news.use_raw_result", False)
    except Exception:
        pass

    if use_raw:
        summary = result  # AI要約をスキップし、生のニュース結果をそのままメインループへ
        dmis_log = f"『{query}』でニュース検索（生データをメインループへ）。"
        summary_token_usage = None
    else:
        summary, summary_token_usage = google_news_summary(query, note or "ニュース検索", history_text, result)
        dmis_log = f"『{query}』でニュース検索。"
    return TEXT, dmis_log, summary, result, summary_token_usage

# 天気関係
def _build_weather_text(fj: dict, fallback_label: str = "") -> str:
    desc = fj.get("description") or {}
    public_time = desc.get("publicTime") or fj.get("publicTime") or ""
    title = fj.get("title") or fallback_label or ""
    text  = (desc.get("text") or "").strip()

    forecasts = fj.get("forecasts") or []
    today = forecasts[0] if forecasts else {}
    detail = today.get("detail") or {}
    weather = (detail.get("weather") or today.get("telop") or "").strip() or "不明"
    wind    = (detail.get("wind") or "").strip() or "不明"

    parts = [f"{public_time}発表の{title}です。"]
    if text: parts.append(text)
    parts.append(f"今日の天気は「{weather}」、風は「{wind}」です。")
    return "\n".join(parts)

def _build_weather_text_open_meteo(weather_data: dict, location_name: str) -> str:
    """Open-Meteoの気象データをきりたん用のテキストに整形"""
    current_time = get_current_time()
    
    parts = [f"「{current_time}」の、「{location_name}」周辺の気象情報は以下の通りです。"]
    
    # 現在の天気
    temp = weather_data.get("temperature")
    humidity = weather_data.get("humidity")
    wind_speed = weather_data.get("wind_speed")
    wind_direction = weather_data.get("wind_direction")
    weather_desc = weather_data.get("weather_description", "不明")
    precipitation = weather_data.get("precipitation", 0)
    
    weather_info = [f"天気: {weather_desc}"]
    if temp is not None:
        weather_info.append(f"気温: {temp}℃")
    if humidity is not None:
        weather_info.append(f"湿度: {humidity}%")
    if wind_speed is not None:
        wind_info = f"風速: {wind_speed}m/s"
        if wind_direction is not None:
            # 風向きを16方位に変換
            directions = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東",
                         "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"]
            dir_idx = int((wind_direction + 11.25) / 22.5) % 16
            wind_info += f"（{directions[dir_idx]}）"
        weather_info.append(wind_info)
    if precipitation and precipitation > 0:
        weather_info.append(f"降水量: {precipitation}mm")
    
    parts.append("、".join(weather_info))
    
    # 今日の予報（最高・最低気温）
    max_temp = weather_data.get("max_temperature")
    min_temp = weather_data.get("min_temperature")
    if max_temp is not None and min_temp is not None:
        parts.append(f"今日の予想気温: 最高{max_temp}℃、最低{min_temp}℃")
    
    return "\n".join(parts)

def _get_weather_jma(place: str):
    """気象庁APIから天気を取得（内部関数）"""
    place = normalize_location_text(place)
    if not place:
        place = "札幌"
    
    client = _get_openai()
    cid, label = ai_resolve_city_id_simple(place, client)
    
    source = "ai"
    if not cid:
        rows = load_rows()
        exact = next(((p, t, i) for (p, t, i) in rows if t == place), None)
        if exact:
            cid, label = exact[2], exact[1]
            source = "exact"
        else:
            pref = detect_pref_from_text(place)
            if pref:
                title, cid2 = best_city_in_pref(pref)
                if cid2:
                    cid, label, source = cid2, title, "pref"
    
    if not cid:
        return None, None, f"地点『{place}』に対応する予報地点が見つかりません。"
    
    try:
        api_url = get_current_weather_api_url()
        api_timeout = get_current_weather_api_timeout()
        r = requests.get(f"{api_url}{cid}", timeout=api_timeout)
        r.raise_for_status()
        fj = r.json()
        
        desc = fj.get("description") or {}
        public_time = desc.get("publicTime") or fj.get("publicTime") or ""
        api_title = fj.get("title") or label or ""
        summary = _build_weather_text(fj, fallback_label=label)
        
        dmis_log = f"気象庁API：『{place}』→{label}({cid})"
        return summary, dmis_log, None
    except Exception as e:
        return None, None, f"気象庁APIからの気象情報取得に失敗しました: {e}"

def _get_weather_open_meteo_from_place(place: str, location_name: str = None, lat: float = None, lon: float = None):
    """Open-Meteo APIから天気を取得（内部関数、現在地または地名指定）"""
    place = normalize_location_text(place)
    location_name = normalize_location_text(location_name or "")
    if lat is None or lon is None:
        if not place:
            return None, None, "地名が指定されていません。"
        
        client = _get_openai()
        lat, lon, label = ai_resolve_location_coordinates(place, client)
        
        source = "ai"
        if lat is None or lon is None:
            rows = load_location_rows()
            exact = next(((p, t, la, lo) for (p, t, la, lo) in rows if t == place), None)
            if exact:
                lat, lon, label = exact[2], exact[3], exact[1]
                source = "exact"
            else:
                pref = detect_pref_from_text_for_location(place)
                if pref:
                    title, la, lo = best_location_in_pref(pref)
                    if la != 0.0 and lo != 0.0:
                        lat, lon, label, source = la, lo, title, "pref"
        
        if lat is None or lon is None:
            return None, None, f"地点『{place}』に対応する緯度経度が見つかりません（locations.csvに登録が必要です）。"
        
        location_name = label
    
    try:
        weather_data = get_weather_open_meteo(lat, lon, location_name or "")
        summary = _build_weather_text_open_meteo(weather_data, location_name or "")
        dmis_log = f"Open-Meteo API：{location_name or place}（緯度{lat:.4f}）（経度{lon:.4f}）"
        return summary, dmis_log, None
    except Exception as e:
        return None, None, f"Open-Meteo APIからの気象情報取得に失敗しました: {e}"

def get_weather(args, TEXT, NOTE=None, ai_raw=None, processing_time=0.0, token_usage=None):
    """
    引数: {'w_location':'自由文'}
    返り: (TEXT='', DMIS-LOG, summary)  ← 喋らない/RETRY用
    """
    # 気象サービスを設定から取得
    try:
        from core.settings_manager import get_setting
        weather_service = get_setting("weather.service", "jma")
    except:
        from config import WEATHER_SERVICE
        weather_service = WEATHER_SERVICE

    place = ""
    if isinstance(args, dict):
        place = normalize_location_text(args.get("w_location") or "")

    # 進捗アナウンスを"先に"出す（音声のみ。/reply直叩きはしない）
    if TEXT and TEXT != "none":
        try:
            speak_response(TEXT, ai_raw=ai_raw, processing_time=processing_time, token_usage=token_usage)     # 音声を先行再生（utter_id付きreplyは内部で飛ぶ）
        except Exception:
            pass

    # 両方モード：気象庁APIとOpen-Meteo APIの両方から取得
    if weather_service == "both":
        is_current_location = (not place) or (place.lower() in ["現在地", "いまの場所", "ここ", "current", "here"])
        
        jma_summary = None
        jma_log = None
        jma_error = None
        om_summary = None
        om_log = None
        om_error = None
        
        # 気象庁APIから取得
        if is_current_location:
            # 現在地の場合：現在地の地名を使って気象庁APIを取得
            current_name = normalize_location_text(get_current_location())
            jma_summary, jma_log, jma_error = _get_weather_jma(current_name)
        else:
            # 地名指定の場合：指定地名から気象庁APIを取得
            jma_summary, jma_log, jma_error = _get_weather_jma(place)
        
        # Open-Meteo APIから取得
        if is_current_location:
            # 現在地の場合：現在地の緯度経度でOpen-Meteo APIを取得
            lat, lon = get_current_coordinates()
            location_name = normalize_location_text(get_current_location())
            if has_coordinates() and lat is not None and lon is not None:
                om_summary, om_log, om_error = _get_weather_open_meteo_from_place("", location_name, lat, lon)
            else:
                om_error = "現在地の緯度経度が設定されていません。"
        else:
            # 地名指定の場合：指定地名からOpen-Meteo APIを取得
            om_summary, om_log, om_error = _get_weather_open_meteo_from_place(place)
        
        # 結果を合成
        combined_parts = []
        combined_log_parts = []
        
        if jma_summary:
            combined_parts.append("【気象庁API】")
            combined_parts.append(jma_summary)
            combined_log_parts.append(jma_log)
        elif jma_error:
            combined_parts.append(f"【気象庁API】エラー: {jma_error}")
            combined_log_parts.append(f"気象庁API：失敗（{jma_error}）")
        
        if om_summary:
            combined_parts.append("\n【Open-Meteo API】")
            combined_parts.append(om_summary)
            combined_log_parts.append(om_log)
        elif om_error:
            combined_parts.append(f"\n【Open-Meteo API】エラー: {om_error}")
            combined_log_parts.append(f"Open-Meteo API：失敗（{om_error}）")
        
        if not combined_parts:
            fail = "両方のAPIからの気象情報取得に失敗しました。"
            _wdebug("Both: 両方のAPI取得失敗")
            return "", "気象情報取得失敗: 両方のAPI失敗", fail
        
        summary = "\n".join(combined_parts)
        dmis_log = "、".join(combined_log_parts)
        
        if is_current_location:
            location_info = get_current_location()
            _wdebug(f"Both: 現在地 {location_info} の天気を両方のAPIから取得")
        else:
            _wdebug(f"Both: '{place}' の天気を両方のAPIから取得")
        
        return "", dmis_log, summary

    # Open-Meteo API使用時
    if weather_service == "open-meteo":
        # 「現在地」が指定された場合、または地名が指定されていない場合：現在地の緯度経度で取得
        is_current_location = (not place) or (place.lower() in ["現在地", "いまの場所", "ここ", "current", "here"])
        
        if is_current_location:
            lat, lon = get_current_coordinates()
            location_name = normalize_location_text(get_current_location())
            if not has_coordinates() or lat is None or lon is None:
                fail = "現在地の緯度経度が設定されていません。GPSで位置情報を取得するか、地名を指定してください。"
                _wdebug("Open-Meteo: 緯度経度未設定（現在地指定）")
                return "", "気象情報取得失敗: 緯度経度未設定", fail

            try:
                weather_data = get_weather_open_meteo(lat, lon, location_name)
                summary = _build_weather_text_open_meteo(weather_data, location_name)
                dmis_log = f"現在地→{location_name}（緯度{lat:.4f}）（経度{lon:.4f}）の気象情報を提供。"
                _wdebug(f"Open-Meteo: 現在地 {location_name}（{lat:.4f}, {lon:.4f}）の天気予報を提供。")
                return "", dmis_log, summary
            except Exception as e:
                fail = f"Open-Meteo APIからの気象情報取得に失敗しました: {e}"
                _wdebug(f"Open-Meteo API取得エラー: {e}")
                return "", f"気象情報取得失敗: {e}", fail

        # 指定地名がある場合：緯度経度テーブル + AIで解決して取得
        client = _get_openai()
        lat, lon, label = ai_resolve_location_coordinates(place, client)

        source = "ai"
        if lat is None or lon is None:
            rows = load_location_rows()
            exact = next(((p, t, la, lo) for (p, t, la, lo) in rows if t == place), None)
            if exact:
                lat, lon, label = exact[2], exact[3], exact[1]
                source = "exact"
            else:
                pref = detect_pref_from_text_for_location(place)
                if pref:
                    title, la, lo = best_location_in_pref(pref)
                    if la != 0.0 and lo != 0.0:
                        lat, lon, label, source = la, lo, title, "pref"

        if lat is None or lon is None:
            fail = f"地点『{place}』に対応する緯度経度が見つかりません（locations.csvに登録が必要です）。"
            _wdebug(f"Open-Meteo解決失敗: 入力='{place}'。AI/完全一致/都道府県寄せで緯度経度決定できず。")
            return "", f"気象情報取得失敗: {place}", fail

        try:
            weather_data = get_weather_open_meteo(lat, lon, label)
            summary = _build_weather_text_open_meteo(weather_data, label)
            dmis_log = f"『{place}』→{label}（緯度{lat:.4f}）（経度{lon:.4f}）の気象情報を提供。"
            _wdebug(f"Open-Meteo: '{place}' を {label}（{lat:.4f}, {lon:.4f}）へ解決（{source}）し、天気予報を提供。")
            return "", dmis_log, summary
        except Exception as e:
            fail = f"Open-Meteo APIからの気象情報取得に失敗しました: {e}"
            _wdebug(f"Open-Meteo API取得エラー: {e}")
            return "", f"気象情報取得失敗: {e}", fail

    # 気象庁API使用時（従来の処理）
    if not place:
        place = "札幌"

    # 1) AIでIDだけを取得
    client = _get_openai()
    cid, label = ai_resolve_city_id_simple(place, client)

    # 2) フォールバック（完全一致 → 都道府県寄せ）
    source = "ai"
    if not cid:
        rows = load_rows()
        exact = next(((p, t, i) for (p, t, i) in rows if t == place), None)
        if exact:
            cid, label = exact[2], exact[1]
            source = "exact"
        else:
            pref = detect_pref_from_text(place)
            if pref:
                title, cid2 = best_city_in_pref(pref)
                if cid2:
                    cid, label, source = cid2, title, "pref"
    if not cid:
        fail = f"地点『{place}』に対応する予報地点が見つかりません。"
        _wdebug(f"解決失敗: 入力='{place}'。AI/完全一致/都道府県寄せでID決定できず。")
        return "", f"気象情報取得失敗: {place}", fail

    # 3) API取得→整形（ここでは**喋らない**）
    api_url = get_current_weather_api_url()
    api_timeout = get_current_weather_api_timeout()
    r = requests.get(f"{api_url}{cid}", timeout=api_timeout)
    r.raise_for_status()
    fj = r.json()

    # タイトル抽出（空落ち対策でラベルをフォールバック）
    desc = fj.get("description") or {}
    public_time = desc.get("publicTime") or fj.get("publicTime") or ""
    api_title = fj.get("title") or label or ""
    summary = _build_weather_text(fj, fallback_label=label)

    _wdebug(f"『{place}』という地名に対しID:{cid}（{label}）を指定、『{api_title}』の天気予報を提供（{source}）。")
    dmis_log = f"『{place}』→{label}({cid})の気象情報を提供。"
    return "", dmis_log, summary


def _normalize_json_text(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    return text.strip()



# テキストファイル読み込み＋要約

def webpage_summary(url: str, extracted_text: str, user_input: str, note: str = ""):
    """Webページ本文を要約する（500文字程度）"""
    base_prompt = get_prompt_setting("webpage_summary")

    note_prompt = f"【検索・読み込みの目的】{note}\n" if note else ""

    system_prompt = (
        f"{base_prompt}\n\n"
        f"この後にAIはあなたの出力を受けて、情報を再度まとめてユーザーへ送ります。あなたは、まず情報をAIに伝える体で出力してください。キャラクター性は必要ありません。\n"
        f"【対象URL】\n{url}\n"
        f"【ユーザーの要求】\n{user_input}\n"
        f"{note_prompt}"
        f"【Webページ本文】\n{extracted_text}\n"
        f"---\n"
        f"【重要】\n"
        f"・要約は500文字程度で、重要な情報を優先的に含めてください。\n"
        f"・挨拶や問いかけは不要です。情報だけを出力してください。\n"
    )

    if _increment_stat_func:
        _increment_stat_func("ai_requests")

    try:
        from core.settings_manager import get_setting
        model = get_setting("ai_models.webpage_summary", GPT_MODEL_WEBPAGE_SUMMARY)
    except Exception:
        model = GPT_MODEL_WEBPAGE_SUMMARY

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system_prompt}]
    )
    content = response.choices[0].message.content
    usage = None
    if hasattr(response, "usage") and response.usage:
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
        }
    return content, usage


def read_webpage(args, TEXT, NOTE=None, ai_raw=None, processing_time=0.0, token_usage=None):
    """
    指定URLのWebページを読み込み、要約または生テキストをRETRYに渡す。
    args: {"url": "https://..."}
    要約するかは設定 webpage.use_raw_result で制御（True=生データ、False=要約）
    """
    url = args.get("url", "").strip() if isinstance(args, dict) else ""
    if not url:
        dmis_log = "READ-PAGEコマンドが実行されたが、urlが指定されず。"
        return TEXT, dmis_log  # 2値返し（execute_commandのelse分岐）

    use_raw = False
    try:
        from core.settings_manager import get_setting
        use_raw = get_setting("webpage.use_raw_result", False)
    except Exception:
        pass
    use_summary = not use_raw

    # 進捗アナウンス
    if TEXT and TEXT != "none":
        speak_response(TEXT, ai_raw=ai_raw, processing_time=processing_time, token_usage=token_usage)

    # Google Newsの中継URLは元記事URLへ解決してから読む
    target_url = resolve_google_news_url(url)

    # スクレイピング実行
    raw_text = scrape_webpage(target_url, use_summary=False)
    if raw_text.startswith("エラー:") or raw_text.startswith("本文が抽出できませんでした"):
        dmis_log = f"Webページ『{target_url}』の読み込みに失敗。"
        print(f"[READ-PAGE] {raw_text}")
        return TEXT, dmis_log, raw_text, raw_text, None

    if use_summary:
        user_input = get_last_user_input()
        note = NOTE or args.get("note", "") if isinstance(args, dict) else ""
        summary, summary_token_usage = webpage_summary(target_url, raw_text, user_input, note)
        dmis_log = f"Webページ『{target_url}』を読み込み、要約を生成。"
    else:
        summary = raw_text
        summary_token_usage = None
        dmis_log = f"Webページ『{target_url}』を読み込み（生データをメインループへ）。"

    print(f"[READ-PAGE] {dmis_log}")
    return TEXT, dmis_log, summary, raw_text, summary_token_usage

