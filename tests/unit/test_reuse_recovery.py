import time

from chatgpt_bridge.config import Config
from chatgpt_bridge.queue import JobQueue
from chatgpt_bridge.registry import Registry
from chatgpt_bridge.state_machine import RequestState


def _conv(turn_count=1, ctx_tokens=0, last_used=None, status="active", href="thread-1"):
    return {
        "conversation_id": "c1",
        "session_key": "s1",
        "turn_count": turn_count,
        "estimated_context_tokens": ctx_tokens,
        "last_used_at": last_used if last_used is not None else int(time.time()),
        "status": status,
        "conversation_href": href,
    }


def test_eligible_defaults():
    cfg = Config()
    assert JobQueue._eligible(_conv(), cfg)


def test_eligible_rejects_max_turns():
    cfg = Config()
    assert not JobQueue._eligible(_conv(turn_count=cfg.conversation.max_turns_per_shard), cfg)


def test_eligible_rejects_max_context():
    cfg = Config()
    assert not JobQueue._eligible(_conv(ctx_tokens=cfg.conversation.max_estimated_context_tokens), cfg)


def test_eligible_rejects_idle():
    cfg = Config()
    stale = int(time.time()) - (cfg.conversation.max_idle_days * 86400 + 60)
    assert not JobQueue._eligible(_conv(last_used=stale), cfg)


def test_eligible_rejects_quarantined():
    cfg = Config()
    assert not JobQueue._eligible(_conv(status="quarantined"), cfg)


def test_recover_requeues_intent_without_evidence(tmp_path):
    reg = Registry(tmp_path / "rec.sqlite3")
    try:
        now = int(time.time())
        reg.create_request({
            "request_id": "r1",
            "idempotency_key": "k1" * 8,
            "session_key": "s1",
            "task_id": "t",
            "conversation_id": None,
            "prompt_hash": "aa" * 32,
            "request_state": RequestState.SEND_INTENT_RECORDED.value,
            "send_sentinel": "[HERMES-BRIDGE v1 request=r1]",
            "attempt_count": 0,
            "deadline_at": now + 600,
            "created_at": now,
        })
        reg.create_request({
            "request_id": "r2",
            "idempotency_key": "k2" * 8,
            "session_key": "s1",
            "task_id": "t",
            "conversation_id": None,
            "prompt_hash": "bb" * 32,
            "request_state": RequestState.WAITING.value,
            "send_sentinel": "[HERMES-BRIDGE v1 request=r2]",
            "attempt_count": 0,
            "deadline_at": now + 600,
            "created_at": now,
        })
        cfg = Config()
        q = JobQueue(reg, cfg.flags)
        q.recover()
        assert reg.get_request("r1")["request_state"] == RequestState.FAILED.value
        assert reg.get_request("r1")["error_code"] == "NEEDS_RECONCILIATION"
        assert reg.get_request("r2")["request_state"] == RequestState.NEEDS_RECONCILIATION.value
        assert reg.get_request("r2")["error_code"] == "PENDING_RECONCILIATION"
    finally:
        reg.close()
