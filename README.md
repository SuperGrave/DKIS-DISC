# DKIS-LL

LINE 上で動作する **DKIS 互換の軽量 AI ボット**です。Flask の Webhook と OpenAI、`dist/settings.json` で定義されたツール（検索・ニュース・天気・ページ読込）を組み合わせます。**Web フロント・SSE・音声・メディア再生・ローカルファイル操作は含みません。**

## 作業ディレクトリ（よくあるエラー対策）

`pyproject.toml` と `.venv` は **リポジトリ（例: `DKIS-LINE`）の直下**にあります。ターミナルでそのフォルダにいることを確認してから `uv sync` / `uv run` を実行してください。`.env` も **リポジトリ直下**に置きます（テンプレートは **`.env.example`**）。

`.env` の書き方は **`変数名=値`** の1行で、**イコールの直後に値を貼り付ければそのまま動く**ことが多いです（値の前後にスペースは入れない／行末に余計な `"` は付けない）。

## 機能概要

- **`dist/settings.json`**: モデル名（`ai_models.main`）、**リトライ上限**（`control.max_retries`）、**会話履歴の長さ**（`control.max_history`。**リトライ1回ごとに user／assistant が増える**ので LINE の発話回数とは一致しません。再起動で消えるインメモリです）、検索・ニュース・天気・入力フォーマットなど。
- **`dist/system_prompt_main.txt`**: メインシステムプロンプト本体（**Markdown 見出し・コードフェンス**で記述。拡張子は `.txt` のまま）。`settings.json` の `system_prompts.main_file` でパスを指定。
- OpenAI は DKIS 形式の `[CMD]` / `[ARGS]` / `[ARGS-2]` を出力します。`SEARCH`・`NEWS`・`WEATHER`・`READ-PAGE` は **ツールの生結果を RI にそのまま載せて** 2 段目のモデル呼び出しへ進みます（中間要約用の別モデル呼び出しはありません）。
- **`[ARGS-2].retry: true` の連鎖**では、中間の `[TEXT]`（例: 「もう少し調べますね」）や **`[RT#n]コマンド:引数 tt=…`**、最終 `[TEXT]` を **処理の進行に合わせて逐次**送ります（通常は **最初の1バブルだけ `reply_message`**、続きは **`push_message`（ユーザー ID 宛）**）。長いパートは `split_line_text` で分割します。`user_id` が取れない異常系では従来どおり 1 回の `reply` にまとめます。送信上限は LINE のプランに依存します。
- **SEARCH**: Google Custom Search API（`GOOGLE_API_KEY` + `GOOGLE_CX` が必要）
- **NEWS**: Google News RSS（キー不要）
- **WEATHER**: Open-Meteo。**`w_location` が空や GPS 相当語のときは実行せず地名を聞き返す**（現在地フォールバックなし）
- **READ-PAGE**: `trafilatura` で本文抽出し、そのテキストをそのまま RI へ

## Environment Variables

### Webhook・`main.py`・本番（LINE ボットとして動かすとき）

- `LINE_CHANNEL_SECRET`
- `LINE_CHANNEL_ACCESS_TOKEN`
- `OPENAI_API_KEY`

### `dev_console.py` だけ試すとき

- **`OPENAI_API_KEY` のみ必須**（LINE の2つは未設定でも起動します）

### 任意

- `DKIS_SETTINGS_PATH`（既定はリポジトリ内の `dist/settings.json`）
- `GOOGLE_API_KEY` / `GOOGLE_CX`（`SEARCH` 用）
- `SUPABASE_URL` / `SUPABASE_KEY`（**記憶コマンド** `LIST-FILES` 等。`service_role` 推奨。未設定なら記憶系は案内エラーのみ）
- `LINE_BOOT_GREETING_USER_IDS`（**任意**。カンマ区切り `userId`。運用者向け。**省略時もマージされる**。ユーザー側は **`SET-SETTING`** の **`notify_worker_restart`** でオプトインすると、`known_line_users` に **`notify_on_restart=true`** が付いた Id にのみ定型 Push）
- `LINE_BOOT_GREETING_PUSH_STORE_LIMIT`（既定 **50**。DB の購読者リストから読む上限）
- `LINE_BOOT_GREETING_SKIP_STORED_IDS`（`1` で **オプトイン済み DB 宛先を無視**し、`LINE_BOOT_GREETING_USER_IDS` のみ）
- `PORT`（ローカル既定 `5000`。Render 等ではプラットフォームが自動設定）

## ローカル開発

リポジトリ直下の **`.python-version`** で **Python 3.12** に固定しています（`uv sync` が自動でその版を使います）。別版にしたい場合は `uv python pin <版>`。

```bash
uv sync
cp .env.example .env   # 値を編集
uv run python main.py  # Flask 開発サーバー（`main.py` の app.run）
```

本番と同じ **gunicorn** で試す場合:

```bash
uv sync
uv run gunicorn --workers 1 --threads 2 --bind "127.0.0.1:5000" main:app
```

