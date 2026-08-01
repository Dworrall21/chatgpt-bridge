import os
import threading
import time

import pytest

from chatgpt_bridge.config import Config, Flags, TransportConfig
from chatgpt_bridge.security.peer_auth import NonceGuard
from chatgpt_bridge.errors import AuthenticationFailedError


def test_nonce_guard_rejects_replay():
    g = NonceGuard(window_seconds=60)
    g.check("n1")
    with pytest.raises(AuthenticationFailedError):
        g.check("n1")


def test_nonce_guard_thread_safe():
    g = NonceGuard(window_seconds=60, max_entries=100)
    errors = []

    def worker(base):
        for i in range(50):
            try:
                g.check(f"{base}-{i}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    threads = [threading.Thread(target=worker, args=(f"t{n}",)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_nonce_guard_bounded_no_clear():
    g = NonceGuard(window_seconds=3600, max_entries=5)
    for i in range(10):
        g.check(f"n{i}")
    # oldest was evicted, but recent ones must still be replay-rejected
    with pytest.raises(AuthenticationFailedError):
        g.check("n9")


def test_config_pins_uid_when_required():
    cfg = Config(transport=TransportConfig(require_peer_uid=True, allowed_peer_uid=None))
    # load() applies pinning; simulate directly here:
    from chatgpt_bridge.config import Config as C

    class _L:  # noqa: N801
        pass

    # use load() with env flags off and a temp config-less default
    cfg2 = C.load()
    assert cfg2.transport.allowed_peer_uid == os.getuid()


def test_flags_gate_side_effects():
    flags = Flags(bridge_enabled=False, allow_send=True)
    assert flags.can_touch_app  # allow_send alone implies app access
    with pytest.raises(RuntimeError):
        flags.assert_send_allowed()  # but master switch must be on

    off = Flags()
    assert not off.can_touch_app


def test_driver_rejects_non_loopback_cdp():
    from chatgpt_bridge.app.driver import DesktopDriver
    from chatgpt_bridge.config import Config as C
    from chatgpt_bridge.errors import ProjectConfinementBreachError
    from chatgpt_bridge.registry import Registry

    class _C:  # noqa: N801
        app = type("A", (), {"cdp_url": "http://evil.example:9222", "renderer_origin": "http://127.0.0.1:5175", "required_project_id": "p", "required_project_name": "chatgpt-bridge", "required_mode": "Work", "required_model_label": "GPT-5.6 Sol", "required_effort_label": "High"})()

    with pytest.raises(ProjectConfinementBreachError):
        DesktopDriver(_C(), Registry.__new__(Registry))
