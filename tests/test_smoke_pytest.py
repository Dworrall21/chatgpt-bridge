import importlib.util
import tempfile
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


def test_conversation_state_helpers_pin_and_reset_per_session():
    bridge = load_bridge_module()
    with tempfile.TemporaryDirectory() as tmp:
        state = bridge.BridgeState(path=Path(tmp) / "state.json")
        state.set_conversation("hermes-1", "chat-old", "Old", privacy_mode="temporary")
        session_key, conversation_id, new_conversation, explicit = bridge._resolve_conversation_state(
            state,
            {"session_id": "hermes-1", "model_search": "thinking"},
        )
        assert session_key == "hermes-1"
        assert conversation_id == "chat-old"
        assert new_conversation is False
        assert explicit is False

        session_key2, conversation_id2, new_conversation2, explicit2 = bridge._resolve_conversation_state(
            state,
            {"session_id": "hermes-1", "new": True},
        )
        assert session_key2 == "hermes-1"
        assert conversation_id2 is None
        assert new_conversation2 is True
        assert explicit2 is False


def test_openai_endpoint_without_session_id_does_not_reuse_global_default():
    bridge = load_bridge_module()
    with tempfile.TemporaryDirectory() as tmp:
        state = bridge.BridgeState(path=Path(tmp) / "state.json")
        state.last_conversation_id = "global-old"
        state.set_conversation("default", "default-old", "Old Default", privacy_mode="standard")

        session_key, conversation_id, new_conversation, explicit = bridge._resolve_conversation_state(
            state,
            {"messages": [{"role": "user", "content": "hello"}]},
            allow_default_fallback=False,
        )

        assert session_key == "default"
        assert conversation_id is None
        assert new_conversation is False
        assert explicit is False


def test_temporary_mode_does_not_reuse_standard_memory_enabled_pin():
    bridge = load_bridge_module()
    with tempfile.TemporaryDirectory() as tmp:
        state = bridge.BridgeState(path=Path(tmp) / "state.json")
        state.set_conversation("hermes-privacy", "standard-old", "Old", privacy_mode="standard")

        session_key, conversation_id, new_conversation, explicit = bridge._resolve_conversation_state(
            state,
            {"session_id": "hermes-privacy", "privacy_mode": "temporary"},
        )

        assert session_key == "hermes-privacy"
        assert conversation_id is None
        assert new_conversation is False
        assert explicit is False


def test_temporary_mode_reuses_temporary_pin_for_same_session():
    bridge = load_bridge_module()
    with tempfile.TemporaryDirectory() as tmp:
        state = bridge.BridgeState(path=Path(tmp) / "state.json")
        state.set_conversation("hermes-privacy", "temp-old", "Old", privacy_mode="temporary")

        session_key, conversation_id, new_conversation, explicit = bridge._resolve_conversation_state(
            state,
            {"session_id": "hermes-privacy", "temporary_chat": True},
        )

        assert session_key == "hermes-privacy"
        assert conversation_id == "temp-old"
        assert new_conversation is False
        assert explicit is False


def test_bridge_state_tracks_sessions_and_reset():
    bridge = load_bridge_module()
    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "state.json"
        state = bridge.BridgeState(path=state_path)
        assert state.get_conversation("hermes-1") is None
        state.set_conversation("hermes-1", "chat-xyz", "Title")
        assert state.get_conversation("hermes-1") == {"conversation_id": "chat-xyz", "conversation_title": "Title"}
        previous = state.clear_conversation("hermes-1")
        assert previous == {"conversation_id": "chat-xyz", "conversation_title": "Title"}
        assert state.get_conversation("hermes-1") is None


def test_fresh_session_navigation_contract_is_background_owned():
    bridge_src = BRIDGE_PATH.read_text()
    background_src = (REPO_ROOT / "background.js").read_text()

    # Bridge should pass new_conversation flag (computed via _resolve_conversation_state)
    assert '"new_conversation": new_conversation,' in bridge_src
    assert "_resolve_conversation_state" in bridge_src
    # Background should handle the navigation contract
    assert "data.new_conversation" in background_src
    assert "handleBridgeNewChat" in background_src
    assert "DEFAULT_TEMPORARY_CHAT_URL" in background_src
    assert "freshChatUrlFor" in background_src
    assert "connectedTabs.delete(tab.id)" in background_src
    assert 'injectContentScript(tab.id, "conversation-navigation")' in background_src
