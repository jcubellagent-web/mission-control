import importlib.util
import sqlite3
import time
from pathlib import Path


def load_module():
    path = Path(__file__).parents[1] / "scripts" / "jaimes_telegram_fast_ack.py"
    spec = importlib.util.spec_from_file_location("jaimes_fast_ack_guard", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_empty_synthetic_user_rows_are_ignored(tmp_path, monkeypatch):
    watcher = load_module()
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, platform_message_id TEXT, timestamp REAL)")
        con.execute("INSERT INTO messages VALUES (1, 's', 'user', '', NULL, ?)", (time.time(),))
        con.execute("INSERT INTO messages VALUES (2, 's', 'user', 'real task', '42', ?)", (time.time(),))
    monkeypatch.setattr(watcher, "HERMES_STATE_DB", db)
    events = watcher.recent_prompt_events_from_state_db("s", 0)
    assert [event["prompt"] for event in events] == ["real task"]
    assert [event["db_message_id"] for event in events] == ["2"]
