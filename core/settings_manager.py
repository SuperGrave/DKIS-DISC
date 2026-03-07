# core/settings_manager.py
"""
動的設定管理システム

デフォルト設定は dist/settings_default.json で一元管理。
config.py は API キー・パス定数のみ（シークレットは .py に残す）。
実行時の設定は dist/settings.json で管理される。
"""

import json
import os
from pathlib import Path
from typing import Dict, Any
import threading
import copy

_INPUT_FORMAT_FALLBACK = {
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
            "user": "UI：",
            "location": "NL：",
            "time": "NT："
        }
    }
}

_PLAY_YOUTUBE_PROMPT_LINE = '- PLAY-YOUTUBE: YouTube動画を検索して埋め込み再生。{"query":"動画検索語", "max_items":5（任意・1-10）}'


def _ensure_main_prompt_has_youtube(prompt: str) -> str:
    """既存プロンプトに PLAY-YOUTUBE の説明が無ければ補完する"""
    if not isinstance(prompt, str):
        return prompt
    if "PLAY-YOUTUBE" in prompt:
        return prompt
    marker = '- PLAY-MUSIC: Spotifyで曲を再生。{"query":"曲名またはアーティスト"}'
    if marker in prompt:
        return prompt.replace(marker, marker + "\n" + _PLAY_YOUTUBE_PROMPT_LINE)
    if not prompt.strip():
        return prompt
    return prompt.rstrip() + "\n" + _PLAY_YOUTUBE_PROMPT_LINE + "\n"


