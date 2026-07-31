"""UDS HTTP API server.

Endpoints:
  POST /v1/delegations                     -> 202 queued | 200 cached | 409 | 422 | 503
  GET  /v1/delegations/{request_id}        -> envelope
  POST /v1/delegations/{request_id}/cancel
  GET  /v1/health

Peer UID + HMAC + nonce + content-hash verified before any processing.
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import jsonschema

from .config import Config
from .errors import AuthenticationFailedError, BridgeError, PolicyRejectionError, SchemaRejectionError
from .metrics import Metrics
from .protocol.canonicalization import sha256_hex
from .protocol.signing import SigningError, verify_hmac
from .queue import JobQueue
from .registry import Registry
from .security.content_policy import ContentPolicy
from .security.peer_auth import NonceGuard, peer_uid
from .state_machine import RequestState

_REQUEST_SCHEMA = json.loads(
    (Path(__file__).parent / "protocol" / "request.schema.json").read_text()
)


class UnixStreamServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class BridgeState:
    """Shared daemon state handed to handlers."""

    def __init__(self, config: Config, registry: Registry, queue: JobQueue, metrics: Metrics, nonce_guard: NonceGuard):
        self.config = config
        self.registry = registry
        self.queue = queue
        self.metrics = metrics
        self.nonce_guard = nonce_guard


def _iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


def build_envelope(config: Config, row: dict) -> dict:
    state = row["request_state"]
    status_map = {
        RequestState.RECEIVED.value: "accepted",
        RequestState.VALIDATED.value: "accepted",
        RequestState.QUEUED.value: "queued",
        RequestState.UI_LOCKED.value: "running",
        RequestState.PROJECT_VERIFIED.value: "running",
        RequestState.CONVERSATION_BOUND.value: "running",
        RequestState.MODEL_VERIFIED.value: "running",
        RequestState.SEND_INTENT_RECORDED.value: "running",
        RequestState.SENT.value: "running",
        RequestState.WAITING.value: "running",
        RequestState.COMPLETED.value: "completed",
        RequestState.FAILED.value: "failed",
        RequestState.NEEDS_RECONCILIATION.value: "needs_reconciliation",
        RequestState.CANCELLED.value: "cancelled",
    }
    status = status_map.get(state, "queued")
    retryable = row.get("error_code") in {"APP_NOT_RUNNING", "CDP_UNAVAILABLE", "COMPLETION_TIMEOUT"}
    error = None
    if row.get("error_code"):
        error = {
            "code": row["error_code"],
            "stage": row.get("error_stage"),
            "message": row.get("error_code"),
            "safe_to_retry": retryable,
            "details_hash": None,
        }
    result = None
    if state == RequestState.COMPLETED.value and row.get("result_text"):
        result = {
            "mime_type": "text/markdown",
            "text": row["result_text"],
            "sha256": row.get("response_hash"),
            "truncated": False,
            "full_response_retained": True,
        }
    return {
        "protocol_version": "1.0",
        "request_id": row["request_id"],
        "idempotency_key": row["idempotency_key"],
        "status": status,
        "retryable": retryable,
        "attempt": row.get("attempt_count", 0),
        "timestamps": {
            "received_at": _iso(row["created_at"]),
            "updated_at": _iso(row.get("sent_at") or row["created_at"]),
            "sent_at": _iso(row.get("sent_at")),
            "completed_at": _iso(row.get("completed_at")),
        },
        "binding": {
            "account_fingerprint": None,
            "project_name": config.app.required_project_name,
            "project_id": None,
            "project_verified": state
            in {
                RequestState.PROJECT_VERIFIED.value,
                RequestState.CONVERSATION_BOUND.value,
                RequestState.MODEL_VERIFIED.value,
                RequestState.SEND_INTENT_RECORDED.value,
                RequestState.SENT.value,
                RequestState.WAITING.value,
                RequestState.COMPLETED.value,
            },
            "conversation_id": row.get("conversation_id"),
            "user_message_id": row.get("user_message_id"),
            "assistant_message_id": row.get("assistant_message_id"),
            "verification_method": None,
        },
        "execution": {
            "mode": config.app.required_mode,
            "model_label": config.app.required_model_label,
            "model_backend_id": None,
            "effort_label": config.app.required_effort_label,
            "app_build": None,
            "selector_profile": None,
        },
        "usage": None,
        "result": result,
        "error": error,
    }


class BridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ChatGPTBridge/0.1"

    @property
    def state(self) -> BridgeState:
        return self.server.bridge_state  # type: ignore[attr-defined]

    # -- helpers ----------------------------------------------------------
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if not length:
            raise SchemaRejectionError("missing Content-Length")
        length = int(length)
        if length > self.state.config.transport.max_request_bytes:
            raise SchemaRejectionError("request too large")
        return self.rfile.read(length)

    def _verify_auth(self, body: bytes) -> None:
        cfg = self.state.config.transport
        if cfg.require_peer_uid:
            try:
                uid = peer_uid(self.connection)
            except BridgeError:
                raise
            allowed = cfg.allowed_peer_uid
            if allowed is not None and uid != allowed:
                raise AuthenticationFailedError("peer UID not allowed")
        if cfg.require_hmac:
            try:
                verify_hmac(
                    cfg.secret(),
                    self.command,
                    self.path.split("?")[0],
                    self.headers.get("X-Bridge-Timestamp", ""),
                    self.headers.get("X-Bridge-Nonce", ""),
                    self.headers.get("X-Content-SHA256", ""),
                    self.headers.get("Idempotency-Key", ""),
                    self.headers.get("Authorization", "").replace("HMAC-SHA256 ", ""),
                    tolerance_seconds=cfg.timestamp_tolerance_seconds,
                )
            except SigningError as e:
                raise AuthenticationFailedError(str(e))
            self.state.nonce_guard.check(self.headers.get("X-Bridge-Nonce", ""))
            actual = sha256_hex(body)
            if actual != self.headers.get("X-Content-SHA256", ""):
                raise AuthenticationFailedError("content hash mismatch")

    # -- routing ----------------------------------------------------------
    def do_GET(self) -> None:
        if self.path == "/v1/health":
            self._health()
            return
        if self.path.startswith("/v1/delegations/"):
            request_id = self.path[len("/v1/delegations/") :].split("/")[0]
            row = self.state.registry.get_request(request_id)
            if row is None:
                self._json(404, {"error": {"code": "NOT_FOUND", "message": "unknown request_id"}})
                return
            self._json(200, build_envelope(self.state.config, row))
            return

    def do_POST(self) -> None:
        if self.path == "/v1/delegations":
            self._create_delegation()
            return
        if self.path.startswith("/v1/delegations/") and self.path.endswith("/cancel"):
            request_id = self.path[len("/v1/delegations/") :].split("/")[0]
            row = self.state.registry.get_request(request_id)
            if row is None:
                self._json(404, {"error": {"code": "NOT_FOUND", "message": "unknown request_id"}})
                return
            if row["request_state"] in {RequestState.COMPLETED.value, RequestState.FAILED.value, RequestState.CANCELLED.value}:
                self._json(409, {"error": {"code": "ALREADY_TERMINAL", "message": "request already terminal"}})
                return
            self.state.registry.set_request_state(request_id, RequestState.CANCELLED)
            updated = self.state.registry.get_request(request_id)
            if updated is not None:
                self._json(200, build_envelope(self.state.config, updated))
            else:
                self._json(500, {"error": {"code": "INTERNAL", "message": "lost request"}})
            return
        self._json(404, {"error": {"code": "NOT_FOUND", "message": "unknown route"}})

    # -- handlers ---------------------------------------------------------
    def _health(self) -> None:
        self._json(200, {
            "ok": True,
            "bridge_enabled": self.state.config.flags.bridge_enabled,
            "allow_send": self.state.config.flags.allow_send,
            "queue": self.state.queue.stats().__dict__,
            "metrics": self.state.metrics.summary(),
        })

    def _create_delegation(self) -> None:
        body = self._read_body()
        try:
            self._verify_auth(body)
        except BridgeError as e:
            self.state.metrics.incr("auth_rejected")
            self._json(401, {"error": e.to_envelope()})
            return
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._json(422, {"error": {"code": "SCHEMA_REJECTED", "message": "invalid JSON"}})
            return
        try:
            jsonschema.validate(payload, _REQUEST_SCHEMA)
        except jsonschema.ValidationError as e:
            self.state.metrics.incr("schema_rejected")
            self._json(422, {"error": {"code": "SCHEMA_REJECTED", "message": e.message}})
            return

        idempotency_key = payload["idempotency_key"]
        existing = self.state.registry.get_request_by_idempotency(idempotency_key)
        if existing is not None:
            if existing.get("prompt_hash") != sha256_hex(body):
                self.state.metrics.incr("idempotency_conflict")
                self._json(409, {"error": {"code": "IDEMPOTENCY_CONFLICT", "message": "same key, different content"}})
                return
            self.state.metrics.incr("idempotency_hit")
            self._json(200, build_envelope(self.state.config, existing))
            return

        policy = ContentPolicy(
            max_instruction_chars=self.state.config.security.max_instruction_chars,
            max_context_items=self.state.config.security.max_context_items,
            max_context_item_chars=self.state.config.security.max_context_item_chars,
        )
        try:
            policy.check(payload["task"])
        except PolicyRejectionError as e:
            self.state.metrics.incr("policy_rejected")
            self._json(422, {"error": e.to_envelope()})
            return

        session_key = self.state.registry.hmac_session_key(
            self.state.config.transport.secret(), payload["hermes"]["session_id"]
        )
        self.state.registry.touch_session(session_key, payload["hermes"]["session_id"][:32])
        request_id = payload["request_id"]
        now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        try:
            deadline = int(datetime.datetime.fromisoformat(payload["deadline_at"]).timestamp())
        except ValueError:
            self._json(422, {"error": {"code": "SCHEMA_REJECTED", "message": "invalid deadline_at"}})
            return
        self.state.registry.create_request({
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "session_key": session_key,
            "task_id": payload["hermes"]["task_id"],
            "conversation_id": None,
            "prompt_hash": sha256_hex(body),
            "request_state": RequestState.RECEIVED.value,
            "send_sentinel": None,
            "attempt_count": 0,
            "deadline_at": deadline,
            "created_at": now,
        })
        from .hermes.prompt_builder import build_prompt

        self.state.registry.set_prompt_text(request_id, build_prompt(payload))
        self.state.registry.set_policy(request_id, payload["conversation"].get("policy", "reuse_session"))
        self.state.registry.set_request_state(request_id, RequestState.VALIDATED)
        self.state.queue.enqueue(request_id, session_key)
        self.state.metrics.incr("delegations_created")
        self.state.metrics.event("delegation_created", request_id=request_id)
        self._json(202, build_envelope(self.state.config, self.state.registry.get_request(request_id)))

    def log_message(self, format, *args):  # silence default stderr noise; redacted
        pass


def serve(config: Config, registry: Registry, queue: JobQueue, metrics: Metrics,
          nonce_guard: NonceGuard | None = None, sock_path: str | None = None) -> UnixStreamServer:
    path = sock_path or config.transport.resolve_socket_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    server = UnixStreamServer(path, BridgeHandler)
    os.chmod(path, 0o600)
    server.bridge_state = BridgeState(config, registry, queue, metrics, nonce_guard or NonceGuard())  # type: ignore[attr-defined]
    return server
