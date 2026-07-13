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


def test_compression_child_without_marker_suppresses_copied_prompt(tmp_path, monkeypatch):
    watcher = load_module()
    db = tmp_path / "state.db"
    prompt = "keep extending the existing work card"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT, end_reason TEXT)"
        )
        con.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, platform_message_id TEXT, timestamp REAL)"
        )
        con.execute("INSERT INTO sessions VALUES ('parent', NULL, 'compression')")
        con.execute("INSERT INTO sessions VALUES ('child', 'parent', NULL)")
        con.execute("INSERT INTO messages VALUES (1, 'parent', 'user', ?, NULL, ?)", (prompt, time.time()))
        con.execute("INSERT INTO messages VALUES (2, 'child', 'user', ?, NULL, ?)", (prompt, time.time()))
    monkeypatch.setattr(watcher, "HERMES_STATE_DB", db)
    event = watcher.recent_prompt_events_from_state_db("child", 0)[0]
    assert not watcher.internal_replay_prompt(prompt)
    assert watcher.session_has_compaction_marker("child")
    assert watcher.replayed_prompt_from_other_session(event)


def test_objective_uses_current_request_not_quoted_old_card():
    watcher = load_module()
    prompt = """I think this was a very old objective but you still showed it as if it were live. Please fix and make sure you map the correct objective to the current task.

🎯 Objective
Make objective cards summarize intent instead of quoting prompts.

Model: openai-codex/gpt-5.6-sol
Objective: Make objective cards summarize intent instead of quoting prompts.
Steps: 2
ETA: ~3–6 min"""
    assert watcher.objective_from_prompt(prompt) == "Fix current-task objective mapping"


def test_quoted_objective_does_not_override_new_formatting_request():
    watcher = load_module()
    prompt = """Please turn the final response summary into code block formatting as well.

🎯 Objective
Make objective cards summarize intent instead of quoting prompts.
Steps: 2"""
    assert watcher.objective_from_prompt(prompt) == "Format final summaries as code blocks"


def test_direct_objective_summary_request_still_maps_normally():
    watcher = load_module()
    prompt = "Please make objective cards summarize intent instead of quoting prompts."
    assert watcher.objective_from_prompt(prompt) == "Make objective cards summarize task intent"
