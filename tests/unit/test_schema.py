import json
from pathlib import Path

import jsonschema

REQUEST_SCHEMA = json.loads(
    (Path(__file__).parent.parent.parent / "src" / "chatgpt_bridge" / "protocol" / "request.schema.json").read_text()
)


def valid_request(**overrides):
    req = {
        "protocol_version": "1.0",
        "request_id": "123e4567-e89b-12d3-a456-426614174000",
        "idempotency_key": "idem-key-abcdef0123456789",
        "created_at": "2026-07-31T12:00:00Z",
        "deadline_at": "2026-07-31T12:30:00Z",
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


def test_valid_request_passes():
    jsonschema.validate(valid_request(), REQUEST_SCHEMA)


def test_wrong_project_rejected():
    with _expect_reject():
        jsonschema.validate(valid_request(**{"target_assertions": {**valid_request()["target_assertions"], "project_name": "other"}}), REQUEST_SCHEMA)


def test_wrong_mode_rejected():
    with _expect_reject():
        jsonschema.validate(valid_request(**{"target_assertions": {**valid_request()["target_assertions"], "mode": "Chat"}}), REQUEST_SCHEMA)


def test_non_delegatable_kind_rejected():
    with _expect_reject():
        jsonschema.validate(valid_request(**{"task": {**valid_request()["task"], "kind": "synthesis"}}), REQUEST_SCHEMA)


def test_unknown_field_rejected():
    with _expect_reject():
        jsonschema.validate(valid_request(**{"evil": True}), REQUEST_SCHEMA)


def test_context_sha256_required():
    req = valid_request()
    req["task"]["context"] = [{"label": "x", "content": "y"}]
    with _expect_reject():
        jsonschema.validate(req, REQUEST_SCHEMA)


def _expect_reject():
    import pytest

    return pytest.raises(jsonschema.ValidationError)
