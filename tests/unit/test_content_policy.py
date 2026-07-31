import pytest

from chatgpt_bridge.security.content_policy import ContentPolicy


def _task(instruction="plan x", context=None):
    return {"instruction": instruction, "context": context or []}


def test_allows_clean_task():
    assert ContentPolicy().check(_task()).allowed


def test_rejects_secret_in_instruction():
    assert not ContentPolicy().check(_task(instruction="token: abcdef1234567890abcdef1234567890")).allowed


def test_rejects_private_key():
    body = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----"
    assert not ContentPolicy().check(_task(context=[{"label": "k", "content": body}])).allowed


def test_rejects_oversized_context():
    policy = ContentPolicy(max_context_item_chars=100)
    assert not policy.check(_task(context=[{"label": "k", "content": "z" * 200}])).allowed


def test_rejects_too_many_items():
    policy = ContentPolicy(max_context_items=2)
    items = [{"label": f"k{i}", "content": "ok"} for i in range(5)]
    assert not policy.check(_task(context=items)).allowed
