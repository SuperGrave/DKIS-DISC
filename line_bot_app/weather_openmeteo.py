"""Open-Meteo（ジオコーディング + 現在気象）。無料・APIキー不要。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

_GEODECODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_WMO_WEATHER_CODES: dict[int, str] = {
    0: "快晴",
    1: "おおむね晴れ",
    2: "部分的に曇り",
    3: "曇り",
    45: "霧",
    48: "着氷性霧",
    51: "弱い霧雨",
    53: "霧雨",
    55: "強い霧雨",
    61: "弱い雨",
    63: "雨",
    65: "強い雨",
    71: "弱い雪",
    73: "雪",
    75: "強い雪",
    80: "にわか雨",
    81: "にわか雨",
    82: "強いにわか雨",
    95: "雷雨",
}


def _decode_wmo(code: int) -> str:
    return _WMO_WEATHER_CODES.get(code, f"不明（コード{code}）")


def geocode_place(name: str, *, timeout: float = 10.0) -> tuple[float, float, str] | None:
    """地名から緯度経度を解決。見つからなければ None。"""
    q = (name or "").strip()
    if not q:
        return None
    try:
        r = requests.get(
            _GEODECODE_URL,
            params={"name": q, "count": 1, "language": "ja"},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        hit = results[0]
        lat = float(hit["latitude"])
        lon = float(hit["longitude"])
        label = hit.get("name") or q
        adm = hit.get("admin1")
        country = hit.get("country")
        extra = " / ".join(x for x in (adm, country) if x)
        loc_name = f"{label}" + (f" ({extra})" if extra else "")
        return lat, lon, loc_name
    except Exception:
        return None


def fetch_weather(lat: float, lon: float, location_label: str, *, timeout: float = 12.0) -> dict[str, Any]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ["temperature_2m", "relative_humidity_2m", "weather_code", "wind_speed_10m"],
        "hourly": ["temperature_2m"],
        "timezone": "Asia/Tokyo",
        "forecast_days": 1,
    }
    r = requests.get(_FORECAST_URL, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    current = data.get("current", {})
    hourly = data.get("hourly", {})
    temps = hourly.get("temperature_2m", [])[:24]
    code = int(current.get("weather_code", 0))
    return {
        "location": location_label,
        "time": current.get("time", datetime.now().strftime("%Y-%m-%dT%H:%M")),
        "temperature": current.get("temperature_2m"),
        "humidity": current.get("relative_humidity_2m"),
        "wind_speed": current.get("wind_speed_10m"),
        "weather_description": _decode_wmo(code),
        "max_temperature": max(temps) if temps else None,
        "min_temperature": min(temps) if temps else None,
    }


def format_weather_text(data: dict[str, Any]) -> str:
    parts = [
        f"地点: {data.get('location', '')}",
        f"参照時刻: {data.get('time', '')}",
        f"天気: {data.get('weather_description', '')}",
    ]
    if data.get("temperature") is not None:
        parts.append(f"気温: {data['temperature']}°C")
    if data.get("humidity") is not None:
        parts.append(f"湿度: {data['humidity']}%")
    if data.get("wind_speed") is not None:
        parts.append(f"風速: {data['wind_speed']} m/s")
    hi = data.get("max_temperature")
    lo = data.get("min_temperature")
    if hi is not None and lo is not None:
        parts.append(f"今日の予想気温（おおよそ）: 最高 {hi}°C / 最低 {lo}°C")
    return "\n".join(parts)


def weather_for_place(place: str, *, default_place: str, timeout: float = 12.0) -> str:
    """
    地名または「現在地」相当のフォールバックで天気テキストを返す。
    LINE では GPS が無いため「現在地」は default_place に読み替える。
    """
    raw = (place or "").strip()
    current_aliases = ("現在地", "いまの場所", "ここ", "current", "here")
    if not raw or raw.lower() in current_aliases:
        raw = default_place

    geo = geocode_place(raw, timeout=timeout)
    if not geo:
        return f"地点「{raw}」の緯度経度が解決できませんでした。別の表記（市区町村名など）で試してください。"

    lat, lon, label = geo
    try:
        w = fetch_weather(lat, lon, label, timeout=timeout)
        return format_weather_text(w)
    except Exception as e:
        return f"天気APIの取得に失敗しました: {e}"
