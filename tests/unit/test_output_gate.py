import pytest

from chatgpt_bridge.security.output_gate import OutputGate


def test_gate_passes_through_small_result():
    g = OutputGate(max_result_chars=1000)
    out = g.gate("hello world")
    assert out.text == "hello world"
    assert not out.truncated
    assert out.sha256
    assert out.advisory_only is True


def test_gate_truncates_large_result():
    g = OutputGate(max_result_chars=10)
    out = g.gate("x" * 100)
    assert len(out.text) == 10
    assert out.truncated is True
    assert out.sha256 == __import__("hashlib").sha256(b"x" * 10).hexdigest()


def test_gate_keeps_marker_on_truncation_boundary():
    body = "A" * 50 + "HERMES-DONE"
    g = OutputGate(max_result_chars=1000)
    out = g.gate(body)
    assert out.text.endswith("HERMES-DONE")
    assert not out.truncated


def test_envelope_truncated_flag():
    from chatgpt_bridge.config import Config
    from chatgpt_bridge.server import build_envelope

    cfg = Config()
    row = {
        "request_id": "r1",
        "idempotency_key": "k" * 16,
        "request_state": "COMPLETED",
        "result_text": "z" * 100,
        "result_truncated": None,
        "response_hash": "ab" * 32,
        "created_at": 1,
        "sent_at": 2,
        "completed_at": 3,
        "error_code": None,
        "error_stage": None,
        "conversation_id": None,
        "user_message_id": None,
        "assistant_message_id": None,
    }
    small = Config(security=__import__("dataclasses").replace(cfg.security, max_result_chars=50))
    env = build_envelope(small, row)
    assert env["result"]["truncated"] is True