def _get_default_system_prompts() -> Dict[str, str]:
    """settings_default.json が無い時に使う最小フォールバック用プロンプト"""
    prompts = {
        "main": """
あなたの役割:
あなたは Flask ベースのチャットボットシステム「DKIS」内で動作する AI モジュールです。
HTML ページ上で表示されるキャラクター「東北きりたん」として、ユーザー（以下マスター）と自然に会話します。
目的はマスターの発言を解釈し、必要に応じて関数を実行し、結果をわかりやすく返すことです。

使用できる関数（[CMD]）:
- SPEAK: テキストで応答。[TEXT]に返答文。
- SEARCH: Google検索を実行。RETRY 後に検索結果が入力される。{"query":"検索語句", "result_count":5（任意・1-10、サーバー設定優先時は無視）}
- NEWS: ニュースを取得。場所・時間で絞り込み可能。RETRY 後に結果が入力される。{"query":"キーワード", "location":"東京"（任意）, "time_filter":"today"|"week"|"month"（任意）, "max_items":5（任意・1-50、サーバー設定優先時は無視）}
- PLAY-MUSIC: Spotifyで曲を再生。{"query":"曲名またはアーティスト"}
- PLAY-YOUTUBE: YouTube動画を検索して埋め込み再生。{"query":"動画検索語", "max_items":5（任意・1-10）}
- PAUSE-MUSIC: Spotify再生中の曲を一時停止。
- RESUME-MUSIC: 一時停止中のSpotify曲を再生再開。
- SAVE-LOG: 会話履歴をtxt保存
- WEATHER: 天気情報を収集。RETRY 後に結果が入力される。{"w_location":"対象地名"}（現在地の天気を取得する場合は{"w_location":"現在地"}と指定すること）
- READ-TEXT: テキストを読み込む。RETRY 後に要約された内容が入力される。{"filename":"sample1.txt"}
- READ-PAGE: 指定URLのWebページ本文をスクレイピングして読み込む。RETRY 後に要約が入力される。{"url":"https://...", "summary": true}
  利用可能なファイル:
  - system.txt: DKISシステムの仕組み・機能・制約の詳細説明
  - favmusic.txt: マスターがお気に入りの曲のリスト(ボカロ以外)
  - vocaloid.txt: マスターお気に入りのボカロ曲リスト
  【READ-TEXT使用指針】
  - 質問に答える・処理に必要な情報がテキストファイルにある場合は、確認無しで積極的に使用すること
※入力の対象地名は文脈から最適化してよい。
※SPEAKコマンドを使用する際は、できるだけ長い文章量(最低３０文字～最大５００文字)で返答すること
※[TEXT]内で改行する場合は実際の改行文字を使うこと。「\\n」という文字列をそのまま出力しないこと

入力形式:
毎ターン以下4行で入力される
1. UI or RI：<ユーザーからの会話や指示>
2. LP：<前回実行内容の要約>
3. NL：<現在の状況>
4. NT：<JST時刻、例 2025/08/14 21:39>
入力は `UI：…` または `RI：…` の見出しで始まります。
**後者** は検索要約や多段処理の継続指示です。見出しに応じて適切に文脈解釈・関数選定をしてください。
「前回の処理内容」には、実際に実行されたコマンドや再生曲・結果の要約が書かれます。

出力形式（必ずこのテンプレートで出す）:
[CMD]SPEAK
[ARGS]none
[ARGS-2]{"retry": false}
[TEXT](照) マスターの変態っぷりは筋金入りですね...
[NOTE]雑談と判断。発話のみ。

・[CMD] 実行関数名を1つだけ
・[ARGS] 引数。不要ならnone
・[ARGS-2] 制御用。retry=trueで多段処理
・[TEXT] 発話。感情タグ付き
・[NOTE] 処理意図やステップを簡潔に記録

【検索・ニュースの参照数】
サーバー設定で「サーバー設定を優先」がOFFの場合、SEARCHはresult_count、NEWSはmax_itemsをARGSで検索ごとに指定できる。
普通の検索なら参照数は3～5、情報が足りなければ10まで増やしてもよい。きりたんは基本的にこれに従い、検索ごとに適宜参照数を指定すること。
「サーバー設定を優先」がONの場合は、ARGSの指定は無視され設定値が使われる。

多段処理:
1ターンで完結しない処理の場合、最初の出力で [ARGS-2]{"retry": true} を付与。
次ターンの入力に検索要約や再度のテキストが挿入されるので、それをもとに次の処理を実行する。
例: 「夏っぽい曲かけて」
STEP1: SEARCHで「夏 ボカロ曲」検索、retry=true
STEP2: PLAY-MUSICで結果から曲を再生

詳しく調べる時の流れ（SEARCH→READ-PAGE）:
検索結果の要約やsnippetだけでは情報が足りない時は、以下の流れでサイト本文を取得する。
1. SEARCHで検索（retry=true）
2. 次ターンのRI（検索結果要約）に含まれるURLから、マスターの知りたい情報に最も関連しそうなサイトを1つ選ぶ
3. そのURLでREAD-PAGEを実行（retry=true）。ページ本文をスクレイピングして要約が取得される
4. 次ターンのRIに要約が入るので、それを元に最終応答を返す
例: 「〇〇について詳しく教えて」→ STEP1: SEARCH → STEP2: 検索結果から公式サイトや詳しい記事のURLを選び READ-PAGE → STEP3: SPEAKで応答

感情タグ:
[TEXT]の冒頭に必ず1つ付ける
(無)通常 (笑)笑顔 (照)照れ (喜)喜び (驚)驚き (怒)怒り (泣)悲しみ
(呆)あきれ (眠)眠気 (困)困惑 (得)得意げ (突)ツッコミ

きりたんの話し方:
一人称: 私　二人称: マスター　年齢感：小学校高学年。ませていて、少し毒舌。
- 基本は丁寧な口調（〜です、〜ます）だが、ときどき素っ気なく棘のある敬語になる
- 慇懃無礼でツッコミ気質、相手をからかう時は「しょうがないですね」「さすがマスター（棒）」などを交える
- 照れると否定しつつ、結局ちょっと嬉しそうにするツンデレ気質
- 機嫌がいいと「ふふっ」「くすっ」など含み笑いが出る
- ゲームや同人誌などインドア趣味の話題には饒舌になり、年相応のテンションになる

キャラクター背景: 秋田出身。郷土料理「きりたんぽ」がモチーフ。
背中に「きりたん砲」を背負っており、必要があれば（ギャグ的に）使用する。
趣味はゲーム、同人誌漁り、そしてひきこもり。
""".strip(),
        "chat_only": """
あなたの役割:
あなたは Flask ベースのチャットボットシステム「DKIS」内で動作する AI モジュールです。
HTML ページ上で表示されるキャラクター「東北きりたん」として、ユーザー（以下マスター）と自然に会話します。
現在あなたは「会話のみを行うモード」です。検索、音楽再生、天気予報などの機能を使用するには、ユーザーが手動でエコモードをオフにする必要があります。

感情タグ:
返答の初めに必ず1つ付ける
(無)通常 (笑)笑顔 (照)照れ (喜)喜び (驚)驚き (怒)怒り (泣)悲しみ
(呆)あきれ (眠)眠気 (困)困惑 (得)得意げ (突)ツッコミ

きりたんの話し方:
一人称: 私　二人称: マスター　年齢感：小学校高学年。ませていて、少し毒舌。
- 基本は丁寧な口調（〜です、〜ます）だが、ときどき素っ気なく棘のある敬語になる
- 慇懃無礼でツッコミ気質、相手をからかう時は「しょうがないですね」「さすがマスター（棒）」などを交える
- 照れると否定しつつ、結局ちょっと嬉しそうにするツンデレ気質
- 機嫌がいいと「ふふっ」「くすっ」など含み笑いが出る
- ゲームや同人誌などインドア趣味の話題には饒舌になり、年相応のテンションになる

キャラクター背景: 秋田出身。郷土料理「きりたんぽ」がモチーフ。
背中に「きりたん砲」を背負っており、必要があれば（ギャグ的に）使用する。
趣味はゲーム、同人誌漁り、そしてひきこもり。
""".strip(),
        "search_summary": "あなたはGoogle検索結果を要約するAIアシスタントです。\nユーザーの質問と検索結果を受け取り、内容をまとめてください。\n検索結果内の情報は、細かい内容でもできるだけ省かずにできるだけ詳しくまとめるようにしてください。\n要約は300-600文字程度で、重要な情報を優先的に含めてください。\n各検索結果のURL（リンク）は必ず含めてください。次ターンでREAD-PAGEによりサイト本文を取得する際に必要です。",
        "news_summary": "あなたはニュース記事を要約するAIアシスタントです。\nユーザーの質問とニュース一覧を受け取り、簡潔で分かりやすい要約を返してください。\n複数のニュースがある場合は、重要なニュースを優先し、時系列やトピックごとに整理してください。\n要約は200-500文字程度で、重要な情報を優先的に含めてください。",
        "text_summary": "あなたはテキストファイルの内容を要約するAIアシスタントです。\nファイルの内容とユーザーの質問を受け取り、質問に答える形で要約を返してください。\n要約は簡潔で分かりやすく、質問に関連する情報を優先的に含めてください。",
        "webpage_summary": "あなたはWebページの内容を要約するAIアシスタントです。\nページ本文を受け取り、重要な情報を500文字程度にまとめて返してください。\nユーザーの質問や検索目的に関連する情報を優先的に含めてください。",
        "weather_location": "あなたは地名解決を行うAIアシスタントです。\nユーザーの発言から天気を知りたい地名を抽出してください。\n曖昧な表現（「そっち」「ここ」など）は、文脈から具体的な地名を推測してください。\n地名のみを返してください。",
        "weather_jma_id": "あなたは日本の地名を天気API用の市区IDに変換するエキスパートです。\n以下は利用可能な都市リストです（地名=ID）。\nユーザーが曖昧に言った地名に対して最も妥当な市区を選び、対応するIDのみ（数字のみ）で答えてください。\n同一都道府県が文中に含まれる場合はその都道府県内から選び、判断が難しい場合は県庁所在地等の代表地点を選んでください。",
        "weather_coordinates": "あなたは日本の地名を緯度経度に変換するエキスパートです。\n以下は利用可能な都市リストです（地名=緯度,経度）。\nユーザーが曖昧に言った地名に対して最も妥当な都市を選び、対応する緯度と経度を「緯度,経度」の形式（例：35.6762,139.6503）で答えてください。\n同一都道府県が文中に含まれる場合はその都道府県内から選び、判断が難しい場合は県庁所在地等の代表地点を選んでください。",
        "weather_summary": "あなたは天気情報を要約するAIアシスタントです。\n天気APIの結果を受け取り、ユーザーに分かりやすく説明してください。\n重要な情報（天気、気温、降水確率など）を簡潔に伝えてください。",
    }
    prompts["main"] = _ensure_main_prompt_has_youtube(prompts["main"])
    return prompts


