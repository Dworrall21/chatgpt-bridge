import json
import subprocess
import sys
import os

import pytest

HERE = os.path.dirname(__file__)
ROUTE = os.path.join(HERE, "..", "..", "scripts", "route.py")


def _route(*args, env_extra=None):
    env = dict(os.environ)
    env.pop("CHATGPT_BRIDGE_AUTOROUTE", None)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, ROUTE, *args], capture_output=True, text=True, env=env)
    return json.loads(r.stdout)


def test_routing_off_by_default():
    d = _route("--kind", "planning", "--instruction", "x" * 200, "--context-chars", "20000")
    assert d["delegate"] is False
    assert d["autonomous_routing_enabled"] is False


def test_routing_on_with_env_switch():
    d = _route("--kind", "planning", "--instruction", "x" * 200, "--context-chars", "20000",
               env_extra={"CHATGPT_BRIDGE_AUTOROUTE": "1"})
    assert d["delegate"] is True
    assert d["provider"] == "chatgpt_desktop_bridge"


def test_routing_off_for_local_classes_even_when_enabled():
    d = _route("--kind", "synthesis", "--instruction", "x" * 200, "--context-chars", "20000",
               env_extra={"CHATGPT_BRIDGE_AUTOROUTE": "1"})
    assert d["delegate"] is False


def test_routing_rejects_tool_tasks_when_enabled():
    d = _route("--kind", "review", "--instruction", "x" * 200, "--context-chars", "20000",
               "--requires-tools", env_extra={"CHATGPT_BRIDGE_AUTOROUTE": "1"})
    assert d["delegate"] is False


def test_force_overrides_switch():
    d = _route("--kind", "planning", "--instruction", "x" * 200, "--context-chars", "20000", "--force")
    assert d["delegate"] is True


def test_small_task_stays_local_even_with_switch():
    d = _route("--kind", "planning", "--instruction", "short", "--context-chars", "0",
               env_extra={"CHATGPT_BRIDGE_AUTOROUTE": "1"})
    assert d["delegate"] is False
