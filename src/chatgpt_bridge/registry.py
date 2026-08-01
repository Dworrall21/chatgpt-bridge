"""SQLite registry (WAL mode): project bindings, sessions, conversations,
requests, token accounting. Idempotency and durable journal semantics.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .state_machine import RequestState, transition

_SCHEMA = """
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

CREATE INDEX IF NOT EXISTS idx_requests_session ON requests(session_key, created_at);
CREATE INDEX IF NOT EXISTS idx_requests_state ON requests(request_state);
CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_key);
"""

_ALTERS = [
    "ALTER TABLE requests ADD COLUMN prompt_text TEXT",
    "ALTER TABLE requests ADD COLUMN result_text TEXT",
    "ALTER TABLE requests ADD COLUMN policy TEXT",
    "ALTER TABLE requests ADD COLUMN result_truncated INTEGER",
]


class Registry:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        for alter in _ALTERS:
            try:
                self._conn.execute(alter)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        self._conn.commit()
        self._lock = __import__("threading").Lock()

    def close(self) -> None:
        self._conn.close()

    # ---- project bindings ----
    def upsert_binding(self, binding: dict) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO project_bindings
                   (binding_id, account_fingerprint, project_id, project_name, project_href,
                    project_context_hash, app_build, selector_profile, verified_at)
                   VALUES (:binding_id, :account_fingerprint, :project_id, :project_name,
                           :project_href, :project_context_hash, :app_build, :selector_profile,
                           :verified_at)
                   ON CONFLICT(binding_id) DO UPDATE SET
                     account_fingerprint=excluded.account_fingerprint,
                     project_id=excluded.project_id,
                     project_name=excluded.project_name,
                     project_href=excluded.project_href,
                     project_context_hash=excluded.project_context_hash,
                     app_build=excluded.app_build,
                     selector_profile=excluded.selector_profile,
                     verified_at=excluded.verified_at,
                     disabled_at=NULL""",
                binding,
            )
            self._conn.commit()

    def get_binding(self, binding_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM project_bindings WHERE binding_id=?", (binding_id,)
        ).fetchone()
        return dict(row) if row else None

    def disable_binding(self, binding_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE project_bindings SET disabled_at=? WHERE binding_id=?",
                (int(time.time()), binding_id),
            )
            self._conn.commit()

    # ---- sessions ----
    def touch_session(self, session_key: str, session_id_hash: str) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """INSERT INTO hermes_sessions (session_key, session_id_hash, created_at, last_used_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(session_key) DO UPDATE SET last_used_at=excluded.last_used_at""",
                (session_key, session_id_hash, now, now),
            )
            self._conn.commit()

    def get_session(self, session_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM hermes_sessions WHERE session_key=?", (session_key,)
        ).fetchone()
        return dict(row) if row else None

    # ---- conversations ----
    def create_conversation(self, conv: dict) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO conversations
                   (conversation_id, session_key, shard_number, project_id, title,
                    conversation_href, mode, model_label, model_backend_id, effort_label,
                    turn_count, estimated_context_tokens, created_at, last_used_at,
                    verification_method, verification_timestamp, status)
                   VALUES (:conversation_id, :session_key, :shard_number, :project_id, :title,
                           :conversation_href, :mode, :model_label, :model_backend_id, :effort_label,
                           :turn_count, :estimated_context_tokens, :created_at, :last_used_at,
                           :verification_method, :verification_timestamp, :status)""",
                conv,
            )
            self._conn.commit()

    def get_active_conversation(self, session_key: str) -> dict | None:
        row = self._conn.execute(
            """SELECT * FROM conversations
               WHERE session_key=? AND status='active'
               ORDER BY shard_number DESC LIMIT 1""",
            (session_key,),
        ).fetchone()
        return dict(row) if row else None

    def get_conversation(self, conversation_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM conversations WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        return dict(row) if row else None

    def quarantine_conversation(self, conversation_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET status='quarantined' WHERE conversation_id=?",
                (conversation_id,),
            )
            self._conn.commit()

    def bump_conversation(self, conversation_id: str, turn_count: int | None = None,
                          estimated_context_tokens: int | None = None) -> None:
        with self._lock:
            sets, args = [], []
            if turn_count is not None:
                sets.append("turn_count=?")
                args.append(turn_count)
            if estimated_context_tokens is not None:
                sets.append("estimated_context_tokens=?")
                args.append(estimated_context_tokens)
            sets.append("last_used_at=?")
            args.append(int(time.time()))
            args.append(conversation_id)
            self._conn.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE conversation_id=?", args)
            self._conn.commit()

    # ---- requests ----
    def create_request(self, req: dict) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO requests
                   (request_id, idempotency_key, session_key, task_id, conversation_id,
                    prompt_hash, request_state, send_sentinel, attempt_count, deadline_at, created_at)
                   VALUES (:request_id, :idempotency_key, :session_key, :task_id, :conversation_id,
                           :prompt_hash, :request_state, :send_sentinel, :attempt_count,
                           :deadline_at, :created_at)""",
                req,
            )
            self._conn.commit()

    def get_request(self, request_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM requests WHERE request_id=?", (request_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_request_by_idempotency(self, idempotency_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM requests WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        return dict(row) if row else None

    def set_request_state(self, request_id: str, state: RequestState, *, error_code: str | None = None,
                          error_stage: str | None = None) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT request_state FROM requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"request not found: {request_id}")
            current = RequestState(row["request_state"])
            transition(current, state)
            now = int(time.time())
            fields = ["request_state=?", "error_code=?", "error_stage=?"]
            args: list = [state.value, error_code, error_stage]
            if state == RequestState.SENT and not row["request_state"]:
                fields.append("sent_at=?")
                args.append(now)
            if state == RequestState.COMPLETED:
                fields.append("completed_at=?")
                args.append(now)
            args.append(request_id)
            self._conn.execute(f"UPDATE requests SET {', '.join(fields)} WHERE request_id=?", args)
            self._conn.commit()

    def set_send_evidence(self, request_id: str, sentinel: str, user_message_id: str | None = None,
                          assistant_message_id: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET send_sentinel=?, user_message_id=COALESCE(?, user_message_id), assistant_message_id=COALESCE(?, assistant_message_id) WHERE request_id=?",
                (sentinel, user_message_id, assistant_message_id, request_id),
            )
            self._conn.commit()

    def set_prompt_text(self, request_id: str, prompt_text: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET prompt_text=? WHERE request_id=?", (prompt_text, request_id)
            )
            self._conn.commit()

    def set_policy(self, request_id: str, policy: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET policy=? WHERE request_id=?", (policy, request_id)
            )
            self._conn.commit()

    def set_request_conversation(self, request_id: str, conversation_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET conversation_id=? WHERE request_id=?", (conversation_id, request_id)
            )
            self._conn.commit()

    def set_conversation_href(self, conversation_id: str, href: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET conversation_href=? WHERE conversation_id=?",
                (href, conversation_id),
            )
            self._conn.commit()

    def set_result_text(self, request_id: str, result_text: str, response_hash: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET result_text=?, response_hash=COALESCE(?, response_hash) WHERE request_id=?",
                (result_text, response_hash, request_id),
            )
            self._conn.commit()

    def set_truncated(self, request_id: str, truncated: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET result_truncated=? WHERE request_id=?",
                (1 if truncated else 0, request_id),
            )
            self._conn.commit()

    def durable_after_send(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM requests WHERE request_state IN ('SEND_INTENT_RECORDED','SENT','WAITING','NEEDS_RECONCILIATION')"
        ).fetchall()
        return [dict(r) for r in rows]

    def requests_in_state(self, state: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM requests WHERE request_state=?", (state,)
        ).fetchall()
        return [dict(r) for r in rows]

    def record_accounting(self, acc: dict) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO token_accounting
                   (request_id, actual_hermes_input_tokens, actual_hermes_output_tokens,
                    delegation_request_tokens, delegation_result_tokens, baseline_method,
                    baseline_input_tokens, baseline_output_tokens, net_hermes_tokens_saved,
                    savings_percent, measurement_confidence)
                   VALUES (:request_id, :actual_hermes_input_tokens, :actual_hermes_output_tokens,
                           :delegation_request_tokens, :delegation_result_tokens, :baseline_method,
                           :baseline_input_tokens, :baseline_output_tokens,
                           :net_hermes_tokens_saved, :savings_percent, :measurement_confidence)""",
                acc,
            )
            self._conn.commit()

    def get_accounting(self, request_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM token_accounting WHERE request_id=?", (request_id,)
        ).fetchone()
        return dict(row) if row else None

    # ---- helpers ----
    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def hmac_session_key(salt: bytes, session_id: str) -> str:
        import hashlib

        return hashlib.sha256(salt + session_id.encode()).hexdigest()
