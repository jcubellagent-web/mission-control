#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# The watcher is a tracked runtime script rather than an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import jaimes_telegram_fast_ack as watcher


class MultiSessionWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "state.db"
        self.state = root / "fast_ack.json"
        self.handoff_dir = root / "handoffs"
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
            patch.object(watcher, "HANDOFF_DIR", self.handoff_dir),
            patch.object(watcher, "verify_bot_identity", return_value=True),
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

    def test_fast_ack_state_writer_repairs_permissions_on_next_write(self) -> None:
        self.state.write_text('{"old":true}', encoding="utf-8")
        self.state.chmod(0o666)
        watcher.save_json(self.state, {"private": True})
        self.assertEqual(json.loads(self.state.read_text()), {"private": True})
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)

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

    def test_inbox_ack_returns_header_and_editable_live_card_receipts(self) -> None:
        event = {"ts": watcher.utc_now(), "prompt": "fix Telegram cards", "db_message_id": "9", "run_id": "telegram-message-9"}
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1", "origin": {"message_id": "77"}}
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
             patch.object(watcher, "run_cmd", return_value={
                 "ok": True,
                 "stdout": json.dumps({"ok": True, "header_message_id": 99, "message_id": 100}),
                 "stderr": "",
             }) as run, \
             patch.object(watcher, "publish_jaimes"):
            result = watcher.send_ack(event, "openai-codex/gpt-5.6-sol", state, meta=meta)
        self.assertTrue(result["reaction_ok"])
        self.assertEqual(result["header_message_id"], "99")
        self.assertEqual(result["ack_message_id"], "100")
        initial.assert_not_called()
        start_cmd = run.call_args.args[0]
        self.assertIn("start", start_cmd)
        self.assertNotIn("--separate-message", start_cmd)

    def test_inbox_reaction_failure_emits_no_header_or_live_card(self) -> None:
        event = {
            "ts": watcher.utc_now(),
            "prompt": "@JAIMES verify routing",
            "platform_message_id": "77",
            "db_message_id": "9",
            "run_id": "telegram-message-9",
        }
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"}
        with patch.object(watcher, "set_eyes_reaction", return_value=False), \
             patch.object(watcher, "run_cmd") as run, \
             patch.object(watcher, "send_initial_ack") as initial:
            result = watcher.send_ack(event, "openai-codex/gpt-5.6-sol", {}, meta=meta)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "eyes_reaction_failed")
        run.assert_not_called()
        initial.assert_not_called()

    def test_bot_identity_success_clears_stale_active_api_error(self) -> None:
        state = {"last_telegram_api_error": {"at": "old", "method": "sendMessage", "ok": False}}
        identity_patch = self.patches[5]
        identity_patch.stop()
        try:
            with patch.object(watcher.work_card, "api_call", return_value={
                "ok": True,
                "result": {"username": "Jaimes_claw_bot"},
            }):
                self.assertTrue(watcher.verify_bot_identity(state))
        finally:
            identity_patch.start()
        self.assertNotIn("last_telegram_api_error", state)
        self.assertTrue(state["telegram_identity"]["ok"])

    def test_bot_identity_mismatch_fails_closed(self) -> None:
        state = {}
        identity_patch = self.patches[5]
        identity_patch.stop()
        try:
            with patch.object(watcher.work_card, "api_call", return_value={
                "ok": True,
                "result": {"username": "wrong_bot"},
            }):
                self.assertFalse(watcher.verify_bot_identity(state))
        finally:
            identity_patch.start()
        self.assertFalse(state["telegram_identity"]["ok"])
        self.assertEqual(state["telegram_identity"]["username"], "")

    def test_exact_handoff_receipt_unblocks_waiter_without_prompt_data(self) -> None:
        output: list[tuple[int, dict]] = []

        def wait() -> None:
            output.append(watcher.await_handoff("-1003589561528", "1", "77", 1.5))

        thread = threading.Thread(target=wait)
        thread.start()
        deadline = time.time() + 1
        record_path = None
        while time.time() < deadline:
            paths = list(self.handoff_dir.glob("*.json"))
            if paths:
                record_path = paths[0]
                break
            time.sleep(0.01)
        self.assertIsNotNone(record_path)
        with watcher.handoff_lock("-1003589561528", "1", "77") as path:
            accepted_at = watcher.dt.datetime.now(watcher.dt.timezone.utc)
            watcher.write_handoff_record(path, {
                "schema_version": 1,
                "status": "accepted",
                "agent": "jaimes",
                "chat_id": "-1003589561528",
                "thread_id": "1",
                "inbound_message_id": "77",
                "reaction_ok": True,
                "header_message_id": "101",
                "live_message_id": "102",
                "accepted_at": watcher.utc_now(),
                "expires_at": (accepted_at + watcher.dt.timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
            })
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        code, receipt = output[0]
        self.assertEqual(code, 0)
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["live_message_id"], "102")
        self.assertFalse({"prompt", "objective", "content"} & set(receipt))

    def test_handoff_timeout_is_cancelled_and_cannot_late_accept(self) -> None:
        code, receipt = watcher.await_handoff("-1003589561528", "1", "88", 0.05)
        self.assertEqual(code, 2)
        self.assertEqual(receipt["status"], "timeout")
        with watcher.handoff_lock("-1003589561528", "1", "88") as path:
            stored = watcher.load_json(path, {})
        self.assertEqual(stored["status"], "cancelled")

    def test_process_ack_event_requires_lease_and_persists_acceptance(self) -> None:
        event = {
            "ts": watcher.utc_now(),
            "prompt": "@JAIMES verify routing",
            "platform_message_id": "99",
            "db_message_id": "9",
            "run_id": "telegram-message-9",
        }
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"}
        with patch.object(watcher, "send_ack") as send:
            missing = watcher.process_ack_event(event, model="model", state={}, dry_run=False, meta=meta)
        self.assertTrue(missing["handoff_terminal_failure"])
        send.assert_not_called()

        future = watcher.dt.datetime.now(watcher.dt.timezone.utc) + watcher.dt.timedelta(seconds=30)
        with watcher.handoff_lock("-1003589561528", "1", "99") as path:
            watcher.write_handoff_record(path, {
                "schema_version": 1,
                "status": "waiting",
                "agent": "jaimes",
                "chat_id": "-1003589561528",
                "thread_id": "1",
                "inbound_message_id": "99",
                "created_at": watcher.utc_now(),
                "expires_at": future.isoformat().replace("+00:00", "Z"),
            })
        with patch.object(watcher, "send_ack", return_value={
            "ok": True,
            "reaction_ok": True,
            "header_message_id": "201",
            "ack_message_id": "202",
        }):
            accepted = watcher.process_ack_event(event, model="model", state={}, dry_run=False, meta=meta)
        self.assertTrue(accepted["ok"])
        with watcher.handoff_lock("-1003589561528", "1", "99") as path:
            stored = watcher.load_json(path, {})
        self.assertEqual(stored["status"], "accepted")
        self.assertEqual(stored["header_message_id"], "201")
        self.assertEqual(stored["live_message_id"], "202")

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

    def test_unstructured_native_final_is_edited_in_place_before_card_closes(self) -> None:
        user_id = self.add_user("older", "run the health check")
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO messages(session_id,role,content,platform_message_id,timestamp) VALUES (?,?,?,?,?)",
                ("older", "assistant", "TLDR\nHealth check passed.\nChallenges\nNone", "555", time.time()),
            )
        state = {
            "active_cards": {
                f"telegram-message-{user_id}": {
                    "key": "health-card",
                    "objective": "Verify JAIMES health",
                    "model": "openai-codex/gpt-5.6-sol",
                    "route": "JAIMES verified execution",
                    "status": "active",
                    "telegram_chat_id": "-1003589561528",
                    "telegram_thread_id": "1",
                }
            }
        }
        completion_patch = self.patches[2]
        completion_patch.stop()
        try:
            with patch.object(watcher, "edit_message", side_effect=[
                {"ok": False, "error": "temporary timeout"},
                {"ok": True, "result": {"message_id": 555}},
            ]) as edit, patch.object(watcher, "run_cmd", return_value={"ok": True}):
                self.assertEqual(watcher.complete_cards_from_final_responses(state, "older"), 0)
                card = state["active_cards"][f"telegram-message-{user_id}"]
                self.assertEqual(card["status"], "active")
                self.assertEqual(card["final_contract_status"], "retry_same_message")

                self.assertEqual(watcher.complete_cards_from_final_responses(state, "older"), 1)
        finally:
            completion_patch.start()
        card = state["active_cards"][f"telegram-message-{user_id}"]
        self.assertEqual(card["status"], "done")
        self.assertEqual(card["native_final_message_id"], "555")
        self.assertEqual(card["final_contract_status"], "canonical")
        self.assertEqual([call.args[0] for call in edit.call_args_list], ["555", "555"])
        rendered = edit.call_args_list[-1].args[1]
        self.assertTrue(watcher.final_contract_is_canonical(rendered))
        self.assertIn("Route: JAIMES verified execution", rendered)
        self.assertIn("Why: verified JAIMES execution", rendered)
        self.assertIn("Appropriate next steps:", rendered)

    def test_final_without_telegram_delivery_id_does_not_close_card(self) -> None:
        user_id = self.add_user("older", "verify delivery")
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO messages(session_id,role,content,platform_message_id,timestamp) VALUES (?,?,?,?,?)",
                ("older", "assistant", "Complete: Yes\nWhat was done:\n- Verified", "", time.time()),
            )
        state = {
            "active_cards": {
                f"telegram-message-{user_id}": {
                    "key": "delivery-card", "objective": "Verify delivery",
                    "model": "openai-codex/gpt-5.6-sol", "route": "JAIMES",
                    "status": "active",
                }
            }
        }
        completion_patch = self.patches[2]
        completion_patch.stop()
        try:
            with patch.object(watcher, "edit_message") as edit, patch.object(watcher, "run_cmd") as run:
                self.assertEqual(watcher.complete_cards_from_final_responses(state, "older"), 0)
        finally:
            completion_patch.start()
        card = state["active_cards"][f"telegram-message-{user_id}"]
        self.assertEqual(card["status"], "active")
        self.assertEqual(card["final_contract_status"], "waiting_for_telegram_delivery_id")
        edit.assert_not_called()
        run.assert_not_called()

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
