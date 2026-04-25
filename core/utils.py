import re
import requests
import csv
import time
import json
import unicodedata
from xml.etree import ElementTree
from urllib.parse import quote_plus, quote, urlparse, parse_qs
from werkzeug.serving import WSGIRequestHandler
from config import GOOGLE_API_KEY, GOOGLE_CX, GOOGLE_SEARCH_NUM, GPS_TIMEOUT

# --- ANSI基本16色 ---
RESET        = "\033[0m"

# 通常色
BLACK        = "\033[30m"
RED          = "\033[31m"
GREEN        = "\033[32m"
YELLOW       = "\033[33m"
BLUE         = "\033[34m"
MAGENTA      = "\033[35m"
CYAN         = "\033[36m"
WHITE        = "\033[37m"

# 明るい色（鮮やか）
BLACK_BRIGHT   = "\033[90m"
RED_BRIGHT     = "\033[91m"
GREEN_BRIGHT   = "\033[92m"
YELLOW_BRIGHT  = "\033[93m"
BLUE_BRIGHT    = "\033[94m"
MAGENTA_BRIGHT = "\033[95m"
CYAN_BRIGHT    = "\033[96m"
WHITE_BRIGHT   = "\033[97m"

# 動的設定管理用のグローバル変数
_current_gps_timeout = GPS_TIMEOUT