**開発用コンソール（LINE と同じ応答経路）**  
Webhook を立てずに `AIResponder.reply` を試すときは `uv run python dev_console.py`。**このときは `.env` に `OPENAI_API_KEY` だけあれば足ります**（LINE のシークレット／トークンは不要）。リトライ連鎖では **LINE と同様にメッセージを逐次表示**します（長文は `split_line_text` で分割）。**本番デプロイ前に `dev_console.py` は削除する想定**です。  
（`.python-version` で 3.12 に揃えている想定。別版で動かす場合は LINE Bot SDK と Python の組み合わせに注意。）

## 本番デプロイ（Render.com・Blueprint）

1. このリポジトリを GitHub に push する。
2. [Render Dashboard](https://dashboard.render.com/) → **New** → **Blueprint**。
3. リポジトリを選び、ルートの **`render.yaml`** を検出して作成する。
4. サービスの **Environment** で次を **ダッシュボードから入力**する（`render.yaml` の `sync: false` のため、値は Git に書かない）。
   - `LINE_CHANNEL_SECRET`
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `OPENAI_API_KEY`
5. デプロイ後の URL を LINE Developers の **Webhook URL** に  
   `https://<サービス名>.onrender.com/webhook` の形式で設定する（**HTTPS 必須**）。
6. `GET /` が `{"ok":true,"service":"dkis-ll-bot",...}` を返せば疎通 OK。

**Render 設定の要点**

- **ビルド**: `uv` を公式インストーラで入れ、`uv sync` で依存解決（`render.yaml` 参照）。
- **起動**: `gunicorn` で **`main:app`**、`--workers 1 --threads 2`（`render.yaml` と揃える）。
- 無料プランはスリープがあり、初回応答が遅れることがあります。
- `dist/settings.json` を別パスにしたい場合は `DKIS_SETTINGS_PATH` を Render の環境変数に追加する。

---

## DKIS マルチPC開発ガイド（USB持ち運び運用）

このプロジェクトを USB メモリで持ち運びつつ、**このPC**と**別PC**の両方で安全に開発するための手順です。  
（Windows + PowerShell 前提）

## 1. PCごとに最初にやること（初回のみ）

### 必須ツール
- Git
- Python 3.12以上（このリポジトリは `.python-version` で 3.12 を既定）
- uv

### uv の確認
```powershell
uv --version
```

### 依存関係セットアップ（PCごと）
`.venv` は PC 依存なので、USBで共有しません。各PCで作り直します。

```powershell
cd "D:\DKISシリーズパッケージ\DKIS-LINE"
if (Test-Path ".venv") { Remove-Item -Recurse -Force ".venv" }
uv venv
uv sync
```

### 設定ファイル
`config.py` は機密情報を含むため Git 管理外です。必要なら作成してください。

```powershell
Copy-Item "config.example.py" "config.py"
```

## 2. 起動手順（どのPCでも共通）

```powershell
cd "D:\DKISシリーズパッケージ\DKIS-LINE"
uv sync
uv run python main.py
```

起動後は以下にアクセス:
- <http://127.0.0.1:5000>

## 3. PCを切り替える時の基本フロー

### A. 作業PCでやること（移動前）
```powershell
git checkout -b feature/xxx   # まだブランチがない場合
git add .
git commit -m "..."
git push -u origin HEAD
```

### B. 別PCでやること（作業再開時）
```powershell
cd "D:\DKISシリーズパッケージ\DKIS-LINE"
git fetch --all
git checkout feature/xxx
git pull
uv sync
uv run python main.py
```

## 4. ブランチとバージョン管理ルール（推奨）

### ブランチ名
- `feature/機能名`
- `fix/不具合名`
- `chore/保守作業名`

### バージョン（x.y.z）
- **z（パッチ）**: バグ修正、軽微な調整
- **y（マイナー）**: 後方互換ありの機能追加
- **x（メジャー）**: 後方互換なしの大きな変更

## 5. よくあるトラブルと対処

### 5-1. `git` で `dubious ownership` が出る
USBを別PCで使うと所有者SID不一致が発生することがあります。  
そのPCでリポジトリ所有権を取り直してください。

```powershell
takeown /F "D:\DKISシリーズパッケージ\DKIS-LINE" /R /D Y
```

その後に確認:
```powershell
git status
```

### 5-2. `uv sync` が壊れた `.venv` 参照で失敗する
`.venv` を削除して作り直す:

```powershell
if (Test-Path ".venv") { Remove-Item -Recurse -Force ".venv" }
uv venv
uv sync
```

### 5-3. Windowsで文字化け/Unicodeエラー
- まず `uv run python main.py` で起動し直す
- 直らない場合は PowerShell を再起動して再実行

## 6. 運用のコツ

- USB内の作業フォルダは固定パスにする（例: `D:\DKISシリーズパッケージ\DKIS-LINE`）
- 大きな変更前に必ずブランチ作成
- PC切替前に必ず `commit` + `push`
- 切替後は `pull` + `uv sync` を必ず実行
- 機密情報（`config.py` など）は GitHub に push しない
