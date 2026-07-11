import importlib.util
import json
import sqlite3
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "chatgpt-cdp.py"


def load_module():
    spec = importlib.util.spec_from_file_location("chatgpt_cdp", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_title_contains_complete_session_id():
    module = load_module()
    session_id = "12345678-1234-1234-1234-123456789abc"
    title = module.build_conversation_title("A useful Hermes summary", session_id)
    assert title == f"A useful Hermes summary — {session_id}"


def test_long_summary_is_trimmed_before_session_id():
    module = load_module()
    session_id = "session-abcdef"
    title = module.build_conversation_title("x" * 500, session_id, max_length=60)
    assert title.endswith(f" — {session_id}")
    assert len(title) <= 60


def test_reads_session_title_from_hermes_state(tmp_path):
    module = load_module()
    database = tmp_path / "state.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT)")
    connection.execute(
        "CREATE TABLE messages (id INTEGER, session_id TEXT, role TEXT, content TEXT)"
    )
    connection.execute(
        "INSERT INTO sessions (id, title) VALUES (?, ?)",
        ("session-1", "Stored Hermes title"),
    )
    connection.commit()
    connection.close()

    assert (
        module.read_hermes_session_summary("session-1", database)
        == "Stored Hermes title"
    )


def test_falls_back_to_first_user_message(tmp_path):
    module = load_module()
    database = tmp_path / "state.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
    connection.execute(
        "CREATE TABLE messages (id INTEGER, session_id TEXT, role TEXT, content TEXT)"
    )
    connection.execute("INSERT INTO sessions (id) VALUES (?)", ("session-2",))
    connection.execute(
        "INSERT INTO messages VALUES (?, ?, ?, ?)",
        (1, "session-2", "user", json.dumps({"text": "First session request"})),
    )
    connection.commit()
    connection.close()

    assert (
        module.read_hermes_session_summary("session-2", database)
        == "First session request"
    )


def test_mapping_is_keyed_by_hermes_session(tmp_path):
    module = load_module()
    state_file = tmp_path / "cdp_sessions.json"
    module.save_session_mapping("session-a", {"conversation_id": "chat-a"}, state_file)
    module.save_session_mapping("session-b", {"conversation_id": "chat-b"}, state_file)

    assert module.get_session_mapping("session-a", state_file)["conversation_id"] == "chat-a"
    assert module.get_session_mapping("session-b", state_file)["conversation_id"] == "chat-b"
