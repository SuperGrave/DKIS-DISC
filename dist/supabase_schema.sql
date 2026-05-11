-- DKIS-LL: Supabase 記憶・設定テーブル
-- Supabase Dashboard → SQL Editor で実行してください。
-- アプリは通常 SUPABASE_KEY に service_role（サーバー秘密鍵）を設定します。
--
-- 記憶 memory_files: LINE の user_id（line_user_id）ごとにファイルを分離
-- 設定 user_settings: setting_key ごとに 1 値のみ（全ユーザー共通）

CREATE TABLE IF NOT EXISTS memory_files (
    line_user_id TEXT NOT NULL DEFAULT '__legacy_shared__',
    filename TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    content_chars INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (line_user_id, filename)
);

CREATE TABLE IF NOT EXISTS user_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL DEFAULT ''
);

-- Discord チャンネル単位の会話入口・通知表示設定。
-- enabled:
--   true  ... このチャンネルでDKIS-DISCを利用する
--   false ... Discord表示「無効」。管理者の /channel_setting value:true で有効化
--   未登録チャンネルはアプリ側でも無効として扱う
-- response_mode:
--   inherit ... グローバル DISCORD_MESSAGE_MODE / DISCORD_CHANNEL_ID に従う
--   normal  ... 通常メッセージすべてに反応
--   mention ... Botメンション時だけ反応
--   off     ... 反応しない
-- tool_notice_display:
--   inherit ... user_settings.tool_notice_display / show_ri_text に従う
--   full | abbrev | minimal | hidden ... チャンネルごとに上書き
-- process_notice:
--   1 ... 非表示 / 2 ... 最小 / 3 ... 表示 / 4 ... 開発者用（AI原文）
CREATE TABLE IF NOT EXISTS channel_settings (
    channel_id TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    response_mode TEXT NOT NULL DEFAULT 'inherit',
    tool_notice_display TEXT NOT NULL DEFAULT 'inherit',
    process_notice TEXT NOT NULL DEFAULT '2',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- LINE が過去ユーザーを一覧しないので、Webhook で見えた userId を記録。
-- notify_on_restart が true のユーザーだけ、ワーカー起動時の定型 Push の宛先になる（オプトイン）。
CREATE TABLE IF NOT EXISTS known_line_users (
    line_user_id TEXT PRIMARY KEY,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notify_on_restart BOOLEAN NOT NULL DEFAULT FALSE,
    talk_with_kiritan BOOLEAN NOT NULL DEFAULT TRUE,
    talking_memory TEXT NOT NULL DEFAULT '1',
    using_model TEXT NOT NULL DEFAULT '2',
    personal_memory BOOLEAN NOT NULL DEFAULT FALSE,
    response_criteria TEXT NOT NULL DEFAULT '1',
    conversation_count INTEGER NOT NULL DEFAULT 0,
    daily_token_date TEXT,
    daily_token_count INTEGER NOT NULL DEFAULT 0
);

-- RLS: anon で公開しない運用なら service_role のみが書き込むので実質問題なし。
ALTER TABLE memory_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE known_line_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_settings ENABLE ROW LEVEL SECURITY;

-- ▼ 次のブロックは「旧スキーマ（filename のみ PRIMARY KEY）」からの移行も兼ねます。
-- 新規作成されたテーブルでも最後まで実行して問題ありません（PK を一度外して付け直します）。
ALTER TABLE memory_files ADD COLUMN IF NOT EXISTS line_user_id TEXT NOT NULL DEFAULT '__legacy_shared__';
ALTER TABLE memory_files DROP CONSTRAINT IF EXISTS memory_files_pkey;
ALTER TABLE memory_files ADD PRIMARY KEY (line_user_id, filename);

ALTER TABLE memory_files ADD COLUMN IF NOT EXISTS content_chars INTEGER NOT NULL DEFAULT 0;
UPDATE memory_files SET content_chars = length(coalesce(content, ''));

ALTER TABLE known_line_users ADD COLUMN IF NOT EXISTS pending_worker_boot_history TEXT;
ALTER TABLE known_line_users ADD COLUMN IF NOT EXISTS mid_term_note TEXT NOT NULL DEFAULT '';
ALTER TABLE known_line_users ADD COLUMN IF NOT EXISTS talk_with_kiritan BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE known_line_users ADD COLUMN IF NOT EXISTS talking_memory TEXT NOT NULL DEFAULT '1';
ALTER TABLE known_line_users ADD COLUMN IF NOT EXISTS using_model TEXT NOT NULL DEFAULT '2';
ALTER TABLE known_line_users ADD COLUMN IF NOT EXISTS personal_memory BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE known_line_users ADD COLUMN IF NOT EXISTS response_criteria TEXT NOT NULL DEFAULT '1';
ALTER TABLE known_line_users ADD COLUMN IF NOT EXISTS conversation_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE known_line_users ADD COLUMN IF NOT EXISTS daily_token_date TEXT;
ALTER TABLE known_line_users ADD COLUMN IF NOT EXISTS daily_token_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS channel_settings (
    channel_id TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    response_mode TEXT NOT NULL DEFAULT 'inherit',
    tool_notice_display TEXT NOT NULL DEFAULT 'inherit',
    process_notice TEXT NOT NULL DEFAULT '2',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE channel_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_settings ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE channel_settings ALTER COLUMN enabled SET DEFAULT FALSE;
ALTER TABLE channel_settings ADD COLUMN IF NOT EXISTS response_mode TEXT NOT NULL DEFAULT 'inherit';
ALTER TABLE channel_settings ADD COLUMN IF NOT EXISTS tool_notice_display TEXT NOT NULL DEFAULT 'inherit';
ALTER TABLE channel_settings ADD COLUMN IF NOT EXISTS process_notice TEXT NOT NULL DEFAULT '2';
ALTER TABLE channel_settings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
