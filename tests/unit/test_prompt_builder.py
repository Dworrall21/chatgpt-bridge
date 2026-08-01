from chatgpt_bridge.hermes.prompt_builder import SENTINEL_PREFIX, build_prompt


def _payload(**task_over):
    task = {
        "kind": "planning",
        "instruction": "Plan the rollout.",
        "context": [{"label": "facts", "content": "alpha beta", "sha256": "ab" * 32}],
        "constraints": ["read-only"],
        "output_contract": {"format": "markdown", "max_characters": 6000, "required_sections": ["Conclusion"]},
    }
    task.update(task_over)
    return {
        "protocol_version": "1.0",
        "request_id": "123e4567-e89b-12d3-a456-426614174000",
        "hermes": {"session_id": "s1", "task_id": "t1", "route": "delegate_task"},
        "task": task,
    }


def test_prompt_has_sentinel():
    p = build_prompt(_payload())
    assert p.startswith(SENTINEL_PREFIX)
    assert "request=123e4567-e89b-12d3-a456-426614174000" in p
    assert "task=t1" in p


def test_prompt_contains_context_and_contract():
    p = build_prompt(_payload())
    assert "[facts]" in p
    assert "alpha beta" in p
    assert "read-only" in p
    assert "Maximum length: 6000" in p
    assert "Section: Conclusion" in p
    assert "Detail level: standard" in p
    assert "HERMES-DONE" in p


def test_detail_rules_emitted():
    for detail in ("brief", "standard", "detailed"):
        payload = _payload()
        payload["task"]["output_contract"] = {
            **payload["task"]["output_contract"],
            "detail": detail,
            "max_characters": {"brief": 1200, "standard": 4000, "detailed": 12000}[detail],
        }
        p = build_prompt(payload)
        assert f"Detail level: {detail}" in p
        assert "Style:" in p


def test_prompt_ends_with_done_marker():
    p = build_prompt(_payload())
    assert p.rstrip().endswith("HERMES-DONE")
