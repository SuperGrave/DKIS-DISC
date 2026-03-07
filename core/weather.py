# ===== オフライン専用ローダに差し替え =====
# 既存の imports から requests はもう不要
import time
import csv
import os
import re
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import List, Tuple, Dict, Optional
from datetime import datetime
import requests
from config import (
    GPT_MODEL_WEATHER_LOCATION,  # 天気地名解決用モデル
    OPEN_METEO_URL,               # Open-Meteo API URL
    WEATHER_API_TIMEOUT          # API タイムアウト
)
from core.settings_manager import get_prompt_setting

# 同梱ファイルの場所（デフォルトはこのファイルと同じディレクトリ）
# 配布先で場所を変えたい場合は環境変数 PRIMARY_AREA_XML で上書き可
_PRIMARY_AREA_XML = Path(os.environ.get("PRIMARY_AREA_XML") or Path(__file__).with_name("primary_area.xml"))
_PRIMARY_AREA_CSV = _PRIMARY_AREA_XML.with_suffix(".csv")

# 緯度経度テーブルの場所
_LOCATIONS_CSV = Path(os.environ.get("LOCATIONS_CSV") or Path(__file__).with_name("locations.csv"))

_CACHE: Dict[str, object] = {"ts": 0.0, "rows": []}  # rows: List[(pref, title, id)]
_LOCATION_CACHE: Dict[str, object] = {"ts": 0.0, "rows": []}  # rows: List[(pref, title, lat, lon)]

def _parse_rows_from_xml(p: Path) -> List[Tuple[str, str, str]]:
    data = p.read_bytes()
    root = ET.fromstring(data)
    rows: List[Tuple[str, str, str]] = []
    # <pref title="都道府県"> … <city id="xxxxx" title="都市名"> or <city id="xxxxx"><title>都市名</title>
    for pref in root.iter("pref"):
        pref_name = (pref.attrib.get("title") or pref.findtext("title") or "").strip()
        for city in pref.iter("city"):
            cid = (city.attrib.get("id") or (city.findtext("id") or "")).strip()
            title = (city.attrib.get("title") or city.findtext("title") or "").strip()
            if cid and title:
                rows.append((pref_name, title, cid))
    return rows

def _parse_rows_from_csv(p: Path) -> List[Tuple[str, str, str]]:
    rows: List[Tuple[str, str, str]] = []
    with p.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        # 期待ヘッダ: pref, title, city_id
        for row in r:
            pref = (row.get("pref") or "").strip()
            title = (row.get("title") or "").strip()
            cid = (row.get("city_id") or "").strip()
            if cid and title:
                rows.append((pref, title, cid))
    return rows

def _load_local_rows() -> List[Tuple[str, str, str]]:
    # CSV があれば最優先
    if _PRIMARY_AREA_CSV.exists():
        return _parse_rows_from_csv(_PRIMARY_AREA_CSV)
    # なければ XML
    if _PRIMARY_AREA_XML.exists():
        return _parse_rows_from_xml(_PRIMARY_AREA_XML)
    # どちらも無いなら明示的に落とす（配布漏れに気づける）
    raise FileNotFoundError(
        f"予報地点リストが見つからないよ。配置してね → "
        f"{_PRIMARY_AREA_XML} または {_PRIMARY_AREA_CSV}"
    )

def load_rows(cache_ttl_sec: int = 86400) -> List[Tuple[str, str, str]]:
    # オフラインでもキャッシュはそのまま使える（再起動ごとに読み直しでもOKなら ttl は無視してもよい）
    now = time.time()
    rows = _CACHE.get("rows") or []
    if rows and (now - float(_CACHE.get("ts") or 0.0) < cache_ttl_sec):
        return rows  # type: ignore[return-value]
    rows = _load_local_rows()
    _CACHE.update(ts=now, rows=rows)
    return rows

# 任意: 配置パスを実行時に差し替えたいとき用
def set_primary_area_path(path: str) -> None:
    global _PRIMARY_AREA_XML, _PRIMARY_AREA_CSV
    _PRIMARY_AREA_XML = Path(path)
    _PRIMARY_AREA_CSV = _PRIMARY_AREA_XML.with_suffix(".csv")
    # 既存キャッシュはクリアしておく
    _CACHE.update(ts=0.0, rows=[])

def warm_primary_area() -> int:
    """起動時プリロード用。強制リロードして件数を返す。"""
    rows = load_rows(cache_ttl_sec=0)
    return len(rows)

