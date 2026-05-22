"""Init BridgeState db schema."""
import sqlite3, os, json

db_path = os.path.expanduser("~/.hermes/profiles/chatgpt-bridge/state.db")
os.makedirs(os.path.dirname(db_path), exist_ok=True)
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS kv_store (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
""")
conn.commit()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("Tables:", cur.fetchall())
conn.close()
print("BridgeState schema created OK")
