-- 003_delegation_payload.sql — durable prompt + bounded result for the executor.

ALTER TABLE requests ADD COLUMN prompt_text TEXT;
ALTER TABLE requests ADD COLUMN result_text TEXT;
