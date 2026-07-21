import importlib.util
import sqlite3
import sys
import tempfile
import time
from pathlib import Path


def load_module():
    path = Path(__file__).parents[1] / "scripts" / "jaimes_telegram_fast_ack.py"
    spec = importlib.util.spec_from_file_location("jaimes_fast_ack_guard", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    scripts_dir = str(path.parent)
    sys.path.insert(0, scripts_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts_dir)
    lifecycle_tmp = tempfile.TemporaryDirectory(prefix="jaimes-lifecycle-test-")
    lifecycle_base = Path(lifecycle_tmp.name)
    rollout_path = lifecycle_base / "rollout.json"
    rollout_path.write_text(
        '{"masterState":"off","globalKillSwitch":false,'
        '"brainKillSwitch":true,"hosts":{"josh2":true,"jaimes":true},'
        '"writerLifecycleVersion":3,"readerLifecycleVersions":[2,3],'
        '"shadowMinimumPerOwner":20,"brainFixtureMinimum":20}',
        encoding="utf-8",
    )
    # Keep the temporary directory alive for exactly as long as this isolated
    # module instance. Every test calls load_module(), so neither the v3 cache
    # nor its SQLite journal can leak across tests or touch the real home root.
    module._TEST_LIFECYCLE_TMP = lifecycle_tmp
    module.LIFECYCLE_PRIVATE_ROOT = lifecycle_base / "private"
    module.LIFECYCLE_ROLLOUT_PATH = rollout_path
    module._GATEWAY_LIFECYCLE = None
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


def test_objective_copy_complaint_maps_to_interpreted_intent():
    watcher = load_module()
    prompt = "The Telegram and Control Tower objective is just a copy of my message. Interpret it in your own words first."
    assert watcher.objective_from_prompt(prompt) == "Make agent task objectives reflect interpreted intent"


def test_near_copy_gate_defers_telegram_and_control_tower(monkeypatch):
    watcher = load_module()
    prompt = "Please stabilize the live card"
    monkeypatch.setattr(watcher, "objective_from_prompt", lambda _prompt: prompt)
    monkeypatch.setattr(watcher, "objective_is_near_copy", lambda _prompt, _objective: True)
    monkeypatch.setattr(watcher, "semantic_reinterpretation", lambda _prompt: "")
    monkeypatch.setattr(watcher, "skill_for_prompt", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must wait before skill selection")))
    monkeypatch.setattr(watcher, "publish_jaimes", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not publish")))
    monkeypatch.setattr(watcher, "run_cmd", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not start a card")))
    result = watcher.send_ack(
        {"platform_message_id": "42", "db_message_id": "7", "ts": "2026-07-13T20:00:00Z", "prompt": prompt, "run_id": "telegram-message-7"},
        model="openai-codex/gpt-5.6-sol",
        state={},
        dry_run=True,
        meta={"telegram_chat_id": "-1003589561528", "telegram_thread_id": "17"},
    )
    assert result["status"] == "awaiting-objective-interpretation"
    assert result["objective"] == ""
    assert result["header_message_id"] == ""
    assert result["ack_message_id"] == ""
    assert result["requires_objective_interpretation"] is True


def _stable_surface_stubs(watcher, monkeypatch):
    monkeypatch.setattr(watcher, "set_eyes_reaction", lambda *args, **kwargs: True)
    monkeypatch.setattr(watcher, "auto_route_for_prompt", lambda *args, **kwargs: {})
    monkeypatch.setattr(watcher, "skill_for_prompt", lambda *args, **kwargs: {"id": "telegram-task-flow", "label": "Telegram task flow"})
    monkeypatch.setattr(watcher, "runtime_route", lambda model: ("JAIMES verified execution", "stable test route"))
    monkeypatch.setattr(watcher, "work_card_target_args", lambda meta: [])
    monkeypatch.setattr(watcher, "send_chat_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(watcher, "publish_jaimes", lambda *args, **kwargs: None)


def test_topic17_visible_card_is_sent_once_as_a_fresh_managed_message(monkeypatch):
    watcher = load_module()
    _stable_surface_stubs(watcher, monkeypatch)
    calls = []
    monkeypatch.setattr(watcher, "should_start_visible_card", lambda *args, **kwargs: True)
    monkeypatch.setattr(watcher, "run_cmd", lambda command, **kwargs: calls.append(command) or {"ok": True, "returncode": 0, "stdout": '{"ok": true, "message_id": 444}', "stderr": ""})
    monkeypatch.setattr(watcher, "send_initial_ack", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("placeholder/objective bubble must not be sent")))
    monkeypatch.setattr(watcher, "edit_message", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("initial Telegram surface must not be edited")))
    result = watcher.send_ack(
        {"platform_message_id": "42", "db_message_id": "7", "ts": "2026-07-13T20:00:00Z", "prompt": "Please stabilize the live card", "run_id": "telegram-message-7"},
        model="openai-codex/gpt-5.6-sol",
        state={},
        meta={"telegram_chat_id": "-1003589561528", "telegram_thread_id": "17"},
    )
    assert result["ack_message_id"] == "444"
    assert len(calls) == 1
    assert "--ack-message-id" not in calls[0]
    assert "--separate-message" in calls[0]


def test_topic_one_requires_a_direct_jaimes_mention():
    watcher = load_module()
    assert watcher.direct_jaimes_mention("@JAIMES please take this")
    assert watcher.direct_jaimes_mention("hey,@JAIMES please take this")
    assert not watcher.direct_jaimes_mention("please let Josh handle this")


def test_non_card_ack_is_sent_once_at_final_objective_text(monkeypatch):
    watcher = load_module()
    _stable_surface_stubs(watcher, monkeypatch)
    sent = []
    monkeypatch.setattr(watcher, "should_start_visible_card", lambda *args, **kwargs: False)
    monkeypatch.setattr(watcher, "run_cmd", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("work card must not start")))
    monkeypatch.setattr(watcher, "send_initial_ack", lambda text, **kwargs: sent.append(text) or {"ok": True, "result": {"message_id": 555}})
    monkeypatch.setattr(watcher, "edit_message", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("single objective surface must not be edited")))
    result = watcher.send_ack(
        {"platform_message_id": "43", "db_message_id": "8", "ts": "2026-07-13T20:00:01Z", "prompt": "Quick status check", "run_id": "telegram-message-8"},
        model="openai-codex/gpt-5.6-sol",
        state={},
        meta={"telegram_chat_id": "-1003589561528", "telegram_thread_id": "17"},
    )
    assert result["ack_message_id"] == "555"
    assert len(sent) == 1
    assert "confirming model and objective" not in sent[0]
    assert "Objective" in sent[0]