# ---------- AIへ渡す表の生成（2パターン） ----------
def build_table_for_ai(user_place: str) -> str:
    """'地名\\tID' 表（都道府県が入っていたらそのprefだけに絞る）"""
    rows = load_rows()
    place = (user_place or "").strip()
    prefs = sorted({p for (p, _, _) in rows if p})
    hit = [p for p in prefs if p and p in place]
    chosen = [r for r in rows if (not hit or r[0] in hit)]
    return "\n".join(f"- {title}\t{cid}" for (_p, title, cid) in chosen)

def build_equals_table_for_ai(user_place: str) -> str:
    """'地名=ID' の行リスト（同上のpref絞り込み）"""
    rows = load_rows()
    place = (user_place or "").strip()
    prefs = sorted({p for (p, _, _) in rows if p})
    hit_prefs = [p for p in prefs if p and p in place]
    chosen = [(t, i) for (p, t, i) in rows if (not hit_prefs or p in hit_prefs)]
    return "\n".join(f"{t}={i}" for (t, i) in chosen)

def id_to_title(cid: str) -> str:
    for (_p, t, i) in (_CACHE["rows"] or []):
        if i == cid:
            return t
    return ""

def load_city_map() -> dict:
    """title -> id の辞書"""
    rows = load_rows()
    return {title: cid for (_p, title, cid) in rows}

# ---------- 都道府県検出＆県庁所在地フォールバック ----------
def detect_pref_from_text(text: str) -> str:
    rows = load_rows()
    prefs = sorted({p for (p, _, _) in rows if p})
    for p in prefs:
        if p and p in (text or ""):
            return p
    return ""

_PREF_CAPITAL_HINT: Dict[str, str] = {
    "北海道":"札幌","青森県":"青森","岩手県":"盛岡","宮城県":"仙台","秋田県":"秋田","山形県":"山形","福島県":"福島",
    "茨城県":"水戸","栃木県":"宇都宮","群馬県":"前橋","埼玉県":"さいたま","千葉県":"千葉","東京都":"東京","神奈川県":"横浜",
    "新潟県":"新潟","富山県":"富山","石川県":"金沢","福井県":"福井","山梨県":"甲府","長野県":"長野",
    "岐阜県":"岐阜","静岡県":"静岡","愛知県":"名古屋","三重県":"津",
    "滋賀県":"大津","京都府":"京都","大阪府":"大阪","兵庫県":"神戸","奈良県":"奈良","和歌山県":"和歌山",
    "鳥取県":"鳥取","島根県":"松江","岡山県":"岡山","広島県":"広島","山口県":"山口",
    "徳島県":"徳島","香川県":"高松","愛媛県":"松山","高知県":"高知",
    "福岡県":"福岡","佐賀県":"佐賀","長崎県":"長崎","熊本県":"熊本","大分県":"大分","宮崎県":"宮崎","鹿児島県":"鹿児島","沖縄県":"那覇",
}

def best_city_in_pref(pref: str) -> Tuple[str, str]:
    """pref内の代表地点 (title, id)。ヒント一致→先頭。無ければ("", "")"""
    rows = load_rows()
    cand = [(t, i) for (p, t, i) in rows if p == pref]
    if not cand:
        return ("", "")
    hint = _PREF_CAPITAL_HINT.get(pref, "")
    if hint:
        for (t, i) in cand:
            if hint in t:
                return (t, i)
    return cand[0]

# ---------- AIリゾルバ（“IDだけ返す”方式） ----------
def ai_resolve_city_id_simple(user_place: str, openai_client) -> Tuple[str, str]:
    """
    OpenAI client（chat.completions互換）を外から渡す形に変更。
    戻り値: (city_id or '', city_label or '')
    """
    table = build_equals_table_for_ai(user_place)
    
    system_prompt = get_prompt_setting("weather_jma_id")
    
    user = table + "\n---\n" + (user_place or "")
    
    # 統計：GPT呼び出し回数をカウント（注入された関数を使用）
    # ※ weather.py では関数注入の仕組みがないので、ここではカウントしない
    # （他の場所で十分カウントされているため）

    # モデル名を設定ファイルから取得（なければconfig.pyのデフォルト）
    try:
        from core.settings_manager import get_setting
        model = get_setting("ai_models.weather_location", GPT_MODEL_WEATHER_LOCATION)
    except:
        model = GPT_MODEL_WEATHER_LOCATION
    
    res = openai_client.chat.completions.create(
        model=model,  # 設定ファイルまたはconfig.pyから
        temperature=0.1,
        messages=[{"role": "system", "content": system_prompt},
                  {"role": "user", "content": user}]
    )
    out = (res.choices[0].message.content or "").strip()
    m = re.search(r"\b(\d{5,6})\b", out)
    cid = m.group(1) if m else ""

    label = id_to_title(cid) if cid else ""
    return (cid if label else "", label)