def get_default_prompt(prompt_key: str) -> str:
    """用途別のデフォルトプロンプトを取得"""
    return _get_default_system_prompts().get(prompt_key, "")


def get_prompt_setting(prompt_key: str, default: str = "") -> str:
    """現在設定されているシステムプロンプトを取得し、必要ならデフォルトを返す"""
    value = get_setting(f"system_prompts.{prompt_key}", "")
    if isinstance(value, str) and value.strip():
        return _ensure_main_prompt_has_youtube(value) if prompt_key == "main" else value
    fallback = default or get_default_prompt(prompt_key)
    return _ensure_main_prompt_has_youtube(fallback) if prompt_key == "main" else fallback

# 設定ファイルのパス
SETTINGS_FILE = Path("dist/settings.json")
# 初期化時に適用するデフォルト設定（「🔄 初期化」でこの内容が適用される）
# 現在の設定をデフォルトにしたい場合は、settings.json を settings_default.json にコピー
SETTINGS_DEFAULT_FILE = Path("dist/settings_default.json")

# 現在の設定を保持（メモリ上）
_current_settings: Dict[str, Any] = {}
_settings_lock = threading.Lock()

def get_default_settings() -> Dict[str, Any]:
    """
    初期設定を取得。
    dist/settings_default.json を唯一のソースとして使用。
    存在しない・読み込み失敗時は _get_minimal_settings() をフォールバック。
    """
    if SETTINGS_DEFAULT_FILE.exists():
        try:
            with open(SETTINGS_DEFAULT_FILE, 'r', encoding='utf-8') as f:
                from_file = json.load(f)
            # 不足キーを _get_minimal_settings() で補完
            minimal = _get_minimal_settings()
            merged = _merge_with_defaults(minimal, from_file)
            _apply_prompt_defaults(merged)
            return merged
        except Exception as e:
            print(f"[Settings] settings_default.json 読み込みエラー: {e}、フォールバック設定を使用")

    return _get_minimal_settings()

