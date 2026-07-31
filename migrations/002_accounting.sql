-- 002_accounting.sql — token accounting records.

CREATE TABLE IF NOT EXISTS token_accounting (
    request_id TEXT PRIMARY KEY,
    actual_hermes_input_tokens INTEGER,
    actual_hermes_output_tokens INTEGER,
    delegation_request_tokens INTEGER,
    delegation_result_tokens INTEGER,
    baseline_method TEXT,
    baseline_input_tokens INTEGER,
    baseline_output_tokens INTEGER,
    net_hermes_tokens_saved INTEGER,
    savings_percent REAL,
    measurement_confidence TEXT
);
