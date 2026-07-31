import time

from chatgpt_bridge.registry import Registry
from chatgpt_bridge.state_machine import RequestState


def test_registry_roundtrip(tmp_path):
    reg = Registry(tmp_path / "r.sqlite3")
    try:
        request_id = reg.new_id()
        reg.create_request({
            "request_id": request_id,
            "idempotency_key": "k" * 16,
            "session_key": "sess-a",
            "task_id": "t1",
            "conversation_id": None,
            "prompt_hash": "ab" * 32,
            "request_state": RequestState.RECEIVED.value,
            "send_sentinel": None,
            "attempt_count": 0,
            "deadline_at": int(time.time()) + 600,
            "created_at": int(time.time()),
        })
        row = reg.get_request(request_id)
        assert row["request_state"] == RequestState.RECEIVED.value
        reg.set_request_state(request_id, RequestState.VALIDATED)
        reg.set_request_state(request_id, RequestState.QUEUED)
        assert reg.get_request(request_id)["request_state"] == RequestState.QUEUED.value
        assert reg.get_request_by_idempotency("k" * 16)["request_id"] == request_id
    finally:
        reg.close()


def test_registry_conversation_and_quarantine(tmp_path):
    reg = Registry(tmp_path / "r2.sqlite3")
    try:
        now = int(time.time())
        reg.create_conversation({
            "conversation_id": "conv-1",
            "session_key": "sess-a",
            "shard_number": 1,
            "project_id": "proj-1",
            "title": "HB abc123 · S1 · plan",
            "conversation_href": None,
            "mode": "Work",
            "model_label": "GPT-5.6 Sol",
            "model_backend_id": None,
            "effort_label": "High",
            "turn_count": 0,
            "estimated_context_tokens": 0,
            "created_at": now,
            "last_used_at": now,
            "verification_method": "cdp",
            "verification_timestamp": now,
            "status": "active",
        })
        conv = reg.get_active_conversation("sess-a")
        assert conv["project_id"] == "proj-1"
        reg.quarantine_conversation("conv-1")
        assert reg.get_active_conversation("sess-a") is None
    finally:
        reg.close()


def test_binding_lifecycle(tmp_path):
    reg = Registry(tmp_path / "r3.sqlite3")
    try:
        reg.upsert_binding({
            "binding_id": "b1",
            "account_fingerprint": "fp1",
            "project_id": "proj-1",
            "project_name": "chatgpt-bridge",
            "project_href": "https://chatgpt.com/p/proj-1",
            "project_context_hash": None,
            "app_build": "2026.07.30.172739",
            "selector_profile": "{}",
            "verified_at": int(time.time()),
        })
        b = reg.get_binding("b1")
        assert b["project_id"] == "proj-1"
        reg.disable_binding("b1")
        assert reg.get_binding("b1")["disabled_at"] is not None
    finally:
        reg.close()


def test_durable_recovery_scan(tmp_path):
    reg = Registry(tmp_path / "r4.sqlite3")
    try:
        now = int(time.time())
        for i, st in enumerate([RequestState.SEND_INTENT_RECORDED, RequestState.SENT, RequestState.WAITING]):
            reg.create_request({
                "request_id": f"req-{i}",
                "idempotency_key": f"key{i:0>16}",
                "session_key": "s1",
                "task_id": "t",
                "conversation_id": None,
                "prompt_hash": "aa" * 32,
                "request_state": st.value,
                "send_sentinel": f"[HERMES-BRIDGE v1 request=req-{i}]",
                "attempt_count": 0,
                "deadline_at": now + 600,
                "created_at": now,
            })
        assert len(reg.durable_after_send()) == 3
    finally:
        reg.close()
