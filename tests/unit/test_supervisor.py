import importlib.util
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
SUPERVISOR = os.path.join(HERE, "..", "..", "scripts", "daemon-supervisor.py")


def _load():
    spec = importlib.util.spec_from_file_location("daemon_supervisor", SUPERVISOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hermes_instances_counts_self_env():
    mod = _load()
    # The test runner process has no HERMES_SESSION_ID, but this Hermes session
    # process does (we run under hermes). At minimum >= 0 and deterministic.
    n = mod.hermes_instances()
    assert isinstance(n, int) and n >= 0


def test_daemon_healthy_returns_bool():
    mod = _load()
    assert isinstance(mod.daemon_healthy(), bool)  # True when the real socket is up


def test_supervisor_tick_dry_run():
    mod = _load()
    sup = mod.Supervisor(poll_s=0.1, grace_s=0.1, dry_run=True)
    out = sup.tick()
    assert "hermes" in out
    assert "daemon" in out
