from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LIFECYCLE = load_module("telegram_gateway_lifecycle_adapter_test", ROOT / "scripts" / "telegram_gateway_lifecycle.py")
WATCHER = load_module("josh_telegram_fast_ack_v3_test", ROOT / "scripts" / "josh_telegram_fast_ack.py")


class JoshTelegramV3AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.state_path = self.root / "telegram" / "fast_ack_state.json"
        self.outbox_dir = self.root / "telegram" / "terminal-final-outbox"
        self.visibility_outbox_dir = self.root / "telegram" / "terminal-visibility-outbox"
        self.work_cards = self.root / "work-cards.json"
        self.path_patches = (
            patch.object(WATCHER, "STATE_PATH", self.state_path),
            patch.object(WATCHER, "TERMINAL_OUTBOX_DIR", self.outbox_dir),
            patch.object(WATCHER, "TERMINAL_VISIBILITY_OUTBOX_DIR", self.visibility_outbox_dir),
            patch.object(WATCHER, "WORK_CARD_STATE_PATH", self.work_cards),
        )
        for item in self.path_patches:
            item.start()
            self.addCleanup(item.stop)

    @staticmethod
    def policy(**overrides):
        values = {
            "master_state": "all",
            "global_kill_switch": False,
            "brain_kill_switch": False,
            "host_enabled": {"josh2": True, "jaimes": True},
            "writer_version": 3,
            "reader_versions": (2, 3),
            "shadow_min_samples": 20,
            "brain_fixture_minimum": 20,
        }
        values.update(overrides)
        return LIFECYCLE.RolloutPolicy(**values)

    def lifecycle(self, **policy_overrides):
        return LIFECYCLE.GatewayLifecycle(
            self.root / f"lifecycle-{len(list(self.root.glob('lifecycle-*')))}",
            rollout=self.policy(**policy_overrides),
            owner="josh2",
        )

    @staticmethod
    def meta() -> dict[str, str]:
        return {
            "telegram_chat_id": WATCHER.CONTROL_CENTER_CHAT_ID,
            "telegram_thread_id": "1",
            "telegram_session_key": "agent:main:telegram:group:-1003589561528:topic:1",
        }

    @staticmethod
    def event(prompt: str = "Answer the short question", *, run_id: str = "run-1") -> dict[str, str]:
        return {
            "session_id": "session-1",
            "run_id": run_id,
            "message_id": "401",
            "ts": "2026-07-20T17:00:00Z",
            "prompt": prompt,
        }

    @staticmethod
    def terminal_args(status: str = "done") -> argparse.Namespace:
        return argparse.Namespace(
            run_id="run-1",
            session_id="session-1",
            session_key="agent:main:telegram:group:-1003589561528:topic:1",
            chat_id=WATCHER.CONTROL_CENTER_CHAT_ID,
            thread_id="1",
            terminal_status=status,
            final_from_stdin=True,
        )

    @staticmethod
    def adapter_dependencies():
        return (
            patch.object(WATCHER, "objective_from_prompt", return_value="Answer the short question"),
            patch.object(WATCHER, "objective_is_near_copy", return_value=False),
            patch.object(WATCHER, "auto_route_for_prompt", return_value={
                "model": "test-model",
                "route": "test-route",
                "route_plan": {"routeId": "test-route"},
            }),
            patch.object(WATCHER, "skill_for_prompt", return_value={}),
            patch.object(WATCHER, "publish_josh", return_value=True),
            patch.object(WATCHER, "send_chat_action"),
            patch.object(WATCHER, "send_message_draft"),
            patch.object(WATCHER, "live_cards_enabled", return_value=True),
            patch.object(WATCHER, "fast_ack_enabled", return_value=True),
        )

    def start_receipt(self, lifecycle, *, tier: int = 2, origin: str = "origin") -> dict:
        work_id = LIFECYCLE.canonical_work_id(origin, "run-1")
        receipt = lifecycle.start_work(
            origin_key=origin,
            run_id="run-1",
            work_id=work_id,
            intake_agent="josh2",
            current_owner="josh2",
            surface_contract="telegram",
            classification=(tier, "conversation" if tier == 1 else "quick-answer" if tier == 2 else "multi-step"),
        )
        return lifecycle.transition(
            work_id,
            "classified",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )

    def no_card_state(self, receipt: dict, *, status: str = "active") -> dict:
        card = {
            "key": "tier-2-final",
            "objective": "Answer the short question",
            "model": "test-model",
            "route": "test-route",
            "session_id": "session-1",
            "telegram_chat_id": WATCHER.CONTROL_CENTER_CHAT_ID,
            "telegram_thread_id": "1",
            "work_id": receipt["workId"],
            "ledger_run_id": "run-1",
            "no_card_required": True,
            "lifecycle_version": 3,
            "delivery_tier": 2,
            "status": status,
            "started_at": WATCHER.utc_now(),
            "last_card_update_at": WATCHER.utc_now(),
        }
        return {"active_cards": {"run-1": card}}

    def coordinator_card_state(self, receipt: dict, *, no_card: bool = False) -> dict:
        card = {
            "key": "gateway-card-1",
            "objective": "Answer the short question",
            "model": "planned-model",
            "route": "planned-route",
            "session_id": "session-1",
            "telegram_chat_id": WATCHER.CONTROL_CENTER_CHAT_ID,
            "telegram_thread_id": "1",
            "work_id": receipt["workId"],
            "ledger_run_id": "ledger-run-1",
            "origin_claim_hash": "safe-origin-hash",
            "no_card_required": no_card,
            "lifecycle_version": 3,
            "delivery_tier": int(receipt["deliveryTier"]),
            "coordinator_owned": True,
            "job_id": "job-1",
            "status": "active",
            "started_at": WATCHER.utc_now(),
            "last_card_update_at": WATCHER.utc_now(),
        }
        return {"active_cards": {"run-1": card}}

    @staticmethod
    def coordinator_job(*, work_id: str, verified: bool = False) -> dict:
        job = {
            "jobId": "job-1",
            "workId": work_id,
            "ledgerRunId": "ledger-run-1",
            "originClaimHash": "safe-origin-hash",
            "status": "running",
            "origin": {
                "runId": "run-1",
                "cardKey": "gateway-card-1",
                "chatId": WATCHER.CONTROL_CENTER_CHAT_ID,
                "threadId": "1",
            },
            "route": {
                "routeId": "luna",
                "provider": "codex",
                "model": "planned-model",
                "worker": "josh-worker",
                "host": "josh2",
                "routingReason": "private execution fit",
                "fallback": "none",
            },
        }
        if verified:
            job["actual"] = {
                "actualProvider": "codex",
                "actualModel": "verified-model",
                "actualWorker": "verified-worker",
                "actualHost": "josh2",
                "modelVerified": True,
                "executionVerified": True,
            }
        return job

    def working_receipt(self, lifecycle, *, tier: int) -> dict:
        receipt = self.start_receipt(lifecycle, tier=tier, origin=f"progress-{tier}")
        receipt = lifecycle.transition(
            receipt["workId"],
            "acknowledged",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )
        return lifecycle.transition(
            receipt["workId"],
            "working",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )

    def test_dry_run_never_begins_or_persists_a_lifecycle(self) -> None:
        lifecycle = self.lifecycle()
        patches = self.adapter_dependencies()
        with ExitStack() as stack:
            for dependency in patches:
                stack.enter_context(dependency)
            stack.enter_context(patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle))
            stack.enter_context(patch.object(WATCHER, "begin_gateway_lifecycle", side_effect=AssertionError("dry run wrote")))
            result = WATCHER.send_ack(self.event(), "test-model", dry_run=True, meta=self.meta())
        self.assertTrue(result["ok"])
        with lifecycle.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM work_receipts").fetchone()[0], 0)

    def test_coordinator_progress_reserves_card_edit_before_helper(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=3)
        WATCHER.save_json(self.state_path, self.coordinator_card_state(receipt))
        observed: dict[str, object] = {}

        def helper(command, *args, **kwargs):
            observed["command"] = list(command)
            with lifecycle.connect() as db:
                row = db.execute(
                    "SELECT state FROM effects WHERE work_id=? AND kind='card_edit' ORDER BY intent_at DESC LIMIT 1",
                    (receipt["workId"],),
                ).fetchone()
            observed["effect_at_call"] = row["state"] if row else ""
            return {"ok": True, "returncode": 0, "stdout": "{}", "stderr": ""}

        payload = {"runId": "run-1", "progressCode": "worker_started"}
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "coordinator_job_snapshot", return_value=self.coordinator_job(work_id=receipt["workId"])), \
             patch.object(WATCHER, "run_cmd", side_effect=helper), \
             patch.object(WATCHER, "publish_josh", return_value=True), \
             patch.object(WATCHER.sys, "stdin", io.StringIO(json.dumps(payload))):
            result = WATCHER.progress_event_from_stdin()
        self.assertTrue(result["ok"])
        self.assertEqual(observed["effect_at_call"], "sending")
        command = observed["command"]
        self.assertIn("Asynchronous worker started", command)
        self.assertIn("--no-brain-feed", command)
        with lifecycle.connect() as db:
            states = [row[0] for row in db.execute(
                "SELECT state FROM effects WHERE work_id=? AND kind='card_edit'",
                (receipt["workId"],),
            )]
        self.assertEqual(states, ["delivered"])

    def test_verifying_progress_uses_trusted_checkpoint_and_transitions_phase(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=3)
        WATCHER.save_json(self.state_path, self.coordinator_card_state(receipt))
        commands: list[list[str]] = []
        payload = {"runId": "run-1", "progressCode": "verifying"}
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "coordinator_job_snapshot", return_value=self.coordinator_job(work_id=receipt["workId"], verified=True)), \
             patch.object(WATCHER, "run_cmd", side_effect=lambda command, *args, **kwargs: commands.append(list(command)) or {"ok": True}), \
             patch.object(WATCHER, "publish_josh", return_value=True), \
             patch.object(WATCHER.sys, "stdin", io.StringIO(json.dumps(payload))):
            result = WATCHER.progress_event_from_stdin()
        self.assertTrue(result["ok"])
        self.assertEqual(lifecycle.read_work(receipt["workId"])["phase"], "verifying")
        self.assertIn("provider=codex; model=verified-model; worker=verified-worker; host=josh2", commands[0])
        saved_card = WATCHER.load_json(self.state_path, {})["active_cards"]["run-1"]
        self.assertTrue(saved_card["route_verified"])
        self.assertTrue(saved_card["route"].startswith("verified route="))

    def test_no_card_progress_advances_lifecycle_without_card_effect(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=2)
        WATCHER.save_json(self.state_path, self.coordinator_card_state(receipt, no_card=True))
        payload = {"runId": "run-1", "progressCode": "verifying"}
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "coordinator_job_snapshot", return_value=self.coordinator_job(work_id=receipt["workId"], verified=True)), \
             patch.object(WATCHER, "run_cmd") as helper, \
             patch.object(WATCHER, "publish_josh", return_value=True), \
             patch.object(WATCHER.sys, "stdin", io.StringIO(json.dumps(payload))):
            result = WATCHER.progress_event_from_stdin()
        helper.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertTrue(result["no_card_required"])
        self.assertEqual(lifecycle.read_work(receipt["workId"])["phase"], "verifying")
        with lifecycle.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM effects WHERE kind='card_edit'").fetchone()[0]
        self.assertEqual(count, 0)

    def test_progress_payload_extra_fields_and_killed_writer_fail_closed(self) -> None:
        with patch.object(WATCHER.sys, "stdin", io.StringIO(json.dumps({
            "runId": "run-1",
            "progressCode": "worker_started",
            "step": "model supplied text",
        }))):
            invalid = WATCHER.progress_event_from_stdin()
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["status"], "invalid-progress-event-fields")

        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=3)
        WATCHER.save_json(self.state_path, self.coordinator_card_state(receipt))
        lifecycle.rollout = self.policy(global_kill_switch=True)
        payload = {"runId": "run-1", "progressCode": "worker_started"}
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "coordinator_job_snapshot", return_value=self.coordinator_job(work_id=receipt["workId"])), \
             patch.object(WATCHER, "run_cmd") as helper, \
             patch.object(WATCHER.sys, "stdin", io.StringIO(json.dumps(payload))):
            killed = WATCHER.progress_event_from_stdin()
        helper.assert_not_called()
        self.assertFalse(killed["ok"])
        self.assertEqual(killed["status"], "progress-card-update-failed")

    def test_progress_requires_string_fields_and_verified_model(self) -> None:
        for payload in (
            {"runId": 1, "progressCode": "worker_started"},
            {"runId": "run-1", "progressCode": True},
        ):
            with self.subTest(payload=payload), patch.object(
                WATCHER.sys, "stdin", io.StringIO(json.dumps(payload))
            ):
                result = WATCHER.progress_event_from_stdin()
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "invalid-progress-event-types")

        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=3)
        WATCHER.save_json(self.state_path, self.coordinator_card_state(receipt))
        job = self.coordinator_job(work_id=receipt["workId"], verified=True)
        job["actual"]["modelVerified"] = False
        payload = {"runId": "run-1", "progressCode": "verifying"}
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "coordinator_job_snapshot", return_value=job), \
             patch.object(WATCHER, "run_cmd") as helper, \
             patch.object(WATCHER.sys, "stdin", io.StringIO(json.dumps(payload))):
            result = WATCHER.progress_event_from_stdin()
        helper.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "execution-not-verified")

    def test_progress_rejects_every_cross_identity_mismatch(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=3)
        WATCHER.save_json(self.state_path, self.coordinator_card_state(receipt))
        base = self.coordinator_job(work_id=receipt["workId"])
        mutations = {
            "job": lambda job: job.__setitem__("jobId", "other-job"),
            "work": lambda job: job.__setitem__("workId", "other-work"),
            "ledger": lambda job: job.__setitem__("ledgerRunId", "other-ledger"),
            "claim": lambda job: job.__setitem__("originClaimHash", "other-claim"),
            "run": lambda job: job["origin"].__setitem__("runId", "other-run"),
            "card": lambda job: job["origin"].__setitem__("cardKey", "other-card"),
            "chat": lambda job: job["origin"].__setitem__("chatId", "other-chat"),
            "thread": lambda job: job["origin"].__setitem__("threadId", "17"),
        }
        payload = {"runId": "run-1", "progressCode": "worker_started"}
        for label, mutate in mutations.items():
            with self.subTest(field=label):
                job = json.loads(json.dumps(base))
                mutate(job)
                with patch.object(WATCHER, "coordinator_job_snapshot", return_value=job), \
                     patch.object(WATCHER, "run_cmd") as helper, \
                     patch.object(WATCHER.sys, "stdin", io.StringIO(json.dumps(payload))):
                    result = WATCHER.progress_event_from_stdin()
                helper.assert_not_called()
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "progress-origin-mismatch")

    def test_terminal_race_cannot_republish_active_progress(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=3)
        WATCHER.save_json(self.state_path, self.coordinator_card_state(receipt))
        publisher = Mock(return_value=True)

        def close_during_helper(command, *args, **kwargs):
            state = WATCHER.load_json(self.state_path, {})
            state["active_cards"]["run-1"]["status"] = "done"
            WATCHER.save_json(self.state_path, state)
            current = lifecycle.read_work(receipt["workId"])
            current = lifecycle.transition(
                receipt["workId"],
                "verifying",
                expected_sequence=current["sequence"],
                fencing_epoch=current["fencingEpoch"],
            )
            lifecycle.commit_terminal(
                receipt["workId"],
                "succeeded",
                expected_sequence=current["sequence"],
                fencing_epoch=current["fencingEpoch"],
                private_payload={"finalHtml": "safe final"},
            )
            return {"ok": True}

        payload = {"runId": "run-1", "progressCode": "worker_started"}
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "coordinator_job_snapshot", return_value=self.coordinator_job(work_id=receipt["workId"])), \
             patch.object(WATCHER, "run_cmd", side_effect=close_during_helper), \
             patch.object(WATCHER, "publish_josh", publisher), \
             patch.object(WATCHER.sys, "stdin", io.StringIO(json.dumps(payload))):
            result = WATCHER.progress_event_from_stdin()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "progress-recorded-before-terminal")
        publisher.assert_not_called()
        self.assertEqual(WATCHER.load_json(self.state_path, {})["active_cards"]["run-1"]["status"], "done")

    def test_progress_network_and_card_io_run_outside_fast_ack_lock(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=3)
        WATCHER.save_json(self.state_path, self.coordinator_card_state(receipt))
        original_lock = WATCHER.fast_ack_state_lock
        depth = {"value": 0}

        from contextlib import contextmanager

        @contextmanager
        def instrumented_lock():
            with original_lock():
                depth["value"] += 1
                try:
                    yield
                finally:
                    depth["value"] -= 1

        def job_lookup(_job_id):
            self.assertEqual(depth["value"], 0)
            return self.coordinator_job(work_id=receipt["workId"])

        def helper(*args, **kwargs):
            self.assertEqual(depth["value"], 0)
            return {"ok": True}

        def publish(*args, **kwargs):
            self.assertEqual(depth["value"], 0)
            return True

        payload = {"runId": "run-1", "progressCode": "worker_started"}
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "fast_ack_state_lock", instrumented_lock), \
             patch.object(WATCHER, "coordinator_job_snapshot", side_effect=job_lookup), \
             patch.object(WATCHER, "run_cmd", side_effect=helper), \
             patch.object(WATCHER, "publish_josh", side_effect=publish), \
             patch.object(WATCHER.sys, "stdin", io.StringIO(json.dumps(payload))):
            result = WATCHER.progress_event_from_stdin()
        self.assertTrue(result["ok"])
        self.assertEqual(depth["value"], 0)

    def test_tier_one_and_two_never_start_a_work_card(self) -> None:
        for tier in (1, 2):
            with self.subTest(tier=tier):
                lifecycle = self.lifecycle()
                patches = self.adapter_dependencies()
                reaction = Mock(return_value=True)
                card_start = Mock(side_effect=AssertionError("Tier 1/2 card created"))
                with ExitStack() as stack:
                    for dependency in patches:
                        stack.enter_context(dependency)
                    stack.enter_context(patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle))
                    stack.enter_context(patch.object(WATCHER, "classify_delivery_tier", return_value=(
                         tier,
                         "conversation" if tier == 1 else "quick-answer",
                    )))
                    stack.enter_context(patch.object(WATCHER, "place_inbox_reaction", reaction))
                    stack.enter_context(patch.object(WATCHER, "run_work_card_start", card_start))
                    result = WATCHER.send_ack(
                        self.event(run_id=f"run-tier-{tier}"),
                        "test-model",
                        meta=self.meta(),
                    )
                self.assertTrue(result["ok"])
                self.assertTrue(result["no_card_required"])
                self.assertEqual(result["delivery_tier"], tier)
                self.assertEqual(reaction.call_count, 0 if tier == 1 else 1)
                card_start.assert_not_called()
                with lifecycle.connect() as db:
                    kinds = [row[0] for row in db.execute("SELECT kind FROM effects ORDER BY kind")]
                self.assertEqual(kinds, [] if tier == 1 else ["reaction"])

    def test_start_publish_marks_planned_route_unverified(self) -> None:
        lifecycle = self.lifecycle()
        publisher = Mock(return_value=True)
        patches = list(self.adapter_dependencies())
        patches[4] = patch.object(WATCHER, "publish_josh", publisher)
        with ExitStack() as stack:
            for dependency in patches:
                stack.enter_context(dependency)
            stack.enter_context(patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle))
            stack.enter_context(patch.object(WATCHER, "classify_delivery_tier", return_value=(1, "conversation")))
            result = WATCHER.send_ack(self.event(), "planned-model", meta=self.meta())
        self.assertTrue(result["ok"])
        self.assertIs(publisher.call_args.kwargs["route_verified"], False)

    def test_queue_failure_and_progress_recovery_do_not_create_or_update_no_card_surface(self) -> None:
        ack = {
            "ok": True,
            "key": "tier-2-final",
            "objective": "Answer the short question",
            "model": "test-model",
            "runtime_model": "test-model",
            "route": "test-route",
            "route_plan": {"routeId": "test-route"},
            "work_id": "work-telegram-0123456789abcdef01234567",
            "ledger_run_id": "run-1",
            "origin_claim_hash": "safe-hash",
            "reaction_ok": True,
            "card_start_ok": True,
            "no_card_required": True,
            "surface_contract": "tier-2-final-v3",
            "delivery_tier": 2,
            "lifecycle_version": 3,
        }
        calls: list[list[str]] = []

        def failed_submit(command, *args, **kwargs):
            calls.append(list(command))
            return {"ok": False, "stdout": "", "stderr": "coordinator unavailable"}

        args = argparse.Namespace(
            chat_id=WATCHER.CONTROL_CENTER_CHAT_ID,
            thread_id="1",
            session_key=self.meta()["telegram_session_key"],
            run_id="run-1",
            message_id="401",
            dry_run=False,
            effect_path="",
            cancel_path="",
            surface_deadline_ms=0,
        )
        with patch.object(WATCHER.sys, "stdin", io.StringIO("short question")), \
             patch.object(WATCHER, "send_ack", return_value=ack), \
             patch.object(WATCHER, "run_cmd", side_effect=failed_submit), \
             patch.object(WATCHER, "publish_josh", return_value=True):
            result = WATCHER.claim_inbox(args)
        self.assertEqual(result["status"], "queue-failed")
        self.assertTrue(result["terminal_fallback_queued"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], "submit")
        self.assertNotIn(str(WATCHER.WORK_CARD_SCRIPT), calls[0])
        self.assertEqual(len(list(self.outbox_dir.glob("*.json"))), 1)

        now = WATCHER.utc_now()
        state = {"active_cards": {"run-1": {
            **self.no_card_state({"workId": ack["work_id"]})["active_cards"]["run-1"],
            "coordinator_owned": True,
            "job_id": "job-1",
            "ledger_run_id": "run-1",
            "last_card_update_at": now,
        }}}
        event = {
            "event_id": "tool-1",
            "run_id": "run-1",
            "type": "tool.completed",
            "ts": now,
            "summary": "Completed one private step",
        }
        with patch.object(WATCHER, "live_cards_enabled", return_value=True), \
             patch.object(WATCHER, "recent_progress_events", return_value=[event]), \
             patch.object(WATCHER, "coordinator_job_snapshot", return_value={"status": "failed", "delivered": False}), \
             patch.object(WATCHER, "run_cmd") as run_cmd, \
             patch.object(WATCHER, "publish_josh", return_value=True):
            updates = WATCHER.update_active_cards(state, "session-1", meta=self.meta())
        run_cmd.assert_not_called()
        self.assertEqual(updates[0]["result"]["status"], "terminal-fallback-queued")
        self.assertIn("tool-1", state["processed_progress_events"])
        self.assertEqual(state["active_cards"]["run-1"]["status"], "awaiting-final-gate")

        stale = state["active_cards"]["run-1"]
        stale["last_card_update_at"] = "2000-01-01T00:00:00Z"
        queued = WATCHER.queue_stale_final_gate_recovery(state)
        self.assertEqual(queued[0]["result"]["status"], "no-card-awaiting-final")
        self.assertEqual(len(list(self.outbox_dir.glob("*.json"))), 1)

    def test_pinned_v3_receipt_drains_after_off_and_commits_before_final_send(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.start_receipt(lifecycle)
        receipt = lifecycle.transition(
            receipt["workId"],
            "acknowledged",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )
        receipt = lifecycle.transition(
            receipt["workId"],
            "working",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )
        lifecycle.rollout = self.policy(master_state="off", writer_version=2)
        self.assertTrue(lifecycle.read_work(receipt["workId"])["writerEnabled"])
        state = self.no_card_state(receipt)
        WATCHER.save_json(self.state_path, state)
        observed: dict[str, object] = {}

        def send_final(text: str, **kwargs):
            current = lifecycle.read_work(receipt["workId"])
            observed["text"] = text
            observed["phase"] = current["phase"]
            observed["delivery"] = current["deliveryState"]
            with lifecycle.connect() as db:
                observed["outbox"] = db.execute(
                    "SELECT state FROM terminal_outbox WHERE work_id=?",
                    (receipt["workId"],),
                ).fetchone()["state"]
                observed["effect"] = db.execute(
                    "SELECT state FROM effects WHERE work_id=? AND kind='final'",
                    (receipt["workId"],),
                ).fetchone()["state"]
            return {"ok": True, "result": {"message_id": 9001}}

        final = "<b>Verified final</b>"
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "send_gateway_final_without_card", side_effect=send_final), \
             patch.object(WATCHER, "publish_terminal_once", return_value=True), \
             patch.object(WATCHER, "run_cmd") as run_cmd, \
             patch.object(WATCHER.sys, "stdin", io.StringIO(final)):
            result = WATCHER.close_before_final(self.terminal_args())
        run_cmd.assert_not_called()
        self.assertEqual(result["status"], "closed-and-final-delivered")
        self.assertEqual(observed, {
            "text": final,
            "phase": "terminal",
            "delivery": "sending",
            "outbox": "sending",
            "effect": "sending",
        })
        current = lifecycle.read_work(receipt["workId"])
        self.assertEqual(current["deliveryState"], "delivered")

    def test_tier_three_terminal_intents_and_control_tower_precede_helper(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=3)
        state = self.coordinator_card_state(receipt)
        state["active_cards"]["run-1"]["route_verified"] = True
        WATCHER.save_json(self.state_path, state)
        WATCHER.save_json(self.work_cards, {"cards": {"gateway-card-1": {
            "status": "running",
            "message_id": "7001",
            "header_message_id": "",
            "surface_contract": "live-only-v2",
        }}})
        events: list[str] = []

        def publish(*_args, **_kwargs):
            events.append("control-tower")
            return True

        def helper(*_args, **_kwargs):
            with lifecycle.connect() as db:
                states = {
                    row["kind"]: row["state"]
                    for row in db.execute(
                        "SELECT kind,state FROM effects WHERE work_id=? AND kind IN ('card_edit','final')",
                        (receipt["workId"],),
                    )
                }
            self.assertEqual(states, {"card_edit": "sending", "final": "sending"})
            self.assertEqual(events, ["control-tower"])
            WATCHER.save_json(self.work_cards, {"cards": {"gateway-card-1": {
                "status": "done",
                "message_id": "7001",
                "final_message_id": "9001",
                "surface_contract": "live-only-v2",
            }}})
            events.append("telegram-helper")
            return {"ok": True, "returncode": 0}

        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "coordinator_job_snapshot", return_value=self.coordinator_job(work_id=receipt["workId"], verified=True)), \
             patch.object(WATCHER, "publish_josh", side_effect=publish), \
             patch.object(WATCHER, "run_cmd", side_effect=helper), \
             patch.object(WATCHER.sys, "stdin", io.StringIO("<b>Verified final</b>")):
            result = WATCHER.close_before_final(self.terminal_args())
        self.assertEqual(result["status"], "closed-and-final-delivered")
        self.assertEqual(events, ["control-tower", "telegram-helper"])
        with lifecycle.connect() as db:
            states = {
                row["kind"]: row["state"]
                for row in db.execute(
                    "SELECT kind,state FROM effects WHERE work_id=? AND kind IN ('card_edit','final')",
                    (receipt["workId"],),
                )
            }
        self.assertEqual(states, {"card_edit": "delivered", "final": "delivered"})

    def test_terminal_visibility_failure_blocks_helper_then_replays_stable_event(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=3)
        WATCHER.save_json(self.state_path, self.coordinator_card_state(receipt))
        WATCHER.save_json(self.work_cards, {"cards": {"gateway-card-1": {
            "status": "running",
            "message_id": "7001",
            "header_message_id": "",
            "surface_contract": "live-only-v2",
        }}})
        publisher = Mock(side_effect=[False, True])
        helper = Mock()

        def deliver(*_args, **_kwargs):
            WATCHER.save_json(self.work_cards, {"cards": {"gateway-card-1": {
                "status": "done",
                "message_id": "7001",
                "final_message_id": "9001",
                "surface_contract": "live-only-v2",
            }}})
            return {"ok": True, "returncode": 0}

        helper.side_effect = deliver
        patches = (
            patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle),
            patch.object(WATCHER, "coordinator_job_snapshot", return_value=self.coordinator_job(work_id=receipt["workId"], verified=True)),
            patch.object(WATCHER, "publish_josh", publisher),
            patch.object(WATCHER, "run_cmd", helper),
        )
        with patches[0], patches[1], patches[2], patches[3], patch.object(
            WATCHER.sys, "stdin", io.StringIO("<b>Verified final</b>")
        ):
            first = WATCHER.close_before_final(self.terminal_args())
        self.assertEqual(first["status"], "terminal-visibility-pending")
        helper.assert_not_called()
        self.assertEqual(lifecycle.read_work(receipt["workId"])["phase"], "working")

        with patches[0], patches[1], patches[2], patches[3], patch.object(
            WATCHER.sys, "stdin", io.StringIO("<b>Verified final</b>")
        ):
            second = WATCHER.close_before_final(self.terminal_args())
        self.assertEqual(second["status"], "closed-and-final-delivered")
        self.assertEqual(helper.call_count, 1)
        event_ids = [call.kwargs["event_id"] for call in publisher.call_args_list]
        self.assertEqual(len(event_ids), 2)
        self.assertEqual(event_ids[0], event_ids[1])
        self.assertTrue(event_ids[0].startswith("telegram-terminal-josh2-"))
        receipt_file = json.loads(next(self.visibility_outbox_dir.glob("*.json")).read_text())
        self.assertTrue(receipt_file["acceptedAt"])
        self.assertNotIn("final_summary", receipt_file)

    def test_coordinator_terminal_route_unverified_fails_closed_before_publish_or_send(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=3)
        WATCHER.save_json(self.state_path, self.coordinator_card_state(receipt))
        WATCHER.save_json(self.work_cards, {"cards": {"gateway-card-1": {
            "status": "running",
            "message_id": "7001",
            "header_message_id": "",
            "surface_contract": "live-only-v2",
        }}})
        publisher = Mock()
        helper = Mock()
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), patch.object(
            WATCHER,
            "coordinator_job_snapshot",
            return_value=self.coordinator_job(work_id=receipt["workId"], verified=False),
        ), patch.object(WATCHER, "publish_josh", publisher), patch.object(
            WATCHER, "run_cmd", helper
        ), patch.object(WATCHER.sys, "stdin", io.StringIO("<b>Unverified final</b>")):
            result = WATCHER.close_before_final(self.terminal_args())
        self.assertEqual(result["status"], "terminal-visibility-blocked")
        publisher.assert_not_called()
        helper.assert_not_called()
        self.assertEqual(lifecycle.read_work(receipt["workId"])["phase"], "working")
        visibility = json.loads(next(self.visibility_outbox_dir.glob("*.json")).read_text())
        self.assertEqual(visibility["incident"]["code"], "terminal-route-unverified")
        self.assertFalse(visibility["routeVerified"])

    def test_accepted_visibility_receipt_replays_after_state_crash_without_republish(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=3)
        state = self.coordinator_card_state(receipt)
        WATCHER.save_json(self.state_path, state)
        publisher = Mock(return_value=True)
        with patch.object(
            WATCHER,
            "coordinator_job_snapshot",
            return_value=self.coordinator_job(work_id=receipt["workId"], verified=True),
        ), patch.object(WATCHER, "publish_josh", publisher):
            self.assertTrue(WATCHER.publish_terminal_once("run-1", "gateway-card-1", "done"))
        first_event_id = publisher.call_args.kwargs["event_id"]

        crashed = WATCHER.load_json(self.state_path, {})
        crashed["active_cards"]["run-1"].pop("ledger_terminal_published_at", None)
        WATCHER.save_json(self.state_path, crashed)
        publisher.reset_mock()
        with patch.object(
            WATCHER,
            "coordinator_job_snapshot",
            side_effect=AssertionError("durable evidence should survive restart"),
        ), patch.object(WATCHER, "publish_josh", publisher):
            self.assertTrue(WATCHER.publish_terminal_once("run-1", "gateway-card-1", "done"))
        publisher.assert_not_called()
        visibility = json.loads(next(self.visibility_outbox_dir.glob("*.json")).read_text())
        self.assertEqual(visibility["eventId"], first_event_id)
        self.assertTrue(visibility["acceptedAt"])

    def test_publisher_passes_event_id_and_requires_accepted_work_ledger(self) -> None:
        accepted = Mock(
            returncode=0,
            stdout=json.dumps({"ok": True, "workLedger": {"accepted": True}}),
            stderr="",
        )
        with patch.object(WATCHER.subprocess, "run", return_value=accepted) as runner:
            result = WATCHER.publish_josh(
                "Safe title",
                "done",
                "Safe detail",
                work_id="work-safe",
                run_id="run-safe",
                event_id="event-stable",
                brain_feed=False,
            )
        self.assertTrue(result)
        command = runner.call_args.args[0]
        self.assertEqual(command[command.index("--event-id") + 1], "event-stable")

        rejected = Mock(
            returncode=0,
            stdout=json.dumps({"ok": True, "workLedger": {"accepted": False}}),
            stderr="",
        )
        with patch.object(WATCHER.subprocess, "run", return_value=rejected):
            self.assertFalse(WATCHER.publish_josh("Safe", "done", "Safe", brain_feed=False))

    def test_stale_visibility_receipt_exposes_sanitized_blocked_incident(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=2)
        state = self.no_card_state(receipt)
        WATCHER.save_json(self.state_path, state)
        card = state["active_cards"]["run-1"]
        path, record = WATCHER.queue_terminal_visibility("run-1", card["key"], card, "done")
        record["createdAt"] = "2000-01-01T00:00:00Z"
        WATCHER.save_json(path, record)
        publisher = Mock()
        with patch.object(WATCHER, "publish_josh", publisher):
            self.assertFalse(WATCHER.publish_terminal_once("run-1", card["key"], "done"))
        publisher.assert_not_called()
        blocked = json.loads(path.read_text())
        self.assertEqual(blocked["incident"]["code"], "terminal-visibility-publication-stale")
        self.assertEqual(blocked["incident"]["status"], "blocked")
        self.assertNotIn("objective", blocked)

    def test_shadow_terminal_uses_legacy_helper_then_finishes_delivery_sample(self) -> None:
        lifecycle = self.lifecycle(master_state="shadow")
        receipt = self.start_receipt(lifecycle, tier=3, origin="shadow-terminal")
        receipt = lifecycle.transition(
            receipt["workId"],
            "acknowledged",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )
        receipt = lifecycle.transition(
            receipt["workId"],
            "working",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )
        finish_shadow = Mock()
        lifecycle.finish_shadow_sample = finish_shadow
        card = {
            "key": "shadow-card",
            "objective": "Compare the legacy terminal surface",
            "model": "legacy-runtime-model",
            "runtime_model": "legacy-runtime-model",
            "route": "legacy-runtime-route",
            "session_id": "session-1",
            "telegram_chat_id": WATCHER.CONTROL_CENTER_CHAT_ID,
            "telegram_thread_id": "1",
            "work_id": receipt["workId"],
            "ledger_run_id": "shadow-ledger-run",
            "origin_claim_hash": "b" * 64,
            "lifecycle_version": 3,
            "lifecycle_writer_enabled": False,
            "lifecycle_shadow": True,
            "delivery_tier": 3,
            "status": "active",
            "started_at": WATCHER.utc_now(),
            "last_card_update_at": WATCHER.utc_now(),
        }
        WATCHER.save_json(self.state_path, {"active_cards": {"run-1": card}})
        WATCHER.save_json(self.work_cards, {"cards": {"shadow-card": {
            "status": "running",
            "message_id": "7001",
            "header_message_id": "",
            "surface_contract": "live-only-v2",
        }}})

        def helper(*_args, **_kwargs):
            WATCHER.save_json(self.work_cards, {"cards": {"shadow-card": {
                "status": "done",
                "message_id": "7001",
                "final_message_id": "9001",
                "surface_contract": "live-only-v2",
            }}})
            return {"ok": True, "returncode": 0}

        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), patch.object(
            WATCHER, "publish_josh", return_value=True
        ), patch.object(WATCHER, "run_cmd", side_effect=helper), patch.object(
            WATCHER.sys, "stdin", io.StringIO("<b>Legacy shadow final</b>")
        ):
            result = WATCHER.close_before_final(self.terminal_args())
        self.assertEqual(result["status"], "closed-and-final-delivered")
        finish_shadow.assert_called_once_with(receipt["workId"], delivered=True)
        with lifecycle.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT state FROM terminal_outbox").fetchone()[0], "pending")

    def test_shadow_indeterminate_terminal_finishes_sample_unclean(self) -> None:
        lifecycle = Mock()
        WATCHER.finish_lifecycle_terminal({
            "shadow": True,
            "writer": False,
            "lifecycle": lifecycle,
            "receipt": {"workId": "work-shadow"},
        }, state="indeterminate")
        lifecycle.finish_shadow_sample.assert_called_once_with(
            "work-shadow",
            delivered=False,
        )

    def test_paused_tier_three_recovery_never_calls_terminal_helper(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.working_receipt(lifecycle, tier=3)
        state = self.coordinator_card_state(receipt)
        card = state["active_cards"]["run-1"]
        WATCHER.queue_terminal_final(
            "run-1",
            card["key"],
            card,
            self.meta(),
            "paused",
            "<b>Waiting for input</b>",
        )
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "run_cmd") as helper, \
             patch.object(WATCHER, "send_gateway_final_without_card") as sender:
            recovered = WATCHER.recover_terminal_final_outbox(state)
        helper.assert_not_called()
        sender.assert_not_called()
        self.assertEqual(recovered[0]["result"]["status"], "nonterminal-paused")
        current = lifecycle.read_work(receipt["workId"])
        self.assertEqual(current["phase"], "awaiting_input")
        with lifecycle.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM terminal_outbox").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM effects WHERE kind IN ('card_edit','final')").fetchone()[0], 0)

    def test_orphan_cleanup_recovers_v3_identity_and_queues_terminal_fallback(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.start_receipt(lifecycle, tier=3, origin="fast-ack-orphan")
        receipt = lifecycle.transition(
            receipt["workId"],
            "acknowledged",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )
        lifecycle.transition(
            receipt["workId"],
            "working",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )
        WATCHER.save_json(self.work_cards, {"cards": {"fast-ack-orphan": {
            "status": "running",
            "title": "Recover an orphaned task",
            "model": "planned-model",
            "route": "planned-route",
            "chat_id": WATCHER.CONTROL_CENTER_CHAT_ID,
            "thread_id": "1",
            "message_id": "7001",
            "surface_contract": "live-only-v2",
            "updated_at": "2000-01-01T00:00:00Z",
        }}})
        state = {"active_cards": {}}
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "run_cmd") as helper:
            reconciled = WATCHER.reconcile_orphan_work_cards(state, meta=self.meta())
        helper.assert_not_called()
        self.assertEqual(reconciled[0]["result"]["status"], "terminal-fallback-queued")
        orphan = next(iter(state["active_cards"].values()))
        self.assertEqual(orphan["work_id"], receipt["workId"])
        self.assertEqual(orphan["status"], "awaiting-final-gate")
        self.assertEqual(len(list(self.outbox_dir.glob("*.json"))), 1)

    def test_live_kill_switch_never_falls_work_back_to_legacy_surface(self) -> None:
        lifecycle = self.lifecycle()
        lifecycle.rollout = self.policy(master_state="off", global_kill_switch=True)
        reaction = Mock(side_effect=AssertionError("kill switch allowed reaction"))
        card_start = Mock(side_effect=AssertionError("kill switch allowed card"))
        patches = self.adapter_dependencies()
        with ExitStack() as stack:
            for dependency in patches:
                stack.enter_context(dependency)
            stack.enter_context(patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle))
            stack.enter_context(patch.object(WATCHER, "place_inbox_reaction", reaction))
            stack.enter_context(patch.object(WATCHER, "run_work_card_start", card_start))
            result = WATCHER.send_ack(self.event(), "test-model", meta=self.meta())
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "lifecycle-unavailable")
        reaction.assert_not_called()
        card_start.assert_not_called()

    def test_indeterminate_v3_final_is_fenced_from_legacy_recovery(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.start_receipt(lifecycle, origin="ambiguous")
        state = self.no_card_state(receipt)
        WATCHER.save_json(self.state_path, state)
        sender = Mock(return_value={"ok": False, "error": "timeout"})
        final = "<b>Ambiguous final</b>"
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "publish_josh", return_value=True), \
             patch.object(WATCHER, "send_gateway_final_without_card", sender), \
             patch.object(WATCHER.sys, "stdin", io.StringIO(final)):
            result = WATCHER.close_before_final(self.terminal_args())
        self.assertEqual(result["status"], "final-delivery-indeterminate")
        self.assertEqual(sender.call_count, 1)
        current = lifecycle.read_work(receipt["workId"])
        self.assertEqual(current["deliveryState"], "indeterminate")

        card = state["active_cards"]["run-1"]
        WATCHER.queue_terminal_final("run-1", card["key"], card, self.meta(), "done", final)
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "send_gateway_final_without_card", sender), \
             patch.object(WATCHER, "run_cmd") as run_cmd:
            recovered = WATCHER.recover_terminal_final_outbox(state)
        run_cmd.assert_not_called()
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(recovered[0]["result"]["status"], "final-delivery-indeterminate")
        record = json.loads(next(self.outbox_dir.glob("*.json")).read_text())
        self.assertTrue(record["lifecycle_fenced_at"])

    def test_paused_no_card_result_remains_nonterminal(self) -> None:
        lifecycle = self.lifecycle()
        receipt = self.start_receipt(lifecycle, origin="paused")
        WATCHER.save_json(self.state_path, self.no_card_state(receipt))
        with patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle), \
             patch.object(WATCHER, "send_gateway_final_without_card") as sender, \
             patch.object(WATCHER, "run_cmd") as run_cmd, \
             patch.object(WATCHER.sys, "stdin", io.StringIO("Approval is required")):
            result = WATCHER.close_before_final(self.terminal_args("paused"))
        sender.assert_not_called()
        run_cmd.assert_not_called()
        self.assertEqual(result["status"], "no-card-required")
        current = lifecycle.read_work(receipt["workId"])
        self.assertEqual(current["phase"], "awaiting_input")
        self.assertIsNone(current["outcome"])
        with lifecycle.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM terminal_outbox").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM effects WHERE kind='final'").fetchone()[0], 0)

    def test_off_and_shadow_keep_legacy_reaction_and_card_surface(self) -> None:
        for master_state in ("off", "shadow"):
            with self.subTest(master_state=master_state):
                lifecycle = self.lifecycle(master_state=master_state)
                patches = self.adapter_dependencies()
                reaction = Mock(return_value=True)
                card_start = Mock(return_value={"ok": True, "stdout": "{}"})
                card_receipt = {
                    "surface_ok": True,
                    "header_message_id": "",
                    "live_message_id": "7001",
                    "header_required": False,
                    "surface_contract": "live-only-v2",
                    "surface_indeterminate": False,
                }
                with ExitStack() as stack:
                    for dependency in patches:
                        stack.enter_context(dependency)
                    stack.enter_context(patch.object(WATCHER, "gateway_lifecycle", return_value=lifecycle))
                    stack.enter_context(patch.object(WATCHER, "classify_delivery_tier", return_value=(3, "multi-step")))
                    stack.enter_context(patch.object(WATCHER, "place_inbox_reaction", reaction))
                    stack.enter_context(patch.object(WATCHER, "run_work_card_start", card_start))
                    stack.enter_context(patch.object(WATCHER, "parse_work_card_start_receipt", return_value=card_receipt))
                    stack.enter_context(patch.object(WATCHER, "render_live_card", None))
                    result = WATCHER.send_ack(
                        self.event(prompt="Complete a multi-step task", run_id=f"run-{master_state}"),
                        "test-model",
                        meta=self.meta(),
                    )
                self.assertTrue(result["ok"])
                self.assertFalse(result["no_card_required"])
                self.assertEqual(reaction.call_count, 1)
                self.assertEqual(card_start.call_count, 1)
                with lifecycle.connect() as db:
                    receipts = db.execute("SELECT COUNT(*) FROM work_receipts").fetchone()[0]
                    effects = db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
                self.assertEqual(receipts, 0 if master_state == "off" else 1)
                self.assertEqual(effects, 0)


if __name__ == "__main__":
    unittest.main()
