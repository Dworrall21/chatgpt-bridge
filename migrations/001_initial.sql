-- 001_initial.sql — core registry tables
-- Applied by Registry.__init__; kept here as the canonical migration source.

CREATE TABLE IF NOT EXISTS project_bindings (
    binding_id TEXT PRIMARY KEY,
    account_fingerprint TEXT NOT NULL,
    project_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    project_href TEXT,
    project_context_hash TEXT,
    app_build TEXT,
    selector_profile TEXT,
    verified_at INTEGER NOT NULL,
    disabled_at INTEGER
);

CREATE TABLE IF NOT EXISTS hermes_sessions (
    session_key TEXT PRIMARY KEY,
    session_id_hash TEXT NOT NULL,
    active_conversation_id TEXT,
    active_shard INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    shard_number INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    title TEXT,
    conversation_href TEXT,
    mode TEXT NOT NULL,
    model_label TEXT,
    model_backend_id TEXT,
    effort_label TEXT,
    turn_count INTEGER NOT NULL DEFAULT 0,
    estimated_context_tokens INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL,
    verification_method TEXT,
    verification_timestamp INTEGER,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    session_key TEXT NOT NULL,
    task_id TEXT NOT NULL,
    conversation_id TEXT,
    prompt_hash TEXT NOT NULL,
    request_state TEXT NOT NULL,
    send_sentinel TEXT,
    user_message_id TEXT,
    assistant_message_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    deadline_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    sent_at INTEGER,
    completed_at INTEGER,
    response_hash TEXT,
    error_code TEXT,
    error_stage TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_session ON requests(session_key, created_at);
CREATE INDEX IF NOT EXISTS idx_requests_state ON requests(request_state);
CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_key);