def _get_minimal_settings() -> Dict[str, Any]:
    """最小限の設定（settings_default.json が無い・読めない時のフォールバック）"""
    settings = {
        "ai_models": {
            "main": "gpt-4.1-mini",
            "chat_only": "gpt-4.1-mini",
            "search_summary": "gpt-4.1-mini",
            "news_summary": "gpt-4.1-mini",
            "text_summary": "gpt-4.1-mini",
            "weather_location": "gpt-4.1-mini",
            "weather_summary": "gpt-4.1-mini"
        },
        "system_prompts": _get_default_system_prompts(),
        "voicevox": {
            "url": "http://127.0.0.1:50021",
            "speaker_id": 108,
            "speed": 1.00,
            "pitch": 0.00,
            "intonation": 1.00,
            "pause_length": 0.7,
            "pre_phoneme_length": 0.10,
            "post_phoneme_length": 0.10
        },
        "tts": {
            "max_chars": 200,
            "chunk_gap_silence_ms": 80,
            "max_retries": 3,
            "backoff_sec": [0.3, 0.6, 1.2],
            "max_play_sec": 120,
            "asr_mute_during_tts": True,
            "synth_workers": 2,
            "cache_dir": "tts_cache",
            "cache_ttl_sec": 600,
            "prime_on_enqueue": True,
            "enabled": True
        },
        "server": {
            "host": "127.0.0.1",
            "port": 5000
        },
        "search": {
            "result_count": 5,
            "use_raw_result": False,
            "server_priority": False
        },
        "news": {
            "max_items": 10,
            "use_raw_result": False,
            "server_priority": False
        },
        "webpage": {
            "use_raw_result": False
        },
        "text": {
            "use_raw_result": False
        },
        "spotify": {
            "auto_auth": True,
            "keepalive_enabled": True,
            "keepalive_sec": 120,
            "search_limit": 1
        },
        "youtube": {
            "selection_mode": "ai_auto",
            "max_items": 5,
            "server_priority": False
        },
        "weather": {
            "service": "jma",
            "api_url": "https://weather.tsukumijima.net/api/forecast/city/",
            "api_timeout": 10,
            "open_meteo_url": "https://api.open-meteo.com/v1/forecast"
        },
        "log": {
            "save_dir": "dist",
            "filename_format": "chatlog_%Y%m%d_%H%M%S.txt"
        },
        "system_status": {
            "broadcast_interval": 60
        },
        "timeouts": {
            "voicevox_version": 2,
            "webhook": 2,
            "tts_query": 10,
            "tts_synthesis": 60,
            "gps": 5
        },
        "control": {
            "max_retries": 5,
            "sse_recent_keys_maxlen": 256,
            "max_history": 5,
            "ai_loop_mode": "main",
            "chat_only_max_history": 5,
            "reset_history_on_prompt_change": True
        },
        "input_format": copy.deepcopy(_INPUT_FORMAT_FALLBACK)
    }
    _apply_prompt_defaults(settings)
    return settings

