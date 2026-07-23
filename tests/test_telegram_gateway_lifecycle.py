from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "telegram_gateway_lifecycle.py"
SPEC = importlib.util.spec_from_file_location("telegram_gateway_lifecycle_tested", MODULE_PATH)
assert SPEC and SPEC.loader
LIFECYCLE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LIFECYCLE
SPEC.loader.exec_module(LIFECYCLE)


class GatewayLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name) / "private-lifecycle"

    @staticmethod
    def rollout(**overrides):
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

    def gateway(self, *, owner: str = "josh2", rollout=None):
        return LIFECYCLE.GatewayLifecycle(
            self.root,
            owner=owner,
            rollout=rollout or self.rollout(),
        )

    @staticmethod
    def start(gateway, *, origin: str = "origin-a", run_id: str = "run-a", **overrides):
        values = {
            "origin_key": origin,
            "run_id": run_id,
            "intake_agent": "josh2",
            "current_owner": "josh2",
            "surface_contract": "telegram",
            "classification": (3, "multi-step"),
        }
        values.update(overrides)
        return gateway.start_work(**values)

    def test_canonical_and_explicit_work_identity_round_trip_and_collisions_fail_closed(self) -> None:
        expected = "work-telegram-" + hashlib.sha256(b"message-key|run-42").hexdigest()[:24]
        self.assertEqual(LIFECYCLE.canonical_work_id("message-key", "run-42"), expected)

        gateway = self.gateway()
        explicit = "work-telegram-0123456789abcdef01234567"
        receipt = self.start(
            gateway,
            origin="origin-explicit",
            run_id="run-explicit",
            work_id=explicit,
        )
        self.assertEqual(receipt["workId"], explicit)
        self.assertEqual(self.start(
            gateway,
            origin="origin-explicit",
            run_id="run-explicit",
            work_id=explicit,
        )["workId"], explicit)

        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "invalid-work-id"):
            self.start(gateway, origin="bad-format", run_id="run", work_id="work-telegram-not-hex")
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "work-identity-collision"):
            self.start(gateway, origin="other-origin", run_id="other-run", work_id=explicit)
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "work-identity-mismatch"):
            self.start(
                gateway,
                origin="origin-explicit",
                run_id="run-explicit",
                work_id=explicit,
                current_owner="jaimes",
                intake_agent="jaimes",
            )

    def test_fsm_rejects_illegal_stale_and_old_epoch_events(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway)
        work_id = receipt["workId"]
        self.assertEqual((receipt["phase"], receipt["sequence"], receipt["fencingEpoch"]), ("received", 1, 1))

        with self.assertRaises(LIFECYCLE.IllegalTransitionError):
            gateway.transition(work_id, "working", expected_sequence=1, fencing_epoch=1)
        classified = gateway.transition(work_id, "classified", expected_sequence=1, fencing_epoch=1)
        with self.assertRaises(LIFECYCLE.StaleEventError):
            gateway.transition(work_id, "acknowledged", expected_sequence=1, fencing_epoch=1)

        cancelled = gateway.request_cancel(
            work_id,
            expected_sequence=classified["sequence"],
            fencing_epoch=classified["fencingEpoch"],
        )
        self.assertTrue(cancelled["cancelRequested"])
        self.assertEqual(cancelled["fencingEpoch"], 2)
        with self.assertRaises(LIFECYCLE.StaleEventError):
            gateway.transition(
                work_id,
                "acknowledged",
                expected_sequence=cancelled["sequence"],
                fencing_epoch=1,
            )

        terminal = gateway.commit_terminal(
            work_id,
            "cancelled",
            expected_sequence=cancelled["sequence"],
            fencing_epoch=cancelled["fencingEpoch"],
            private_payload={"final": "cancelled safely"},
        )
        self.assertFalse(terminal["duplicate"])
        with self.assertRaises(LIFECYCLE.StaleEventError):
            gateway.transition(
                work_id,
                "working",
                expected_sequence=cancelled["sequence"] + 1,
                fencing_epoch=cancelled["fencingEpoch"],
            )

    def test_classifier_promotion_and_tier_effect_contract(self) -> None:
        self.assertEqual(LIFECYCLE.classify_delivery_tier("hello"), (1, "conversation"))
        self.assertEqual(LIFECYCLE.classify_delivery_tier("What time is it?"), (2, "quick-answer"))
        self.assertEqual(
            LIFECYCLE.classify_delivery_tier(
                "Please explain to me how often you are using codex that is installed on your device vs other models?"
            ),
            (2, "quick-answer"),
        )
        self.assertEqual(
            LIFECYCLE.classify_delivery_tier(
                "testing behavior, are you fully functioning?"
            ),
            (2, "quick-answer"),
        )
        self.assertEqual(
            LIFECYCLE.classify_delivery_tier(
                "Quick response check: can you reply?"
            ),
            (2, "quick-answer"),
        )
        self.assertEqual(
            LIFECYCLE.classify_delivery_tier(
                'Canary JAIMES: reply exactly "JAIMES receipt confirmed".'
            ),
            (2, "quick-answer"),
        )
        self.assertEqual(LIFECYCLE.classify_delivery_tier("please deploy the change")[0], 3)
        self.assertEqual(
            LIFECYCLE.classify_delivery_tier(
                "testing behavior, can you restart the gateway?"
            )[0],
            3,
        )
        self.assertEqual(
            LIFECYCLE.classify_delivery_tier(
                "Can you restart the gateway and confirm it is available?"
            )[0],
            3,
        )
        self.assertEqual(
            LIFECYCLE.classify_delivery_tier(
                "verify every service is fully functioning"
            ),
            (3, "multi-step"),
        )
        self.assertEqual(LIFECYCLE.classify_delivery_tier("opaque request")[0], 3)
        self.assertEqual(LIFECYCLE.classify_delivery_tier("hello", has_media=True), (3, "brain-media"))

        gateway = self.gateway()
        tier1 = self.start(
            gateway,
            origin="tier-1",
            run_id="r1",
            classification=(1, "conversation"),
        )
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "effect-not-allowed-for-delivery-tier"):
            gateway.claim_effect(tier1["workId"], "reaction", sequence=1, fencing_epoch=1)
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "effect-not-allowed-for-delivery-tier"):
            gateway.claim_effect(tier1["workId"], "card", sequence=1, fencing_epoch=1)

        tier2 = self.start(
            gateway,
            origin="tier-2",
            run_id="r2",
            classification=(2, "quick-answer"),
        )
        reaction = gateway.claim_effect(tier2["workId"], "reaction", sequence=1, fencing_epoch=1)
        self.assertTrue(reaction["allowed"])
        gateway.finish_effect(reaction["idempotencyKey"], state="delivered")
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "effect-not-allowed-for-delivery-tier"):
            gateway.claim_effect(tier2["workId"], "card", sequence=1, fencing_epoch=1)

        promoted = gateway.promote_tier(tier2["workId"], expected_sequence=1, fencing_epoch=1)
        self.assertEqual((promoted["deliveryTier"], promoted["classifierReason"]), (3, "promotion"))
        card = gateway.claim_effect(
            tier2["workId"],
            "card",
            sequence=promoted["sequence"],
            fencing_epoch=promoted["fencingEpoch"],
        )
        self.assertTrue(card["allowed"])
        gateway.finish_effect(card["idempotencyKey"], state="delivered")
        with self.assertRaises(LIFECYCLE.IllegalTransitionError):
            gateway.promote_tier(
                tier2["workId"],
                expected_sequence=promoted["sequence"],
                fencing_epoch=promoted["fencingEpoch"],
            )

    def test_effect_claims_require_current_sequence_and_fence_indeterminate_singletons(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway)
        with self.assertRaisesRegex(LIFECYCLE.StaleEventError, "stale-or-out-of-order-sequence"):
            gateway.claim_effect(receipt["workId"], "card", sequence=999, fencing_epoch=1)

        reaction = gateway.claim_effect(receipt["workId"], "reaction", sequence=1, fencing_epoch=1)
        advanced = gateway.transition(receipt["workId"], "classified", expected_sequence=1, fencing_epoch=1)
        gateway.finish_effect(reaction["idempotencyKey"], state="indeterminate", error_class="timeout")
        fenced = gateway.claim_effect(
            receipt["workId"],
            "reaction",
            sequence=advanced["sequence"],
            fencing_epoch=advanced["fencingEpoch"],
        )
        self.assertFalse(fenced["allowed"])
        self.assertEqual(fenced["state"], "indeterminate")
        self.assertFalse(gateway.status()["ok"])
        self.assertEqual(gateway.status()["effectIndeterminate"], 1)

        retryable = self.start(gateway, origin="retry", run_id="retry")
        first = gateway.claim_effect(retryable["workId"], "card", sequence=1, fencing_epoch=1)
        gateway.finish_effect(first["idempotencyKey"], state="dead_letter", error_class="rate-limit")
        retry_revision = gateway.transition(retryable["workId"], "classified", expected_sequence=1, fencing_epoch=1)
        retried = gateway.claim_effect(
            retryable["workId"],
            "card",
            sequence=retry_revision["sequence"],
            fencing_epoch=retry_revision["fencingEpoch"],
        )
        self.assertTrue(retried["allowed"])

    def test_terminal_outbox_is_committed_before_send_and_replay_conflicts_fail(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway)
        work_id = receipt["workId"]
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "terminal-outbox-missing"):
            gateway.claim_terminal_delivery(work_id)
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "final-effect-requires-terminal-commit"):
            gateway.claim_effect(work_id, "final", sequence=1, fencing_epoch=1)

        payload = {"final": "private terminal text", "format": "HTML"}
        committed = gateway.commit_terminal(
            work_id,
            "succeeded",
            expected_sequence=1,
            fencing_epoch=1,
            private_payload=payload,
        )
        current = gateway.read_work(work_id)
        self.assertEqual((current["phase"], current["deliveryState"]), ("terminal", "pending"))
        with gateway.connect() as db:
            outbox = db.execute("SELECT state,payload_hash FROM terminal_outbox WHERE work_id=?", (work_id,)).fetchone()
        self.assertEqual(outbox["state"], "pending")
        self.assertEqual(outbox["payload_hash"], LIFECYCLE.payload_hash(payload))

        final_effect = gateway.claim_effect(
            work_id,
            "final",
            sequence=current["sequence"],
            fencing_epoch=current["fencingEpoch"],
        )
        self.assertTrue(final_effect["allowed"])
        delivery = gateway.claim_terminal_delivery(work_id)
        self.assertTrue(delivery["allowed"])
        self.assertEqual(delivery["payload"], payload)
        self.assertFalse(gateway.claim_terminal_delivery(work_id)["allowed"])
        gateway.finish_effect(final_effect["idempotencyKey"], state="delivered")
        gateway.finish_terminal_delivery(work_id, "delivered")
        self.assertEqual(gateway.read_work(work_id)["deliveryState"], "delivered")

        duplicate = gateway.commit_terminal(
            work_id,
            "succeeded",
            expected_sequence=1,
            fencing_epoch=1,
            private_payload=payload,
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["eventId"], committed["eventId"])
        with self.assertRaisesRegex(LIFECYCLE.StaleEventError, "terminal-commit-conflict"):
            gateway.commit_terminal(
                work_id,
                "failed",
                expected_sequence=1,
                fencing_epoch=1,
                private_payload={"final": "different"},
            )

    def test_late_nonterminal_effect_cannot_clobber_final_delivery_state(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway)
        card = gateway.claim_effect(receipt["workId"], "card", sequence=1, fencing_epoch=1)
        gateway.commit_terminal(
            receipt["workId"],
            "succeeded",
            expected_sequence=1,
            fencing_epoch=1,
            private_payload={"final": "complete"},
        )
        gateway.claim_terminal_delivery(receipt["workId"])
        gateway.finish_terminal_delivery(receipt["workId"], "delivered")
        gateway.finish_effect(card["idempotencyKey"], state="delivered")
        self.assertEqual(gateway.read_work(receipt["workId"])["deliveryState"], "delivered")

    def test_known_terminal_failure_can_retry_but_indeterminate_send_stays_fenced(self) -> None:
        gateway = self.gateway()
        failed = self.start(gateway, origin="failed-final", run_id="failed-final")
        gateway.commit_terminal(
            failed["workId"],
            "succeeded",
            expected_sequence=1,
            fencing_epoch=1,
            private_payload={"final": "complete"},
        )
        terminal = gateway.read_work(failed["workId"])
        effect = gateway.claim_effect(
            failed["workId"],
            "final",
            sequence=terminal["sequence"],
            fencing_epoch=terminal["fencingEpoch"],
        )
        gateway.claim_terminal_delivery(failed["workId"])
        gateway.finish_effect(effect["idempotencyKey"], state="dead_letter", error_class="rate-limit")
        gateway.finish_terminal_delivery(failed["workId"], "dead_letter")
        requeued = gateway.requeue_terminal_delivery(
            failed["workId"],
            expected_sequence=terminal["sequence"],
            fencing_epoch=terminal["fencingEpoch"],
        )
        self.assertEqual((requeued["state"], requeued["sequence"]), ("pending", terminal["sequence"] + 1))
        retry_effect = gateway.claim_effect(
            failed["workId"],
            "final",
            sequence=requeued["sequence"],
            fencing_epoch=terminal["fencingEpoch"],
        )
        self.assertTrue(retry_effect["allowed"])

        unknown = self.start(gateway, origin="unknown-final", run_id="unknown-final")
        gateway.commit_terminal(
            unknown["workId"],
            "succeeded",
            expected_sequence=1,
            fencing_epoch=1,
            private_payload={"final": "complete"},
        )
        unknown_terminal = gateway.read_work(unknown["workId"])
        gateway.claim_terminal_delivery(unknown["workId"])
        gateway.finish_terminal_delivery(unknown["workId"], "indeterminate")
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "indeterminate-terminal-delivery-fenced"):
            gateway.requeue_terminal_delivery(
                unknown["workId"],
                expected_sequence=unknown_terminal["sequence"],
                fencing_epoch=unknown_terminal["fencingEpoch"],
            )

    def test_callbacks_bind_every_context_field_revision_expiry_and_one_time_consumption(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway)
        token = gateway.create_action(
            work_id=receipt["workId"],
            lifecycle_revision=receipt["sequence"],
            authorized_user="user-ref",
            chat_ref="chat-ref",
            topic_ref="topic-ref",
            message_ref="message-ref",
            artifact_ref="artifact-ref",
            action="cancel",
        )
        token_secret, nonce_secret = token.split(".")[1:]
        with gateway.connect() as db:
            stored = db.execute("SELECT token_hash,nonce_hash FROM actions").fetchone()
        self.assertNotEqual(stored["token_hash"], token_secret)
        self.assertNotEqual(stored["nonce_hash"], nonce_secret)

        with self.assertRaisesRegex(LIFECYCLE.UnauthorizedActionError, "action-binding-mismatch"):
            gateway.consume_action(
                token,
                authorized_user="other-user",
                chat_ref="chat-ref",
                topic_ref="topic-ref",
                message_ref="message-ref",
                artifact_ref="artifact-ref",
            )
        consumed = gateway.consume_action(
            token,
            authorized_user="user-ref",
            chat_ref="chat-ref",
            topic_ref="topic-ref",
            message_ref="message-ref",
            artifact_ref="artifact-ref",
        )
        self.assertEqual((consumed["workId"], consumed["action"]), (receipt["workId"], "cancel"))
        with self.assertRaisesRegex(LIFECYCLE.UnauthorizedActionError, "action-already-consumed"):
            gateway.consume_action(
                token,
                authorized_user="user-ref",
                chat_ref="chat-ref",
                topic_ref="topic-ref",
                message_ref="message-ref",
                artifact_ref="artifact-ref",
            )

        stale_token = gateway.create_action(
            work_id=receipt["workId"],
            lifecycle_revision=receipt["sequence"],
            authorized_user="user-ref",
            chat_ref="chat-ref",
            topic_ref="topic-ref",
            message_ref="message-ref",
            action="cancel",
        )
        gateway.transition(receipt["workId"], "classified", expected_sequence=1, fencing_epoch=1)
        with self.assertRaisesRegex(LIFECYCLE.UnauthorizedActionError, "action-lifecycle-revision-stale"):
            gateway.consume_action(
                stale_token,
                authorized_user="user-ref",
                chat_ref="chat-ref",
                topic_ref="topic-ref",
                message_ref="message-ref",
            )

        current = gateway.read_work(receipt["workId"])
        expired_token = gateway.create_action(
            work_id=receipt["workId"],
            lifecycle_revision=current["sequence"],
            authorized_user="user-ref",
            chat_ref="chat-ref",
            topic_ref="topic-ref",
            message_ref="message-ref",
            action="cancel",
        )
        expired_hash = hashlib.sha256(expired_token.split(".")[1].encode()).hexdigest()
        with gateway.connect() as db, gateway.transaction(db):
            db.execute(
                "UPDATE actions SET expires_at=? WHERE token_hash=?",
                ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)).isoformat(), expired_hash),
            )
        with self.assertRaisesRegex(LIFECYCLE.UnauthorizedActionError, "action-expired"):
            gateway.consume_action(
                expired_token,
                authorized_user="user-ref",
                chat_ref="chat-ref",
                topic_ref="topic-ref",
                message_ref="message-ref",
            )

    def test_callback_consume_compare_and_set_has_exactly_one_winner(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway)
        token = gateway.create_action(
            work_id=receipt["workId"],
            lifecycle_revision=receipt["sequence"],
            authorized_user="user-ref",
            chat_ref="chat-ref",
            topic_ref="topic-ref",
            message_ref="message-ref",
            action="cancel",
        )

        def consume():
            try:
                gateway.consume_action(
                    token,
                    authorized_user="user-ref",
                    chat_ref="chat-ref",
                    topic_ref="topic-ref",
                    message_ref="message-ref",
                )
                return "won"
            except LIFECYCLE.UnauthorizedActionError:
                return "lost"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: consume(), range(2)))
        self.assertEqual(sorted(results), ["lost", "won"])

    def test_postterminal_callback_ack_effects_are_scoped_per_action(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway, surface_contract="brain-intake")
        terminal = gateway.commit_terminal(
            receipt["workId"], "succeeded",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
            private_payload={"surfaceContract": "brain-intake", "result": "safe"},
        )
        current = gateway.read_work(receipt["workId"])
        first = gateway.claim_effect(
            receipt["workId"], "callback_ack",
            sequence=current["sequence"], fencing_epoch=current["fencingEpoch"],
            scope_ref="brain-action-one",
        )
        second = gateway.claim_effect(
            receipt["workId"], "callback_ack",
            sequence=current["sequence"], fencing_epoch=current["fencingEpoch"],
            scope_ref="brain-action-two",
        )
        duplicate = gateway.claim_effect(
            receipt["workId"], "callback_ack",
            sequence=current["sequence"], fencing_epoch=current["fencingEpoch"],
            scope_ref="brain-action-one",
        )
        self.assertTrue(first["allowed"])
        self.assertTrue(second["allowed"])
        self.assertNotEqual(first["idempotencyKey"], second["idempotencyKey"])
        self.assertFalse(duplicate["allowed"])
        self.assertEqual(duplicate["idempotencyKey"], first["idempotencyKey"])
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "callback-ack-scope-required"):
            gateway.claim_effect(
                receipt["workId"], "callback_ack",
                sequence=current["sequence"], fencing_epoch=current["fencingEpoch"],
            )

    def test_worker_route_cas_is_separate_from_owner_and_handoff_fences_prior_owner(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway)
        before_timeout = gateway.read_work(receipt["workId"])
        # A proposed handoff that times out or fails acceptance performs no
        # mutation: only accept_handoff is the durable ownership CAS.
        after_timeout = gateway.read_work(receipt["workId"])
        self.assertEqual(after_timeout, before_timeout)

        with self.assertRaisesRegex(LIFECYCLE.StaleEventError, "stale-current-owner"):
            gateway.update_worker_route(
                receipt["workId"],
                "ollama/glm-5.2:cloud",
                expected_owner="jaimes",
                expected_sequence=1,
                fencing_epoch=1,
            )
        unchanged = gateway.read_work(receipt["workId"])
        self.assertEqual((unchanged["sequence"], unchanged["workerRoute"]), (1, ""))

        delegated = gateway.update_worker_route(
            receipt["workId"],
            "ollama/glm-5.2:cloud",
            expected_owner="josh2",
            expected_sequence=1,
            fencing_epoch=1,
        )
        self.assertEqual(delegated["currentOwner"], "josh2")
        self.assertEqual(delegated["intakeAgent"], "josh2")
        self.assertEqual(delegated["workerRoute"], "ollama/glm-5.2:cloud")
        self.assertEqual((delegated["sequence"], delegated["fencingEpoch"]), (2, 1))

        with self.assertRaisesRegex(LIFECYCLE.StaleEventError, "stale-or-out-of-order-sequence"):
            gateway.accept_handoff(
                receipt["workId"],
                "jaimes",
                expected_owner="josh2",
                expected_sequence=1,
                fencing_epoch=1,
            )
        handed_off = gateway.accept_handoff(
            receipt["workId"],
            "jaimes",
            expected_owner="josh2",
            expected_sequence=delegated["sequence"],
            fencing_epoch=delegated["fencingEpoch"],
        )
        self.assertEqual(handed_off["currentOwner"], "jaimes")
        self.assertEqual(handed_off["intakeAgent"], "josh2")
        self.assertEqual(handed_off["workerRoute"], "ollama/glm-5.2:cloud")
        self.assertEqual((handed_off["sequence"], handed_off["fencingEpoch"]), (3, 2))
        with self.assertRaises(LIFECYCLE.StaleEventError):
            gateway.transition(
                receipt["workId"],
                "classified",
                expected_sequence=handed_off["sequence"],
                fencing_epoch=1,
            )

    def test_owner_change_invalidates_pre_handoff_callback(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway)
        token = gateway.create_action(
            work_id=receipt["workId"],
            lifecycle_revision=receipt["sequence"],
            authorized_user="user-ref",
            chat_ref="chat-ref",
            topic_ref="topic-ref",
            message_ref="message-ref",
            action="handoff",
        )
        gateway.accept_handoff(
            receipt["workId"],
            "jaimes",
            expected_owner="josh2",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )
        with self.assertRaisesRegex(LIFECYCLE.UnauthorizedActionError, "action-owner-mismatch"):
            gateway.consume_action(
                token,
                authorized_user="user-ref",
                chat_ref="chat-ref",
                topic_ref="topic-ref",
                message_ref="message-ref",
            )
        with gateway.connect() as db:
            self.assertIsNone(db.execute("SELECT consumed_at FROM actions").fetchone()["consumed_at"])

    def test_action_schema_migration_leaves_pre_owner_tokens_fail_closed(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway)
        token = "v3.pre-owner-token.pre-owner-nonce"
        with gateway.connect() as db, gateway.transaction(db):
            db.execute("DROP TABLE actions")
            db.execute(
                """CREATE TABLE actions (
                     token_hash TEXT PRIMARY KEY, nonce_hash TEXT NOT NULL UNIQUE,
                     work_id TEXT NOT NULL, lifecycle_revision INTEGER NOT NULL,
                     authorized_user TEXT NOT NULL, chat_ref TEXT NOT NULL,
                     topic_ref TEXT NOT NULL, message_ref TEXT NOT NULL,
                     artifact_ref TEXT NOT NULL, action TEXT NOT NULL,
                     expires_at TEXT NOT NULL, consumed_at TEXT, created_at TEXT NOT NULL
                   )"""
            )
            db.execute(
                "INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    hashlib.sha256(b"pre-owner-token").hexdigest(),
                    hashlib.sha256(b"pre-owner-nonce").hexdigest(),
                    receipt["workId"],
                    receipt["sequence"],
                    "user-ref",
                    "chat-ref",
                    "topic-ref",
                    "message-ref",
                    "",
                    "cancel",
                    (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)).isoformat(),
                    None,
                    dt.datetime.now(dt.timezone.utc).isoformat(),
                ),
            )
        migrated = LIFECYCLE.GatewayLifecycle(self.root, owner="josh2", rollout=self.rollout())
        with migrated.connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(actions)")}
        self.assertIn("authorized_owner", columns)
        with self.assertRaisesRegex(LIFECYCLE.UnauthorizedActionError, "action-owner-mismatch"):
            migrated.consume_action(
                token,
                authorized_user="user-ref",
                chat_ref="chat-ref",
                topic_ref="topic-ref",
                message_ref="message-ref",
            )

    def test_rollout_kills_owner_scoping_brain_gate_and_n_minus_one_read_only(self) -> None:
        self.assertTrue(self.rollout().writer_enabled("josh2"))
        self.assertFalse(self.rollout(global_kill_switch=True).writer_enabled("josh2"))
        self.assertFalse(self.rollout(host_enabled={"josh2": False}).writer_enabled("josh2"))
        self.assertFalse(self.rollout().writer_enabled("unknown-owner"))
        self.assertFalse(self.rollout(writer_version=2).writer_enabled("josh2"))
        self.assertFalse(self.rollout(brain_kill_switch=True).brain_enabled("josh2"))
        with self.assertRaises(LIFECYCLE.UnknownSchemaError):
            self.rollout(reader_versions=(3,)).validate()
        with self.assertRaises(LIFECYCLE.LifecycleError):
            self.rollout(shadow_min_samples=19).validate()

        brain_root = Path(self.tempdir.name) / "brain-gated"
        brain_gateway = LIFECYCLE.GatewayLifecycle(
            brain_root,
            owner="josh2",
            rollout=self.rollout(brain_kill_switch=True),
        )
        brain = self.start(
            brain_gateway,
            origin="brain",
            run_id="brain",
            surface_contract="brain-intake",
            classification=(3, "brain-media"),
        )
        self.assertFalse(brain["writerEnabled"])
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "lifecycle-writer-disabled"):
            brain_gateway.claim_effect(brain["workId"], "card", sequence=1, fencing_epoch=1)

        gateway = self.gateway()
        receipt = self.start(gateway)
        with gateway.connect() as db, gateway.transaction(db):
            db.execute("UPDATE work_receipts SET lifecycle_version=2 WHERE work_id=?", (receipt["workId"],))
        old = gateway.read_work(receipt["workId"])
        self.assertEqual(old["lifecycleVersion"], 2)
        self.assertFalse(old["writerEnabled"])
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "lifecycle-receipt-read-only"):
            gateway.transition(receipt["workId"], "classified", expected_sequence=1, fencing_epoch=1)
        with gateway.connect() as db, gateway.transaction(db):
            db.execute("UPDATE work_receipts SET lifecycle_version=4 WHERE work_id=?", (receipt["workId"],))
        with self.assertRaises(LIFECYCLE.UnknownSchemaError):
            gateway.read_work(receipt["workId"])

    def test_writer_authority_persists_for_terminal_drain_after_master_rollback(self) -> None:
        enabled = self.gateway()
        active = self.start(enabled, origin="active-before-rollback", run_id="active-before-rollback")
        self.assertTrue(active["writerAuthorityAtStart"])
        self.assertTrue(active["writerEnabled"])

        rolled_back = LIFECYCLE.GatewayLifecycle(
            self.root,
            owner="josh2",
            rollout=self.rollout(master_state="off", writer_version=2),
        )
        pinned = rolled_back.read_work(active["workId"])
        self.assertTrue(pinned["writerAuthorityAtStart"])
        self.assertTrue(pinned["writerEnabled"])

        committed = rolled_back.commit_terminal(
            active["workId"],
            "succeeded",
            expected_sequence=pinned["sequence"],
            fencing_epoch=pinned["fencingEpoch"],
            private_payload={"final": "drained by the pinned v3 writer"},
        )
        self.assertFalse(committed["duplicate"])
        terminal = rolled_back.read_work(active["workId"])
        effect = rolled_back.claim_effect(
            active["workId"],
            "final",
            sequence=terminal["sequence"],
            fencing_epoch=terminal["fencingEpoch"],
        )
        delivery = rolled_back.claim_terminal_delivery(active["workId"])
        self.assertTrue(effect["allowed"])
        self.assertTrue(delivery["allowed"])
        rolled_back.finish_effect(effect["idempotencyKey"], state="delivered")
        rolled_back.finish_terminal_delivery(active["workId"], "delivered")
        self.assertEqual(rolled_back.read_work(active["workId"])["deliveryState"], "delivered")

        new_work = self.start(
            rolled_back,
            origin="new-after-rollback",
            run_id="new-after-rollback",
        )
        self.assertFalse(new_work["writerAuthorityAtStart"])
        self.assertFalse(new_work["writerEnabled"])
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "lifecycle-writer-disabled"):
            rolled_back.claim_effect(
                new_work["workId"],
                "card",
                sequence=new_work["sequence"],
                fencing_epoch=new_work["fencingEpoch"],
            )

    def test_live_global_and_host_kills_fence_visible_writes_for_pinned_receipts(self) -> None:
        enabled = self.gateway()
        global_work = self.start(enabled, origin="global-kill", run_id="global-kill")
        host_work = self.start(enabled, origin="host-kill", run_id="host-kill")

        global_killed = LIFECYCLE.GatewayLifecycle(
            self.root,
            owner="josh2",
            rollout=self.rollout(master_state="off", global_kill_switch=True),
        )
        global_receipt = global_killed.read_work(global_work["workId"])
        self.assertTrue(global_receipt["writerAuthorityAtStart"])
        self.assertFalse(global_receipt["writerEnabled"])
        global_killed.commit_terminal(
            global_work["workId"],
            "succeeded",
            expected_sequence=global_receipt["sequence"],
            fencing_epoch=global_receipt["fencingEpoch"],
            private_payload={"final": "durable while delivery is fenced"},
        )
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "lifecycle-writer-disabled"):
            global_killed.claim_terminal_delivery(global_work["workId"])

        host_killed = LIFECYCLE.GatewayLifecycle(
            self.root,
            owner="josh2",
            rollout=self.rollout(master_state="off", host_enabled={"josh2": False, "jaimes": True}),
        )
        host_receipt = host_killed.read_work(host_work["workId"])
        self.assertTrue(host_receipt["writerAuthorityAtStart"])
        self.assertFalse(host_receipt["writerEnabled"])
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "lifecycle-writer-disabled"):
            host_killed.claim_effect(
                host_work["workId"],
                "card",
                sequence=host_receipt["sequence"],
                fencing_epoch=host_receipt["fencingEpoch"],
            )

    def test_writer_authority_column_migrates_additively_and_v2_stays_read_only(self) -> None:
        original = self.gateway()
        receipt = self.start(original, origin="pre-column", run_id="pre-column")
        with original.connect() as db, original.transaction(db):
            db.execute("ALTER TABLE work_receipts DROP COLUMN writer_authority_at_start")

        migrated = self.gateway()
        with migrated.connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(work_receipts)")}
            persisted = db.execute(
                "SELECT writer_authority_at_start FROM work_receipts WHERE work_id=?",
                (receipt["workId"],),
            ).fetchone()["writer_authority_at_start"]
        self.assertIn("writer_authority_at_start", columns)
        self.assertEqual(persisted, 1)
        self.assertTrue(migrated.read_work(receipt["workId"])["writerAuthorityAtStart"])

        with migrated.connect() as db, migrated.transaction(db):
            db.execute("UPDATE work_receipts SET lifecycle_version=2 WHERE work_id=?", (receipt["workId"],))
        old = migrated.read_work(receipt["workId"])
        self.assertEqual(old["lifecycleVersion"], 2)
        self.assertFalse(old["writerEnabled"])
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "lifecycle-receipt-read-only"):
            migrated.transition(
                receipt["workId"],
                "classified",
                expected_sequence=old["sequence"],
                fencing_epoch=old["fencingEpoch"],
            )

    def test_shadow_evidence_requires_shadow_mode_matching_owner_clean_and_unique_samples(self) -> None:
        off = LIFECYCLE.GatewayLifecycle(
            Path(self.tempdir.name) / "off",
            owner="josh2",
            rollout=self.rollout(master_state="off"),
        )
        off_receipt = self.start(off, origin="off", run_id="off")
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "shadow-sampling-disabled"):
            off.record_shadow_sample(
                off_receipt["workId"], observed_contract="reaction-card-final",
            )

        shadow = LIFECYCLE.GatewayLifecycle(
            Path(self.tempdir.name) / "shadow",
            owner="josh2",
            rollout=self.rollout(master_state="shadow"),
        )
        latest = None
        for index in range(20):
            receipt = self.start(shadow, origin=f"shadow-{index}", run_id=f"shadow-{index}")
            self.assertTrue(receipt["shadowOnly"])
            self.assertFalse(receipt["writerEnabled"])
            latest = shadow.record_shadow_sample(
                receipt["workId"], observed_contract="reaction-card-final",
            )
            self.assertFalse(latest["eligible"])
            shadow.commit_terminal(
                receipt["workId"],
                "succeeded",
                expected_sequence=receipt["sequence"],
                fencing_epoch=receipt["fencingEpoch"],
                private_payload={"final": "shadow-only comparison"},
            )
            latest = shadow.finish_shadow_sample(receipt["workId"], delivered=True)
        self.assertTrue(latest["eligible"])
        duplicate = shadow.record_shadow_sample(
            receipt["workId"], observed_contract="reaction-card-final",
        )
        self.assertEqual(duplicate["total"], 20)
        self.assertTrue(duplicate["eligible"])
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "shadow-observation-conflict"):
            shadow.record_shadow_sample(
                receipt["workId"], observed_contract="final-only",
            )

        wrong_owner = self.start(
            shadow,
            origin="shadow-jaimes",
            run_id="shadow-jaimes",
            current_owner="jaimes",
            intake_agent="jaimes",
        )
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "shadow-owner-mismatch"):
            shadow.record_shadow_sample(
                wrong_owner["workId"], observed_contract="reaction-card-final",
            )

    def test_shadow_advances_private_lifecycle_and_render_without_visible_authority(self) -> None:
        shadow = LIFECYCLE.GatewayLifecycle(
            Path(self.tempdir.name) / "shadow-simulation",
            owner="josh2",
            rollout=self.rollout(master_state="shadow"),
        )
        receipt = self.start(shadow, origin="shadow-sim", run_id="shadow-sim")
        classified = shadow.transition(
            receipt["workId"],
            "classified",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )
        routed = shadow.update_worker_route(
            receipt["workId"],
            "shadow/legacy-comparison",
            expected_owner="josh2",
            expected_sequence=classified["sequence"],
            fencing_epoch=classified["fencingEpoch"],
        )
        rendered = LIFECYCLE.render_live_card(
            routed,
            objective="shadow lifecycle comparison",
            phase_label=routed["phase"],
            model="shadow",
            route=routed["workerRoute"],
            progress=50,
        )
        self.assertTrue(shadow.update_render_hash(receipt["workId"], rendered))
        self.assertFalse(shadow.update_render_hash(receipt["workId"], rendered))
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "lifecycle-writer-disabled"):
            shadow.claim_effect(
                receipt["workId"],
                "card",
                sequence=routed["sequence"],
                fencing_epoch=routed["fencingEpoch"],
            )

        shadow.commit_terminal(
            receipt["workId"],
            "succeeded",
            expected_sequence=routed["sequence"],
            fencing_epoch=routed["fencingEpoch"],
            private_payload={"final": "shadow-only comparison"},
        )
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "lifecycle-writer-disabled"):
            shadow.claim_terminal_delivery(receipt["workId"])
        sample = shadow.record_shadow_sample(
            receipt["workId"],
            observed_contract="reaction-card-final",
        )
        self.assertEqual(
            (sample["total"], sample["clean"], sample["unobserved"], sample["eligible"]),
            (1, 0, 1, False),
        )
        finished = shadow.finish_shadow_sample(receipt["workId"], delivered=True)
        self.assertEqual(
            (finished["total"], finished["clean"], finished["unobserved"], finished["eligible"]),
            (1, 1, 0, False),
        )
        self.assertTrue(shadow.finish_shadow_sample(receipt["workId"], delivered=True)["duplicate"])
        with self.assertRaisesRegex(
            LIFECYCLE.LifecycleError, "shadow-terminal-observation-conflict",
        ):
            shadow.finish_shadow_sample(receipt["workId"], delivered=False)
        with shadow.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 0)
            outbox = db.execute(
                "SELECT state,attempts FROM terminal_outbox WHERE work_id=?",
                (receipt["workId"],),
            ).fetchone()
        self.assertEqual((outbox["state"], outbox["attempts"]), ("pending", 0))

        promotable = self.start(
            shadow,
            origin="shadow-promotion",
            run_id="shadow-promotion",
            classification=(2, "quick-answer"),
        )
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "lifecycle-writer-disabled"):
            shadow.promote_tier(
                promotable["workId"],
                expected_sequence=promotable["sequence"],
                fencing_epoch=promotable["fencingEpoch"],
            )

    def test_preterminal_observation_shadow_schema_migrates_without_column_order_corruption(self) -> None:
        root = Path(self.tempdir.name) / "old-shadow-schema"
        root.mkdir(mode=0o700)
        with sqlite3.connect(root / "lifecycle.sqlite3") as db:
            db.execute(
                """CREATE TABLE shadow_samples (
                     id TEXT PRIMARY KEY,owner TEXT NOT NULL,work_id TEXT NOT NULL,
                     tier INTEGER NOT NULL,reason TEXT NOT NULL,
                     legacy_contract TEXT NOT NULL,matched INTEGER NOT NULL,
                     created_at TEXT NOT NULL,UNIQUE(owner,work_id)
                   )"""
            )
        shadow = LIFECYCLE.GatewayLifecycle(
            root,
            owner="josh2",
            rollout=self.rollout(master_state="shadow"),
        )
        receipt = self.start(shadow, origin="migrated-shadow", run_id="migrated-shadow")
        shadow.record_shadow_sample(
            receipt["workId"], observed_contract="reaction-card-final",
        )
        with shadow.connect() as db:
            row = db.execute(
                "SELECT legacy_contract,matched,terminal_observed,terminal_delivered,created_at "
                "FROM shadow_samples WHERE work_id=?",
                (receipt["workId"],),
            ).fetchone()
        self.assertEqual(row["legacy_contract"], "reaction-card-final")
        self.assertEqual(
            (row["matched"], row["terminal_observed"], row["terminal_delivered"]),
            (1, 0, 0),
        )
        self.assertRegex(str(row["created_at"]), r"^\d{4}-\d{2}-\d{2}T")

    def test_native_surfaces_remain_unwritten_and_private_store_permissions_are_restrictive(self) -> None:
        gateway = self.gateway()
        native = gateway.start_work(
            origin_key="native",
            run_id="native",
            intake_agent="josh2",
            current_owner="josh2",
            surface_contract="native-desktop",
        )
        self.assertTrue(native["native"])
        with gateway.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM work_receipts").fetchone()[0], 0)
        self.assertEqual(os.stat(self.root).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(gateway.db_path).st_mode & 0o777, 0o600)

    def test_events_drop_private_fields_and_renderer_escapes_untrusted_text(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway)
        gateway.transition(
            receipt["workId"],
            "classified",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
            safe_payload={"status": "classified", "rawMessage": "private payload must not persist"},
        )
        with gateway.connect() as db:
            event = db.execute(
                "SELECT safe_payload_json FROM lifecycle_events WHERE work_id=? AND sequence=2",
                (receipt["workId"],),
            ).fetchone()["safe_payload_json"]
        self.assertIn("classified", event)
        self.assertNotIn("private payload", event)
        rendered = LIFECYCLE.render_live_card(
            receipt,
            objective="<script>unsafe</script>",
            phase_label="working",
            model="model&route",
            route="owner<worker",
            progress=150,
        )
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("100%", rendered)

    def test_coalesced_progress_advances_fence_without_persisting_display_text(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway)
        receipt = gateway.transition(
            receipt["workId"],
            "classified",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )
        progressed = gateway.record_progress(
            receipt["workId"],
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
            status="heartbeat",
        )
        self.assertEqual(progressed["sequence"], receipt["sequence"] + 1)
        self.assertTrue(progressed["eventId"].startswith("receipt-"))
        with gateway.connect() as db:
            event = db.execute(
                "SELECT safe_payload_json FROM lifecycle_events WHERE work_id=? AND sequence=?",
                (receipt["workId"], progressed["sequence"]),
            ).fetchone()["safe_payload_json"]
        self.assertEqual(json.loads(event)["status"], "heartbeat")
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "unsafe-progress-status"):
            gateway.record_progress(
                receipt["workId"],
                expected_sequence=progressed["sequence"],
                fencing_epoch=progressed["fencingEpoch"],
                status="raw private worker text",
            )

    def test_terminal_rejects_progress_but_allows_one_receipted_tier_three_close_edit(self) -> None:
        gateway = self.gateway()
        receipt = self.start(gateway, classification=(3, "multi-step"))
        receipt = gateway.transition(
            receipt["workId"],
            "classified",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
        )
        gateway.commit_terminal(
            receipt["workId"],
            "succeeded",
            expected_sequence=receipt["sequence"],
            fencing_epoch=receipt["fencingEpoch"],
            private_payload={"final": "private terminal payload"},
        )
        terminal = gateway.read_work(receipt["workId"])
        with self.assertRaisesRegex(LIFECYCLE.LifecycleError, "progress-after-terminal"):
            gateway.record_progress(
                receipt["workId"],
                expected_sequence=terminal["sequence"],
                fencing_epoch=terminal["fencingEpoch"],
                status="heartbeat",
            )
        effect = gateway.claim_effect(
            receipt["workId"],
            "card_edit",
            sequence=terminal["sequence"],
            fencing_epoch=terminal["fencingEpoch"],
        )
        self.assertTrue(effect["allowed"])
        gateway.finish_effect(effect["idempotencyKey"], state="delivered", private_receipt="confirmed")
        duplicate = gateway.claim_effect(
            receipt["workId"],
            "card_edit",
            sequence=terminal["sequence"],
            fencing_epoch=terminal["fencingEpoch"],
        )
        self.assertFalse(duplicate["allowed"])
        self.assertEqual(duplicate["state"], "delivered")


if __name__ == "__main__":
    unittest.main()