def get_current_gps_timeout():
    """現在のGPSタイムアウトを取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        return get_setting("timeouts.gps", _current_gps_timeout)
    except:
        return _current_gps_timeout

def reload_utils_settings():
    """Utils設定をリロード"""
    global _current_gps_timeout
    try:
        from core.settings_manager import get_setting
        _current_gps_timeout = get_setting("timeouts.gps", GPS_TIMEOUT)
        print(f"[Utils] 設定をリロードしました: GPS_Timeout={_current_gps_timeout}")
    except Exception as e:
        print(f"[Utils] 設定リロードエラー: {e}")

def sanitize_text(text):
    """テキストをクリーンに整形"""
    return re.sub(r"\s{2,}", " ", text.replace("\n", " ")).strip()


def normalize_location_text(text: str) -> str:
    """位置情報文字列向けの正規化。不可視文字や文字化け混入を抑える。"""
    if text is None:
        return ""

    s = unicodedata.normalize("NFKC", str(text))
    # 置換文字・BOM・ゼロ幅系・私用領域を除去
    s = re.sub(r"[\uFFFD\uFEFF\u200B-\u200D\u2060\uE000-\uF8FF]", "", s)
    # 制御文字を除去（改行・タブは位置情報では不要なのでまとめて落とす）
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def print_color(text, color=GREEN):
    """色付きで出力（デフォルト緑）"""
    print(f"{color}{text}{RESET}")

# --- カスタムリクエストログ出力 ---
class CustomRequestHandler(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        method = self.command
        path = self.path
        client_ip = self.client_address[0]
        timestamp = self.log_date_time_string()

        # 状況に応じたログメッセージ
        if path == "/callback" and method == "POST" and code == 200:
            status_msg = "200 返答生成成功"
        elif path == "/" and code == 200:
            status_msg = "200 接続成功(ブラウザからのアクセス)"
        elif path == "/" and code == 304:
            status_msg = "304 キャッシュ再使用(再読み込みを検知)"
        elif path == "/favicon.ico" and code == 404:
            status_msg = "404 ページアイコン欠如"
        else:
            status_msg = f"未定義通信　要求： {method} {path} → 結果： {code}"

        print(f"{GREEN}{client_ip} - [{timestamp}] {status_msg}{RESET}")


def get_current_google_search_num():
    """検索結果の取得数を取得（設定ファイルから）"""
    try:
        from core.settings_manager import get_setting
        n = get_setting("search.result_count", GOOGLE_SEARCH_NUM)
        return max(1, min(10, int(n)))  # 1-10にクランプ
    except Exception:
        return max(1, min(10, int(GOOGLE_SEARCH_NUM)))

def google_search(query, num=None):
    """Google検索。numが指定されていればそれを使用、なければ設定値"""
    url = f"https://www.googleapis.com/customsearch/v1"
    if num is None:
        num = get_current_google_search_num()
    else:
        num = max(1, min(10, int(num)))
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CX,
        "q": query,
        "num": num,
        "hl": "ja"
    }
    try:
        res = requests.get(url, params=params)
        results = res.json().get("items", [])
        output = ""
        for item in results:
            title = item.get("title")
            snippet = item.get("snippet")
            link = item.get("link")
            output += f"🔹{title}\n{snippet}\n{link}\n\n"
        return output.strip() if output else "検索結果は見つからなかったわ。"
    except Exception as e:
        return f"検索エラー: {e}"


def extract_youtube_video_id(url: str) -> str | None:
    """YouTube系URLから videoId を抽出する。"""
    if not url:
        return None

    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.strip("/")

        candidates = []
        if host in ("youtu.be", "www.youtu.be"):
            candidates.append(path.split("/", 1)[0])
        elif host.endswith("youtube.com"):
            if path == "watch":
                candidates.append(parse_qs(parsed.query).get("v", [None])[0])
            elif path.startswith("shorts/"):
                candidates.append(path.split("/", 1)[1].split("/", 1)[0])
            elif path.startswith("embed/"):
                candidates.append(path.split("/", 1)[1].split("/", 1)[0])
            elif path.startswith("live/"):
                candidates.append(path.split("/", 1)[1].split("/", 1)[0])

        for candidate in candidates:
            if candidate and re.fullmatch(r"[\w-]{11}", candidate):
                return candidate
    except Exception:
        return None

    return None


def build_youtube_embed_url(video_id: str, autoplay: bool = False, mute: bool = False) -> str:
    """埋め込み再生用のYouTube URLを構築する。"""
    params = [
        "rel=0",
        "modestbranding=1",
        "playsinline=1",
    ]
    if autoplay:
        params.append("autoplay=1")
    if mute:
        params.append("mute=1")
    return f"https://www.youtube.com/embed/{video_id}?{'&'.join(params)}"


def search_youtube_videos(query: str, max_items: int = 5) -> list[dict]:
    """
    既存の Google Custom Search を使って YouTube 動画候補を取得する。
    APIキーは既存の検索設定を流用する。
    """
    max_items = max(1, min(10, int(max_items)))
    search_query = f"{query} (site:youtube.com/watch OR site:youtu.be OR site:youtube.com/shorts)"

    try:
        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": GOOGLE_API_KEY,
                "cx": GOOGLE_CX,
                "q": search_query,
                "num": max_items,
                "hl": "ja",
            },
            timeout=15,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
    except Exception as e:
        print(f"[YOUTUBE] 検索エラー: {e}")
        return []

    candidates = []
    seen_video_ids = set()

    for item in items:
        link = (item.get("link") or "").strip()
        video_id = extract_youtube_video_id(link)
        if not video_id or video_id in seen_video_ids:
            continue

        pagemap = item.get("pagemap") or {}
        thumbnails = pagemap.get("cse_thumbnail") or []
        images = pagemap.get("cse_image") or []
        thumbnail = ""
        if thumbnails:
            thumbnail = thumbnails[0].get("src", "")
        elif images:
            thumbnail = images[0].get("src", "")
        if not thumbnail:
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        title = (item.get("title") or "").strip()
        title = re.sub(r"\s*-\s*YouTube\s*$", "", title, flags=re.IGNORECASE)

        candidates.append({
            "video_id": video_id,
            "title": title or f"YouTube動画 {video_id}",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail": thumbnail,
            "snippet": (item.get("snippet") or "").strip(),
            "source_url": link,
        })
        seen_video_ids.add(video_id)

    return candidates

# --- Google News RSS（NEWSコマンド用）---
def google_news_search(query: str, max_items: int = 20):
    """
    Google News RSSでニュースを取得（queryベース）。
    APIキー不要、検索回数制限なし。
    """
    q = query.strip()
    encoded = quote_plus(q)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        root = ElementTree.fromstring(r.content)
        # RSS 2.0: channel > item
        ns = {"media": "http://search.yahoo.com/mrss/"}
        items = root.findall(".//item") or root.findall("channel/item")
        output_parts = []
        count = 0
        for item in items:
            if count >= max_items:
                break
            title_el = item.find("title")
            link_el = item.find("link")
            pub_el = item.find("pubDate")
            source_el = item.find("source")
            title = (title_el.text or "").strip() if title_el is not None else ""
            link = (link_el.text or "").strip() if link_el is not None else ""
            pub_str = (pub_el.text or "").strip() if pub_el is not None else ""
            source = (source_el.text or "").strip() if source_el is not None else ""
            if title or link:
                line = f"🔹{title}\n{link}"
                if source:
                    line += f"\n出典: {source}"
                output_parts.append(line)
                count += 1
        if not output_parts:
            return "該当するニュースは見つかりませんでした。"
        return "\n\n".join(output_parts)
    except Exception as e:
        return f"ニュース取得エラー: {e}"


def resolve_google_news_url(source_url: str, timeout: int = 20) -> str:
    """
    Google News の中継URLを元記事URLへ解決する。

    解決できない場合は安全側で元のURLを返す。
    """
    if not source_url or not isinstance(source_url, str):
        return source_url

    try:
        parsed = urlparse(source_url)
        path_parts = parsed.path.split("/")
        if parsed.hostname != "news.google.com" or len(path_parts) < 2 or path_parts[-2] != "articles":
            return source_url

        article_id = path_parts[-1]
        headers = {"User-Agent": "Mozilla/5.0"}

        article_html = None
        for article_url in (
            f"https://news.google.com/articles/{article_id}",
            f"https://news.google.com/rss/articles/{article_id}",
        ):
            try:
                response = requests.get(article_url, headers=headers, timeout=timeout)
                response.raise_for_status()
                article_html = response.text
                break
            except requests.RequestException:
                continue

        if not article_html:
            return source_url

        signature_match = re.search(r'data-n-a-sg="([^"]+)"', article_html)
        timestamp_match = re.search(r'data-n-a-ts="([^"]+)"', article_html)
        if not signature_match or not timestamp_match:
            return source_url

        signature = signature_match.group(1)
        timestamp = timestamp_match.group(1)
        payload = [[[
            "Fbv4je",
            (
                f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"JP:ja",null,1,'
                f'null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
                f'"{article_id}",{timestamp},"{signature}"]'
            ),
            None,
            "generic",
        ]]]

        decode_response = requests.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "User-Agent": "Mozilla/5.0",
            },
            data="f.req=" + quote(json.dumps(payload)),
            timeout=timeout,
        )
        decode_response.raise_for_status()

        response_parts = decode_response.text.split("\n\n", 1)
        if len(response_parts) < 2:
            return source_url

        decoded_payload = json.loads(response_parts[1])
        if not decoded_payload or len(decoded_payload[0]) < 3:
            return source_url

        resolved_url = json.loads(decoded_payload[0][2])[1]
        return resolved_url or source_url
    except Exception:
        return source_url

def load_muniCd_dict(csv_path: str) -> dict:
    """
    muniCd.csv を読み込んで {コード表記: 自治体名} の辞書を作る。
    例）'01101' と '1101' の両方で引けるようにキーを二重登録。
    """
    mapping = {}
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = str(row["muniCd"]).strip()
            name = normalize_location_text(row["chiriin_city_name"])
            # ゼロ詰め/非ゼロ詰めの両対応
            mapping[raw] = name
            mapping[raw.zfill(5)] = name
            mapping[raw.lstrip("0")] = name
    return mapping

def _pick(*vals):
    for v in vals:
        if v:
            return v
    return None

PREF_MAP = {
    "01":"北海道","02":"青森県","03":"岩手県","04":"宮城県","05":"秋田県","06":"山形県","07":"福島県",
    "08":"茨城県","09":"栃木県","10":"群馬県","11":"埼玉県","12":"千葉県","13":"東京都","14":"神奈川県",
    "15":"新潟県","16":"富山県","17":"石川県","18":"福井県","19":"山梨県","20":"長野県",
    "21":"岐阜県","22":"静岡県","23":"愛知県","24":"三重県",
    "25":"滋賀県","26":"京都府","27":"大阪府","28":"兵庫県","29":"奈良県","30":"和歌山県",
    "31":"鳥取県","32":"島根県","33":"岡山県","34":"広島県","35":"山口県",
    "36":"徳島県","37":"香川県","38":"愛媛県","39":"高知県",
    "40":"福岡県","41":"佐賀県","42":"長崎県","43":"熊本県","44":"大分県","45":"宮崎県","46":"鹿児島県","47":"沖縄県",
}

def _pref_name_from_muniCd(muni_cd: str) -> str:
    code = str(muni_cd).zfill(5)[:2]
    return PREF_MAP.get(code, "")

def latlon_to_address(lat: float, lon: float, muniCd_dict: dict, timeout: int = None):
    """
    国土地理院のリバースジオコーダで muniCd と丁目等を取得。
    muniCd → 市区名（CSV）を引いて「（場合により都道府県＋）市区町村＋丁目」を返す。
    戻り値: (full_addr: str, elapsed_sec: float)
    """
    # timeoutがNoneの場合は設定ファイルから取得
    if timeout is None:
        timeout = get_current_gps_timeout()
    
    t0 = time.time()
    url = f"https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat={lat}&lon={lon}"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    js = r.json()
    res = js.get("results", {}) or {}

    muni_cd = str(res.get("muniCd", "")).strip()
    detail = normalize_location_text("".join([res.get(k, "") for k in ("lv01Nm", "lv02Nm", "lv03Nm")]))

    keys = (muni_cd, muni_cd.zfill(5), muni_cd.lstrip("0"))
    city = _pick(muniCd_dict.get(keys[0]),
                 muniCd_dict.get(keys[1]),
                 muniCd_dict.get(keys[2]))
    city = normalize_location_text(city)

    pref = normalize_location_text(_pref_name_from_muniCd(muni_cd))

    if city:
        # 市/区はプレフィックスなし、町/村や郡を含む場合は都道府県を付ける
        needs_pref = ("郡" in city) or city.endswith(("町", "村"))
        full_addr = f"{pref}{city}{detail}" if (needs_pref and pref) else f"{city}{detail}"
    else:
        full_addr = (pref + detail) if (pref and detail) else (detail or "（住所不明）")

    elapsed = time.time() - t0
    full_addr = normalize_location_text(full_addr) or "（住所不明）"
    print(YELLOW_BRIGHT + "[GPS]位置情報自動入力     " + RESET + full_addr )
    return full_addr, elapsed