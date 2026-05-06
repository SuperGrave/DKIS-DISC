# DKIS-LL: Supabase 移行・記憶機能 — 実装レポート

本書は、Render.com 等の **ステートレス** 環境で動作する **DKIS-LL（LINE ボット）** に、**Supabase（PostgreSQL）** を外部ストアとして接続し、本家 DKIS の **`dist/memory/*.txt` 相当**のファイル記憶と **`user_settings` 相当**のランタイム設定を実装した内容をまとめたものです。

---

## 1. 目的とスコープ

| 項目 | 内容 |
|------|------|
| 目的 | サーバー再起動・複数ワーカーでも **記憶・設定が残る** ようにする |
| 再現対象 | テキスト「ファイル」の一覧・読み・書き・追記；会話ログの書き出し；モデル名など少数設定 |
| 非対象 | LINE でユーザーがファイル添付する処理（別機能）；記憶ファイルの **削除 API／CMD**（今回未実装） |

---

## 2. 依存関係

- **`supabase` Python SDK（≥2.29）** — `uv add supabase` で追加済み、`pyproject.toml` に記載。
- 環境変数 **`SUPABASE_URL`**（Project URL）と **`SUPABASE_KEY`**（通常は **`service_role` のシークレット**）で初期化。
- **Render**：`render.yaml` にキーを **`sync: false`** で宣言。値は Dashboard の Environment で入力。

**鍵の選び方**

- **サーバー専用**なので **`anon`** より **`service_role`** を推奨（RLS をバイパスしてサーバーから読み書きする）。
- `anon` + RLS ポリシーだけで運用する場合は、本実装のクエリに合わせたポリシー設計が別途必要。

---

## 3. データベース設計（DDL）

スクリプト: **`dist/supabase_schema.sql`**

### 3.1 `memory_files`

| カラム | 型 | 説明 |
|--------|-----|------|
| `filename` | `TEXT PRIMARY KEY` | メモの名前（パストラバーサル禁止はアプリ側で検証） |
| `content` | `TEXT` | 本文 |
| `description` | `TEXT` | メタ（一覧 JSON に載せる説明文） |
| `updated_at` | `TIMESTAMPTZ` | アプリ upsert 時に ISO8601 で更新 |

### 3.2 `user_settings`

| カラム | 型 | 説明 |
|--------|-----|------|
| `setting_key` | `TEXT PRIMARY KEY` | 設定キー |
| `setting_value` | `TEXT` | 値（文字列で統一） |

### 3.3 RLS

両テーブルで **RLS を有効化**。**`service_role` は RLS をバイパス**するため、サーバーからは追加ポリシーなしで書き込み可能。

---

## 4. ソース構成と責務

| ファイル | 役割 |
|----------|------|
| `line_bot_app/supabase_store.py` | Supabase クライアントの遅延生成、`get_db_setting` / `set_db_setting`、memory の CRUD、`validate_memory_filename` |
| `line_bot_app/commands_memory.py` | `LIST-FILES`, `READ-TEXT`, `WRITE-TEXT`, `APPEND-TEXT`, `SAVE-LOG`, `GET-SETTING`, `SET-SETTING` |
| `line_bot_app/commands.py` | `ExportHooks`・`CommandServices`（`client: OpenAI`, `hooks`）と既存ツール。末尾で `MEMORY_COMMAND_HANDLERS` をマージ |
| `line_bot_app/engine.py` | `_resolve_openai_model`（DB `current_model`）、`_resolve_show_ri_text`（`show_ri_text`）、会話 **エクスポートバッファ**、RETRY 時の `[RT#…]` 表示制御、`_execute(..., user_id)` |
| `dist/system_prompt_main.txt` | モデル向けに記憶コマンドの説明を追記 |
| `dist/supabase_schema.sql` | テーブル作成用 SQL |

---

## 5. 設定キー（`user_settings`）

アプリが読み書きするキー。**`SET-SETTING` で変更できるキーはホワイトリスト**（`commands_memory._ALLOWED_SETTING_KEYS`）。

| `setting_key` | 意味 | 既定の扱い |
|---------------|------|------------|
| `current_model` | OpenAI の **チャットモデル ID** | 空なら `dist/settings.json` の `ai_models.main` |
| `show_ri_text` | **`[RT#n]コマンド:…`** を LINE に送るか | `true`（`false`/`off`/`no`/`0` で非表示） |
| `text.use_raw_result` | **READ-TEXT** で要約せず全文を RI に載せるか | `false`（true 系なら全文） |

**適用タイミング**

- **`current_model`**: 各 `_complete` 直前に `get_db_setting` で解決。
- **`show_ri_text`**: RETRY ループ内で `[RT#…]` 行を **`_emit_chunks` するか** の分岐。
- **`text.use_raw_result`**: `READ-TEXT` 実行時に参照。

---

## 6. コマンド仕様（`(TEXT, dmis_log, summary, raw_result)` と RETRY）

DKIS 形式どおり、**`retry: true` は AI 応答の `[ARGS-2]`** に依存（**ハンドラ側では自動では付けない**）。モデルが LIST のあと READ へ進む場合、`[ARGS-2]{"retry": true}` を出力する必要がある。

