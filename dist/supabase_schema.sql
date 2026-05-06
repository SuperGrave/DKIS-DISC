-- DKIS-LL: Supabase 記憶・設定テーブル
-- Supabase Dashboard → SQL Editor で実行してください。
-- アプリは通常 SUPABASE_KEY に service_role（サーバー秘密鍵）を設定します。

CREATE TABLE IF NOT EXISTS memory_files (
    filename TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    content_chars INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL DEFAULT ''
);

-- RLS: anon で公開しない運用なら service_role のみが書き込むので実質問題なし。
ALTER TABLE memory_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_settings ENABLE ROW LEVEL SECURITY;

-- updated_at はアプリの upsert 時に ISO8601 で更新します（トリガー不要）。
--
-- ▼ 既存 DB に適用する場合（LIST-FILES の転送量軽量化・一覧時のみ文字数を参照）
ALTER TABLE memory_files ADD COLUMN IF NOT EXISTS content_chars INTEGER NOT NULL DEFAULT 0;
UPDATE memory_files SET content_chars = length(coalesce(content, ''));
