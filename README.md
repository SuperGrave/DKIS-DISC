# DKIS-DISC

Discord 上で動作する **DKIS 互換の軽量 AI ボット**です。`discord.py` の常時接続 bot と OpenAI、`dist/settings.json` で定義されたツール（検索・ニュース・天気・ページ読込）を組み合わせます。Web フロント・SSE・音声・メディア再生・ローカルファイル操作は含みません。

## 機能概要

- **`dist/settings.json`**: モデル名、リトライ上限、会話履歴の長さ、検索・ニュース・天気・入力フォーマットなど。
- **`dist/system_prompt_main.txt`**: メインシステムプロンプト本体。`settings.json` の `system_prompts.main_file` で読み込みます。
- OpenAI は DKIS 形式の `[CMD]` / `[ARGS]` / `[ARGS-2]` を出力します。
- `SEARCH` / `NEWS` / `WEATHER` / `READ-PAGE` はツール結果を RI として再投入し、必要に応じてリトライ連鎖します。
- Supabase を設定すると、記憶ファイル・共通設定・中期記憶を永続化できます。
- 起動時はデバッグルームへ、起動時刻・登録ユーザー数・本日トークン数などのステータスを送信します。デバッグルーム未設定時は `DISCORD_CHANNEL_ID` へ送信します。
- 長文返信は Discord の 2,000 文字制限に合わせて `split_line_text` で分割し、順番に送信します。

## Environment Variables

### 必須

- `DISCORD_BOT_TOKEN`
- `DISCORD_CHANNEL_ID`
- `OPENAI_API_KEY`

### 任意

- `DKIS_SETTINGS_PATH`（既定はリポジトリ内の `dist/settings.json`）
- `GOOGLE_API_KEY` / `GOOGLE_CX`（`SEARCH` 用）
- `SUPABASE_URL` / `SUPABASE_KEY`（記憶コマンド用。未設定なら記憶系は案内エラーのみ）
- `DISCORD_DAILY_MESSAGE_CHANNEL_ID`（まいにちメッセージ/起動通知の既定送信先。未設定時は互換のため `DISCORD_CHANNEL_ID` を使います）
- `DISCORD_COMMAND_REGISTRATION_SCOPE`（既定 `global`。`guild` は即時反映テスト用、`both` は二重表示に注意）
- `DISCORD_RESTART_ALLOWED_USER_IDS`（カンマ区切り。未設定時は Discord サーバー管理者だけが再起動可能）
- `DISCORD_OPERATOR_USER_IDS`（カンマ区切り。デバッグルーム設定などの operator 権限を環境変数で付与）
- `DISCORD_BOOT_GREETING_PUSH_STORE_LIMIT`（既定 `50`）
- `DISCORD_BOOT_GREETING_SKIP_STORED_IDS`（`1` で DB の起動通知オプトインを無視）
- `CHANNEL_SETTINGS_CACHE_SECONDS`（既定 `300`。`0` でチャンネル設定キャッシュ無効）
- `PORT`（Render Web Service のヘルスチェック用。既定 `5000`）
- `KEEPALIVE_URL` / `RENDER_EXTERNAL_URL`（5 分おきのセルフ ping 用）
- `KEEPALIVE_INTERVAL_SECONDS`（既定 `300`）
- `DISABLE_KEEPALIVE`（`1` でセルフ ping 無効）

## ローカル開発

```powershell
uv sync
Copy-Item ".env.example" ".env"
uv run python main.py
```

Discord Developer Portal で `MESSAGE CONTENT INTENT` を ON にしてください。これが無いと `on_message` で本文を読めません。

開発用コンソールで `AIResponder.reply` だけ試す場合は次を使います。

```powershell
uv run python dev_console.py
```

## Discord 操作

- 通常メッセージ: ボットが `AIResponder` を通して返信します。
- `/get_setting`: ユーザー別設定と現在チャンネルの表示設定を確認します。
- `/set_setting`: `value` を `true/false/1/2/3/4/5` から選び、ユーザー別設定または現在チャンネルの `process_notice` を変更します。
- `/channel_setting`: 実行したチャンネルで bot を有効/無効にします（既定は無効）。operator は `debug` でデバッグルーム化、`normal` で通常チャンネルへ戻せます。`daily` でまいにちメッセージ送信先に設定し、`daily_off` で未設定に戻せます。
- `サーバー再起動` / `再起動` / `restart` / `/restart`: 権限があれば `os.execv` でプロセスを再起動します。

権限は `visitor` / `member` / `operator` の3段階です。`visitor` は本日1,000,000トークンまででモデル変更不可、`member` は本日10,000,000トークンまで、`operator` はデバッグルーム運用向けです。`operator` は Supabase の `known_line_users.user_role` または `DISCORD_OPERATOR_USER_IDS` で付与します。

運用で最初にやること:

1. Supabase SQL Editor で `dist/supabase_schema.sql` を実行し、追加列を反映します。
2. 管理者の Discord ID を `DISCORD_OPERATOR_USER_IDS` に追加します。
3. まいにちメッセージを送りたいチャンネルで `/channel_setting value:daily` を実行します。
4. デバッグ通知を受けたいチャンネルで `/channel_setting value:debug` を実行します。

## Render デプロイ

1. GitHub に `DKIS-DISC` リポジトリを作成し、このプロジェクトを push します。
2. Render Dashboard で新しい Web Service または Blueprint を作成します。
3. 起動コマンドは `uv run python main.py` です。
4. 環境変数に `DISCORD_BOT_TOKEN`、`DISCORD_CHANNEL_ID`、`OPENAI_API_KEY` を設定します。
5. スリープ対策を使う場合は `KEEPALIVE_URL=https://<サービス名>.onrender.com/` を設定します。
6. GitHub Actions の定期 ping を使う場合は Repository Variables に `KEEPALIVE_URL` を設定します。未設定時は `https://dkis-disc.onrender.com/` を5分ごとに叩きます。

スラッシュコマンドは起動時にグローバル登録されるため、Bot を招待した各サーバーで使えます。反映を急ぎたいテストサーバーだけに登録したい場合は `DISCORD_COMMAND_REGISTRATION_SCOPE=guild` と `DISCORD_GUILD_ID` または `DISCORD_GUILD_IDS` を設定します。`both` は global/guild の同名コマンドがDiscord上で二重表示されることがあります。

`main.py` は Discord bot と同時に軽量 HTTP ヘルスチェックサーバーを起動します。Render の Web Service として動かしても `GET /` が `{"ok": true, "service": "dkis-disc-bot"}` を返します。
