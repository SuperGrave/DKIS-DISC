"""Open-Meteo（ジオコーディング + 現在気象）。無料・APIキー不要。"""

from __future__ import annotations

from datetime import datetime, timedelta
import time
from typing import Any

import requests

_GEODECODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_WEATHER_FETCH_ATTEMPTS = 3
_WEATHER_RETRY_STATUS = frozenset({429, 502, 503, 504})
_WEATHER_CACHE_TTL = timedelta(minutes=10)
_weather_report_cache: dict[str, tuple[datetime, str]] = {}
_DEFAULT_HEADERS = {
    "User-Agent": "DKIS-DISC weather client (https://github.com/SuperGrave/DKIS-DISC)",
    "Accept": "application/json",
}

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


def _get_json_with_retry(url: str, *, params: dict[str, Any], timeout: float) -> tuple[dict[str, Any] | None, str | None]:
    last_err: str | None = None
    for attempt in range(_WEATHER_FETCH_ATTEMPTS):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=_DEFAULT_HEADERS)
            if r.status_code in _WEATHER_RETRY_STATUS:
                last_err = f"{r.status_code} Server Error"
                if attempt < _WEATHER_FETCH_ATTEMPTS - 1:
                    time.sleep(min(6.0, 1.5 * (2**attempt)))
                    continue
                return None, last_err
            r.raise_for_status()
            return r.json(), None
        except requests.RequestException as exc:
            last_err = str(exc)
            if attempt < _WEATHER_FETCH_ATTEMPTS - 1:
                time.sleep(min(6.0, 1.5 * (2**attempt)))
                continue
            return None, last_err
        except ValueError as exc:
            return None, f"JSON解析に失敗しました: {exc}"
    return None, last_err or "リクエストに失敗しました"


def geocode_place(name: str, *, timeout: float = 10.0) -> tuple[float, float, str] | None:
    """地名から緯度経度を解決。見つからなければ None。"""
    q = (name or "").strip()
    if not q:
        return None
    try:
        data, err = _get_json_with_retry(
            _GEODECODE_URL,
            params={"name": q, "count": 1, "language": "ja"},
            timeout=timeout,
        )
        if err or data is None:
            return None
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
    data, err = _get_json_with_retry(_FORECAST_URL, params=params, timeout=timeout)
    if err or data is None:
        raise RuntimeError(err or "天気APIの取得に失敗しました")
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


def fetch_weather_report(place: str, *, timeout: float = 12.0) -> str:
    """地名から Open-Meteo で天気テキストを返す（place は空でないことが前提）。"""
    raw = (place or "").strip()
    if not raw:
        return "内部エラー: 地名が空です。"

    cache_key = raw.casefold()
    now = datetime.now()
    cached = _weather_report_cache.get(cache_key)
    if cached and now - cached[0] < _WEATHER_CACHE_TTL:
        return cached[1] + "\n（短時間の再取得を避けるため、直近の天気結果を再利用しました）"

    geo = geocode_place(raw, timeout=timeout)
    if not geo:
        return f"地点「{raw}」の緯度経度が解決できませんでした。別の表記（市区町村名など）で試してください。"

    lat, lon, label = geo
    try:
        w = fetch_weather(lat, lon, label, timeout=timeout)
        report = format_weather_text(w)
        _weather_report_cache[cache_key] = (now, report)
        return report
    except Exception as e:
        if cached:
            return cached[1] + f"\n（天気APIの再取得に失敗したため、前回結果を表示しています: {e}）"
        return (
            f"天気APIの取得に失敗しました: {e}\n"
            "Open-Meteo 側の一時的なレート制限や混雑の可能性があります。少し時間を置いて再試行してください。"
        )
