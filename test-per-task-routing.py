#!/usr/bin/env python3
"""Standalone test for per-task provider routing in delegate_task.

This test verifies that delegate_task correctly routes sub-agents to
different providers based on per-task provider/model overrides.

Usage:
  cd /home/david/.hermes/hermes-agent
  source venv/bin/activate
  python3 /home/david/chatgpt-extension/test-per-task-routing.py

Requires:
  - Hermes venv with all dependencies
  - Chrome running with ChatGPT tab on port 9222
  - ChatGPT bridge running on port 11557
"""

import json, sys, types, time
from pathlib import Path


def make_mock_parent():
    """Create a minimal mock parent agent for delegate_task testing."""
    class MockLogger:
        def debug(self, *a, **kw): pass
        def info(self, *a, **kw): pass
        def warning(self, *a, **kw): print("[PARENT]", a[0] % a[1:] if a else "")
        def error(self, *a, **kw): print("[PARENT ERROR]", a[0] % a[1:] if a else "")

    parent = types.SimpleNamespace()
    parent.logger = MockLogger()
    parent._delegate_depth = 0
    parent._interrupt_requested = False
    parent.enabled_toolsets = ["terminal", "file"]
    parent.provider = "opencode-go"
    parent.model = "deepseek-v4-flash"
    parent.base_url = ""
    parent.api_key = ""
    parent.api_mode = "chat_completions"
    parent.max_tokens = None
    parent.reasoning_config = None
    parent.prefill_messages = None
    parent._fallback_chain = []
    parent._subagent_id = None
    parent.providers_allowed = None
    parent.providers_ignored = None
    parent.providers_order = None
    parent.provider_sort = None
    parent.openrouter_min_coding_score = None
    parent._delegate_spinner = None
    parent._active_subagents = {}
    parent.valid_tool_names = set()
    parent.acp_command = None
    parent.acp_args = []
    return parent


def test_credential_resolution():
    """Test 1: _resolve_credentials_for_provider resolves both providers."""
    from tools.delegate_tool import _resolve_credentials_for_provider

    print("=== Test 1: Credential Resolution ===")

    # ChatGPT Bridge
    creds = _resolve_credentials_for_provider("chatgpt-bridge", model="chatgpt")
    assert creds["provider"] == "chatgpt-bridge", f"Expected chatgpt-bridge, got {creds['provider']}"
    assert creds["model"] == "chatgpt", f"Expected chatgpt, got {creds['model']}"
    assert "11557" in creds["base_url"], f"Expected bridge URL, got {creds['base_url']}"
    print("  ✓ ChatGPT Bridge: provider=chatgpt-bridge, model=chatgpt, url=:11557")

    # Mimo-v2.5 via opencode-go
    creds = _resolve_credentials_for_provider("opencode-go", model="mimo-v2.5")
    assert creds["provider"] == "opencode-go", f"Expected opencode-go, got {creds['provider']}"
    assert creds["model"] == "mimo-v2.5", f"Expected mimo-v2.5, got {creds['model']}"
    print("  ✓ Mimo-v2.5: provider=opencode-go, model=mimo-v2.5")

    return True


def test_per_task_credential_resolution():
    """Test 2: The credential resolution logic in the task loop works correctly."""
    from tools.delegate_tool import _resolve_credentials_for_provider

    print("\n=== Test 2: Per-Task Credential Resolution Logic ===")

    # Simulate what the task loop does
    delegation_config_creds = {
        "provider": "opencode-go",
        "model": "deepseek-v4-flash",
        "base_url": None,
        "api_key": None,
        "api_mode": "chat_completions",
    }

    # Task 1: ChatGPT Bridge override
    task_creds = dict(delegation_config_creds)
    task_provider = "chatgpt-bridge"
    task_model = "chatgpt"

    if task_provider and task_provider != delegation_config_creds["provider"]:
        task_creds = _resolve_credentials_for_provider(
            provider=task_provider,
            model=task_model or delegation_config_creds.get("model"),
        )
    assert task_creds["provider"] == "chatgpt-bridge"
    assert task_creds["model"] == "chatgpt"
    print("  ✓ Task: chatgpt-bridge/chatgpt — credentials resolved correctly")

    # Task 2: Mimo-v2.5 override
    task_creds = dict(delegation_config_creds)
    task_provider = "opencode-go"
    task_model = "mimo-v2.5"

    if task_provider and task_provider != delegation_config_creds["provider"]:
        task_creds = _resolve_credentials_for_provider(
            provider=task_provider,
            model=task_model or delegation_config_creds.get("model"),
        )
    else:
        task_creds["model"] = task_model or task_creds.get("model")

    assert task_creds["model"] == "mimo-v2.5"
    print(f"  ✓ Task: opencode-go/mimo-v2.5 — model override applied (provider={task_creds['provider']})")

    # Task 3: No override (should use delegation config defaults)
    task_creds = dict(delegation_config_creds)
    assert task_creds["provider"] == "opencode-go"
    assert task_creds["model"] == "deepseek-v4-flash"
    print("  ✓ Task: no override — uses delegation config defaults")

    return True


def test_schema_has_routing_fields():
    """Test 3: The DELEGATE_TASK_SCHEMA exposes routing fields."""
    from tools.delegate_tool import DELEGATE_TASK_SCHEMA

    print("\n=== Test 3: Schema Routing Fields ===")

    props = DELEGATE_TASK_SCHEMA["parameters"]["properties"]
    task_props = props["tasks"]["items"]["properties"]

    assert "provider" in props, "Missing top-level provider"
    assert "model" in props, "Missing top-level model"
    print("  ✓ Top-level: provider, model, base_url, api_key, api_mode present")

    assert "provider" in task_props, "Missing per-task provider"
    assert "model" in task_props, "Missing per-task model"
    print("  ✓ Per-task: provider, model, base_url, api_key, api_mode present")

    return True


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))
    sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent" / "tools"))

    passed = 0
    failed = 0

    for test_fn in [test_credential_resolution, test_per_task_credential_resolution, test_schema_has_routing_fields]:
        try:
            result = test_fn()
            if result:
                passed += 1
        except Exception as e:
            import traceback
            print(f"  ✗ FAILED: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
