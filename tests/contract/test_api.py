"""Contract test: full UDS HTTP round trip against a real daemon on a temp socket.

Auth is disabled for contract tests via hmac_secret + require_hmac=False so the
test can focus on protocol behavior; HMAC itself is covered in unit tests.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

import pytest

from chatgpt_bridge.config import Config, TransportConfig
from chatgpt_bridge.metrics import Metrics
from chatgpt_bridge.queue import JobQueue
from chatgpt_bridge.registry import Registry
from chatgpt_bridge.server import serve

# Ensure the package is importable
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from chatgpt_bridge.hermes.provider import BridgeClient  # noqa: E402


@pytest.fixture()
def daemon(tmp_path):
    sock = str(tmp_path / "bridge.sock")
    db = str(tmp_path / "registry.sqlite3")
    cfg = Config(
        transport=TransportConfig(
            socket_path=sock,
            require_peer_uid=False,
            require_hmac=False,
            hmac_secret="test-secret",
            allowed_peer_uid=os.getuid(),
        ),
    )
    registry = Registry(db)
    metrics = Metrics()
    queue = JobQueue(registry, cfg.flags)
    server = serve(cfg, registry, queue, metrics, sock_path=sock)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"socket": sock, "registry": registry, "cfg": cfg}
    finally:
        server.shutdown()
        server.server_close()
        registry.close()


def _request(**overrides):
    req = {
        "protocol_version": "1.0",
        "request_id": str(uuid.uuid4()),
        "idempotency_key": "idem-" + uuid.uuid4().hex[:20],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "deadline_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 600)),
        "hermes": {"session_id": "sess-1", "task_id": "t1", "route": "delegate_task"},
        "target_assertions": {
            "project_name": "chatgpt-bridge",
            "mode": "Work",
            "model_label": "GPT-5.6 Sol",
            "effort_label": "High",
        },
        "conversation": {"policy": "reuse_session"},
        "task": {
            "kind": "planning",
            "instruction": "Plan the rollout.",
            "context": [],
            "constraints": [],
            "output_contract": {"format": "markdown", "max_characters": 6000},
        },
    }
    req.update(overrides)
    return req


def _client(daemon):
    return BridgeClient(daemon["socket"], b"test-secret")


def test_health(daemon):
    code, payload = _client(daemon).health()
    assert code == 200
    assert payload["ok"] is True
    assert payload["allow_send"] is False


def test_submit_and_poll(daemon):
    client = _client(daemon)
    req = _request()
    code, env = client.submit(req)
    assert code == 202
    assert env["status"] in {"accepted", "queued", "running"}
    # give the stub executor a moment to fail closed (sends disabled)
    env2 = None
    for _ in range(15):
        time.sleep(0.4)
        code2, env2 = client.poll(req["request_id"])
        if env2["status"] in ("failed", "completed", "needs_reconciliation", "cancelled"):
            break
    assert code2 == 200
    assert env2["status"] == "failed"
    assert env2["error"]["code"] == "SENDS_DISABLED"


def test_idempotency_same_key_cached(daemon):
    client = _client(daemon)
    req = _request()
    code1, _ = client.submit(req)
    assert code1 == 202
    code2, env2 = client.submit(req)
    assert code2 == 200
    assert env2["request_id"] == req["request_id"]


def test_idempotency_conflict(daemon):
    client = _client(daemon)
    req = _request()
    req2 = dict(req)
    req2["request_id"] = str(uuid.uuid4())
    req2["task"] = dict(req["task"])
    req2["task"]["instruction"] = "different content"
    code1, _ = client.submit(req)
    assert code1 == 202
    code2, _ = client.submit(req2)
    assert code2 == 409


def test_schema_rejection(daemon):
    client = _client(daemon)
    req = _request()
    req["task"]["kind"] = "synthesis"  # not delegatable per contract
    code, env = client.submit(req)
    assert code == 422
    assert env["error"]["code"] == "SCHEMA_REJECTED"


def test_unknown_request_404(daemon):
    code, _ = _client(daemon).poll("does-not-exist")
    assert code == 404
