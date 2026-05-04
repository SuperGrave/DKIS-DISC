# DKIS-LINE

LINE Bot 専用の軽量 Flask アプリです。Web フロントエンド、SSE、音声合成、メディア再生、ローカルファイル操作は含めません。

## 機能概要

- **`dist/settings.json`**: メインシステムプロンプト、`ai_models.main`（OpenAI モデル名）、`control.max_retries` / `control.max_history`（リトライ上限・会話履歴ターン数）、検索件数・ニュース件数・入力フォーマット（`input_format.main`）を定義します。
- OpenAI は DKIS 形式の `[CMD]` / `[ARGS]` / `[ARGS-2]` を出力します。`SEARCH`・`NEWS`・`WEATHER`・`READ-PAGE` は **ツールの生結果を RI にそのまま載せて** 2 段目のモデル呼び出しへ進みます（中間要約用の別モデル呼び出しはありません）。
- **SEARCH**: Google Custom Search API（`GOOGLE_API_KEY` + `GOOGLE_CX` が必要）
- **NEWS**: Google News RSS（キー不要）
- **WEATHER**: Open-Meteo。**`w_location` が空や GPS 相当語のときは実行せず地名を聞き返す**（現在地フォールバックなし）
- **READ-PAGE**: `trafilatura` で本文抽出し、そのテキストをそのまま RI へ

## Environment Variables

- `LINE_CHANNEL_SECRET`
- `LINE_CHANNEL_ACCESS_TOKEN`
- `OPENAI_API_KEY`
- `DKIS_SETTINGS_PATH`（任意・既定はリポジトリ内の `dist/settings.json`）
- `GOOGLE_API_KEY` / `GOOGLE_CX`（任意、`SEARCH` 用）
- `PORT`（任意、既定: `5000`）

## Run Locally

```bash
uv sync
uv run python main.py
```

LINE Developers の Webhook URL には、デプロイ先の `https://.../webhook` を設定してください。
# DKIS マルチPC開発ガイド（USB持ち運び運用）

このプロジェクトを USB メモリで持ち運びつつ、**このPC**と**別PC**の両方で安全に開発するための手順です。  
（Windows + PowerShell 前提）

## 1. PCごとに最初にやること（初回のみ）

### 必須ツール
- Git
- Python 3.10以上（推奨: 3.14系）
- uv

### uv の確認
```powershell
uv --version
```

### 依存関係セットアップ（PCごと）
`.venv` は PC 依存なので、USBで共有しません。各PCで作り直します。

```powershell
cd "D:\DKISシリーズパッケージ\DKIS"
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
cd "D:\DKISシリーズパッケージ\DKIS"
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
cd "D:\DKISシリーズパッケージ\DKIS"
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
takeown /F "D:\DKISシリーズパッケージ\DKIS" /R /D Y
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

- USB内の作業フォルダは固定パスにする（例: `D:\DKISシリーズパッケージ\DKIS`）
- 大きな変更前に必ずブランチ作成
- PC切替前に必ず `commit` + `push`
- 切替後は `pull` + `uv sync` を必ず実行
- 機密情報（`config.py` など）は GitHub に push しない