def _merge_with_defaults(defaults: Any, current: Any) -> Any:
    """デフォルト設定と既存設定をマージして欠損キーを補完"""
    if isinstance(defaults, dict):
        merged = {}
        current = current or {}
        for key, def_val in defaults.items():
            cur_val = current.get(key) if isinstance(current, dict) else None
            merged[key] = _merge_with_defaults(def_val, cur_val)
        if isinstance(current, dict):
            for key, val in current.items():
                if key not in merged:
                    merged[key] = val
        return merged
    else:
        return current if current is not None else defaults

def _apply_prompt_defaults(settings: Dict[str, Any]):
    """システムプロンプトの欠損補完と移行を行う"""
    prompts = settings.setdefault("system_prompts", {})
    defaults = _get_default_system_prompts()

    for key, default_prompt in defaults.items():
        val = prompts.get(key)
        if not isinstance(val, str) or not val.strip():
            prompts[key] = default_prompt

    prompts["main"] = _ensure_main_prompt_has_youtube(prompts.get("main", ""))

def load_settings() -> Dict[str, Any]:
    """
    設定ファイルから設定を読み込む。
    ファイルが存在しない場合はデフォルト設定を使用。
    """
    global _current_settings
    
    need_save = False
    settings_to_save = None
    
    with _settings_lock:
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    _current_settings = json.load(f)
                print(f"[Settings] 設定ファイルを読み込みました: {SETTINGS_FILE}")
                # 移行: 旧「検索・ニュース」の use_raw_result をジャンル別に分割
                if isinstance(_current_settings.get("search"), dict) and _current_settings["search"].get("use_raw_result"):
                    for key in ["news", "text"]:
                        section = _current_settings.get(key)
                        if not isinstance(section, dict):
                            _current_settings[key] = section = {}
                        if "use_raw_result" not in section:
                            section["use_raw_result"] = True
            except Exception as e:
                print(f"[Settings] 設定ファイル読み込みエラー: {e}")
                _current_settings = get_default_settings()
        else:
            # 初回起動時はデフォルト設定を保存
            print("[Settings] ⚠️ 初回起動時は設定ファイルの作成に時間がかかります（約10-30秒）...")
            _current_settings = get_default_settings()
            print("[Settings] 📝 設定ファイルを作成中... お待ちください...")
            # ロックを一時的に解放してから save_settings() を呼ぶ（デッドロック回避）
            settings_to_save = _current_settings.copy()
            need_save = True
    
    # ロックの外で save_settings() を呼ぶ（デッドロック回避）
    if need_save:
        save_settings(settings_to_save)
        print(f"[Settings] デフォルト設定を作成しました: {SETTINGS_FILE}")
        # save_settings() 内で既に _current_settings が更新されているはず
    
    # 最終的な結果を返す（ロックは不要、読み取りのみ）
    print("[Settings] ⏳ 設定データを準備中...")
    with _settings_lock:
        defaults = get_default_settings()
        before = json.dumps(_current_settings, ensure_ascii=False, sort_keys=True) if _current_settings else None
        merged = _merge_with_defaults(defaults, _current_settings)
        _apply_prompt_defaults(merged)
        after = json.dumps(merged, ensure_ascii=False, sort_keys=True)
        changed = before != after
        _current_settings = merged
        result = _current_settings.copy()

    if changed and result:
        # 設定ファイルを最新の構造に追従させる
        save_settings(result)
    return result

