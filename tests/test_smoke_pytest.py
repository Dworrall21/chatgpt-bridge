import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = REPO_ROOT / "bridge-host.py"


def load_bridge_module():
    spec = importlib.util.spec_from_file_location("bridge_host", BRIDGE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {BRIDGE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_is_trusted_url_blocks_localhost():
    bridge = load_bridge_module()
    assert bridge.is_trusted_url("http://localhost/foo") is False
    assert bridge.is_trusted_url("https://127.0.0.1/foo") is False


def test_is_trusted_url_allows_chatgpt_host():
    bridge = load_bridge_module()
    assert bridge.is_trusted_url("https://chatgpt.com/") is True
