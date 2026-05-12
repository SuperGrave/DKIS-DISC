-- DKIS-DISC: v1.3.0 マイグレーション
-- Supabase Dashboard → SQL Editor で実行してください。
-- 既存データを保持しながらテーブル・列名を LINE 由来から Discord 向けに変更します。
--
-- 実行順序: このファイルを 1 回だけ実行してください（冪等ではありません）。

-- =========================================================
-- 1. known_line_users → discord_users へリネーム
-- =========================================================
ALTER TABLE known_line_users RENAME TO discord_users;

-- =========================================================
-- 2. discord_users.line_user_id → user_id へリネーム
-- =========================================================
ALTER TABLE discord_users RENAME COLUMN line_user_id TO user_id;

-- =========================================================
-- 3. memory_files.line_user_id → user_id へリネーム
-- =========================================================
ALTER TABLE memory_files RENAME COLUMN line_user_id TO user_id;
-- PRIMARY KEY は (user_id, filename) に自動で追従します。

-- =========================================================
-- 4. mid_term_note の実質上限を 500 字へ拡張
--    （TEXT 型に文字数制限はないため、アプリ側で制御します）
-- =========================================================
-- No DDL change needed; enforced at application layer.

-- =========================================================
-- 5. 会話履歴の永続化カラムを追加
-- =========================================================
ALTER TABLE discord_users
    ADD COLUMN IF NOT EXISTS chat_history JSONB;

-- =========================================================
-- 6. RLS ポリシーの参照先を新テーブル名に合わせて再作成
--    （既存ポリシーがあれば削除してから再作成する）
-- =========================================================
-- memory_files は既存の RLS 設定を継承。discord_users も同様。
-- 追加ポリシーが必要な場合は別途設定してください。

-- =========================================================
-- 完了確認クエリ（オプション）
-- =========================================================
-- SELECT table_name FROM information_schema.tables
--   WHERE table_schema = 'public'
--   ORDER BY table_name;