### 6.1 LIST-FILES

- **処理**: `memory_files` を `filename` 昇順で取得し、`filename` / `description` / `updated_at` の **JSON 配列文字列**を `summary` に格納。
- **Supabase 未設定**: エラーメッセージを `summary` に。

### 6.2 READ-TEXT

- **ARGS**: `filename` 必須。
- **処理**: 行取得 → `text.use_raw_result` が真なら `meta + 全文`、偽なら **別途 OpenAI で要約**（入力上限約 14k 文字）。
- **モデル**: `_effective_openai_model(svc)`（DB → `settings.json` の順）。

### 6.3 WRITE-TEXT / APPEND-TEXT

- **WRITE**: `filename`, `content`, 任意 `description` で upsert。`content` は上限 **MAX_MEMORY_CONTENT_CHARS**（既定 500k）で切り詰め。
- **APPEND**: 既存 `content` を読み、改行調整のうえ結合して upsert。

### 6.4 SAVE-LOG

- **ARGS**: `filename` 必須、任意 `description`。
- **処理**: **ユーザー別インメモリバッファ**の全文を `memory_files.content` に書き込み、当該ユーザーのバッファを **クリア**。
- **バッファ内容**（`engine.LineBrain`）:
  - 各 `reply` で **`USER`** 行（マスター入力テキスト）
  - 各 GPT 完了ごとに **`ASSISTANT_RAW`**（当該ターンの生 `[CMD]` ブロック含む応答）
  - RETRY のたびに assistant_raw が複数回溜まる場合あり

### 6.5 GET-SETTING / SET-SETTING

- **GET**: `{"key":"..."}` → 許可キーの現在値をテキストで返す（retry なし想定）。
- **SET**: `{"key":"...","value":"..."}` → upsert。

---

## 7. セキュリティ — `filename` バリデーション

`supabase_store.validate_memory_filename` で以下を拒否。

- `..`, `/`, `\` を含むもの、先頭 `.`
- 長さ **200 超**
- 許可文字：**Unicode 文字・数字・`_` `-` `.`** のみ（正規表現 `\w` 系＋日本語ブロック）

これにより **ディレクトリトラバーサル**やパス紛れ込みを防止。

---

## 8. RETRY・RI・メモリ使用量との関係

- ツール結果は従来どおり **`engine._truncate_tool_payload`**（`control.max_retry_payload_chars`）で LLM 再投入時に切り詰め。
- **READ-TEXT の要約**は RI を短くする一方、**全文モード**はメモリ・トークン負荷が増える。
- **`show_ri_text` を false** にすると、マスターには **`[RT#…]` が見えない**（ログ・デバッグ用途向け）。

---

## 9. 運用上の注意（ステートレス）

- **Render Free**: ディスクはエフェメラル。**記憶は Supabase のみ永続**。インメモリの **会話履歴・エクスポートバッファ**はプロセス／再起動で失われる。
- **複数インスタンス**: 同じ `user_id` が別マシンに振られると **インメモリ状態は共有されない**。永続は DB のみ。
- **SAVE-LOG** は「そのプロセスに溜まったバッファ」を書くため、デプロイ直後はバッファが空になりやすい。

---

## 10. セットアップ手順（開発者向け）

1. Supabase でプロジェクト作成。
2. **SQL Editor** で `dist/supabase_schema.sql` を実行。
3. **Project Settings → API** で `URL` と **`service_role` key** を取得。
4. ローカル `.env` に `SUPABASE_URL` / `SUPABASE_KEY` を設定。
5. `uv sync` 後、`uv run python dev_console.py` で記憶コマンドをモデルに出力させて動作確認。
6. Render の Environment に同じ変数を追加し再デプロイ。

---

## 11. テスト観点（手動）

- Supabase **未設定**で `LIST-FILES` → エラーメッセージが返ること。
- `WRITE-TEXT` → `READ-TEXT`（要約／全文）→ 内容が一致すること。
- `APPEND-TEXT` で改行が妥当であること。
- 会話後 `SAVE-LOG` → DB にバッファが入り、**連続 SAVE でバッファが空**から始まること。
- `SET-SETTING` で `current_model` / `show_ri_text` が効くこと。
- 不正 `filename`（`../x` 等）が拒否されること。

---

## 12. 既知の限界・将来拡張

- **DELETE-FILE**／DB行削除コマンドは未実装。
- **ユーザー別 `memory_files`**: `line_user_id`（LINE `user_id`）と `filename` の複合キーで分離。
- **`user_settings` はボット全体共通**: `setting_key` ごとに 1 値のみ（全ユーザーで共有）。
- **Rate limit・バックオフ**は SDK 任せ。大量 LIST でのコストは運用側で調整。

---

## 13. 変更サマリ（バージョン）

- **`pyproject.toml`**: **0.8.0**（マイナー: Supabase・記憶・設定の追加）
- **関連ファイル**: `render.yaml`, `.env.example`, `README.md`, `dist/system_prompt_main.txt`, 上記モジュール群

---

以上。
