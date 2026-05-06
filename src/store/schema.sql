-- Meridian conversation persistence (PostgreSQL / Neon / Supabase).
-- Run once or let PostgresConversationStore apply on first connect.

CREATE TABLE IF NOT EXISTS chat_threads (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New chat',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    summary_checkpoint TEXT NOT NULL DEFAULT '',
    structured_checkpoint JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES chat_threads (id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    result_snapshot JSONB
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages (thread_id);
CREATE INDEX IF NOT EXISTS idx_chat_threads_updated ON chat_threads (updated_at DESC);
