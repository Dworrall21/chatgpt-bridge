import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "chatgpt-cdp.py"
spec = importlib.util.spec_from_file_location("chatgpt_cdp", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_title_preserves_full_session_id_and_summary():
    session_id = "hermes-12345678-1234-1234-1234-123456789abc"
    title = mod.build_conversation_title("Fix the MoA browser bridge", session_id)
    assert title == f"Fix the MoA browser bridge — {session_id}"


def test_long_title_truncates_summary_not_session_id():
    session_id = "session-with-a-stable-identifier"
    title = mod.build_conversation_title("x" * 200, session_id)
    assert len(title) <= mod.MAX_TITLE_LENGTH
    assert title.endswith(f" — {session_id}")


def test_summary_is_stable_after_first_turn():
    assert mod.infer_summary("follow-up question", stored="Initial MoA setup") == "Initial MoA setup"
    assert mod.infer_summary("first prompt here", explicit="Hermes summary") == "Hermes summary"


def test_state_round_trip(tmp_path):
    state_file = tmp_path / "state.json"
    state = {
        "version": 1,
        "projects": {"MoA": "https://chatgpt.com/g/g-p-test/project"},
        "sessions": {
            "session-1": {
                "conversation_id": "conversation-1",
                "conversation_title": "Summary — session-1",
            }
        },
    }
    mod.save_state(state, state_file)
    assert mod.load_state(state_file) == state