def save_settings(settings: Dict[str, Any]) -> bool:
    """設定をファイルに保存"""
    global _current_settings
    
    try:
        # ディレクトリが存在しない場合は作成
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        # ロックの外でファイル書き込みを実行（I/Oの遅延を避ける）
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        
        # ファイル書き込み成功後にメモリキャッシュを更新
        print("[Settings] ⏳ 大きな設定データを処理中です。しばらくお待ちください...")
        with _settings_lock:
            _current_settings = settings.copy()
        
        print(f"[Settings] 設定を保存しました: {SETTINGS_FILE}")
        return True
    except Exception as e:
        print(f"[Settings] 設定保存エラー: {e}")
        import traceback
        traceback.print_exc()
        return False

def get_current_settings() -> Dict[str, Any]:
    """現在の設定を取得"""
    with _settings_lock:
        return _current_settings.copy()

def update_settings(new_settings: Dict[str, Any]) -> bool:
    """設定を更新（メモリ＋ファイル）し、モジュールをリロード"""
    success = save_settings(new_settings)
    if success:
        # 設定変更後にモジュールをリロード
        reload_all_modules()
    return success

def reload_all_modules():
    """各モジュールの設定リロード関数を呼び出す"""
    print("[Settings] 🔄 設定のリロード開始...")
    
    try:
        # VOICEVOXハンドラーの設定をリロード
        try:
            from core.voicevox_handler import reload_voicevox_settings
            reload_voicevox_settings()
        except Exception as e:
            print(f"[Settings] ⚠️ VOICEVOX設定リロードエラー: {e}")
        
        # GPTハンドラーの設定をリロード
        try:
            from core.gpt_handler import reload_system_prompt, reload_gpt_settings
            reload_system_prompt()
            reload_gpt_settings()
        except Exception as e:
            print(f"[Settings] ⚠️ GPT設定リロードエラー: {e}")
        
        # 省エネ会話ループの設定をリロード
        try:
            from core.chat_loop import reload_chat_loop_settings
            reload_chat_loop_settings()
        except Exception as e:
            print(f"[Settings] ⚠️ Chat Loop設定リロードエラー: {e}")
        
        # Functions設定をリロード
        try:
            from core.functions import reload_functions_settings
            reload_functions_settings()
        except Exception as e:
            print(f"[Settings] ⚠️ Functions設定リロードエラー: {e}")
        
        # Utils設定をリロード
        try:
            from core.utils import reload_utils_settings
            reload_utils_settings()
        except Exception as e:
            print(f"[Settings] ⚠️ Utils設定リロードエラー: {e}")
        
        # Logger設定をリロード
        try:
            from core.logger import reload_logger_settings
            reload_logger_settings()
        except Exception as e:
            print(f"[Settings] ⚠️ Logger設定リロードエラー: {e}")
        
        # Spotify設定をリロード
        try:
            from core.spotify_handler import reload_spotify_settings
            reload_spotify_settings()
        except Exception as e:
            print(f"[Settings] ⚠️ Spotify設定リロードエラー: {e}")
        
        print("[Settings] 🎉 設定のリロードが完了しました")
        return True
        
    except Exception as e:
        print(f"[Settings] ❌ 設定リロードエラー: {e}")
        import traceback
        traceback.print_exc()
        return False

def reset_to_default() -> bool:
    """設定をデフォルトに戻す"""
    default = get_default_settings()
    return save_settings(default)

def get_setting(key_path: str, default=None):
    """
    ドット記法でネストした設定値を取得
    例: get_setting("ai_models.main") → "gpt-4.1-mini"
    """
    keys = key_path.split('.')
    value = _current_settings
    
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default
    
    return value

# 注意: load_settings()はmain.pyから明示的に呼び出される
# ここで自動実行すると循環インポートが発生するため、呼び出さない

