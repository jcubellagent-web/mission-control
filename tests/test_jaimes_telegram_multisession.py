#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import inspect
import json
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

# The watcher is a tracked runtime script rather than an installed package.
# Prefer the sibling staged copy when this regression file is exercised before
# deployment, while retaining the canonical repository layout after it lands.
test_dir = Path(__file__).resolve().parent
staged_script = test_dir / "jaimes_telegram_fast_ack.py"
script_dir = test_dir if staged_script.exists() else Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(script_dir))
try:
    import jaimes_completion_evidence  # noqa: F401
except ModuleNotFoundError:
    completion_evidence = types.ModuleType("jaimes_completion_evidence")
    completion_evidence.write_completion_evidence = lambda **_kwargs: None
    sys.modules["jaimes_completion_evidence"] = completion_evidence
import jaimes_telegram_fast_ack as watcher


class MultiSessionWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "state.db"
        self.state = root / "fast_ack.json"
        self.handoff_dir = root / "handoffs"
        self.lifecycle_root = root / "telegram-lifecycle"
        self.lifecycle_rollout = root / "telegram-lifecycle-rollout.json"
        self.lifecycle_rollout.write_text(json.dumps({
            "masterState": "off",
            "globalKillSwitch": False,
            "brainKillSwitch": True,
            "hosts": {"josh2": True, "jaimes": True},
            "writerLifecycleVersion": 3,
            "readerLifecycleVersions": [2, 3],
            "shadowMinimumPerOwner": 20,
            "brainFixtureMinimum": 20,
        }), encoding="utf-8")
        # The imported watcher is shared across this unittest class. Clear its
        # cached lifecycle before every case and redirect all durable state to
        # this case's temporary root before any send/update helper can run.
        watcher._GATEWAY_LIFECYCLE = None
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
            patch.object(watcher, "set_eyes_reaction", return_value=True),
            # Keep the historical patch indices above stable: several legacy
            # tests intentionally stop and restart those exact mocks.
            patch.object(watcher, "LIFECYCLE_PRIVATE_ROOT", self.lifecycle_root),
            patch.object(watcher, "LIFECYCLE_ROLLOUT_PATH", self.lifecycle_rollout),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        watcher._GATEWAY_LIFECYCLE = None
        for item in reversed(self.patches):
            item.stop()
        watcher._GATEWAY_LIFECYCLE = None
        self.tmp.cleanup()

    def add_user(self, session: str, content: str, age: float = 0) -> int:
        with sqlite3.connect(self.db) as con:
            cur = con.execute(
                "INSERT INTO messages(session_id,role,content,platform_message_id,timestamp) VALUES (?,?,?,?,?)",
                (session, "user", content, None, time.time() - age),
            )
            assert cur.lastrowid is not None
            return int(cur.lastrowid)

    def test_identity_publish_targets_josh2_canonical_ledger(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(watcher.subprocess, "run", return_value=completed) as run:
            ok = watcher.publish_jaimes(
                "Review current market signals",
                "active",
                "Executing a dashboard-safe research pass",
                work_id="work-telegram-safe",
                run_id="run-telegram-safe",
                phase="research",
                model_id="gemini-2.5-pro",
                route_verified=True,
                origin_claim_hash="a" * 64,
                work_event="start",
            )
        self.assertTrue(ok)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "ssh")
        self.assertEqual(watcher.CONTROL_TOWER_SSH_HOST, "josh2.0@josh2")
        self.assertIn(watcher.CONTROL_TOWER_SSH_HOST, command)
        remote = command[-1]
        self.assertIn("/Users/josh2.0/.openclaw/workspace/mission-control", remote)
        self.assertIn("--work-id work-telegram-safe", remote)
        self.assertIn("--run-id run-telegram-safe", remote)
        self.assertIn("--origin-claim-hash", remote)
        self.assertIn("--route-verified", remote)

    @staticmethod
    def fake_ack(event, model, state, dry_run, meta, **_kwargs):
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

    def test_awaiting_objective_result_never_registers_active_or_pending_state(self) -> None:
        self.add_user("older", "transport metadata before an unresolved request")

        def awaiting(event, model, state, dry_run, meta, **_kwargs):
            return {
                "ok": True,
                "status": "awaiting-objective-interpretation",
                "requires_objective_interpretation": True,
                "ack_message_id": "",
                "key": "unresolved-key",
                "model": model,
                "route": "",
                "objective": "",
                "run_id": event["run_id"],
                "last_card_update_at": watcher.utc_now(),
            }

        with patch.object(watcher, "send_ack", side_effect=awaiting):
            watcher.poll_once()
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["active_cards"], {})
        self.assertNotIn("latest_pending_ack", saved)
        self.assertNotIn("unresolved-key", saved.get("processed_task_keys", []))

    def test_result_without_positive_message_receipt_is_never_pending(self) -> None:
        self.add_user("older", "verify no-receipt handling")

        def no_receipt(event, model, state, dry_run, meta, **_kwargs):
            return {
                "ok": True,
                "ack_message_id": "0",
                "key": "no-receipt-key",
                "model": model,
                "route": "JAIMES verified execution",
                "objective": "Verify no-receipt handling",
                "run_id": event["run_id"],
                "last_card_update_at": watcher.utc_now(),
            }

        with patch.object(watcher, "send_ack", side_effect=no_receipt):
            watcher.poll_once()
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["active_cards"], {})
        self.assertNotIn("latest_pending_ack", saved)
        self.assertNotIn("no-receipt-key", saved.get("processed_task_keys", []))

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
        with patch.object(watcher, "objective_from_prompt", return_value="Fix Telegram cards"), \
             patch.object(watcher, "objective_is_near_copy", return_value=False), \
             patch.object(watcher, "set_eyes_reaction", return_value=True), \
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
        self.assertIn("--separate-message", start_cmd)
        self.assertEqual(start_cmd[start_cmd.index("--timeout") + 1], "4")
        self.assertEqual(run.call_args.kwargs["timeout"], 12)

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

    def test_inbox_card_receipt_requires_positive_header_and_live_ids(self) -> None:
        event = {
            "ts": watcher.utc_now(),
            "prompt": "@JAIMES verify receipt validation",
            "platform_message_id": "78",
            "db_message_id": "10",
            "run_id": "telegram-message-10",
        }
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"}
        with patch.object(watcher, "objective_from_prompt", return_value="Verify receipt validation"), \
             patch.object(watcher, "objective_is_near_copy", return_value=False), \
             patch.object(watcher, "set_eyes_reaction", return_value=True), \
             patch.object(watcher, "auto_route_for_prompt", return_value={}), \
             patch.object(watcher, "skill_for_prompt", return_value={"id": "", "label": "", "reason": ""}), \
             patch.object(watcher, "run_cmd", return_value={
                 "ok": True,
                 "stdout": json.dumps({"ok": True, "header_message_id": "invalid", "message_id": 0}),
                 "stderr": "",
             }), \
             patch.object(watcher, "record_api_result"), \
             patch.object(watcher, "send_chat_action"), \
             patch.object(watcher, "publish_jaimes"):
            result = watcher.send_ack(event, "model", {}, meta=meta)
        self.assertFalse(result["ok"])
        self.assertEqual(result["header_message_id"], "")
        self.assertEqual(result["ack_message_id"], "")

    def test_inbox_ambiguous_card_delivery_propagates_durable_quarantine(self) -> None:
        event = {
            "ts": watcher.utc_now(),
            "prompt": "@JAIMES verify ambiguous delivery fencing",
            "platform_message_id": "79",
            "db_message_id": "11",
            "run_id": "telegram-message-11",
        }
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"}
        durable_state = {
            "cards": {
                "jaimes-fast-ack--1003589561528-79": {
                    "header_message_id": "301",
                    "message_id": None,
                    "live_delivery_status": "indeterminate",
                }
            }
        }
        with patch.object(watcher, "objective_from_prompt", return_value="Verify ambiguous delivery fencing"), \
             patch.object(watcher, "objective_is_near_copy", return_value=False), \
             patch.object(watcher, "set_eyes_reaction", return_value=True), \
             patch.object(watcher, "auto_route_for_prompt", return_value={}), \
             patch.object(watcher, "skill_for_prompt", return_value={"id": "", "label": "", "reason": ""}), \
             patch.object(watcher, "run_cmd", return_value={
                 "ok": False,
                 "stdout": "",
                 "stderr": "Telegram delivery timed out",
             }), \
             patch.object(watcher.work_card, "load_state", return_value=durable_state), \
             patch.object(watcher, "record_api_result"), \
             patch.object(watcher, "send_chat_action"), \
             patch.object(watcher, "publish_jaimes"):
            result = watcher.send_ack(event, "model", {}, meta=meta)
        self.assertFalse(result["ok"])
        self.assertTrue(result["surface_indeterminate"])
        self.assertEqual(result["header_message_id"], "301")
        self.assertEqual(result["ack_message_id"], "")

    def test_topic17_card_child_timeout_is_bounded_inside_parent_timeout(self) -> None:
        event = {
            "ts": watcher.utc_now(),
            "prompt": "restore the JAIMES live updates",
            "platform_message_id": "803",
            "db_message_id": "51",
            "run_id": "telegram-message-51",
        }
        meta = {
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        receipt = {"ok": True, "message_id": 803}
        with patch.object(watcher, "objective_from_prompt", return_value="Restore JAIMES live updates"), \
             patch.object(watcher, "objective_is_near_copy", return_value=False), \
             patch.object(watcher, "skill_for_prompt", return_value={"id": "", "label": "", "reason": ""}), \
             patch.object(watcher, "should_start_visible_card", return_value=True), \
             patch.object(watcher, "work_card_surface_receipt", return_value={}), \
             patch.object(watcher, "run_cmd", return_value={
                 "ok": True,
                 "stdout": json.dumps(receipt),
                 "stderr": "",
             }) as run, \
             patch.object(watcher, "record_api_result"), \
             patch.object(watcher, "send_chat_action"), \
             patch.object(watcher, "publish_jaimes"):
            result = watcher.send_ack(event, "openai-codex/gpt-5.6-sol", {}, meta=meta)

        self.assertTrue(result["ok"])
        self.assertEqual(result["ack_message_id"], "803")
        self.assertEqual(result["header_message_id"], "")
        command = run.call_args.args[0]
        self.assertEqual(command[2], "start")
        self.assertEqual(command[command.index("--timeout") + 1], str(watcher.WORK_CARD_API_TIMEOUT_SECONDS))
        self.assertIn("--no-brain-feed", command)
        self.assertIn("--separate-message", command)
        self.assertLess(watcher.WORK_CARD_API_TIMEOUT_SECONDS, watcher.WORK_CARD_PARENT_TIMEOUT_SECONDS)
        self.assertEqual(run.call_args.kwargs["timeout"], watcher.WORK_CARD_PARENT_TIMEOUT_SECONDS)
        self.assertEqual(run.call_args.kwargs["extra_env"], {"ALLOW_NO_BRAIN_FEED": "1"})

    def test_topic17_ambiguous_card_delivery_propagates_durable_quarantine(self) -> None:
        event = {
            "ts": watcher.utc_now(),
            "prompt": "restore the JAIMES live updates",
            "platform_message_id": "804",
            "db_message_id": "52",
            "run_id": "telegram-message-52",
        }
        meta = {
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        with patch.object(watcher, "objective_from_prompt", return_value="Restore JAIMES live updates"), \
             patch.object(watcher, "objective_is_near_copy", return_value=False), \
             patch.object(watcher, "skill_for_prompt", return_value={"id": "", "label": "", "reason": ""}), \
             patch.object(watcher, "should_start_visible_card", return_value=True), \
             patch.object(watcher, "run_cmd", return_value={
                 "ok": False,
                 "stdout": "",
                 "stderr": "command timed out after 12s",
             }), \
             patch.object(watcher, "work_card_surface_receipt", return_value={
                 "message_id": "",
                 "surface_indeterminate": True,
             }), \
             patch.object(watcher, "record_api_result"), \
             patch.object(watcher, "send_chat_action"), \
             patch.object(watcher, "publish_jaimes"):
            result = watcher.send_ack(event, "openai-codex/gpt-5.6-sol", {}, meta=meta)

        self.assertFalse(result["ok"])
        self.assertTrue(result["surface_indeterminate"])
        self.assertEqual(result["ack_message_id"], "")

    def test_definitive_surface_failure_keeps_cursor_for_one_safe_retry(self) -> None:
        real_datetime = watcher.dt.datetime

        class FrozenDateTime(real_datetime):
            current = real_datetime.now(watcher.dt.timezone.utc).replace(microsecond=0)

            @classmethod
            def now(cls, tz=None):
                value = cls.current
                return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

        event = {
            "session_id": "older",
            "ts": FrozenDateTime.current.isoformat().replace("+00:00", "Z"),
            "prompt": "restore JAIMES live updates",
            "platform_message_id": "805",
            "db_message_id": "55",
            "run_id": "telegram-message-55",
        }
        meta = {
            "sessionId": "older",
            "model": "openai-codex/gpt-5.6-sol",
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        failed = {
            "ok": False,
            "key": "topic17-retry",
            "objective": "Restore JAIMES live updates",
            "route": "JAIMES verified execution",
            "ack_message_id": "",
            "run_id": "telegram-message-55",
        }
        succeeded = {
            **failed,
            "ok": True,
            "ack_message_id": "905",
            "last_card_update_at": watcher.utc_now(),
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        self.state.write_text(json.dumps({"direct_db_cursor:older": 0}), encoding="utf-8")

        def events_after_cursor(_session_id: str, cursor: int):
            return [event] if cursor < 55 else []

        with patch.object(watcher, "active_hermes_sessions_metadata", return_value=[meta]), \
             patch.object(watcher, "recent_prompt_events_from_state_db", side_effect=events_after_cursor), \
             patch.object(watcher, "send_ack", side_effect=[failed, succeeded]) as send, \
             patch.object(watcher.dt, "datetime", FrozenDateTime):
            first = watcher.poll_once()
            first_saved = json.loads(self.state.read_text(encoding="utf-8"))
            FrozenDateTime.current += watcher.dt.timedelta(
                seconds=watcher.SURFACE_RETRY_BASE_SECONDS + 1
            )
            second = watcher.poll_once()
            second_saved = json.loads(self.state.read_text(encoding="utf-8"))
            third = watcher.poll_once()

        self.assertFalse(first["sent"][0]["result"]["ok"])
        self.assertEqual(first_saved["direct_db_cursor:older"], 0)
        self.assertTrue(second["sent"][0]["result"]["ok"])
        self.assertEqual(second_saved["direct_db_cursor:older"], 55)
        self.assertEqual(third["sent"], [])
        self.assertEqual(send.call_count, 2)

    def test_failed_surface_retry_is_persistently_backed_off_between_fast_polls(self) -> None:
        event = {
            "session_id": "older",
            "ts": watcher.utc_now(),
            "prompt": "restore JAIMES live updates",
            "platform_message_id": "806",
            "db_message_id": "56",
            "run_id": "telegram-message-56",
        }
        meta = {
            "sessionId": "older",
            "model": "openai-codex/gpt-5.6-sol",
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        failed = {
            "ok": False,
            "key": "topic17-backed-off-retry",
            "objective": "Restore JAIMES live updates",
            "route": "JAIMES verified execution",
            "ack_message_id": "",
            "run_id": "telegram-message-56",
        }
        self.state.write_text(json.dumps({"direct_db_cursor:older": 0}), encoding="utf-8")

        def events_after_cursor(_session_id: str, cursor: int):
            return [event] if cursor < 56 else []

        with patch.object(watcher, "active_hermes_sessions_metadata", return_value=[meta]), \
             patch.object(watcher, "recent_prompt_events_from_state_db", side_effect=events_after_cursor), \
             patch.object(watcher, "send_ack", return_value=failed) as send:
            first = watcher.poll_once()
            watcher.poll_once()

        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertFalse(first["sent"][0]["result"]["ok"])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(saved["direct_db_cursor:older"], 0)

    def test_pending_surface_retry_is_not_discarded_by_two_minute_stale_cutoff(self) -> None:
        event = {
            "session_id": "older",
            "ts": watcher.utc_now(),
            "prompt": "restore JAIMES live updates",
            "platform_message_id": "807",
            "db_message_id": "57",
            "run_id": "telegram-message-57",
        }
        meta = {
            "sessionId": "older",
            "model": "openai-codex/gpt-5.6-sol",
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        failed = {
            "ok": False,
            "key": "topic17-aged-retry",
            "objective": "Restore JAIMES live updates",
            "route": "JAIMES verified execution",
            "ack_message_id": "",
            "run_id": "telegram-message-57",
        }
        ages = iter([0.0, float(watcher.STALE_BOOTSTRAP_SECONDS + 30)])
        self.state.write_text(json.dumps({"direct_db_cursor:older": 0}), encoding="utf-8")

        def events_after_cursor(_session_id: str, cursor: int):
            return [event] if cursor < 57 else []

        with patch.object(watcher, "active_hermes_sessions_metadata", return_value=[meta]), \
             patch.object(watcher, "recent_prompt_events_from_state_db", side_effect=events_after_cursor), \
             patch.object(watcher, "event_age_seconds", side_effect=lambda _ts: next(ages)), \
             patch.object(watcher, "send_ack", return_value=failed) as send:
            watcher.poll_once()
            watcher.poll_once()

        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(saved["direct_db_cursor:older"], 0)
        self.assertNotIn(watcher.prompt_event_id(event), saved["acked_prompt_events"])

    def test_newer_same_session_event_supersedes_backoff_and_clears_exact_delivery_incident(self) -> None:
        now = watcher.dt.datetime.now(watcher.dt.timezone.utc).replace(microsecond=0)
        older = {
            "session_id": "older",
            "ts": now.isoformat().replace("+00:00", "Z"),
            "prompt": "restore the older JAIMES live update",
            "platform_message_id": "809",
            "db_message_id": "59",
            "run_id": "telegram-message-59",
        }
        newer = {
            "session_id": "older",
            "ts": (now + watcher.dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            "prompt": "handle the newer JAIMES request",
            "platform_message_id": "810",
            "db_message_id": "60",
            "run_id": "telegram-message-60",
        }
        meta = {
            "sessionId": "older",
            "model": "openai-codex/gpt-5.6-sol",
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        older_key = "topic17-superseded-retry"
        failed = {
            "ok": False,
            "key": older_key,
            "objective": "Restore the older JAIMES live update",
            "route": "JAIMES verified execution",
            "reaction_ok": True,
            "ack_message_id": "",
            "run_id": "telegram-message-59",
        }
        succeeded = {
            "ok": True,
            "key": "topic17-newer-surface",
            "objective": "Handle the newer JAIMES request",
            "route": "JAIMES verified execution",
            "reaction_ok": True,
            "ack_message_id": "910",
            "run_id": "telegram-message-60",
            "last_card_update_at": watcher.utc_now(),
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        batches = iter([[older], [older, newer]])
        self.state.write_text(json.dumps({"direct_db_cursor:older": 0}), encoding="utf-8")

        def ack_with_exact_incident(event, model, state, dry_run, meta, **_kwargs):
            if event["db_message_id"] == "59":
                watcher.record_api_result(state, "sendMessage", {
                    "ok": False,
                    "error": "definitive no-effect send failure",
                    "delivery_key": older_key,
                })
                return failed
            return succeeded

        with patch.object(watcher, "active_hermes_sessions_metadata", return_value=[meta]), \
             patch.object(watcher, "recent_prompt_events_from_state_db", side_effect=lambda *_args: next(batches)), \
             patch.object(watcher, "send_ack", side_effect=ack_with_exact_incident) as send:
            first = watcher.poll_once()
            first_saved = json.loads(self.state.read_text(encoding="utf-8"))
            second = watcher.poll_once()

        saved = json.loads(self.state.read_text(encoding="utf-8"))
        older_event_id = watcher.prompt_event_id(older)
        operation_id = watcher.delivery_operation_id("sendMessage", older_key)
        self.assertFalse(first["sent"][0]["result"]["ok"])
        self.assertIn(older_event_id, first_saved["surface_retry_events"])
        self.assertIn(operation_id, first_saved["unresolved_telegram_deliveries"])
        self.assertEqual(first_saved["status"], "telegram-delivery-error")
        self.assertTrue(second["sent"][0]["result"]["ok"])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(saved["direct_db_cursor:older"], 60)
        self.assertNotIn(older_event_id, saved["surface_retry_events"])
        self.assertNotIn(operation_id, saved.get("unresolved_telegram_deliveries", {}))
        self.assertNotIn("last_telegram_delivery_error", saved)
        self.assertNotIn("last_error", saved)
        self.assertEqual(saved["status"], "ok")

    def test_followup_or_attachment_cursor_advance_sweeps_superseded_retry_incident(self) -> None:
        now = watcher.dt.datetime.now(watcher.dt.timezone.utc).replace(microsecond=0)
        older = {
            "session_id": "older",
            "ts": now.isoformat().replace("+00:00", "Z"),
            "prompt": "restore the older JAIMES live update",
            "platform_message_id": "812",
            "db_message_id": "62",
            "run_id": "telegram-message-62",
        }
        newer = {
            "session_id": "older",
            "ts": (now + watcher.dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            "prompt": "additional context for the current task",
            "platform_message_id": "813",
            "db_message_id": "63",
            "run_id": "telegram-message-63",
        }
        meta = {
            "sessionId": "older",
            "model": "openai-codex/gpt-5.6-sol",
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        older_event_id = watcher.prompt_event_id(older)
        older_key = "topic17-context-superseded-retry"
        operation_id = watcher.delivery_operation_id("sendMessage", older_key)
        incident = {
            "at": older["ts"],
            "method": "sendMessage",
            "ok": False,
            "operation": operation_id,
            "error": "definitive no-effect send failure",
        }

        for continuation_kind in ("contextual-followup", "attachment"):
            with self.subTest(continuation_kind=continuation_kind):
                state = {
                    "direct_db_cursor:older": 0,
                    "active_cards": {},
                    "acked_prompt_events": [],
                    "surface_retry_events": {
                        older_event_id: {
                            "attempts": 1,
                            "first_failed_at": older["ts"],
                            "last_failed_at": older["ts"],
                            "next_retry_at": (
                                now + watcher.dt.timedelta(seconds=60)
                            ).isoformat().replace("+00:00", "Z"),
                            "reaction_ok": True,
                            "session_id": "older",
                            "db_message_id": 62,
                            "delivery_key": older_key,
                        }
                    },
                    "unresolved_telegram_deliveries": {operation_id: incident},
                    "last_telegram_delivery_error": incident,
                    "status": "telegram-delivery-error",
                    "last_error": "A managed Telegram card send lacks a confirmed receipt.",
                }
                self.state.write_text(json.dumps(state), encoding="utf-8")
                attachment_card = {"key": "current-card"}
                with patch.object(watcher, "active_hermes_sessions_metadata", return_value=[meta]), \
                     patch.object(watcher, "recent_prompt_events_from_state_db", return_value=[older, newer]), \
                     patch.object(
                         watcher,
                         "attach_contextual_followup",
                         return_value=continuation_kind == "contextual-followup",
                     ), \
                     patch.object(
                         watcher,
                         "media_only_prompt",
                         return_value=continuation_kind == "attachment",
                     ), \
                     patch.object(
                         watcher,
                         "recent_active_card_for_meta",
                         return_value=attachment_card if continuation_kind == "attachment" else None,
                     ), \
                     patch.object(watcher, "send_ack") as send:
                    result = watcher.poll_once()

                saved = json.loads(self.state.read_text(encoding="utf-8"))
                self.assertEqual(result["sent"], [])
                send.assert_not_called()
                self.assertEqual(saved["direct_db_cursor:older"], 63)
                self.assertNotIn(older_event_id, saved["surface_retry_events"])
                self.assertNotIn(operation_id, saved.get("unresolved_telegram_deliveries", {}))
                self.assertNotIn("last_telegram_delivery_error", saved)
                self.assertNotIn("last_error", saved)
                self.assertEqual(saved["status"], "ok")

    def test_due_surface_retry_does_not_queue_x_intelligence_twice(self) -> None:
        real_datetime = watcher.dt.datetime

        class FrozenDateTime(real_datetime):
            current = real_datetime.now(watcher.dt.timezone.utc).replace(microsecond=0)

            @classmethod
            def now(cls, tz=None):
                value = cls.current
                return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

        event = {
            "session_id": "older",
            "ts": FrozenDateTime.current.isoformat().replace("+00:00", "Z"),
            "prompt": "assess https://x.com/example/status/123456789 without making changes",
            "platform_message_id": "811",
            "db_message_id": "61",
            "run_id": "telegram-message-61",
        }
        meta = {
            "sessionId": "older",
            "model": "openai-codex/gpt-5.6-sol",
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        failed = {
            "ok": False,
            "key": "topic17-x-retry",
            "objective": "Assess the public X post",
            "route": "JAIMES verified execution",
            "reaction_ok": True,
            "ack_message_id": "",
            "run_id": "telegram-message-61",
        }
        succeeded = {
            **failed,
            "ok": True,
            "ack_message_id": "911",
            "last_card_update_at": event["ts"],
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        self.state.write_text(json.dumps({"direct_db_cursor:older": 0}), encoding="utf-8")

        def events_after_cursor(_session_id: str, cursor: int):
            return [event] if cursor < 61 else []

        with patch.object(watcher, "active_hermes_sessions_metadata", return_value=[meta]), \
             patch.object(watcher, "recent_prompt_events_from_state_db", side_effect=events_after_cursor), \
             patch.object(watcher, "queue_forwarded_x_intelligence", return_value=1) as queue_x, \
             patch.object(watcher, "send_ack", side_effect=[failed, succeeded]) as send, \
             patch.object(watcher.dt, "datetime", FrozenDateTime):
            first = watcher.poll_once()
            FrozenDateTime.current += watcher.dt.timedelta(
                seconds=watcher.SURFACE_RETRY_BASE_SECONDS + 1
            )
            second = watcher.poll_once()

        self.assertEqual(first["sent"][0]["result"]["x_intelligence_queued"], 1)
        self.assertNotIn("x_intelligence_queued", second["sent"][0]["result"])
        self.assertEqual(queue_x.call_count, 1)
        self.assertEqual(send.call_count, 2)
        self.assertFalse(send.call_args_list[0].kwargs["reaction_already_done"])
        self.assertTrue(send.call_args_list[1].kwargs["reaction_already_done"])

    def test_durable_message_receipt_clears_delivery_error_after_parent_timeout(self) -> None:
        event = {
            "ts": watcher.utc_now(),
            "prompt": "restore the JAIMES live updates",
            "platform_message_id": "808",
            "db_message_id": "58",
            "run_id": "telegram-message-58",
        }
        meta = {
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        state = {
            "last_telegram_delivery_error": {
                "at": "2026-07-19T13:00:00Z",
                "method": "sendMessage",
                "ok": False,
            }
        }
        with patch.object(watcher, "objective_from_prompt", return_value="Restore JAIMES live updates"), \
             patch.object(watcher, "objective_is_near_copy", return_value=False), \
             patch.object(watcher, "skill_for_prompt", return_value={"id": "", "label": "", "reason": ""}), \
             patch.object(watcher, "should_start_visible_card", return_value=True), \
             patch.object(watcher, "run_cmd", return_value={
                 "ok": False,
                 "stdout": "",
                 "stderr": "command timed out after 12s",
             }), \
             patch.object(watcher, "work_card_surface_receipt", return_value={
                 "message_id": "908",
                 "surface_indeterminate": False,
             }), \
             patch.object(watcher, "send_chat_action"), \
             patch.object(watcher, "publish_jaimes"):
            result = watcher.send_ack(
                event,
                "openai-codex/gpt-5.6-sol",
                state,
                meta=meta,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["ack_message_id"], "908")
        self.assertNotIn("last_telegram_delivery_error", state)

    def test_ambiguous_handoff_surface_stays_owned_and_never_fails_open(self) -> None:
        event = {
            "ts": watcher.utc_now(),
            "prompt": "@JAIMES preserve ambiguous ownership",
            "platform_message_id": "101",
            "db_message_id": "12",
            "run_id": "telegram-message-12",
        }
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"}
        future = watcher.dt.datetime.now(watcher.dt.timezone.utc) + watcher.dt.timedelta(seconds=30)
        with watcher.handoff_lock("-1003589561528", "1", "101") as path:
            watcher.write_handoff_record(path, {
                "schema_version": 1,
                "status": "waiting",
                "agent": "jaimes",
                "chat_id": "-1003589561528",
                "thread_id": "1",
                "inbound_message_id": "101",
                "created_at": watcher.utc_now(),
                "expires_at": future.isoformat().replace("+00:00", "Z"),
            })
        with patch.object(watcher, "send_ack", return_value={
            "ok": False,
            "surface_indeterminate": True,
            "reaction_ok": True,
            "header_message_id": "401",
            "ack_message_id": "",
        }):
            result = watcher.process_ack_event(event, model="model", state={}, dry_run=False, meta=meta)
        self.assertFalse(result["ok"])
        self.assertTrue(result["handoff_indeterminate"])
        self.assertNotIn("claim_token", result["handoff_receipt"])
        with watcher.handoff_lock("-1003589561528", "1", "101") as path:
            stored = watcher.load_json(path, {})
        self.assertEqual(stored["status"], "indeterminate")
        self.assertEqual(stored["ownership_state"], "claimed_in_flight")
        code, receipt = watcher.await_handoff("-1003589561528", "1", "101", 0.5)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["status"], "indeterminate")

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

    def test_keyed_delivery_success_is_operation_scoped_and_migrates_legacy_error(self) -> None:
        state: dict = {}
        watcher.record_api_result(state, "editMessageText", {
            "ok": False,
            "error": "card A edit failed",
            "delivery_key": "card-a",
        })
        card_a_operation = watcher.delivery_operation_id("editMessageText", "card-a")
        watcher.record_api_result(state, "editMessageText", {
            "ok": True,
            "delivery_key": "card-b",
        })

        self.assertIn(card_a_operation, state["unresolved_telegram_deliveries"])
        self.assertEqual(
            state["last_telegram_delivery_error"]["operation"],
            card_a_operation,
        )

        legacy = {
            "at": "2026-07-19T12:00:00Z",
            "method": "editMessageText",
            "ok": False,
            "error": "legacy pre-operation-key error",
        }
        legacy_state = {"last_telegram_delivery_error": legacy}
        watcher.record_api_result(legacy_state, "editMessageText", {
            "ok": True,
            "delivery_key": "first-confirmed-keyed-edit",
        })

        self.assertEqual(legacy_state["unresolved_telegram_deliveries"], {})
        self.assertNotIn("last_telegram_delivery_error", legacy_state)

    def test_error_sanitizer_redacts_bearer_and_quoted_json_token_values(self) -> None:
        secret_values = [
            "telegram-env-secret",
            "openai-env-secret",
            "github-env-secret",
            "access-env-secret",
            "api-env-secret",
            "telegram-json-secret",
            "openai-json-secret",
            "github-json-secret",
            "access-json-secret",
            "api-json-secret",
            "bearer-secret-abc",
        ]
        sanitized = watcher.sanitize_error_text(
            "TELEGRAM_BOT_TOKEN=telegram-env-secret; "
            "OPENAI_API_KEY: openai-env-secret; "
            "GITHUB_TOKEN=github-env-secret; "
            "access_token=access-env-secret; "
            "api_key=api-env-secret; "
            "Authorization: Bearer bearer-secret-abc; "
            'payload={"telegram_bot_token": "telegram-json-secret", '
            '"openai_api_key": "openai-json-secret", '
            '"github_token": "github-json-secret", '
            '"access_token": "access-json-secret", '
            '"api_key": "api-json-secret"}'
        )

        for secret in secret_values:
            with self.subTest(secret=secret):
                self.assertNotIn(secret, sanitized)
        self.assertNotIn("Bearer", sanitized)
        self.assertGreaterEqual(sanitized.count("<redacted>"), len(secret_values))

    def test_daemon_exception_persistence_uses_secret_safe_sanitizer(self) -> None:
        self.state.write_text("{}", encoding="utf-8")
        failure = RuntimeError(
            'Authorization: Bearer daemon-bearer-secret; '
            'OPENAI_API_KEY=daemon-openai-secret; '
            'payload={"access_token": "daemon-json-secret"}'
        )

        with patch.object(watcher.sys, "argv", ["jaimes_telegram_fast_ack.py"]), \
             patch.object(watcher, "poll_once", side_effect=failure), \
             patch.object(watcher.time, "sleep", side_effect=KeyboardInterrupt), \
             self.assertRaises(KeyboardInterrupt):
            watcher.main()

        saved = json.loads(self.state.read_text(encoding="utf-8"))
        persisted = saved["last_error"]
        self.assertNotIn("daemon-bearer-secret", persisted)
        self.assertNotIn("daemon-openai-secret", persisted)
        self.assertNotIn("daemon-json-secret", persisted)
        self.assertNotIn("Bearer", persisted)
        self.assertGreaterEqual(persisted.count("<redacted>"), 3)
        self.assertTrue(saved["last_error_at"])

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

    def test_claimed_without_surface_times_out_to_fallback(self) -> None:
        future = watcher.dt.datetime.now(watcher.dt.timezone.utc) + watcher.dt.timedelta(seconds=30)
        with watcher.handoff_lock("-1003589561528", "1", "89") as path:
            watcher.write_handoff_record(path, {
                "schema_version": 1,
                "status": "claimed",
                "ownership_state": "claimed_no_effect",
                "reaction_ok": True,
                "claim_token": "crashed-owner",
                "agent": "jaimes",
                "chat_id": "-1003589561528",
                "thread_id": "1",
                "inbound_message_id": "89",
                "created_at": watcher.utc_now(),
                "expires_at": future.isoformat().replace("+00:00", "Z"),
            })
        code, receipt = watcher.await_handoff("-1003589561528", "1", "89", 0.05)
        self.assertEqual(code, 2)
        self.assertEqual(receipt["status"], "timeout")
        with watcher.handoff_lock("-1003589561528", "1", "89") as path:
            stored = watcher.load_json(path, {})
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["reason"], "handoff_timeout_before_surface")

    def test_surface_intent_without_child_checkpoint_falls_back(self) -> None:
        future = watcher.dt.datetime.now(watcher.dt.timezone.utc) + watcher.dt.timedelta(seconds=30)
        with watcher.handoff_lock("-1003589561528", "1", "90") as path:
            watcher.write_handoff_record(path, {
                "schema_version": 1,
                "status": "claimed",
                "ownership_state": "surface_inflight",
                "reaction_ok": True,
                "claim_token": "crashed-after-intent",
                "card_key": "jaimes-fast-ack--1003589561528-90",
                "agent": "jaimes",
                "chat_id": "-1003589561528",
                "thread_id": "1",
                "inbound_message_id": "90",
                "created_at": watcher.utc_now(),
                "expires_at": future.isoformat().replace("+00:00", "Z"),
            })
        with patch.object(watcher, "work_card_surface_receipt", return_value={}):
            code, receipt = watcher.await_handoff("-1003589561528", "1", "90", 0.05)
        self.assertEqual(code, 2)
        self.assertEqual(receipt["status"], "timeout")
        with watcher.handoff_lock("-1003589561528", "1", "90") as path:
            stored = watcher.load_json(path, {})
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["reason"], "handoff_timeout_without_durable_surface_evidence")

    def test_expired_surface_owner_is_terminally_consumed_instead_of_wedging(self) -> None:
        event = {
            "platform_message_id": "91",
            "prompt": "@JAIMES expired owner",
        }
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"}
        past = watcher.dt.datetime.now(watcher.dt.timezone.utc) - watcher.dt.timedelta(seconds=1)
        with watcher.handoff_lock("-1003589561528", "1", "91") as path:
            watcher.write_handoff_record(path, {
                "schema_version": 1,
                "status": "claimed",
                "ownership_state": "surface_inflight",
                "claim_token": "dead-owner",
                "agent": "jaimes",
                "chat_id": "-1003589561528",
                "thread_id": "1",
                "inbound_message_id": "91",
                "created_at": watcher.utc_now(),
                "expires_at": past.isoformat().replace("+00:00", "Z"),
            })
        decision, record = watcher.handoff_event_state(meta, event)
        self.assertEqual(decision, "consume")
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["reason"], "handoff_surface_owner_expired")
        with watcher.handoff_lock("-1003589561528", "1", "91") as path:
            stored = watcher.load_json(path, {})
        self.assertEqual(stored["status"], "failed")

    def test_missing_lease_waits_briefly_then_consumes_as_josh_owned(self) -> None:
        old = watcher.dt.datetime.now(watcher.dt.timezone.utc) - watcher.dt.timedelta(
            seconds=watcher.HANDOFF_LEASE_ARRIVAL_GRACE_SECONDS + 1
        )
        event = {
            "platform_message_id": "92",
            "prompt": "@JAIMES no lease was created",
            "ts": old.isoformat().replace("+00:00", "Z"),
        }
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"}
        decision, record = watcher.handoff_event_state(meta, event)
        self.assertEqual(decision, "consume")
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["reason"], "handoff_lease_never_arrived_josh_fallback_owned")

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

    def test_handoff_send_runs_without_holding_the_file_lock(self) -> None:
        event = {
            "ts": watcher.utc_now(),
            "prompt": "@JAIMES verify lock scope",
            "platform_message_id": "109",
            "db_message_id": "19",
            "run_id": "telegram-message-19",
        }
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"}
        future = watcher.dt.datetime.now(watcher.dt.timezone.utc) + watcher.dt.timedelta(seconds=30)
        with watcher.handoff_lock("-1003589561528", "1", "109") as path:
            watcher.write_handoff_record(path, {
                "schema_version": 1,
                "status": "waiting",
                "agent": "jaimes",
                "chat_id": "-1003589561528",
                "thread_id": "1",
                "inbound_message_id": "109",
                "created_at": watcher.utc_now(),
                "expires_at": future.isoformat().replace("+00:00", "Z"),
            })

        original_lock = watcher.handoff_lock
        lock_state = {"held": False}

        @contextlib.contextmanager
        def tracked_lock(*args, **kwargs):
            with original_lock(*args, **kwargs) as record_path:
                self.assertFalse(lock_state["held"])
                lock_state["held"] = True
                try:
                    yield record_path
                finally:
                    lock_state["held"] = False

        def assert_unlocked_send(*args, **kwargs):
            self.assertFalse(lock_state["held"])
            self.assertTrue(kwargs["surface_attempt_callback"]())
            self.assertFalse(lock_state["held"])
            return {
                "ok": True,
                "reaction_ok": True,
                "header_message_id": "301",
                "ack_message_id": "302",
            }

        with patch.object(watcher, "handoff_lock", tracked_lock), \
             patch.object(watcher, "send_ack", side_effect=assert_unlocked_send):
            result = watcher.process_ack_event(event, model="model", state={}, dry_run=False, meta=meta)

        self.assertTrue(result["ok"])
        self.assertFalse(lock_state["held"])

    def test_claimed_sender_outlives_short_waiting_lease_without_self_cancelling(self) -> None:
        event = {
            "ts": watcher.utc_now(),
            "prompt": "@JAIMES verify claimed lease extension",
            "platform_message_id": "114",
            "db_message_id": "24",
            "run_id": "telegram-message-24",
        }
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"}
        short_future = watcher.dt.datetime.now(watcher.dt.timezone.utc) + watcher.dt.timedelta(milliseconds=30)
        with watcher.handoff_lock("-1003589561528", "1", "114") as path:
            watcher.write_handoff_record(path, {
                "schema_version": 1,
                "status": "waiting",
                "agent": "jaimes",
                "chat_id": "-1003589561528",
                "thread_id": "1",
                "inbound_message_id": "114",
                "created_at": watcher.utc_now(),
                "expires_at": short_future.isoformat().replace("+00:00", "Z"),
            })

        def send_after_waiting_expiry(*args, **kwargs):
            self.assertTrue(kwargs["surface_attempt_callback"]())
            time.sleep(0.06)
            return {
                "ok": True,
                "reaction_ok": True,
                "header_message_id": "451",
                "ack_message_id": "452",
            }

        with patch.object(watcher, "send_ack", side_effect=send_after_waiting_expiry):
            result = watcher.process_ack_event(event, model="model", state={}, dry_run=False, meta=meta)
        self.assertTrue(result["ok"])
        with watcher.handoff_lock("-1003589561528", "1", "114") as path:
            stored = watcher.load_json(path, {})
        self.assertEqual(stored["status"], "accepted")
        self.assertEqual(stored["live_message_id"], "452")

    def test_slow_handoff_send_returns_indeterminate_then_reconciles_accepted(self) -> None:
        event = {
            "ts": watcher.utc_now(),
            "prompt": "@JAIMES verify timeout fencing",
            "platform_message_id": "119",
            "db_message_id": "29",
            "run_id": "telegram-message-29",
        }
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"}
        waiter_output: list[tuple[int, dict]] = []
        processor_output: list[dict] = []
        send_started = threading.Event()
        release_send = threading.Event()

        def wait_for_acceptance() -> None:
            waiter_output.append(watcher.await_handoff("-1003589561528", "1", "119", 0.8))

        waiter = threading.Thread(target=wait_for_acceptance)
        waiter.start()
        deadline = time.time() + 1
        while time.time() < deadline:
            with watcher.handoff_lock("-1003589561528", "1", "119") as path:
                if watcher.load_json(path, {}).get("status") == "waiting":
                    break
            time.sleep(0.01)
        else:
            self.fail("handoff waiter never published its lease")

        def slow_send(*args, **kwargs):
            self.assertTrue(kwargs["surface_attempt_callback"]())
            send_started.set()
            self.assertTrue(release_send.wait(3))
            return {
                "ok": True,
                "reaction_ok": True,
                "header_message_id": "401",
                "ack_message_id": "402",
            }

        def process() -> None:
            processor_output.append(
                watcher.process_ack_event(event, model="model", state={}, dry_run=False, meta=meta)
            )

        with patch.object(watcher, "send_ack", side_effect=slow_send) as send, \
             patch.object(watcher, "work_card_surface_receipt", return_value={"surface_indeterminate": True}):
            processor = threading.Thread(target=process)
            processor.start()
            self.assertTrue(send_started.wait(1))
            waiter.join(timeout=2)
            self.assertFalse(waiter.is_alive(), "indeterminate ownership receipt was blocked by slow Telegram work")
            self.assertTrue(processor.is_alive(), "slow Telegram work should still be outside the lock")
            self.assertEqual(waiter_output[0][0], 0)
            self.assertEqual(waiter_output[0][1]["status"], "indeterminate")
            self.assertEqual(waiter_output[0][1]["ownership_state"], "claimed_in_flight")
            self.assertNotIn("claim_token", waiter_output[0][1])
            release_send.set()
            processor.join(timeout=2)
            self.assertFalse(processor.is_alive())
            self.assertEqual(send.call_count, 1)

        self.assertTrue(processor_output[0]["ok"])
        with watcher.handoff_lock("-1003589561528", "1", "119") as path:
            stored = watcher.load_json(path, {})
        self.assertEqual(stored["status"], "accepted")
        self.assertEqual(stored["header_message_id"], "401")
        self.assertEqual(stored["live_message_id"], "402")
        self.assertIn("accepted_at", stored)

    def test_concurrent_handoff_processors_have_one_claim_owner(self) -> None:
        event = {
            "ts": watcher.utc_now(),
            "prompt": "@JAIMES verify one owner",
            "platform_message_id": "129",
            "db_message_id": "39",
            "run_id": "telegram-message-39",
        }
        meta = {"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"}
        future = watcher.dt.datetime.now(watcher.dt.timezone.utc) + watcher.dt.timedelta(seconds=30)
        with watcher.handoff_lock("-1003589561528", "1", "129") as path:
            watcher.write_handoff_record(path, {
                "schema_version": 1,
                "status": "waiting",
                "agent": "jaimes",
                "chat_id": "-1003589561528",
                "thread_id": "1",
                "inbound_message_id": "129",
                "created_at": watcher.utc_now(),
                "expires_at": future.isoformat().replace("+00:00", "Z"),
            })

        first_send_started = threading.Event()
        release_first_send = threading.Event()
        first_output: list[dict] = []

        def one_slow_send(*args, **kwargs):
            self.assertTrue(kwargs["surface_attempt_callback"]())
            first_send_started.set()
            self.assertTrue(release_first_send.wait(3))
            return {
                "ok": True,
                "reaction_ok": True,
                "header_message_id": "501",
                "ack_message_id": "502",
            }

        with patch.object(watcher, "send_ack", side_effect=one_slow_send) as send:
            first = threading.Thread(target=lambda: first_output.append(
                watcher.process_ack_event(event, model="model", state={}, dry_run=False, meta=meta)
            ))
            first.start()
            self.assertTrue(first_send_started.wait(1))
            second = watcher.process_ack_event(event, model="model", state={}, dry_run=False, meta=meta)
            self.assertFalse(second["ok"])
            self.assertEqual(second["error"], "handoff_lease_unavailable")
            release_first_send.set()
            first.join(timeout=2)
            self.assertFalse(first.is_alive())
            self.assertEqual(send.call_count, 1)

        self.assertTrue(first_output[0]["ok"])
        with watcher.handoff_lock("-1003589561528", "1", "129") as path:
            stored = watcher.load_json(path, {})
        self.assertEqual(stored["status"], "accepted")
        self.assertEqual(stored["header_message_id"], "501")
        self.assertEqual(stored["live_message_id"], "502")

    def test_preexisting_accepted_receipt_advances_stale_cursor_without_resend(self) -> None:
        event = {
            "session_id": "inbox",
            "ts": watcher.utc_now(),
            "prompt": "@JAIMES recover accepted work",
            "platform_message_id": "177",
            "db_message_id": "44",
            "run_id": "telegram-message-44",
        }
        meta = {
            "sessionId": "inbox",
            "model": "gpt-5.6-sol",
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "1",
        }
        future = watcher.dt.datetime.now(watcher.dt.timezone.utc) + watcher.dt.timedelta(seconds=30)
        with watcher.handoff_lock("-1003589561528", "1", "177") as path:
            watcher.write_handoff_record(path, {
                "schema_version": 1,
                "status": "accepted",
                "agent": "jaimes",
                "chat_id": "-1003589561528",
                "thread_id": "1",
                "inbound_message_id": "177",
                "reaction_ok": True,
                "header_message_id": "701",
                "live_message_id": "702",
                "accepted_at": watcher.utc_now(),
                "expires_at": future.isoformat().replace("+00:00", "Z"),
            })
        self.state.write_text(json.dumps({"direct_db_cursor:inbox": 0}), encoding="utf-8")
        with patch.object(watcher, "active_hermes_sessions_metadata", return_value=[meta]), \
             patch.object(watcher, "recent_prompt_events_from_state_db", return_value=[event]), \
             patch.object(watcher, "send_ack") as send:
            result = watcher.poll_once()
        send.assert_not_called()
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["direct_db_cursor:inbox"], 44)
        recovered = saved["active_cards"]["telegram-message-44"]
        self.assertTrue(recovered["recovered_from_handoff_receipt"])
        self.assertEqual(recovered["header_message_id"], "701")
        self.assertEqual(recovered["ack_message_id"], "702")
        self.assertEqual(result["sent"], [])

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

    def test_question_mark_followup_stays_on_current_card_and_rekeys_run(self) -> None:
        started = watcher.utc_now()
        card = {
            "key": "assessment-card",
            "objective": "Assess Agent RH safely",
            "status": "active",
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
            "started_at": started,
        }
        state = {"active_cards": {"telegram-message-40": card}}
        event = {
            "prompt": "??",
            "run_id": "telegram-message-41",
            "db_message_id": "41",
        }
        meta = {
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "17",
        }
        self.assertTrue(watcher.attach_contextual_followup(state, event, meta))
        self.assertNotIn("telegram-message-40", state["active_cards"])
        continued = state["active_cards"]["telegram-message-41"]
        self.assertIs(continued, card)
        self.assertEqual(continued["followup_message_ids"], ["41"])
        self.assertEqual(continued["continued_from_run_ids"], ["telegram-message-40"])
        self.assertFalse(watcher.contextual_followup_prompt("check the Sorare lineup"))

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

    def test_failed_progress_edit_is_not_consumed_and_retries_successfully(self) -> None:
        unchanged = watcher.utc_now()
        state = {
            "active_cards": {
                "telegram-message-61": {
                    "key": "retry-progress-card",
                    "objective": "Restore JAIMES live updates",
                    "model": "model",
                    "route": "route",
                    "status": "active",
                    "session_id": "older",
                    "telegram_chat_id": "-1003589561528",
                    "telegram_thread_id": "17",
                    "current_summary": "Reading current state",
                    "started_at": unchanged,
                    "last_progress_at": unchanged,
                    "last_card_update_at": unchanged,
                }
            },
            "processed_progress_events": [],
        }
        event = {
            "event_id": "progress-retry-61",
            "run_id": "telegram-message-61",
            "type": "tool.result",
            "summary": "Verified the active card receipt",
        }
        update_patch = self.patches[3]
        update_patch.stop()
        try:
            with patch.dict("os.environ", {"JAIMES_TELEGRAM_LIVE_CARDS": "1"}), \
                 patch.object(watcher, "recent_progress_events", return_value=[event]), \
                 patch.object(watcher, "run_work_card_cmd", side_effect=[
                     {"ok": False, "stderr": "temporary Telegram timeout"},
                     {"ok": True, "stdout": "{}", "stderr": ""},
                 ]) as run, \
                 patch.object(watcher, "publish_jaimes"), \
                 patch.object(watcher, "send_chat_action"):
                first = watcher.update_active_cards(state, "older")
                first_card = dict(state["active_cards"]["telegram-message-61"])
                first_processed = list(state["processed_progress_events"])
                second = watcher.update_active_cards(state, "older")
        finally:
            update_patch.start()

        self.assertFalse(first[0]["result"]["ok"])
        self.assertEqual(first_processed, [])
        self.assertEqual(first_card["current_summary"], "Reading current state")
        self.assertEqual(first_card["last_progress_at"], unchanged)
        self.assertTrue(second[0]["result"]["ok"])
        self.assertIn("progress-retry-61", state["processed_progress_events"])
        self.assertEqual(
            state["active_cards"]["telegram-message-61"]["current_summary"],
            "Verified the active card receipt",
        )
        self.assertEqual(run.call_count, 2)

    def test_idle_active_card_receives_same_card_heartbeat_without_progress_mutation(self) -> None:
        now = watcher.dt.datetime.now(watcher.dt.timezone.utc)
        old = (now - watcher.dt.timedelta(seconds=watcher.HEARTBEAT_SECONDS + 5)).replace(
            microsecond=0
        ).isoformat().replace("+00:00", "Z")
        started = (now - watcher.dt.timedelta(minutes=3)).replace(
            microsecond=0
        ).isoformat().replace("+00:00", "Z")
        state = {
            "active_cards": {
                "telegram-message-62": {
                    "key": "heartbeat-card",
                    "objective": "Restore JAIMES live updates",
                    "model": "model",
                    "route": "route",
                    "status": "active",
                    "session_id": "older",
                    "telegram_chat_id": "-1003589561528",
                    "telegram_thread_id": "17",
                    "current_summary": "Checking the Telegram receipt lifecycle",
                    "started_at": started,
                    "last_progress_at": old,
                    "last_card_update_at": old,
                }
            },
            "processed_progress_events": [],
        }
        update_patch = self.patches[3]
        update_patch.stop()
        try:
            with patch.dict("os.environ", {"JAIMES_TELEGRAM_LIVE_CARDS": "1"}), \
                 patch.object(watcher, "recent_progress_events", return_value=[]), \
                 patch.object(watcher, "run_work_card_cmd", return_value={"ok": True}) as run, \
                 patch.object(watcher, "publish_jaimes") as publish:
                updates = watcher.update_active_cards(state, "older")
        finally:
            update_patch.start()

        self.assertEqual(len(updates), 1)
        command = run.call_args.args[0]
        self.assertEqual(command[2], "update")
        self.assertEqual(command[command.index("--key") + 1], "heartbeat-card")
        rendered_phase = command[command.index("--now") + 1]
        self.assertEqual(rendered_phase, "Checking the Telegram receipt lifecycle")
        self.assertNotIn("Still working", rendered_phase)
        self.assertNotIn("--done", command)
        active = state["active_cards"]["telegram-message-62"]
        self.assertEqual(active["last_progress_at"], old)
        self.assertEqual(active["current_summary"], "Checking the Telegram receipt lifecycle")
        self.assertNotEqual(active["last_card_update_at"], old)
        self.assertEqual(active["heartbeat_checked_at"], active["last_card_update_at"])
        self.assertEqual(publish.call_args.kwargs["phase"], "heartbeat")
        self.assertFalse(publish.call_args.kwargs["brain_feed"])

    def test_old_card_with_recent_progress_does_not_expire(self) -> None:
        now = watcher.dt.datetime.now(watcher.dt.timezone.utc)
        started = (now - watcher.dt.timedelta(seconds=watcher.MAX_ACTIVE_CARD_SECONDS + 5)).replace(
            microsecond=0
        ).isoformat().replace("+00:00", "Z")
        recent_progress = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        state = {
            "active_cards": {
                "telegram-message-64": {
                    "key": "expired-card",
                    "objective": "Restore JAIMES live updates",
                    "model": "model",
                    "route": "route",
                    "status": "active",
                    "session_id": "older",
                    "telegram_chat_id": "-1003589561528",
                    "telegram_thread_id": "17",
                    "current_summary": "Checking the Telegram receipt lifecycle",
                    "started_at": started,
                    "last_progress_at": recent_progress,
                    "last_card_update_at": recent_progress,
                }
            },
            "processed_progress_events": [],
        }
        update_patch = self.patches[3]
        update_patch.stop()
        try:
            with patch.dict("os.environ", {"JAIMES_TELEGRAM_LIVE_CARDS": "1"}), \
                 patch.object(watcher, "recent_progress_events", return_value=[]), \
                 patch.object(watcher, "run_work_card_cmd") as run, \
                 patch.object(watcher, "publish_jaimes") as publish:
                updates = watcher.update_active_cards(state, "older")
        finally:
            update_patch.start()

        self.assertEqual(state["active_cards"]["telegram-message-64"]["status"], "active")
        self.assertEqual(updates, [])
        run.assert_not_called()
        publish.assert_not_called()

    def test_compaction_child_progress_rebinds_to_parent_card(self) -> None:
        timestamp = watcher.utc_now()
        state = {
            "active_cards": {
                "telegram-message-63": {
                    "key": "lineage-card",
                    "objective": "Restore JAIMES live updates",
                    "model": "model",
                    "route": "route",
                    "status": "active",
                    "session_id": "parent-session",
                    "telegram_chat_id": "-1003589561528",
                    "telegram_thread_id": "17",
                    "current_summary": "Reading current state",
                    "started_at": timestamp,
                    "last_progress_at": timestamp,
                    "last_card_update_at": timestamp,
                }
            },
            "processed_progress_events": [],
        }
        child_event = {
            "event_id": "child-progress-63",
            "run_id": "telegram-message-999",
            "type": "tool.result",
            "summary": "Continued after context compression",
        }
        update_patch = self.patches[3]
        update_patch.stop()
        try:
            with patch.dict("os.environ", {"JAIMES_TELEGRAM_LIVE_CARDS": "1"}), \
                 patch.object(watcher, "hermes_session_lineage", return_value={
                     "child-session", "parent-session"
                 }), \
                 patch.object(watcher, "recent_progress_events", return_value=[child_event]), \
                 patch.object(watcher, "run_work_card_cmd", return_value={"ok": True}) as run, \
                 patch.object(watcher, "publish_jaimes"), \
                 patch.object(watcher, "send_chat_action"):
                updates = watcher.update_active_cards(state, "child-session")
        finally:
            update_patch.start()

        self.assertEqual(len(updates), 1)
        self.assertEqual(run.call_args.args[0][run.call_args.args[0].index("--key") + 1], "lineage-card")
        rebound = state["active_cards"]["telegram-message-63"]
        self.assertEqual(rebound["session_id"], "child-session")
        self.assertEqual(rebound["continued_from_session_ids"], ["parent-session"])
        self.assertEqual(rebound["current_summary"], "Continued after context compression")
        self.assertIn("child-progress-63", state["processed_progress_events"])

    def test_model_completed_waits_for_confirmed_telegram_delivery(self) -> None:
        state = {
            "active_cards": {
                "telegram-message-9": {
                    "key": "card-key", "objective": "Test task", "model": "model",
                    "route": "route", "status": "active", "session_id": "older",
                    "telegram_chat_id": "-1003589561528", "telegram_thread_id": "17",
                    "last_card_update_at": watcher.utc_now(),
                }
            },
            "processed_progress_events": [],
        }
        event = {
            "event_id": "complete-9",
            "run_id": "telegram-message-9",
            "type": "model.completed",
            "summary": "Model response prepared",
            "final_text": "final",
        }
        update_patch = self.patches[3]
        update_patch.stop()
        try:
            with patch.dict("os.environ", {"JAIMES_TELEGRAM_LIVE_CARDS": "1"}), \
                 patch.object(watcher, "recent_progress_events", return_value=[event]), \
                 patch.object(watcher, "run_cmd", return_value={"ok": True}) as run, \
                 patch.object(watcher, "publish_jaimes"):
                watcher.update_active_cards(state, "older")
        finally:
            update_patch.start()
        command = run.call_args.args[0]
        self.assertEqual(command[2], "update")
        self.assertTrue(any("awaiting Telegram delivery" in item for item in command))
        card = state["active_cards"]["telegram-message-9"]
        self.assertEqual(card["status"], "active")
        self.assertTrue(card["model_completed_at"])

    def test_adapter_success_receipt_closes_watcher_card(self) -> None:
        work_state = Path(self.tmp.name) / "jaimes_work_cards.json"
        work_state.write_text(json.dumps({"cards": {
            "card-key": {
                "status": "done",
                "updated_at": "2026-07-18T18:00:00Z",
                "work_log": ["Final summary delivered"],
                "final_message_id": "3914",
                "work_id": "work-current",
                "run_id": "run-current",
                "task_started_at": "2026-07-18T17:59:00Z",
            }
        }}))
        state = {"active_cards": {"run-9": {
            "key": "card-key",
            "objective": "Test task",
            "model": "openai-codex/gpt-5.6-sol",
            "work_id": "work-current",
            "ledger_run_id": "run-current",
            "task_started_at": "2026-07-18T17:59:00Z",
            "status": "active",
        }}}
        with patch.object(watcher, "JAIMES_WORK_CARD_STATE_PATH", work_state), \
             patch.object(watcher, "publish_jaimes") as publish:
            confirmed = watcher.reconcile_adapter_confirmed_deliveries(state)
        self.assertEqual(confirmed, 1)
        card = state["active_cards"]["run-9"]
        self.assertEqual(card["status"], "done")
        self.assertEqual(card["final_contract_status"], "canonical")
        self.assertEqual(card["final_delivery_verified_by"], "hermes-adapter-success")
        self.assertEqual(card["native_final_message_id"], "3914")
        self.assertEqual(card["final_message_id"], "3914")
        publish.assert_called_once()

    def test_adapter_receipt_recovers_after_timeout_overwrites_work_card_status(self) -> None:
        work_state = Path(self.tmp.name) / "jaimes_work_cards.json"
        work_state.write_text(json.dumps({"cards": {
            "card-key": {
                "status": "failed",
                "updated_at": "2026-07-23T00:13:27Z",
                "work_log": ["Work completed; final delivery receipt is unavailable"],
                "final_message_id": "4213",
                "final_delivery_verified_by": "hermes-adapter-success",
                "final_delivery_confirmed_at": "2026-07-23T00:11:51Z",
                "work_id": "work-current",
                "run_id": "run-current",
                "task_started_at": "2026-07-23T00:10:42Z",
            }
        }}))
        state = {"active_cards": {"run-9": {
            "key": "card-key",
            "objective": "Explain model usage",
            "model": "openai-codex/gpt-5.6-sol",
            "work_id": "work-current",
            "ledger_run_id": "run-current",
            "task_started_at": "2026-07-23T00:10:42Z",
            "status": "done",
            "final_contract_status": "delivery_indeterminate",
        }}}
        with patch.object(watcher, "JAIMES_WORK_CARD_STATE_PATH", work_state), \
             patch.object(watcher, "publish_jaimes"):
            confirmed = watcher.reconcile_adapter_confirmed_deliveries(state)
        self.assertEqual(confirmed, 1)
        card = state["active_cards"]["run-9"]
        self.assertEqual(card["status"], "done")
        self.assertEqual(card["final_contract_status"], "canonical")
        self.assertEqual(card["final_message_id"], "4213")
        self.assertEqual(card["final_delivery_verified_by"], "hermes-adapter-success")

    def test_adapter_confirmed_terminal_delivery_retires_live_and_final_edit_incidents(self) -> None:
        work_state = Path(self.tmp.name) / "jaimes_work_cards.json"
        work_state.write_text(json.dumps({"cards": {
            "receipt-card": {
                "status": "done",
                "updated_at": "2026-07-19T18:00:00Z",
                "work_log": ["Final summary delivered"],
                "final_message_id": "4914",
                "work_id": "work-receipt",
                "run_id": "run-receipt",
                "task_started_at": "2026-07-19T17:59:00Z",
            }
        }}))
        live_operation = watcher.delivery_operation_id(
            "editMessageText", "receipt-card"
        )
        final_operation = watcher.delivery_operation_id(
            "editMessageText", "receipt-card:final"
        )
        live_incident = {
            "at": "2026-07-19T17:59:30Z",
            "method": "editMessageText",
            "ok": False,
            "operation": live_operation,
            "error": "live edit receipt unresolved",
        }
        final_incident = {
            "at": "2026-07-19T17:59:40Z",
            "method": "editMessageText",
            "ok": False,
            "operation": final_operation,
            "error": "final edit receipt unresolved",
        }
        state = {
            "active_cards": {
                "run-receipt": {
                    "key": "receipt-card",
                    "objective": "Verify adapter terminal delivery",
                    "model": "openai-codex/gpt-5.6-sol",
                    "work_id": "work-receipt",
                    "ledger_run_id": "run-receipt",
                    "task_started_at": "2026-07-19T17:59:00Z",
                    "status": "active",
                }
            },
            "unresolved_telegram_deliveries": {
                live_operation: live_incident,
                final_operation: final_incident,
            },
            "last_telegram_delivery_error": final_incident,
        }

        with patch.object(watcher, "JAIMES_WORK_CARD_STATE_PATH", work_state), \
             patch.object(watcher, "publish_jaimes"):
            confirmed = watcher.reconcile_adapter_confirmed_deliveries(state)

        self.assertEqual(confirmed, 1)
        self.assertEqual(state["active_cards"]["run-receipt"]["status"], "done")
        self.assertEqual(state["unresolved_telegram_deliveries"], {})
        self.assertNotIn("last_telegram_delivery_error", state)

    def test_superseding_old_card_retires_only_its_unretryable_edit_incidents(self) -> None:
        old_live = watcher.delivery_operation_id("editMessageText", "old-card")
        old_final = watcher.delivery_operation_id("editMessageText", "old-card:final")
        current_live = watcher.delivery_operation_id("editMessageText", "current-card")

        def incident(operation: str, at: str) -> dict:
            return {
                "at": at,
                "method": "editMessageText",
                "ok": False,
                "operation": operation,
                "error": "edit receipt unresolved",
            }

        state = {
            "active_cards": {
                "run-old": {"key": "old-card", "status": "active"},
                "run-current": {"key": "current-card", "status": "active"},
            },
            "unresolved_telegram_deliveries": {
                old_live: incident(old_live, "2026-07-19T17:59:10Z"),
                old_final: incident(old_final, "2026-07-19T17:59:20Z"),
                current_live: incident(current_live, "2026-07-19T17:59:30Z"),
            },
        }
        watcher.refresh_delivery_error_state(
            state, state["unresolved_telegram_deliveries"]
        )

        retired = watcher.retire_noncurrent_active_cards(state, "run-current")

        self.assertEqual(retired, 1)
        self.assertEqual(state["active_cards"]["run-old"]["status"], "done")
        self.assertEqual(
            state["active_cards"]["run-old"]["retired_reason"],
            "superseded-by-newer-user-turn",
        )
        self.assertEqual(state["active_cards"]["run-current"]["status"], "active")
        self.assertNotIn(old_live, state["unresolved_telegram_deliveries"])
        self.assertNotIn(old_final, state["unresolved_telegram_deliveries"])
        self.assertIn(current_live, state["unresolved_telegram_deliveries"])
        self.assertEqual(
            state["last_telegram_delivery_error"]["operation"], current_live
        )

    def test_superseding_managed_card_pauses_surface_and_terminalizes_without_final(self) -> None:
        receipt = {
            "workId": "work-telegram-" + "a" * 24,
            "phase": "working",
            "sequence": 4,
            "fencingEpoch": 1,
            "deliveryState": "pending",
        }
        lifecycle = Mock()
        lifecycle.read_work.side_effect = [
            dict(receipt),
            {**receipt, "phase": "terminal", "sequence": 5, "deliveryState": "pending"},
        ]
        lifecycle.claim_terminal_delivery.return_value = {
            "allowed": True,
            "state": "sending",
        }
        state = {
            "active_cards": {
                "run-old": {
                    "key": "old-card",
                    "status": "active",
                    "objective": "Run the older task",
                    "model": "openai-codex/gpt-5.6-sol",
                    "route": "JAIMES verified execution",
                    "work_id": receipt["workId"],
                    "telegram_chat_id": "-1003589561528",
                    "telegram_thread_id": "17",
                },
                "run-current": {"key": "current-card", "status": "active"},
            }
        }

        with patch.object(
            watcher,
            "gateway_context_for_card",
            side_effect=lambda card: (
                {"lifecycle": lifecycle, "receipt": receipt, "writer": True}
                if card.get("key") == "old-card"
                else {}
            ),
        ), patch.object(
            watcher,
            "run_gateway_card_command",
            return_value={"ok": True},
        ) as pause_card:
            retired = watcher.retire_noncurrent_active_cards(state, "run-current")

        self.assertEqual(retired, 1)
        old = state["active_cards"]["run-old"]
        self.assertEqual(old["status"], "cancelled")
        self.assertEqual(old["terminal_outcome"], "superseded")
        self.assertEqual(old["terminal_delivery_state"], "dead_letter")
        self.assertEqual(old["final_contract_status"], "superseded-no-final")
        pause_card.assert_called_once()
        self.assertEqual(pause_card.call_args.kwargs["status"], "progress")
        lifecycle.commit_terminal.assert_called_once()
        self.assertEqual(lifecycle.commit_terminal.call_args.args[1], "superseded")
        lifecycle.claim_terminal_delivery.assert_called_once_with(receipt["workId"])
        lifecycle.finish_terminal_delivery.assert_called_once_with(
            receipt["workId"], "dead_letter"
        )

    def test_adapter_receipt_without_final_message_id_does_not_close_watcher_card(self) -> None:
        work_state = Path(self.tmp.name) / "jaimes_work_cards.json"
        work_state.write_text(json.dumps({"cards": {
            "card-key": {
                "status": "done",
                "updated_at": "2026-07-18T18:00:00Z",
                "work_log": ["Final summary delivered"],
                "work_id": "work-current",
                "run_id": "run-current",
                "task_started_at": "2026-07-18T17:59:00Z",
            }
        }}))
        state = {"active_cards": {"run-9": {
            "key": "card-key",
            "objective": "Test task",
            "work_id": "work-current",
            "ledger_run_id": "run-current",
            "task_started_at": "2026-07-18T17:59:00Z",
            "status": "active",
        }}}
        with patch.object(watcher, "JAIMES_WORK_CARD_STATE_PATH", work_state), \
             patch.object(watcher, "publish_jaimes") as publish:
            confirmed = watcher.reconcile_adapter_confirmed_deliveries(state)
        self.assertEqual(confirmed, 0)
        self.assertEqual(state["active_cards"]["run-9"]["status"], "active")
        publish.assert_not_called()

    def test_adapter_receipt_for_different_run_does_not_close_watcher_card(self) -> None:
        work_state = Path(self.tmp.name) / "jaimes_work_cards.json"
        work_state.write_text(json.dumps({"cards": {
            "card-key": {
                "status": "done",
                "updated_at": "2026-07-18T18:00:00Z",
                "work_log": ["Final summary delivered"],
                "final_message_id": "3914",
                "work_id": "work-current",
                "run_id": "run-other",
                "task_started_at": "2026-07-18T17:59:00Z",
            }
        }}))
        state = {"active_cards": {"run-9": {
            "key": "card-key",
            "objective": "Test task",
            "work_id": "work-current",
            "ledger_run_id": "run-current",
            "task_started_at": "2026-07-18T17:59:00Z",
            "status": "active",
        }}}
        with patch.object(watcher, "JAIMES_WORK_CARD_STATE_PATH", work_state), \
             patch.object(watcher, "publish_jaimes") as publish:
            confirmed = watcher.reconcile_adapter_confirmed_deliveries(state)
        self.assertEqual(confirmed, 0)
        self.assertEqual(state["active_cards"]["run-9"]["status"], "active")
        publish.assert_not_called()

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

    def test_native_final_not_modified_is_success_and_clears_exact_final_incident(self) -> None:
        user_id = self.add_user("older", "run the health check")
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO messages(session_id,role,content,platform_message_id,timestamp) VALUES (?,?,?,?,?)",
                ("older", "assistant", "TLDR\nHealth check passed.\nChallenges\nNone", "556", time.time()),
            )
        key = "idempotent-final-card"
        delivery_key = f"{key}:final"
        operation_id = watcher.delivery_operation_id("editMessageText", delivery_key)
        incident = {
            "at": watcher.utc_now(),
            "method": "editMessageText",
            "ok": False,
            "operation": operation_id,
            "error": "prior final edit receipt was unresolved",
        }
        state = {
            "active_cards": {
                f"telegram-message-{user_id}": {
                    "key": key,
                    "objective": "Verify JAIMES health",
                    "model": "openai-codex/gpt-5.6-sol",
                    "route": "JAIMES verified execution",
                    "status": "active",
                    "telegram_chat_id": "-1003589561528",
                    "telegram_thread_id": "17",
                }
            },
            "unresolved_telegram_deliveries": {operation_id: incident},
            "last_telegram_delivery_error": incident,
        }
        completion_patch = self.patches[2]
        completion_patch.stop()
        try:
            with patch.object(watcher, "edit_message", return_value={
                     "ok": False,
                     "description": "Bad Request: message is not modified",
                 }) as edit, \
                 patch.object(watcher, "run_work_card_cmd", return_value={"ok": True}) as run, \
                 patch.object(watcher, "publish_jaimes"):
                completed = watcher.complete_cards_from_final_responses(state, "older")
        finally:
            completion_patch.start()

        self.assertEqual(completed, 1)
        edit.assert_called_once()
        run.assert_called_once()
        completed_card = state["active_cards"][f"telegram-message-{user_id}"]
        self.assertEqual(completed_card["status"], "done")
        self.assertEqual(completed_card["final_contract_status"], "canonical")
        self.assertEqual(completed_card["native_final_message_id"], "556")
        self.assertNotIn(operation_id, state.get("unresolved_telegram_deliveries", {}))
        self.assertNotIn("last_telegram_delivery_error", state)

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

    def test_missing_terminal_receipt_turns_live_card_into_needs_attention(self) -> None:
        user_id = self.add_user("older", "verify delivery")
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO messages(session_id,role,content,platform_message_id,timestamp) VALUES (?,?,?,?,?)",
                ("older", "assistant", "Complete: Yes\nWhat was done:\n- Verified", "", time.time() - 600),
            )
        card = {
            "key": "delivery-card",
            "objective": "Verify delivery",
            "model": "openai-codex/gpt-5.6-sol",
            "route": "JAIMES verified execution",
            "status": "active",
        }
        state = {"active_cards": {f"telegram-message-{user_id}": card}}
        completion_patch = self.patches[2]
        completion_patch.stop()
        try:
            with patch.object(watcher, "finish_card_terminal_delivery") as finish, \
                 patch.object(watcher, "run_work_card_cmd", return_value={"ok": True}) as run:
                self.assertEqual(watcher.complete_cards_from_final_responses(state, "older"), 0)
        finally:
            completion_patch.start()

        finish.assert_not_called()
        command = run.call_args.args[0]
        self.assertEqual(command[2], "fail")
        self.assertEqual(
            command[command.index("--blocker") + 1],
            "Telegram final delivery receipt is unavailable",
        )
        self.assertEqual(card["status"], "failed")
        self.assertEqual(card["final_contract_status"], "delivery_indeterminate")
        self.assertEqual(card["terminal_card_recovery_status"], "needs-attention")

    def test_concurrent_terminal_merge_does_not_reopen_indeterminate_delivery(self) -> None:
        current = {
            "final_contract_status": "delivery_indeterminate",
            "terminal_delivery_state": "indeterminate",
        }
        disk = {
            "terminal_delivery_state": "sending",
            "terminal_final_effect_key": "effect-from-concurrent-preparation",
        }

        watcher.merge_concurrent_terminal_fields(current, disk)

        self.assertEqual(current["terminal_delivery_state"], "indeterminate")
        self.assertEqual(
            current["terminal_final_effect_key"],
            "effect-from-concurrent-preparation",
        )

    def test_concurrent_terminal_merge_accepts_confirmed_delivery(self) -> None:
        current = {"terminal_delivery_state": "sending"}
        disk = {"terminal_delivery_state": "delivered"}

        watcher.merge_concurrent_terminal_fields(current, disk)

        self.assertEqual(current["terminal_delivery_state"], "delivered")

    def test_missing_terminal_receipt_terminates_no_card_task_without_retry(self) -> None:
        user_id = self.add_user("older", "verify delivery")
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO messages(session_id,role,content,platform_message_id,timestamp) VALUES (?,?,?,?,?)",
                ("older", "assistant", "Complete: Yes\nWhat was done:\n- Verified", "", time.time() - 600),
            )
        card = {
            "key": "",
            "objective": "Verify delivery",
            "model": "openai-codex/gpt-5.6-sol",
            "route": "JAIMES verified execution",
            "status": "active",
            "no_card_required": True,
        }
        state = {"active_cards": {f"telegram-message-{user_id}": card}}
        completion_patch = self.patches[2]
        completion_patch.stop()
        try:
            with patch.object(watcher, "finish_card_terminal_delivery") as finish, \
                 patch.object(watcher, "run_work_card_cmd") as run:
                self.assertEqual(watcher.complete_cards_from_final_responses(state, "older"), 0)
        finally:
            completion_patch.start()

        finish.assert_not_called()
        run.assert_not_called()
        self.assertEqual(card["status"], "failed")
        self.assertEqual(card["final_contract_status"], "delivery_indeterminate")
        self.assertEqual(card["terminal_card_recovery_status"], "no-card-needs-attention")
        self.assertTrue(card["ended_at"])

    def test_failed_delivery_card_is_not_reactivated_by_old_progress(self) -> None:
        card = {
            "key": "failed-card",
            "objective": "Verify delivery",
            "status": "failed",
            "session_id": "older",
            "started_at": "2026-07-20T00:00:00Z",
        }
        event = {
            "event_id": "old-model-completed",
            "run_id": "telegram-message-9",
            "type": "model.completed",
            "summary": "Model response prepared",
        }
        state = {"active_cards": {"telegram-message-9": card}}
        with patch.dict("os.environ", {"JAIMES_TELEGRAM_LIVE_CARDS": "1"}), \
             patch.object(watcher, "recent_progress_events", return_value=[event]), \
             patch.object(watcher, "hermes_session_lineage", return_value={"older"}), \
             patch.object(watcher, "run_work_card_cmd") as run:
            self.assertEqual(watcher.update_active_cards(state, "older"), [])
        self.assertEqual(card["status"], "failed")
        run.assert_not_called()

    def test_status_only_completion_is_demoted_without_invented_success(self) -> None:
        rendered = watcher.structured_final_text(
            """Complete: Yes
What was done:
- Assessment complete.
- Verified the runtime outcome.
- Prepared the result for Telegram delivery.
Issues:
n/a
Appropriate next steps:
No action needed.
Approval needed:
n/a""",
            objective="Assess Agent RH",
            model="openai-codex/gpt-5.6-sol",
            route="JAIMES verified execution",
        )
        plain = watcher.html.unescape(rendered)
        normalized = " ".join(plain.split())
        self.assertIn("Complete: No", normalized)
        self.assertIn("did not include enough concrete", normalized)
        self.assertIn("without inventing missing facts", normalized)
        self.assertNotIn("Verified the runtime outcome", normalized)
        self.assertNotIn("Prepared the result for Telegram", normalized)

    def test_agent_rh_findings_and_recommendation_remain_complete(self) -> None:
        rendered = watcher.structured_final_text(
            """Complete: Yes
What was done:
- Confirmed Agent RH monitors Robinhood Chain activity only.
- Found it cannot trade a Robinhood brokerage account.
- Identified credential and automated trade-control risks.
- Recommended read-only signal use without connected credentials or wallets.
Issues:
- Connecting credentials would create avoidable account-control risk.
Appropriate next steps:
- Keep Agent RH read-only and do not connect credentials or wallets.
Approval needed:
n/a""",
            objective="Assess Agent RH for safe Robinhood use",
            model="openai-codex/gpt-5.6-sol",
            route="JAIMES verified execution",
        )
        plain = watcher.html.unescape(rendered)
        self.assertIn("Complete: Yes", plain)
        self.assertIn("cannot trade a Robinhood", plain)
        self.assertIn("read-only", plain)
        self.assertNotIn("No action needed", plain)
        self.assertTrue(watcher.final_contract_is_canonical(rendered))

    def test_topic17_repair_findings_remain_complete_and_route_why_is_not_duplicated(self) -> None:
        rendered = watcher.structured_final_text(
            """Complete: Yes
What was done:
- The live LaunchAgent used a different fast-ack script than the previously patched copy.
- Missing chat/topic metadata caused heartbeat edits to default into the JAIMES DM; 26 misplaced cards and records were repaired.
- Duplicate fast-ack cards were disabled, leaving jaimes_live_card.py as the sole card owner; Topic 17 routing then verified correctly.
Issues:
- n/a
Appropriate next steps:
- Keep an origin-route canary that verifies every Topic 17 card update remains in Topic 17.
Approval needed:
- n/a""",
            objective="Summarize and review",
            model="openai-codex/gpt-5.6-sol",
            route="JAIMES verified execution | Why: heavy workhorse reasoning",
        )
        plain = watcher.html.unescape(rendered)
        normalized = " ".join(plain.split())
        self.assertIn("Complete: Yes", normalized)
        self.assertEqual(plain.count("Why:"), 1)
        self.assertIn("26 misplaced cards", normalized)
        self.assertIn("cards were disabled", normalized)
        self.assertIn("jaimes_live_card.py", normalized)
        self.assertIn("heavy workhorse reasoning", normalized)
        self.assertIn("origin-route canary", normalized)
        self.assertNotIn("Detailed findings were not captured", normalized)
        self.assertNotIn("Retry with evidence", normalized)
        self.assertTrue(watcher.final_contract_is_canonical(rendered))
        self.assertLessEqual(max(map(len, plain.removeprefix("<pre>").removesuffix("</pre>").splitlines())), 38)

    def test_topic17_completed_health_audit_with_negative_findings_stays_complete(self) -> None:
        rendered = watcher.structured_final_text(
            """Complete: Yes — verification performed
What was done:
- Gateway is running under launchd, PID 14045.
- Fast-ack is running, PID 14050.
- Exact request has one native Telegram ID: 4291.
- Lifecycle confirms reaction and card delivery.
- Current owner is JAIMES; route evidence matches.
- No services were restarted or modified.
Issues:
- Zero-stranded-receipts check failed: 2 stranded.
- Both receipts have been pending since July 21.
- Hermes reports its service definition as stale.
- Hermes CLI says Telegram is not configured despite confirmed native Telegram lifecycle delivery.
Appropriate next steps:
- Separately reconcile the two stranded receipts.
- Audit the Hermes Telegram configuration mismatch.
- Refresh the stale service definition in a write window.
Approval needed:
- Approval required before any cleanup or service repair.""",
            objective="Run JAIMES health check",
            model="openai-codex/gpt-5.6-sol",
            route="JAIMES verified execution",
            work_id="work-current-health",
            run_id="run-current-health",
            task_started_at="2026-07-23T06:12:42Z",
            response_recorded_at="2026-07-23T06:15:16Z",
        )
        plain = watcher.html.unescape(rendered)
        normalized = " ".join(plain.split())
        self.assertIn("Complete: Yes", normalized)
        self.assertIn("2 stranded", normalized)
        self.assertIn("configuration mismatch", normalized)
        self.assertNotIn("Retry with evidence", normalized)
        self.assertTrue(watcher.final_contract_is_canonical(rendered))

    def test_topic17_self_canary_drops_only_expected_pre_delivery_state(self) -> None:
        rendered = watcher.structured_final_text(
            """Complete: Yes — verification performed
What was done:
- Topic 17 ownership resolved exclusively to JAIMES.
- Josh 2.0 remained inactive during this request.
- Gateway is connected; fast-ack is running.
- Native Telegram message ID 4294 is unique.
- Exactly one reaction and one live card were confirmed.
- Terminal-issue count: 0.
- Stranded-lifecycle count: 0.
- Control Tower matches the JAIMES work/run IDs.
Issues:
- Active-card count is 1, not 0, during execution.
- Lifecycle remains Working, not yet Delivered.
- Final receipt is necessarily unconfirmed before this final is sent.
- One current card-edit receipt is pending before this final.
- Control Tower still marks this canary working until the final delivery adapter closes it.
Appropriate next steps:
- Run a post-delivery read-only receipt check.
- Confirm the active-card count returns to 0.
- Confirm this final advances the same card to Delivered and publishes the terminal receipt.
Approval needed:
- n/a""",
            objective="Verify Topic 17 delivery and Control Tower agreement",
            model="openai-codex/gpt-5.6-sol",
            route="JAIMES verified execution",
            why="heavy workhorse reasoning",
        )
        plain = watcher.html.unescape(rendered)
        normalized = " ".join(plain.split())
        self.assertIn("Complete: Yes", normalized)
        self.assertIn("Why: heavy workhorse reasoning", normalized)
        self.assertIn("No action needed", normalized)
        self.assertNotIn("Active-card count is 1", normalized)
        self.assertNotIn("Lifecycle remains Working", normalized)
        self.assertNotIn("Final receipt is necessarily", normalized)
        self.assertNotIn("post-delivery read-only receipt", normalized)
        self.assertNotIn("Retry with evidence", normalized)
        self.assertTrue(watcher.final_contract_is_canonical(rendered))

    def test_terminal_prepare_passes_verified_why_to_single_formatter(self) -> None:
        source = inspect.getsource(watcher.prepare_terminal_response)
        self.assertIn('why=evidence["why"]', source)

    def test_july_11_session_history_is_downgraded_for_july_18_current_task(self) -> None:
        rendered = watcher.structured_final_text(
            """Model: openai-codex/gpt-5.6-sol | Route: session-history verification | Why: prior repair record checked
Complete: Yes
What was done:
- The live LaunchAgent used a different fast-ack script than the previously patched copy.
- Missing chat/topic metadata caused heartbeat edits to default into the JAIMES DM; 26 misplaced cards and records were repaired.
- Duplicate fast-ack cards were disabled, leaving jaimes_live_card.py as the sole card owner; Topic 17 routing then verified correctly.
Issues:
- n/a
Appropriate next steps:
- Keep an origin-route canary that verifies every Topic 17 card update remains in Topic 17.
Approval needed:
- n/a""",
            objective="Verify today's Topic 17 response-contract deployment",
            model="openai-codex/gpt-5.6-sol",
            route="JAIMES verified execution",
            work_id="work-20260718",
            run_id="run-20260718",
            task_started_at="2026-07-18T19:39:36Z",
            response_recorded_at="2026-07-18T19:40:41Z",
        )
        plain = watcher.html.unescape(rendered)
        normalized = " ".join(plain.split())
        self.assertIn("Complete: No", normalized)
        self.assertIn("not bound to the current", normalized)
        self.assertIn("Retry using evidence produced", normalized)
        self.assertNotIn("26 misplaced cards", normalized)
        self.assertNotIn("jaimes_live_card.py", normalized)
        self.assertNotIn("work-20260718", plain)
        self.assertNotIn("run-20260718", plain)
        self.assertTrue(watcher.final_contract_is_canonical(rendered))
        self.assertLessEqual(
            max(map(len, plain.removeprefix("<pre>").removesuffix("</pre>").splitlines())),
            38,
        )

    def test_explicit_july_11_history_request_may_use_session_history(self) -> None:
        rendered = watcher.structured_final_text(
            """Model: openai-codex/gpt-5.6-sol | Route: session-history verification | Why: requested historical review
Complete: Yes
What was done:
- Confirmed the July 11 repair changed the active fast-ack script.
- Found topic metadata had caused misplaced card updates.
- Verified the prior repair disabled duplicate card ownership.
Issues:
- n/a
Appropriate next steps:
- Keep the historical record for future comparison.
Approval needed:
- n/a""",
            objective="Summarize the historical July 11 Topic 17 repair",
            model="openai-codex/gpt-5.6-sol",
            route="JAIMES verified execution",
            work_id="work-20260718",
            run_id="run-20260718",
            task_started_at="2026-07-18T19:39:36Z",
        )
        plain = watcher.html.unescape(rendered)
        self.assertIn("Complete: Yes", plain)
        self.assertIn("July 11 repair", plain)
        self.assertTrue(watcher.final_contract_is_canonical(rendered))

    def test_current_run_evidence_binding_is_private_and_complete(self) -> None:
        rendered = watcher.structured_final_text(
            """Model: openai-codex/gpt-5.6-sol | Route: session-history verification | Why: current run inspected
Evidence: workId=work-private-20260718 | runId=run-private-20260718 | observedAt=2026-07-18T19:40:00Z | mode=current
Complete: Yes
What was done:
- Confirmed the current adapter validates the final before Telegram delivery.
- Found the current work card reaches final-ready before the send begins.
- Verified the current card closes only after the adapter confirms delivery.
Issues:
- n/a
Appropriate next steps:
- Keep the current-run evidence canary enabled.
Approval needed:
- n/a""",
            objective="Verify today's Topic 17 delivery contract",
            model="openai-codex/gpt-5.6-sol",
            route="JAIMES verified execution",
            work_id="work-private-20260718",
            run_id="run-private-20260718",
            task_started_at="2026-07-18T19:39:36Z",
        )
        plain = watcher.html.unescape(rendered)
        self.assertIn("Complete: Yes", plain)
        self.assertNotIn("work-private-20260718", plain)
        self.assertNotIn("run-private-20260718", plain)
        self.assertNotIn("observedAt", plain)
        self.assertTrue(watcher.final_contract_is_canonical(rendered))

    def test_final_contract_rejects_duplicate_why_header(self) -> None:
        malformed = """<pre>Model: openai-codex/gpt-5.6-sol |
   Route: JAIMES execution |
   Why: primary | Why: duplicate

Complete: No - malformed header

What was done:
- Preserved the source response.
- Identified a duplicate field.
- Kept the result fail closed.

Issues:
- Header is malformed.

Appropriate next steps:
- Regenerate one verified header.

Approval needed:
- n/a</pre>"""

        self.assertFalse(watcher.final_contract_is_canonical(malformed))

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

    def test_recent_native_parent_turn_survives_compaction_adjacency(self) -> None:
        prompt = "verify the current Topic 17 delivery after compaction"
        timestamp = time.time()
        with sqlite3.connect(self.db) as con:
            con.execute("ALTER TABLE sessions ADD COLUMN parent_session_id TEXT")
            con.execute("ALTER TABLE sessions ADD COLUMN end_reason TEXT")
            con.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = 'compression' WHERE id = 'older'",
                (timestamp,),
            )
            con.execute(
                "UPDATE sessions SET parent_session_id = 'older', started_at = ? WHERE id = 'newer'",
                (timestamp,),
            )
            con.execute(
                """INSERT INTO messages(
                       session_id, role, content, platform_message_id, timestamp
                   ) VALUES (?, ?, ?, ?, ?)""",
                ("older", "user", prompt, "4297", timestamp - 1),
            )
            con.execute(
                "INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,?,?,?)",
                ("newer", "user", "[CONTEXT COMPACTION — REFERENCE ONLY]", timestamp),
            )
            current = con.execute(
                "INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,?,?,?)",
                ("newer", "user", prompt, timestamp),
            ).lastrowid
        source = watcher.native_compaction_source({
            "session_id": "newer",
            "prompt": prompt,
        })
        self.assertEqual(source["platform_message_id"], "4297")
        with patch.object(watcher, "send_ack", side_effect=self.fake_ack) as send:
            result = watcher.poll_once()
        self.assertEqual(len(result["sent"]), 1)
        self.assertEqual(result["sent"][0]["result"]["run_id"], f"telegram-message-{current}")
        event = send.call_args.args[0]
        self.assertEqual(event["platform_message_id"], "4297")
        self.assertEqual(event["native_source_db_message_id"], "1")
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["direct_db_cursor:newer"], current)

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

    def test_architecture_review_objective_and_privacy_ignore_output_labels(self) -> None:
        prompt = (
            "Assess whether our model routing is resilient and whether private work and "
            "execution are routed appropriately. Make no changes.\n"
            "Return three findings, the verified model and authentication route actually "
            "used, any fallback that occurred, and a final conclusion of functioning or "
            "needs attention."
        )
        self.assertEqual(watcher.classify_privacy(prompt), "dashboard-safe")
        self.assertEqual(
            watcher.objective_from_prompt(prompt),
            "Assess whether our model routing is resilient and whether private work",
        )

    def test_real_private_content_remains_sensitive(self) -> None:
        self.assertEqual(
            watcher.classify_privacy("Review this private email account login failure."),
            "sensitive-account",
        )

    def test_route_assessment_findings_complete_on_semantic_evidence(self) -> None:
        complete, sections = watcher.parse_final_sections(
            "Complete: Yes\n"
            "What was done:\n"
            "- Dashboard-safe architecture reviews route to the verified specialist lane when allowance remains.\n"
            "- Private execution remains reserved for the coordinator and never crosses the public specialist boundary.\n"
            "- The actual fallback occurred only when the requested provider authentication route was unavailable.\n"
            "Issues:\n- No routing contradiction was observed in this assessment.\n"
            "Appropriate next steps:\n- Keep the current policy and rerun the parity canary after routing changes.\n"
            "Approval needed:\n- n/a"
        )
        self.assertTrue(complete)
        self.assertEqual(len(sections["done"]), 3)


if __name__ == "__main__":
    unittest.main()
