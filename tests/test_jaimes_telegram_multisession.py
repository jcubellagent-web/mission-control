#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import jaimes_telegram_fast_ack as watcher


class MultiSessionWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "state.db"
        self.state = root / "fast_ack.json"
        with sqlite3.connect(self.db) as con:
            con.executescript("""
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, model TEXT, model_config TEXT,
                    session_key TEXT, chat_id TEXT, thread_id TEXT,
                    started_at REAL, origin_json TEXT, source TEXT, ended_at REAL
                );
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT, role TEXT, content TEXT,
                    platform_message_id TEXT, timestamp REAL
                );
            """)
            rows = [
                ("newer", "gpt-5.6-sol", "{}", "agent:main:telegram:group:-1003589561528:17", "-1003589561528", "17", time.time(), "{}", "telegram", None),
                ("older", "gpt-5.6-sol", "{}", "agent:main:telegram:group:-1003589561528:17", "-1003589561528", "17", time.time() - 60, "{}", "telegram", None),
            ]
            con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        self.patches = [
            patch.object(watcher, "HERMES_STATE_DB", self.db),
            patch.object(watcher, "STATE_PATH", self.state),
            patch.object(watcher, "complete_cards_from_final_responses", return_value=0),
            patch.object(watcher, "update_active_cards", return_value=[]),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def add_user(self, session: str, content: str, age: float = 0) -> int:
        with sqlite3.connect(self.db) as con:
            cur = con.execute(
                "INSERT INTO messages(session_id,role,content,platform_message_id,timestamp) VALUES (?,?,?,?,?)",
                (session, "user", content, None, time.time() - age),
            )
            assert cur.lastrowid is not None
            return int(cur.lastrowid)

    @staticmethod
    def fake_ack(event, model, state, dry_run, meta):
        return {
            "ok": True,
            "ack_message_id": "999",
            "key": "test-key",
            "model": model,
            "route": "test",
            "objective": event["prompt"],
            "run_id": event["run_id"],
            "last_card_update_at": watcher.utc_now(),
        }

    def test_fresh_prompt_in_older_owned_session_is_seen(self) -> None:
        message_id = self.add_user("older", "fresh rollover task")
        with patch.object(watcher, "send_ack", side_effect=self.fake_ack):
            result = watcher.poll_once()
        self.assertEqual(len(result["session_ids"]), 2)
        self.assertEqual(len(result["sent"]), 1)
        self.assertEqual(result["sent"][0]["result"]["run_id"], f"telegram-message-{message_id}")
        saved = json.loads(self.state.read_text())
        card = saved["active_cards"][f"telegram-message-{message_id}"]
        self.assertEqual(card["session_id"], "older")
        self.assertEqual(card["telegram_thread_id"], "17")

    def test_empty_synthetic_user_row_never_creates_card(self) -> None:
        self.add_user("newer", "")
        with patch.object(watcher, "send_ack", side_effect=self.fake_ack) as send:
            result = watcher.poll_once()
        self.assertEqual(result["sent"], [])
        send.assert_not_called()

    def test_stale_prompt_in_older_session_is_consumed_silently(self) -> None:
        self.add_user("older", "historical task", age=watcher.STALE_BOOTSTRAP_SECONDS + 30)
        with patch.object(watcher, "send_ack", side_effect=self.fake_ack) as send:
            result = watcher.poll_once()
        self.assertEqual(result["sent"], [])
        send.assert_not_called()

    def test_visible_ack_is_the_single_editable_work_card(self) -> None:
        event = {"ts": watcher.utc_now(), "prompt": "fix Telegram cards", "db_message_id": "9", "run_id": "telegram-message-9"}
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "17", "origin": {"message_id": "77"}}
        state = {}
        with patch.object(watcher, "set_eyes_reaction", return_value=True), \
             patch.object(watcher, "send_initial_ack", return_value={"ok": True, "result": {"message_id": 100}}) as initial, \
             patch.object(watcher, "edit_message", return_value={"ok": True}), \
             patch.object(watcher, "record_api_result"), \
             patch.object(watcher, "auto_route_for_prompt", return_value={}), \
             patch.object(watcher, "skill_for_prompt", return_value={"id": "", "label": "", "reason": ""}), \
             patch.object(watcher, "send_chat_action"), \
             patch.object(watcher, "send_message_draft"), \
             patch.object(watcher, "should_start_visible_card", return_value=True), \
             patch.object(watcher, "run_cmd", return_value={"ok": True, "message_id": "100"}) as run, \
             patch.object(watcher, "publish_jaimes"):
            result = watcher.send_ack(event, "openai-codex/gpt-5.6-sol", state, meta=meta)
        self.assertTrue(result["reaction_ok"])
        self.assertEqual(result["ack_message_id"], "100")
        initial.assert_not_called()
        start_cmd = run.call_args.args[0]
        self.assertIn("start", start_cmd)
        self.assertNotIn("--separate-message", start_cmd)

    def test_same_task_key_never_sends_a_second_ack_or_card(self) -> None:
        event = {"ts": watcher.utc_now(), "prompt": "fix Telegram cards", "db_message_id": "9", "run_id": "telegram-message-9"}
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "17"}
        state = {"processed_task_keys": ["jaimes-fast-ack--1003589561528-9"]}
        with patch.object(watcher, "send_initial_ack") as initial, \
             patch.object(watcher, "set_eyes_reaction") as reaction, \
             patch.object(watcher, "run_cmd") as run:
            result = watcher.send_ack(event, "openai-codex/gpt-5.6-sol", state, meta=meta)
        self.assertTrue(result["duplicate_suppressed"])
        initial.assert_not_called()
        reaction.assert_not_called()
        run.assert_not_called()

    def test_progress_burst_is_coalesced_to_one_edit(self) -> None:
        active = {
            "telegram-message-9": {
                "key": "card-key", "objective": "Test task", "model": "model",
                "route": "route", "status": "active", "session_id": "older",
                "telegram_chat_id": "-1003589561528", "telegram_thread_id": "17",
                "last_card_update_at": watcher.utc_now(),
            }
        }
        state = {"active_cards": active, "processed_progress_events": []}
        events = [
            {"event_id": f"e{i}", "run_id": "telegram-message-9", "type": "tool.result", "summary": f"step {i}"}
            for i in range(3)
        ]
        update_patch = self.patches[3]
        update_patch.stop()
        try:
            with patch.dict("os.environ", {"JAIMES_TELEGRAM_LIVE_CARDS": "1"}), \
                 patch.object(watcher, "recent_progress_events", return_value=events), \
                 patch.object(watcher, "run_cmd", return_value={"ok": True}) as run, \
                 patch.object(watcher, "publish_jaimes"), \
                 patch.object(watcher, "send_chat_action"):
                updates = watcher.update_active_cards(state, "older")
        finally:
            update_patch.start()
        self.assertEqual(len(updates), 1)
        self.assertEqual(run.call_count, 1)
        self.assertIn("e2", state["processed_progress_events"])

    def test_compaction_adjacent_replay_is_suppressed(self) -> None:
        timestamp = time.time()
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,?,?,?)", ("older", "user", "[CONTEXT COMPACTION — REFERENCE ONLY]", timestamp))
            con.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,?,?,?)", ("older", "user", "replayed historical user prompt", timestamp))
        with patch.object(watcher, "send_ack", side_effect=self.fake_ack) as send:
            result = watcher.poll_once()
        self.assertEqual(result["sent"], [])
        send.assert_not_called()

    def test_compaction_replay_after_marker_cursor_is_suppressed(self) -> None:
        original = self.add_user("older", "approve all next steps", age=30)
        marker = self.add_user("newer", "[CONTEXT COMPACTION — REFERENCE ONLY]")
        replay = self.add_user("newer", "approve all next steps")
        self.state.write_text(json.dumps({
            "direct_db_cursor:older": original,
            "direct_db_cursor:newer": marker,
            "active_cards": {},
            "last_checked_at": watcher.utc_now(),
        }))
        with patch.object(watcher, "send_ack", side_effect=self.fake_ack) as send:
            result = watcher.poll_once()
        self.assertEqual(result["sent"], [])
        send.assert_not_called()
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["direct_db_cursor:newer"], replay)

    def test_media_only_continuation_attaches_to_current_card(self) -> None:
        first = self.add_user("newer", "fix the Telegram lifecycle")
        media = self.add_user("newer", "[J|6218150306]\n[Image attached at: /tmp/example.jpg]\n[screenshot]")
        self.state.write_text(json.dumps({
            "direct_db_cursor:newer": first,
            "direct_db_cursor:older": 0,
            "last_checked_at": watcher.utc_now(),
            "active_cards": {
                "telegram-message-1": {
                    "status": "active",
                    "started_at": watcher.utc_now(),
                    "telegram_chat_id": "-1003589561528",
                    "telegram_thread_id": "17",
                    "key": "current-card",
                }
            },
        }))
        with patch.object(watcher, "send_ack", side_effect=self.fake_ack) as send:
            result = watcher.poll_once()
        self.assertEqual(result["sent"], [])
        send.assert_not_called()
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["multipart_rows_attached"], 1)
        self.assertIn(str(media), saved["active_cards"]["telegram-message-1"]["attachment_message_ids"])


if __name__ == "__main__":
    unittest.main()
