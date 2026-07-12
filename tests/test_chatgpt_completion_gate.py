import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "chatgpt-cdp.py"
spec = importlib.util.spec_from_file_location("chatgpt_cdp_completion", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def completed_snapshot():
    return {
        "found": True,
        "generating": False,
        "actions": {"copy": True, "good": True, "bad": True},
    }


def test_completion_requires_copy_thumbs_up_and_thumbs_down():
    snapshot = completed_snapshot()
    assert mod.response_snapshot_complete(snapshot, stable_count=1)

    for action in ("copy", "good", "bad"):
        incomplete = completed_snapshot()
        incomplete["actions"][action] = False
        assert not mod.response_snapshot_complete(incomplete, stable_count=1)


def test_completion_requires_finished_stable_text():
    snapshot = completed_snapshot()
    assert not mod.response_snapshot_complete(snapshot, stable_count=0)

    snapshot["generating"] = True
    assert not mod.response_snapshot_complete(snapshot, stable_count=2)

    snapshot = completed_snapshot()
    snapshot["found"] = False
    assert not mod.response_snapshot_complete(snapshot, stable_count=2)