# ---------- Open-Meteo API関連 ----------
# WMO Weather Interpretation Codes (WWIC) を日本語に変換
_WMO_WEATHER_CODES: Dict[int, str] = {
    0: "快晴",
    1: "おおむね晴れ",
    2: "部分的に曇り",
    3: "曇り",
    45: "霧",
    48: "着氷性霧",
    51: "弱い霧雨",
    53: "霧雨",
    55: "強い霧雨",
    56: "弱い着氷性霧雨",
    57: "着氷性霧雨",
    61: "弱い雨",
    63: "雨",
    65: "強い雨",
    66: "弱い着氷性雨",
    67: "着氷性雨",
    71: "弱い雪",
    73: "雪",
    75: "強い雪",
    77: "雪の結晶",
    80: "弱いにわか雨",
    81: "にわか雨",
    82: "強いにわか雨",
    85: "弱いにわか雪",
    86: "にわか雪",
    95: "雷雨",
    96: "弱い雹を伴う雷雨",
    99: "強い雹を伴う雷雨",
}

def _decode_wmo_code(code: int) -> str:
    """WMO天気コードを日本語に変換"""
    return _WMO_WEATHER_CODES.get(code, f"不明（コード{code}）")

def get_weather_open_meteo(lat: float, lon: float, location_name: str = "") -> Dict:
    """
    Open-Meteo APIから気象情報を取得
    戻り値: 気象情報の辞書
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ["temperature_2m", "relative_humidity_2m", "weather_code", "wind_speed_10m", "wind_direction_10m", "precipitation"],
        "hourly": ["temperature_2m", "weather_code", "precipitation_probability"],
        "timezone": "Asia/Tokyo",
        "forecast_days": 1
    }
    
    try:
        response = requests.get(OPEN_METEO_URL, params=params, timeout=WEATHER_API_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        
        current = data.get("current", {})
        hourly = data.get("hourly", {})
        
        # 現在の天気情報
        weather_code = current.get("weather_code", 0)
        weather_desc = _decode_wmo_code(weather_code)
        
        # 今日の予報（最初の24時間）
        hourly_times = hourly.get("time", [])[:24]
        hourly_temps = hourly.get("temperature_2m", [])[:24]
        hourly_codes = hourly.get("weather_code", [])[:24]
        hourly_precip = hourly.get("precipitation_probability", [])[:24]
        
        # 最高気温・最低気温
        max_temp = max(hourly_temps) if hourly_temps else None
        min_temp = min(hourly_temps) if hourly_temps else None
        
        return {
            "location": location_name or f"緯度{lat:.4f}, 経度{lon:.4f}",
            "time": current.get("time", datetime.now().strftime("%Y-%m-%dT%H:%M")),
            "temperature": current.get("temperature_2m"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "wind_direction": current.get("wind_direction_10m"),
            "precipitation": current.get("precipitation", 0),
            "weather_code": weather_code,
            "weather_description": weather_desc,
            "max_temperature": max_temp,
            "min_temperature": min_temp,
            "hourly_forecast": [
                {
                    "time": t,
                    "temperature": temp,
                    "weather_code": code,
                    "weather_description": _decode_wmo_code(code),
                    "precipitation_probability": precip
                }
                for t, temp, code, precip in zip(hourly_times, hourly_temps, hourly_codes, hourly_precip)
            ]
        }
    except Exception as e:
        raise Exception(f"Open-Meteo API取得エラー: {e}")

# ---------- 緯度経度テーブル関連（主要地点の天気予報用） ----------
def _parse_location_rows_from_csv(p: Path) -> List[Tuple[str, str, float, float]]:
    """緯度経度テーブルをCSVから読み込む"""
    rows: List[Tuple[str, str, float, float]] = []
    if not p.exists():
        return rows
    
    with p.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        # 期待ヘッダ: pref, title, lat, lon
        for row in r:
            pref = (row.get("pref") or "").strip()
            title = (row.get("title") or "").strip()
            try:
                lat = float(row.get("lat", 0))
                lon = float(row.get("lon", 0))
                if title and lat != 0 and lon != 0:
                    rows.append((pref, title, lat, lon))
            except (ValueError, TypeError):
                continue
    return rows

def load_location_rows(cache_ttl_sec: int = 86400) -> List[Tuple[str, str, float, float]]:
    """緯度経度テーブルを読み込む（キャッシュ付き）"""
    now = time.time()
    rows = _LOCATION_CACHE.get("rows") or []
    if rows and (now - float(_LOCATION_CACHE.get("ts") or 0.0) < cache_ttl_sec):
        return rows  # type: ignore[return-value]
    rows = _parse_location_rows_from_csv(_LOCATIONS_CSV)
    _LOCATION_CACHE.update(ts=now, rows=rows)
    return rows

def warm_location_table() -> int:
    """起動時プリロード用。強制リロードして件数を返す。"""
    rows = load_location_rows(cache_ttl_sec=0)
    return len(rows)

def build_location_table_for_ai(user_place: str) -> str:
    """AI用の緯度経度テーブルを生成（都道府県が入っていたらそのprefだけに絞る）"""
    rows = load_location_rows()
    place = (user_place or "").strip()
    prefs = sorted({p for (p, _, _, _) in rows if p})
    hit_prefs = [p for p in prefs if p and p in place]
    chosen = [(p, t, lat, lon) for (p, t, lat, lon) in rows if (not hit_prefs or p in hit_prefs)]
    # 地名=緯度,経度 の形式で返す
    return "\n".join(f"{t}={lat:.4f},{lon:.4f}" for (_, t, lat, lon) in chosen)

def detect_pref_from_text_for_location(text: str) -> str:
    """テキストから都道府県を検出（緯度経度テーブル用）"""
    rows = load_location_rows()
    prefs = sorted({p for (p, _, _, _) in rows if p})
    for p in prefs:
        if p and p in (text or ""):
            return p
    return ""

def best_location_in_pref(pref: str) -> Tuple[str, float, float]:
    """都道府県内の代表地点 (title, lat, lon)。無ければ("", 0.0, 0.0)"""
    rows = load_location_rows()
    cand = [(t, lat, lon) for (p, t, lat, lon) in rows if p == pref]
    if not cand:
        return ("", 0.0, 0.0)
    hint = _PREF_CAPITAL_HINT.get(pref, "")
    if hint:
        for (t, lat, lon) in cand:
            if hint in t:
                return (t, lat, lon)
    return cand[0]

def ai_resolve_location_coordinates(user_place: str, openai_client) -> Tuple[Optional[float], Optional[float], str]:
    """
    地名から緯度経度を解決するAI関数
    戻り値: (lat or None, lon or None, location_label or '')
    """
    table = build_location_table_for_ai(user_place)
    
    system_prompt = get_prompt_setting("weather_coordinates")
    
    user = table + "\n---\n" + (user_place or "")
    
    # モデル名を設定ファイルから取得（なければconfig.pyのデフォルト）
    try:
        from core.settings_manager import get_setting
        model = get_setting("ai_models.weather_location", GPT_MODEL_WEATHER_LOCATION)
    except:
        model = GPT_MODEL_WEATHER_LOCATION
    
    res = openai_client.chat.completions.create(
        model=model,
        temperature=0.1,
        messages=[{"role": "system", "content": system_prompt},
                  {"role": "user", "content": user}]
    )
    out = (res.choices[0].message.content or "").strip()
    
    # 緯度,経度の形式を抽出
    m = re.search(r"(\d+\.\d+),(\d+\.\d+)", out)
    if m:
        lat = float(m.group(1))
        lon = float(m.group(2))
        # 地名を抽出（テーブルから検索）
        rows = load_location_rows()
        label = ""
        for (p, t, la, lo) in rows:
            if abs(la - lat) < 0.0001 and abs(lo - lon) < 0.0001:
                label = t
                break
        if not label:
            # 完全一致しない場合は、最も近い地点を探す
            min_dist = float('inf')
            for (p, t, la, lo) in rows:
                dist = ((la - lat) ** 2 + (lo - lon) ** 2) ** 0.5
                if dist < min_dist:
                    min_dist = dist
                    label = t
        return (lat, lon, label)
    
    return (None, None, "")


