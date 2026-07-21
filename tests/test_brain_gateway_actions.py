from __future__ import annotations

import json
import inspect
import hashlib
import io
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brain_gateway_actions as actions_module
import brain_gateway_dispatcher as dispatcher_module
import brain_media_intake as brain
import memory_registry
from telegram_gateway_lifecycle import GatewayLifecycle, RolloutPolicy


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.responses: list[dict[str, object]] = []
        self.next_message = 900

    def __call__(self, method: str, payload: dict[str, object], _timeout: int) -> dict[str, object]:
        self.calls.append((method, dict(payload)))
        if self.responses:
            return self.responses.pop(0)
        self.next_message += 1
        return {
            "ok": True,
            "state": "delivered",
            "result": {"message_id": self.next_message},
        }


class RecordingPublisher:
    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.events: list[dict[str, object]] = []

    def __call__(self, event: dict[str, object]) -> bool:
        self.events.append(dict(event))
        return self.accept


class BrainGatewayActionsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="brain-actions-test-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.store_root = self.folder / "private" / "brain"
        self.lifecycle_root = self.folder / "private" / "lifecycle"
        self.dispatcher_root = self.folder / "private" / "dispatcher"
        self.action_root = self.folder / "private" / "actions"
        self.rollout = self.folder / "rollout.json"
        self.config = self.folder / "lanes.json"
        self.topic_receipt = self.folder / "private" / "topic.json"
        self.sender_receipt = self.folder / "private" / "sender.json"
        self.registry_path = self.folder / "private" / "memory.sqlite3"
        self.registry_status = self.folder / "private" / "memory-status.json"
        self.download_root = self.folder / "private" / "downloads"
        self.download_root.mkdir(parents=True, mode=0o700)
        self._write_rollout()
        self._write_config(enabled=True)
        self.topic_receipt.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._write_private(self.topic_receipt, {
            "state": "confirmed", "topicName": "Brain", "attemptCount": 1,
            "chatId": "-100123", "topicId": "77", "botId": "42",
        })
        self._write_private(self.sender_receipt, {
            "state": "confirmed", "owner": "josh2", "authorizedSenderId": "9001",
            "chatId": "-100123", "topicId": "77",
        })
        self.store = brain.BrainStore(
            self.store_root,
            download_roots=[self.download_root],
            authorized_sender_receipt=self.sender_receipt,
        )
        self.transport = RecordingTransport()
        self.publisher = RecordingPublisher()

    @staticmethod
    def _write_private(path: Path, payload: dict[str, object]) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o600)

    def _write_rollout(self, *, brain_kill: bool = False) -> None:
        self.rollout.write_text(json.dumps({
            "masterState": "josh2",
            "globalKillSwitch": False,
            "brainKillSwitch": brain_kill,
            "hosts": {"josh2": True},
            "writerLifecycleVersion": 3,
            "readerLifecycleVersions": [2, 3],
            "shadowMinimumPerOwner": 20,
            "brainFixtureMinimum": 20,
        }), encoding="utf-8")

    def _write_config(self, *, enabled: bool) -> None:
        self.config.write_text(json.dumps({
            "dynamicTopics": {"brain": {
                "label": "Brain", "owner": "josh2", "lane": "brain-intake",
                "topicIdSource": "private-confirmed-receipt", "enabled": enabled,
            }},
        }), encoding="utf-8")

    def adapter(self) -> actions_module.BrainGatewayActions:
        return actions_module.BrainGatewayActions(
            self.store_root,
            lifecycle_root=self.lifecycle_root,
            rollout_path=self.rollout,
            config_path=self.config,
            topic_receipt_path=self.topic_receipt,
            authorized_sender_receipt_path=self.sender_receipt,
            dispatcher_state_root=self.dispatcher_root,
            state_root=self.action_root,
            transport=self.transport,
            action_publisher=self.publisher,
        )

    def gateway(self) -> GatewayLifecycle:
        return GatewayLifecycle(
            self.lifecycle_root,
            rollout=RolloutPolicy.load(self.rollout),
            owner="josh2",
        )

    def allow_delete_permission(self) -> None:
        self.transport.responses.extend((
            {
                "ok": True,
                "state": "delivered",
                "result": {"id": 42},
            },
            {
                "ok": True,
                "state": "delivered",
                "result": {
                    "status": "administrator",
                    "can_manage_topics": True,
                    "can_delete_messages": True,
                },
            },
        ))

    def ready_isolated_journal(
        self,
        work_id: str,
        *,
        privacy_path: bool = True,
    ) -> tuple[actions_module.BrainHumanCanaryJournal, dict[str, str]]:
        journal = actions_module.BrainHumanCanaryJournal(
            self.action_root / "isolated-human-canary", work_id,
        )
        targets = {
            target_class: str(1000 + ordinal)
            for ordinal, target_class in enumerate(actions_module.HUMAN_CANARY_DELETE_ORDER)
        }
        journal.activate(
            chat_ref="-100123",
            topic_ref="77",
            source_message_ref=targets["source_media"],
            card_message_ref=targets["ingestion_card"],
            final_message_ref=targets["ingestion_final"],
        )
        classes = [
            *(
                actions_module.HUMAN_CANARY_PRIVACY_CLASSES
                if privacy_path else ()
            ),
            *actions_module.HUMAN_CANARY_FORGET_CLASSES,
        ]
        for target_class in classes:
            journal.record(
                target_class=target_class,
                direction=("inbound" if target_class.endswith(("command", "confirm")) else "outbound"),
                chat_ref="-100123",
                topic_ref="77",
                message_ref=targets[target_class],
                delivery_state=("received" if target_class.endswith(("command", "confirm")) else "delivered"),
            )
        journal.seal()
        journal.mark_bindings_scrubbed()
        journal.mark_post_forget_verified()
        return journal, targets

    def mark_lifecycle_delivered(self, work_id: str) -> None:
        with self.gateway().connect() as db, self.gateway().transaction(db):
            db.execute(
                """UPDATE work_receipts SET phase='terminal',outcome='succeeded',
                          reaction_delivered=1,card_created=1,final_delivered=1,
                          delivery_state='delivered' WHERE work_id=?""",
                (work_id,),
            )

    def activate_canary(self, work_id: str) -> actions_module.BrainGatewayActions:
        self.mark_lifecycle_delivered(work_id)
        self.allow_delete_permission()
        adapter = self.adapter()
        result = adapter.activate_human_canary(work_id)
        self.assertTrue(result["ok"])
        self.assertTrue(result["deletePermissionVerified"])
        return adapter

    def begin_verified_human_canary(
        self,
        message: str = "180",
        *,
        privacy: str = "dashboard-safe",
    ) -> str:
        source_path = self.download_root / f"fixture-{message}.txt"
        source_path.write_text(
            "fact: JCU10 human canary | validates | cleanup journal\n",
            encoding="utf-8",
        )
        source_path.chmod(0o600)
        receipt = self.store.begin_submission({
            "chatId": "-100123",
            "threadId": "77",
            "messageId": message,
            "senderId": "9001",
            "senderIsBot": False,
            "mediaGroupId": "",
            "caption": "",
            "attachments": [{
                "sourceMessageId": message,
                "fileId": f"private-file-{message}",
                "kind": "document",
                "mime": "text/plain",
                "size": source_path.stat().st_size,
            }],
        }, privacy=privacy)
        work_id = str(receipt["workId"])
        brain.ensure_brain_lifecycle(
            self.store,
            work_id,
            lifecycle_root=self.lifecycle_root,
            rollout_path=self.rollout,
        )
        dispatcher = dispatcher_module.BrainGatewayDispatcher(
            self.store_root,
            lifecycle_root=self.lifecycle_root,
            rollout_path=self.rollout,
            config_path=self.config,
            topic_receipt_path=self.topic_receipt,
            state_root=self.dispatcher_root,
            transport=self.transport,
            visibility_publisher=self.publisher,
            dispatcher_id="human-canary-test",
        )
        self.assertEqual(dispatcher.process_work(work_id), "active")
        token = receipt["downloadTokens"][0]
        accepted = self.store.accept_download(
            work_id=work_id,
            attachment_id=str(token["attachmentId"]),
            token=str(token["token"]),
            source_path=source_path,
        )
        self.assertTrue(accepted["stored"])
        extracted = self.store.extract_submission(work_id)
        self.assertEqual(extracted["phase"], "indexed")
        synthesized = self.store.synthesize_candidates(work_id)
        self.assertGreaterEqual(int(synthesized["candidateCount"]), 1)
        self.store.review_candidates(work_id)
        dispatcher.process_work(work_id)
        binding = self.store.lifecycle_binding(work_id)
        self.assertIsNotNone(binding)
        terminal_payload = {
            "handoffSchemaVersion": 1,
            "surfaceContract": "brain-intake",
            "deliveryTier": 3,
            "owner": "josh2",
            "brainWorkRef": "private-opaque-ref",
            "sourceRevision": int(binding["source_revision_at_start"]),
            "terminalStatus": "succeeded",
            "errorClass": "n/a",
            "receipt": self.store.final_receipt(work_id),
        }
        encoded = json.dumps(
            terminal_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                """INSERT INTO intake_terminal_prepares(
                     work_id,outcome,payload_hash,private_payload_json,
                     attempt_fence,lease_owner_hash,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    work_id, "succeeded", hashlib.sha256(encoded.encode()).hexdigest(),
                    encoded, 1, "test-worker-fence", brain.utc_now(),
                ),
            )
        gateway = self.gateway()
        lifecycle = gateway.read_work(work_id)
        gateway.commit_terminal(
            work_id,
            "succeeded",
            expected_sequence=int(lifecycle["sequence"]),
            fencing_epoch=int(lifecycle["fencingEpoch"]),
            private_payload=terminal_payload,
        )
        self.assertEqual(dispatcher.process_work(work_id), "delivered")
        return work_id

    def set_review_only_partial_terminal(self, work_id: str) -> dict[str, object]:
        with self.store.connect() as db:
            receipt = self.store.final_receipt(work_id)
            binding = db.execute(
                "SELECT lifecycle_work_id FROM lifecycle_bindings WHERE work_id=?",
                (work_id,),
            ).fetchone()
        receipt["Stored"] = "Yes"
        receipt["Source indexed"] = "Yes"
        receipt["Learned"] = {"count": 0, "types": ["n/a"]}
        receipt["Pending review"] = {
            "count": 1,
            "reasons": ["manual-review-required"],
        }
        receipt["Unsupported"] = ["n/a"]
        receipt["Approval needed"] = "memory review"
        payload: dict[str, object] = {
            "handoffSchemaVersion": 1,
            "surfaceContract": "brain-intake",
            "deliveryTier": 3,
            "owner": "josh2",
            "brainWorkRef": "private-opaque-ref",
            "sourceRevision": 1,
            "terminalStatus": "partial",
            "errorClass": "n/a",
            "receipt": receipt,
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
        with self.gateway().connect() as db, self.gateway().transaction(db):
            outbox = db.execute(
                "SELECT event_id FROM terminal_outbox WHERE work_id=?", (work_id,),
            ).fetchone()
            self.assertIsNotNone(outbox)
            db.execute(
                """UPDATE work_receipts SET outcome='partial' WHERE work_id=?""",
                (work_id,),
            )
            db.execute(
                """UPDATE terminal_outbox
                      SET outcome='partial',state='delivered',payload_json=?,payload_hash=?
                    WHERE work_id=?""",
                (encoded, payload_hash, work_id),
            )
            db.execute(
                "DELETE FROM lifecycle_events WHERE work_id=? AND event_type='verifying'",
                (work_id,),
            )
            next_sequence = int(db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM lifecycle_events WHERE work_id=?",
                (work_id,),
            ).fetchone()[0])
            db.execute(
                """INSERT INTO lifecycle_events(
                     event_id,work_id,sequence,fencing_epoch,event_type,
                     safe_payload_json,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    f"review-event-{work_id}", work_id, next_sequence, 1, "verifying",
                    json.dumps({"candidateCount": 1, "reviewCount": 1}, sort_keys=True),
                    brain.utc_now(),
                ),
            )
        with self.store.connect() as db, self.store.transaction(db):
            db.execute("DELETE FROM intake_results WHERE work_id=?", (work_id,))
            db.execute(
                """INSERT INTO intake_results(
                     result_id,work_id,lifecycle_work_id,terminal_event_id,outcome,
                     payload_hash,private_payload_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    f"partial-result-{work_id}", work_id,
                    str(binding["lifecycle_work_id"]), str(outbox["event_id"]), "partial",
                    payload_hash, encoded, brain.utc_now(),
                ),
            )
        return payload

    def begin(self, message: str = "100") -> str:
        receipt = self.store.begin_submission({
            "chatId": "-100123",
            "threadId": "77",
            "messageId": message,
            "senderId": "9001",
            "senderIsBot": False,
            "mediaGroupId": "",
            "caption": "private caption",
            "attachments": [{
                "sourceMessageId": message,
                "fileId": f"private-file-{message}",
                "kind": "document",
                "mime": "text/plain",
                "size": 0,
            }],
        })
        work_id = str(receipt["workId"])
        brain.ensure_brain_lifecycle(
            self.store,
            work_id,
            lifecycle_root=self.lifecycle_root,
            rollout_path=self.rollout,
        )
        self._surface(
            work_id,
            source=message,
            card=str(600 + int(message)),
            final=str(700 + int(message)),
        )
        return work_id

    def _surface(
        self,
        work_id: str,
        *,
        source: str = "100",
        card: str = "700",
        final: str = "800",
        route_verified: bool = False,
    ) -> None:
        self.dispatcher_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        path = self.dispatcher_root / "dispatcher.sqlite3"
        db = sqlite3.connect(path)
        try:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS surfaces (
                  work_id TEXT PRIMARY KEY, chat_ref TEXT NOT NULL, topic_ref TEXT NOT NULL,
                  source_message_ref TEXT NOT NULL, card_message_ref TEXT NOT NULL,
                  final_message_ref TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (work_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS visibility_outbox (
                  lifecycle_work_id TEXT NOT NULL, route_verified INTEGER NOT NULL,
                  route_class TEXT NOT NULL, stage TEXT NOT NULL
                );
                """
            )
            db.execute(
                "INSERT OR REPLACE INTO surfaces VALUES(?,?,?,?,?,?)",
                (work_id, "-100123", "77", source, card, final),
            )
            if route_verified:
                db.execute(
                    "INSERT INTO visibility_outbox VALUES(?,?,?,?)",
                    (work_id, 1, "local-deterministic", "verifying"),
                )
            db.commit()
        finally:
            db.close()

    @staticmethod
    def envelope(
        text: str,
        *,
        message: str = "200",
        reply: str = "100",
        sender: str = "9001",
        bot: bool = False,
        chat: str = "-100123",
        topic: str = "77",
        edited: bool = False,
    ) -> dict[str, object]:
        return {
            "chatId": chat,
            "threadId": topic,
            "messageId": message,
            "replyToMessageId": reply,
            "senderId": sender,
            "senderIsBot": bot,
            "text": text,
            "edited": edited,
        }

    def test_bot_unauthorized_wrong_topic_and_unbound_actions_are_silent_without_state(self) -> None:
        adapter = self.adapter()
        for envelope in (
            self.envelope("/cancel", bot=True),
            self.envelope("/cancel", sender="9999"),
        ):
            result = adapter.handle_event(envelope)
            self.assertTrue(result["handled"])
            self.assertTrue(result["silentDrop"])
        self.assertFalse(adapter.db_path.exists())

        wrong_topic = adapter.handle_event(self.envelope("/cancel", topic="78"))
        self.assertFalse(wrong_topic["handled"])
        self.assertFalse(adapter.db_path.exists())

        unbound = adapter.handle_event(self.envelope("Privacy: internal", reply="999"))
        self.assertTrue(unbound["handled"])
        self.assertTrue(unbound["silentDrop"])
        self.assertFalse(adapter.db_path.exists())

    def test_missing_sender_receipt_does_not_affect_non_brain_messages(self) -> None:
        self.sender_receipt.unlink()
        adapter = self.adapter()

        result = adapter.handle_event(self.envelope("ordinary message", topic="78", reply=""))

        self.assertTrue(result["ok"])
        self.assertFalse(result["handled"])
        self.assertFalse(result["silentDrop"])
        self.assertFalse(adapter.db_path.exists())

    def test_missing_sender_receipt_keeps_brain_message_replayable(self) -> None:
        self.sender_receipt.unlink()
        adapter = self.adapter()

        result = adapter.handle_event(self.envelope("/cancel"))

        self.assertFalse(result["ok"])
        self.assertFalse(result["handled"])
        self.assertFalse(result["silentDrop"])
        self.assertTrue(result["routingUnavailable"])
        self.assertFalse(adapter.db_path.exists())

    def test_missing_tracked_config_does_not_affect_non_brain_messages(self) -> None:
        self.config.unlink()
        adapter = self.adapter()

        result = adapter.handle_event(self.envelope("ordinary message", topic="78", reply=""))

        self.assertTrue(result["ok"])
        self.assertFalse(result["handled"])
        self.assertFalse(result["silentDrop"])
        self.assertFalse(adapter.db_path.exists())

    def test_openclaw_hook_action_probe_is_private_and_precedes_generic_route_fragment(self) -> None:
        hook = brain.hook_source()
        self.assertIn("function jcu10BrainActionCheck(message, edited = false)", hook)
        self.assertIn('[script, "process-event", "--private-stdin"]', hook)
        action_function = hook.split("function jcu10BrainActionCheck", 1)[1].split(
            "function jcu10BrainRouteCheck", 1,
        )[0]
        self.assertNotIn("fileId", action_function)
        self.assertNotIn("filename", action_function.lower())
        installer = inspect.getsource(brain.patch_openclaw_ingress)
        action_fragment = 'const jcu10BrainAction = jcu10BrainActionCheck(event.msg);'
        route_fragment = 'const jcu10BrainRoute = hasInboundMedia(event.msg)'
        self.assertIn(action_fragment, installer)
        self.assertIn(route_fragment, installer)
        self.assertLess(installer.index(action_fragment), installer.index(route_fragment))

    def test_exact_source_and_active_card_cancel_are_effective_once_and_retain_source(self) -> None:
        for index, reply in enumerate(("100", "701"), start=1):
            work_id = self.begin(str(99 + index))
            # The helper uses distinct source ids; keep the card stable.
            if reply == "100":
                reply = str(99 + index)
            adapter = self.adapter()
            event = self.envelope(
                "/cancel" if index == 1 else "Cancel this Brain intake",
                message=str(210 + index),
                reply=reply,
            )
            first = adapter.handle_event(event)
            second = adapter.handle_event(event)
            self.assertEqual(first["actionState"], "delivered")
            self.assertTrue(second["duplicate"])
            with self.store.connect() as db:
                submission = db.execute(
                    "SELECT user_cancel_requested,source_private_json FROM submissions WHERE work_id=?",
                    (work_id,),
                ).fetchone()
            self.assertTrue(submission["user_cancel_requested"])
            self.assertNotEqual(submission["source_private_json"], "{}")
            self.assertTrue(self.gateway().read_work(work_id)["cancelRequested"])
        self.assertEqual(len(self.transport.calls), 2)

    def test_privacy_broadening_requires_exact_reply_bound_confirmation_and_lowering_is_immediate(self) -> None:
        work_id = self.begin("110")
        adapter = self.adapter()
        preview = adapter.handle_event(self.envelope(
            "Privacy: internal", message="220", reply="110",
        ))
        self.assertEqual(preview["actionState"], "delivered")
        self.assertNotIn("private caption", json.dumps(self.transport.calls[-1][1]))
        with adapter.connect() as db:
            mapping = db.execute(
                "SELECT message_ref FROM message_mappings WHERE mapping_kind='privacy-preview'",
            ).fetchone()
        self.assertIsNotNone(mapping)
        preview_message = str(mapping["message_ref"])

        wrong = adapter.handle_event(self.envelope(
            "confirm privacy", message="221", reply=preview_message,
        ))
        self.assertEqual(wrong["actionState"], "ignored")
        with self.store.connect() as db:
            self.assertEqual(db.execute(
                "SELECT privacy_class FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()[0], "private")

        confirmed_event = self.envelope(
            "CONFIRM PRIVACY", message="222", reply=preview_message,
        )
        confirmed = adapter.handle_event(confirmed_event)
        duplicate = adapter.handle_event(confirmed_event)
        self.assertEqual(confirmed["actionState"], "delivered")
        self.assertTrue(duplicate["duplicate"])
        with self.store.connect() as db:
            self.assertEqual(db.execute(
                "SELECT privacy_class FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()[0], "internal")

        lowered = adapter.handle_event(self.envelope(
            "Privacy: private", message="223", reply="110",
        ))
        self.assertEqual(lowered["actionState"], "delivered")
        with self.store.connect() as db:
            self.assertEqual(db.execute(
                "SELECT privacy_class FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()[0], "private")
        self.assertNotIn("private caption", json.dumps(self.publisher.events))

    def test_all_governed_action_forms_use_distinct_scoped_ack_effects(self) -> None:
        work_id = self.begin("111")
        adapter = self.adapter()
        governed_store = mock.Mock()
        governed_store.correct.return_value = {"ok": True}
        governed_store.mark_reference_only.return_value = {"ok": True}
        governed_store.approve_candidate.return_value = {"ok": True}
        governed_store.reject_candidate.return_value = {"ok": True}
        governed_store.supersede_memory.return_value = {"ok": True}
        commands = (
            "Correct: Mars | has color | red",
            "Reference only",
            "Approve candidate: candidate-1",
            "Reject candidate: candidate-2 | outdated",
            "Supersede memory: memory-1 | with candidate: candidate-3",
        )
        with mock.patch.object(adapter, "_store", return_value=governed_store):
            for offset, command in enumerate(commands):
                result = adapter.handle_event(self.envelope(
                    command,
                    message=str(300 + offset),
                    reply="111",
                ))
                self.assertEqual(result["actionState"], "delivered")
            card_rejected = adapter.handle_event(self.envelope(
                "Correct: Mars | has color | blue",
                message="399",
                reply="711",
            ))
        self.assertEqual(card_rejected["actionState"], "ignored")
        self.assertEqual(governed_store.correct.call_count, 1)
        governed_store.mark_reference_only.assert_called_once()
        governed_store.approve_candidate.assert_called_once()
        governed_store.reject_candidate.assert_called_once()
        governed_store.supersede_memory.assert_called_once()
        with self.gateway().connect() as db:
            effects = db.execute(
                """SELECT idempotency_key,scope_ref FROM effects
                     WHERE work_id=? AND kind='callback_ack'""",
                (work_id,),
            ).fetchall()
        self.assertEqual(len(effects), len(commands))
        self.assertEqual(len({row["idempotency_key"] for row in effects}), len(commands))
        self.assertTrue(all(row["scope_ref"] for row in effects))

    def test_reserved_restart_resumes_but_executing_restart_is_indeterminate(self) -> None:
        work_id = self.begin("120")
        adapter = self.adapter()
        event = self.envelope("Reference only", message="230", reply="120")
        event_key = actions_module.stable_ref(
            "brain-action-inbound", "-100123", "77", "230",
        )
        action_ref = actions_module.stable_ref(
            "brain-action", "-100123", "77", "230", "120",
        )
        self.assertTrue(adapter._reserve_inbound(
            event_key=event_key,
            message_ref="230",
            reply_ref="120",
            work_id=work_id,
            action_ref=action_ref,
            action="reference-only",
        ))
        resumed = adapter.handle_event(event)
        self.assertEqual(resumed["actionState"], "delivered")

        work_two = self.begin("121")
        event_two = self.envelope("Reference only", message="231", reply="121")
        key_two = actions_module.stable_ref(
            "brain-action-inbound", "-100123", "77", "231",
        )
        ref_two = actions_module.stable_ref(
            "brain-action", "-100123", "77", "231", "121",
        )
        self.assertTrue(adapter._reserve_inbound(
            event_key=key_two,
            message_ref="231",
            reply_ref="121",
            work_id=work_two,
            action_ref=ref_two,
            action="reference-only",
        ))
        adapter._update_inbound(key_two, "executing")
        fenced = adapter.handle_event(event_two)
        self.assertEqual(fenced["actionState"], "indeterminate")
        self.assertEqual(len(self.transport.calls), 1)

    def test_ambiguous_telegram_ack_is_never_retried_and_status_is_unhealthy(self) -> None:
        self.begin("130")
        adapter = self.adapter()
        self.transport.responses.append({
            "ok": False,
            "state": "indeterminate",
            "errorClass": "telegram-result-unknown",
        })
        event = self.envelope("Reference only", message="240", reply="130")
        first = adapter.handle_event(event)
        duplicate = adapter.handle_event(event)
        self.assertEqual(first["actionState"], "indeterminate")
        self.assertEqual(duplicate["actionState"], "indeterminate")
        self.assertEqual(len(self.transport.calls), 1)
        self.assertFalse(adapter.status()["ok"])

    def test_hard_kill_defers_without_action_or_telegram_then_resumes_after_clear(self) -> None:
        work_id = self.begin("140")
        self._write_rollout(brain_kill=True)
        adapter = self.adapter()
        event = self.envelope("/cancel", message="250", reply="140")
        blocked = adapter.handle_event(event)
        self.assertEqual(blocked["actionState"], "deferred")
        self.assertEqual(self.transport.calls, [])
        with self.store.connect() as db:
            self.assertFalse(db.execute(
                "SELECT user_cancel_requested FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()[0])
        with self.gateway().connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM actions").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 0)

        self._write_rollout(brain_kill=False)
        drained = adapter.drain_pending(max_actions=4)
        self.assertEqual(drained["counts"]["deferred"], 0)
        self.assertEqual(len(self.transport.calls), 1)
        with self.store.connect() as db:
            self.assertTrue(db.execute(
                "SELECT user_cancel_requested FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()[0])

        # A confirmation token already bound before the hard stop is not
        # consumed under kill; the same durable event resumes only after clear.
        privacy_work = self.begin("141")
        preview = adapter.handle_event(self.envelope(
            "Privacy: internal", message="251", reply="141",
        ))
        self.assertEqual(preview["actionState"], "delivered")
        with adapter.connect() as db:
            preview_ref = str(db.execute(
                "SELECT message_ref FROM message_mappings WHERE work_id=? AND mapping_kind='privacy-preview'",
                (privacy_work,),
            ).fetchone()[0])
        calls_before_kill = len(self.transport.calls)
        self._write_rollout(brain_kill=True)
        confirmation = adapter.handle_event(self.envelope(
            "CONFIRM PRIVACY", message="252", reply=preview_ref,
        ))
        self.assertEqual(confirmation["actionState"], "deferred")
        self.assertEqual(len(self.transport.calls), calls_before_kill)
        with self.store.connect() as db:
            self.assertEqual(db.execute(
                "SELECT privacy_class FROM submissions WHERE work_id=?", (privacy_work,),
            ).fetchone()[0], "private")
        self._write_rollout(brain_kill=False)
        adapter.drain_pending(max_actions=4)
        with self.store.connect() as db:
            self.assertEqual(db.execute(
                "SELECT privacy_class FROM submissions WHERE work_id=?", (privacy_work,),
            ).fetchone()[0], "internal")

    def test_routine_ingestion_disable_rejects_new_intake_but_existing_action_drains(self) -> None:
        work_id = self.begin("150")
        self._write_config(enabled=False)
        self.assertFalse(brain.brain_ingestion_enabled(
            brain.load_json(self.config), self.rollout,
        ))
        result = self.adapter().handle_event(self.envelope(
            "Reference only", message="260", reply="150",
        ))
        self.assertEqual(result["actionState"], "delivered")
        with self.store.connect() as db:
            self.assertTrue(db.execute(
                "SELECT reference_only FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()[0])

    def test_forget_two_step_scrubs_private_bindings_after_ack(self) -> None:
        work_id = self.begin("160")
        adapter = self.adapter()
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            preview = adapter.handle_event(self.envelope(
                "Forget", message="270", reply="160",
            ))
            self.assertEqual(preview["actionState"], "delivered")
            with adapter.connect() as db:
                mapping = db.execute(
                    "SELECT message_ref FROM message_mappings WHERE mapping_kind='forget-preview'",
                ).fetchone()
            self.assertIsNotNone(mapping)
            self._write_config(enabled=False)
            confirmation = adapter.handle_event(self.envelope(
                "CONFIRM FORGET", message="271", reply=str(mapping["message_ref"]),
            ))
        self.assertEqual(confirmation["actionState"], "delivered")
        with self.store.connect() as db:
            source = db.execute(
                """SELECT phase,caption_private,objective_private,source_private_json
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
        self.assertEqual(source["phase"], "forgotten")
        self.assertEqual(source["caption_private"], "")
        self.assertEqual(source["objective_private"], "")
        self.assertEqual(source["source_private_json"], "{}")
        with adapter.connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM inbound_events WHERE work_id=?", (work_id,),
            ).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM pending_actions WHERE work_id=?", (work_id,),
            ).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM pending_responses WHERE work_id=?", (work_id,),
            ).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM deletion_receipts").fetchone()[0], 1)
        with sqlite3.connect(self.dispatcher_root / "dispatcher.sqlite3") as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM surfaces WHERE work_id=?", (work_id,),
            ).fetchone()[0], 0)
        with self.gateway().connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM actions WHERE work_id=?", (work_id,),
            ).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM effects WHERE work_id=? AND kind='callback_ack'", (work_id,),
            ).fetchone()[0], 0)

    def test_human_canary_journals_exact_forget_surfaces_and_cleans_newest_first(self) -> None:
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            work_id = self.begin_verified_human_canary("180")
            adapter = self.activate_canary(work_id)
            pre_forget = adapter.human_canary_status(
                work_id,
                stage="pre-forget",
                retrieval_query="JCU10 human canary validates cleanup journal",
            )
            self.assertFalse(pre_forget["ok"])
            self.assertTrue(pre_forget["preForget"]["ok"])
            self.assertFalse(pre_forget["privacyPath"])
            self.assertEqual(pre_forget["targetCount"], 3)
            self.assertEqual(pre_forget["preForget"]["retrievalAgentsWithProvenance"], 4)
            active_journal = adapter._human_canary(work_id)
            self.assertEqual(active_journal.directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(active_journal.db_path.stat().st_mode & 0o777, 0o600)
            redacted_status = json.dumps(pre_forget)
            self.assertNotIn("-100123", redacted_status)
            self.assertNotIn("JCU10 human canary validates cleanup journal", redacted_status)
            preview = adapter.handle_event(self.envelope(
                "Forget", message="290", reply="180",
            ))
            self.assertEqual(preview["actionState"], "delivered")
            with adapter.connect() as db:
                preview_ref = str(db.execute(
                    "SELECT message_ref FROM message_mappings WHERE mapping_kind='forget-preview'",
                ).fetchone()[0])
            confirmation = adapter.handle_event(self.envelope(
                "CONFIRM FORGET", message="291", reply=preview_ref,
            ))
        self.assertEqual(confirmation["actionState"], "delivered")
        journal = adapter._human_canary(work_id)
        status = journal.status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["targetCount"], 7)
        with journal.connect() as db:
            targets = {
                str(row["class"]): str(row["message_ref"])
                for row in db.execute("SELECT class,message_ref FROM targets")
            }
        self.assertEqual(set(targets), {
            "source_media", "ingestion_card", "ingestion_final",
            "forget_command", "forget_preview", "forget_confirm", "forget_final",
        })
        self.assertEqual(targets["source_media"], "180")
        self.assertEqual(targets["forget_command"], "290")
        self.assertEqual(targets["forget_confirm"], "291")

        call_offset = len(self.transport.calls)
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            post_forget = adapter.human_canary_status(
                work_id,
                stage="post-forget",
                retrieval_query="JCU10 human canary validates cleanup journal",
            )
            self.assertFalse(post_forget["ok"])
            self.assertTrue(post_forget["postForget"]["ok"])
            journal.mark_post_forget_verified()
            cleanup = journal.cleanup(self.transport)
        self.assertTrue(cleanup["ok"])
        self.assertEqual(cleanup["targetCount"], 7)
        self.assertEqual(cleanup["unresolved"], 0)
        delete_ids = [
            str(payload["message_id"])
            for method, payload in self.transport.calls[call_offset:]
            if method == "deleteMessage"
        ]
        self.assertEqual(delete_ids, [
            targets["forget_final"], targets["forget_confirm"],
            targets["forget_preview"], targets["forget_command"],
            targets["ingestion_final"], targets["ingestion_card"],
            targets["source_media"],
        ])
        self.assertFalse(journal.db_path.exists())
        self.assertFalse(Path(f"{journal.db_path}-wal").exists())
        self.assertFalse(Path(f"{journal.db_path}-shm").exists())
        self.assertTrue(journal.receipt_path.is_file())
        self.assertEqual(journal.receipt_path.stat().st_mode & 0o777, 0o600)
        receipt = json.loads(journal.receipt_path.read_text(encoding="utf-8"))
        self.assertTrue(receipt["cleanupConfirmed"])
        self.assertEqual(receipt["unresolved"], 0)
        encoded_receipt = json.dumps(receipt)
        for private_ref in targets.values():
            self.assertNotIn(f'"{private_ref}"', encoded_receipt)

    def test_review_only_partial_terminal_requires_frozen_clean_evidence(self) -> None:
        work_id = self.begin_verified_human_canary("184", privacy="private")
        canonical_payload = self.set_review_only_partial_terminal(work_id)
        adapter = self.adapter()
        self.assertTrue(adapter._review_only_partial_terminal_verified(work_id))

        # Candidate activation is mutable and must not invalidate the frozen attestation.
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "UPDATE candidates SET status='active',eligibility_reason='' WHERE work_id=?",
                (work_id,),
            )
        self.assertTrue(adapter._review_only_partial_terminal_verified(work_id))

        def install_payload(payload: dict[str, object]) -> None:
            encoded = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            )
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            with self.store.connect() as db, self.store.transaction(db):
                db.execute(
                    """UPDATE intake_results
                          SET outcome=?,payload_hash=?,private_payload_json=?
                        WHERE work_id=?""",
                    (str(payload["terminalStatus"]), digest, encoded, work_id),
                )
            with self.gateway().connect() as db, self.gateway().transaction(db):
                db.execute(
                    """UPDATE terminal_outbox
                          SET outcome=?,payload_hash=?,payload_json=?
                        WHERE work_id=?""",
                    (str(payload["terminalStatus"]), digest, encoded, work_id),
                )

        unsupported = json.loads(json.dumps(canonical_payload))
        unsupported["receipt"]["Unsupported"] = ["oversize"]
        install_payload(unsupported)
        self.assertFalse(adapter._review_only_partial_terminal_verified(work_id))
        install_payload(canonical_payload)

        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "UPDATE attachment_intents SET failure_reason='download-unavailable' WHERE work_id=?",
                (work_id,),
            )
        self.assertFalse(adapter._review_only_partial_terminal_verified(work_id))
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "UPDATE attachment_intents SET failure_reason='' WHERE work_id=?",
                (work_id,),
            )
            db.execute(
                "UPDATE extractions SET prompt_injection=1 WHERE work_id=?",
                (work_id,),
            )
        self.assertFalse(adapter._review_only_partial_terminal_verified(work_id))
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "UPDATE extractions SET prompt_injection=0 WHERE work_id=?",
                (work_id,),
            )
            db.execute(
                """UPDATE artifacts SET quarantine_reason='unsafe-content'
                     WHERE digest IN (
                       SELECT digest FROM submission_artifacts WHERE work_id=?
                     )""",
                (work_id,),
            )
        self.assertFalse(adapter._review_only_partial_terminal_verified(work_id))
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                """UPDATE artifacts SET quarantine_reason=''
                     WHERE digest IN (
                       SELECT digest FROM submission_artifacts WHERE work_id=?
                     )""",
                (work_id,),
            )
        with self.gateway().connect() as db, self.gateway().transaction(db):
            db.execute(
                """UPDATE lifecycle_events SET safe_payload_json=?
                     WHERE work_id=? AND event_type='verifying'""",
                (json.dumps({"candidateCount": 1, "reviewCount": 0}), work_id),
            )
        self.assertFalse(adapter._review_only_partial_terminal_verified(work_id))
        with self.gateway().connect() as db, self.gateway().transaction(db):
            db.execute(
                """UPDATE lifecycle_events SET safe_payload_json=?
                     WHERE work_id=? AND event_type='verifying'""",
                (json.dumps({"candidateCount": 1, "reviewCount": 1}), work_id),
            )
        self.assertTrue(adapter._review_only_partial_terminal_verified(work_id))

        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "UPDATE intake_results SET payload_hash='invalid' WHERE work_id=?",
                (work_id,),
            )
        self.assertFalse(adapter._review_only_partial_terminal_verified(work_id))

    def test_human_canary_privacy_path_requires_and_captures_all_four_surfaces(self) -> None:
        work_id = self.begin("181")
        adapter = self.activate_canary(work_id)
        privacy = adapter.handle_event(self.envelope(
            "Privacy: internal", message="292", reply="181",
        ))
        self.assertEqual(privacy["actionState"], "delivered")
        with adapter.connect() as db:
            privacy_preview = str(db.execute(
                "SELECT message_ref FROM message_mappings WHERE mapping_kind='privacy-preview'",
            ).fetchone()[0])
        confirmed = adapter.handle_event(self.envelope(
            "CONFIRM PRIVACY", message="293", reply=privacy_preview,
        ))
        self.assertEqual(confirmed["actionState"], "delivered")
        status = adapter._human_canary(work_id).status()
        self.assertTrue(status["privacyPath"])
        self.assertEqual(status["targetCount"], 7)
        with adapter._human_canary(work_id).connect() as db:
            classes = {str(row[0]) for row in db.execute("SELECT class FROM targets")}
        self.assertTrue(set(actions_module.HUMAN_CANARY_PRIVACY_CLASSES).issubset(classes))

    def test_human_canary_privacy_then_forget_cleans_all_eleven_targets(self) -> None:
        query = "JCU10 human canary validates cleanup journal"
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            work_id = self.begin_verified_human_canary("183", privacy="private")
            adapter = self.activate_canary(work_id)
            privacy = adapter.handle_event(self.envelope(
                "Privacy: dashboard-safe", message="296", reply="183",
            ))
            self.assertEqual(privacy["actionState"], "delivered")
            with adapter.connect() as db:
                privacy_preview = str(db.execute(
                    "SELECT message_ref FROM message_mappings WHERE mapping_kind='privacy-preview'",
                ).fetchone()[0])
            privacy_confirmed = adapter.handle_event(self.envelope(
                "CONFIRM PRIVACY", message="297", reply=privacy_preview,
            ))
            self.assertEqual(privacy_confirmed["actionState"], "delivered")
            pre_forget = adapter.human_canary_status(
                work_id, stage="pre-forget", retrieval_query=query,
            )
            self.assertTrue(pre_forget["ok"])
            self.assertTrue(pre_forget["privacyPath"])
            self.assertEqual(pre_forget["targetCount"], 7)
            adapter.handle_event(self.envelope(
                "Forget", message="298", reply="183",
            ))
            with adapter.connect() as db:
                forget_preview = str(db.execute(
                    "SELECT message_ref FROM message_mappings WHERE mapping_kind='forget-preview'",
                ).fetchone()[0])
            forgotten = adapter.handle_event(self.envelope(
                "CONFIRM FORGET", message="299", reply=forget_preview,
            ))
            self.assertEqual(forgotten["actionState"], "delivered")

        journal = adapter._human_canary(work_id)
        with journal.connect() as db:
            targets = {
                str(row["class"]): str(row["message_ref"])
                for row in db.execute("SELECT class,message_ref FROM targets")
            }
        self.assertEqual(set(targets), set(actions_module.HUMAN_CANARY_DELETE_ORDER))
        self.assertEqual(len(set(targets.values())), 11)
        self.allow_delete_permission()
        call_offset = len(self.transport.calls)
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            cleanup = adapter.cleanup_human_canary_telegram(
                work_id, retrieval_query=query,
            )
        self.assertTrue(cleanup["ok"])
        self.assertEqual(cleanup["targetCount"], 11)
        delete_ids = [
            str(payload["message_id"])
            for method, payload in self.transport.calls[call_offset:]
            if method == "deleteMessage"
        ]
        self.assertEqual(
            delete_ids,
            [targets[target_class] for target_class in actions_module.HUMAN_CANARY_DELETE_ORDER],
        )
        self.assertFalse(journal.db_path.exists())
        self.assertFalse(Path(f"{journal.db_path}-wal").exists())
        self.assertFalse(Path(f"{journal.db_path}-shm").exists())
        receipt = json.loads(journal.receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["targetCount"], 11)
        self.assertTrue(receipt["postForgetVerified"])
        encoded_receipt = json.dumps(receipt)
        for private_ref in targets.values():
            self.assertNotIn(f'"{private_ref}"', encoded_receipt)
        self.allow_delete_permission()
        call_offset = len(self.transport.calls)
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            repeated_cleanup = adapter.cleanup_human_canary_telegram(
                work_id, retrieval_query=query,
            )
        self.assertTrue(repeated_cleanup["ok"])
        self.assertEqual(repeated_cleanup["state"], "complete")
        self.assertTrue(repeated_cleanup["journalRemoved"])
        self.assertTrue(repeated_cleanup["receiptPresent"])
        self.assertFalse(any(
            method == "deleteMessage"
            for method, _payload in self.transport.calls[call_offset:]
        ))

    def test_human_canary_post_forget_accepts_verified_shared_digest_retention(self) -> None:
        query = "JCU10 human canary validates cleanup journal"
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            work_id = self.begin_verified_human_canary("184", privacy="private")
            duplicate_source = self.download_root / "shared-digest.txt"
            duplicate_source.write_text(
                "fact: JCU10 human canary | validates | cleanup journal\n",
                encoding="utf-8",
            )
            duplicate_source.chmod(0o600)
            duplicate = self.store.begin_submission({
                "chatId": "-100123",
                "threadId": "77",
                "messageId": "185",
                "senderId": "9001",
                "senderIsBot": False,
                "mediaGroupId": "",
                "caption": "",
                "attachments": [{
                    "sourceMessageId": "185",
                    "fileId": "private-file-185",
                    "kind": "document",
                    "mime": "text/plain",
                    "size": duplicate_source.stat().st_size,
                }],
            })
            duplicate_token = duplicate["downloadTokens"][0]
            self.store.accept_download(
                work_id=str(duplicate["workId"]),
                attachment_id=str(duplicate_token["attachmentId"]),
                token=str(duplicate_token["token"]),
                source_path=duplicate_source,
            )
            with self.store.connect() as db:
                shared = db.execute(
                    """SELECT a.digest,a.stored_path,a.ref_count
                         FROM submission_artifacts sa JOIN artifacts a ON a.digest=sa.digest
                         WHERE sa.work_id=?""",
                    (work_id,),
                ).fetchone()
            self.assertEqual(shared["ref_count"], 2)
            adapter = self.activate_canary(work_id)
            journal = adapter._human_canary(work_id)
            with journal.connect() as db:
                evidence = db.execute(
                    """SELECT expected_ref_count,expected_work_ref_count,require_path_absent
                         FROM cleanup_evidence WHERE kind='artifact_path'""",
                ).fetchone()
            self.assertEqual(tuple(evidence), (2, 1, 0))
            adapter.handle_event(self.envelope(
                "Privacy: dashboard-safe", message="302", reply="184",
            ))
            with adapter.connect() as db:
                privacy_preview = str(db.execute(
                    "SELECT message_ref FROM message_mappings WHERE mapping_kind='privacy-preview'",
                ).fetchone()[0])
            adapter.handle_event(self.envelope(
                "CONFIRM PRIVACY", message="303", reply=privacy_preview,
            ))
            self.assertTrue(adapter.human_canary_status(
                work_id, stage="pre-forget", retrieval_query=query,
            )["ok"])
            adapter.handle_event(self.envelope("Forget", message="304", reply="184"))
            with adapter.connect() as db:
                forget_preview = str(db.execute(
                    "SELECT message_ref FROM message_mappings WHERE mapping_kind='forget-preview'",
                ).fetchone()[0])
            adapter.handle_event(self.envelope(
                "CONFIRM FORGET", message="305", reply=forget_preview,
            ))
            post = adapter.human_canary_status(
                work_id, stage="post-forget", retrieval_query=query,
            )
            with journal.connect() as db:
                self.assertEqual(db.execute(
                    "SELECT post_forget_verified FROM metadata WHERE singleton=1",
                ).fetchone()[0], 0)
        self.assertTrue(post["ok"])
        self.assertTrue(post["postForget"]["privatePathCleanupVerified"])
        self.assertFalse(post["postForget"]["privatePathsAbsent"])
        self.assertEqual(post["postForget"]["retainedSharedArtifactCount"], 1)
        with self.store.connect() as db:
            retained = db.execute(
                "SELECT stored_path,ref_count FROM artifacts WHERE digest=?",
                (shared["digest"],),
            ).fetchone()
            original_associations = int(db.execute(
                "SELECT COUNT(*) FROM submission_artifacts WHERE work_id=?",
                (work_id,),
            ).fetchone()[0])
        self.assertEqual(retained["ref_count"], 1)
        self.assertEqual(original_associations, 0)
        self.assertEqual(str(retained["stored_path"]), str(shared["stored_path"]))
        self.assertTrue(Path(str(retained["stored_path"])).is_file())
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            preview = self.store.forget_preview(
                str(duplicate["workId"]), authorized_user="9001",
            )
            final_forget = self.store.forget(
                str(duplicate["workId"]),
                authorized_user="9001",
                confirmation_token=str(preview["confirmationToken"]),
            )
        self.assertTrue(final_forget["ok"])
        self.assertEqual(final_forget["blobDeletedCount"], 1)
        self.assertFalse(Path(str(shared["stored_path"])).exists())
        with self.store.connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM artifacts WHERE digest=?",
                (shared["digest"],),
            ).fetchone()[0], 0)

    def test_human_canary_pre_forget_rejects_parity_pointer_visibility_and_weak_rows(self) -> None:
        query = "JCU10 human canary validates cleanup journal"
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            work_id = self.begin_verified_human_canary("186", privacy="private")
            adapter = self.activate_canary(work_id)
            adapter.handle_event(self.envelope(
                "Privacy: dashboard-safe", message="306", reply="186",
            ))
            with adapter.connect() as db:
                preview_ref = str(db.execute(
                    "SELECT message_ref FROM message_mappings WHERE mapping_kind='privacy-preview'",
                ).fetchone()[0])
            adapter.handle_event(self.envelope(
                "CONFIRM PRIVACY", message="307", reply=preview_ref,
            ))
            self.assertTrue(adapter.human_canary_status(
                work_id, stage="pre-forget", retrieval_query=query,
            )["ok"])

            original_search = brain.BrainStore.search_source
            for weakness in (
                "missing-source", "fake-source", "fake-chunk", "wrong-privacy",
                "weak-confidence", "foreign-row",
            ):
                with self.subTest(weakness=weakness):
                    def weak_search(
                        store: brain.BrainStore, *, query: str, agent: str, limit: int = 6,
                    ) -> dict[str, object]:
                        result = original_search(store, query=query, agent=agent, limit=limit)
                        rows = [dict(row) for row in result.get("results", [])]
                        if rows and weakness == "missing-source":
                            rows[0]["sourceRef"] = ""
                        elif rows and weakness == "fake-source":
                            rows[0]["sourceRef"] = "source-evidence-nonempty-fake"
                        elif rows and weakness == "fake-chunk":
                            rows[0]["chunkRef"] = "source-chunk-nonempty-fake"
                        elif rows and weakness == "wrong-privacy":
                            rows[0]["privacy"] = "private"
                        elif rows and weakness == "weak-confidence":
                            rows[0]["confidence"] = "0.99"
                        elif rows and weakness == "foreign-row":
                            foreign = dict(rows[0])
                            foreign["workId"] = "other-private-work"
                            rows.append(foreign)
                        return {**result, "count": len(rows), "results": rows}

                    with mock.patch.object(brain.BrainStore, "search_source", new=weak_search):
                        result = adapter.human_canary_status(
                            work_id, stage="pre-forget", retrieval_query=query,
                        )
                    self.assertFalse(result["ok"])
                    self.assertFalse(result["preForget"]["retrievalAllRowsValid"])

            with self.store.connect() as db, self.store.transaction(db):
                vector = db.execute(
                    """SELECT v.* FROM source_vectors v JOIN source_chunks c ON c.id=v.chunk_id
                         WHERE c.work_id=?""",
                    (work_id,),
                ).fetchone()
                db.execute("DELETE FROM source_vectors WHERE chunk_id=?", (vector["chunk_id"],))
            parity = adapter.human_canary_status(
                work_id, stage="pre-forget", retrieval_query=query,
            )
            self.assertFalse(parity["ok"])
            self.assertNotEqual(
                parity["preForget"]["chunkCount"], parity["preForget"]["vectorCount"],
            )
            with self.store.connect() as db, self.store.transaction(db):
                db.execute(
                    "INSERT INTO source_vectors VALUES(?,?,?,?,?,?)",
                    tuple(vector),
                )

            with self.store.connect() as db, self.store.transaction(db):
                chunk = db.execute(
                    "SELECT id,text_private FROM source_chunks WHERE work_id=?",
                    (work_id,),
                ).fetchone()
                db.execute("DELETE FROM source_chunk_fts WHERE work_id=?", (work_id,))
            chunk_fts = adapter.human_canary_status(
                work_id, stage="pre-forget", retrieval_query=query,
            )
            self.assertFalse(chunk_fts["ok"])
            self.assertEqual(chunk_fts["preForget"]["chunkFtsCount"], 0)
            with self.store.connect() as db, self.store.transaction(db):
                db.execute(
                    "INSERT INTO source_chunk_fts VALUES(?,?,?)",
                    (chunk["id"], work_id, chunk["text_private"]),
                )

            with self.store.connect() as db, self.store.transaction(db):
                db.execute("DELETE FROM source_chunk_fts WHERE work_id=?", (work_id,))
                db.execute(
                    "INSERT INTO source_chunk_fts VALUES(?,?,?)",
                    ("same-count-bogus-chunk", work_id, chunk["text_private"]),
                )
            substituted = adapter.human_canary_status(
                work_id, stage="pre-forget", retrieval_query=query,
            )
            self.assertFalse(substituted["ok"])
            self.assertEqual(
                substituted["preForget"]["chunkCount"],
                substituted["preForget"]["chunkFtsCount"],
            )
            self.assertFalse(substituted["preForget"]["chunkIndexIdentityVerified"])
            with self.store.connect() as db, self.store.transaction(db):
                db.execute("DELETE FROM source_chunk_fts WHERE work_id=?", (work_id,))
                db.execute(
                    "INSERT INTO source_chunk_fts VALUES(?,?,?)",
                    (chunk["id"], work_id, chunk["text_private"]),
                )

            with self.store.connect() as db, self.store.transaction(db):
                candidate = db.execute(
                    "SELECT id,registry_candidate_id FROM candidates WHERE work_id=?",
                    (work_id,),
                ).fetchone()
                db.execute(
                    "UPDATE candidates SET registry_candidate_id='missing-registry-row' WHERE id=?",
                    (candidate["id"],),
                )
            pointer = adapter.human_canary_status(
                work_id, stage="pre-forget", retrieval_query=query,
            )
            self.assertFalse(pointer["ok"])
            self.assertFalse(pointer["preForget"]["candidateGovernanceReady"])
            with self.store.connect() as db, self.store.transaction(db):
                db.execute(
                    "UPDATE candidates SET registry_candidate_id=? WHERE id=?",
                    (candidate["registry_candidate_id"], candidate["id"]),
                )

            registry = memory_registry.connect()
            try:
                original_registry = registry.execute(
                    "SELECT * FROM memory_candidates WHERE id=?",
                    (candidate["registry_candidate_id"],),
                ).fetchone()
                columns = [
                    str(row["name"])
                    for row in registry.execute("PRAGMA table_info(memory_candidates)")
                ]
                unrelated = {column: original_registry[column] for column in columns}
                unrelated["id"] = "candidate-unrelated-same-source"
                unrelated["subject"] = "Unrelated same-source candidate"
                unrelated["content_hash"] = hashlib.sha256(
                    b"unrelated-same-source-candidate"
                ).hexdigest()
                registry.execute(
                    f"INSERT INTO memory_candidates({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                    tuple(unrelated[column] for column in columns),
                )
                registry.commit()
            finally:
                registry.close()
            with self.store.connect() as db, self.store.transaction(db):
                db.execute(
                    "UPDATE candidates SET registry_candidate_id=? WHERE id=?",
                    ("candidate-unrelated-same-source", candidate["id"]),
                )
            unrelated_pointer = adapter.human_canary_status(
                work_id, stage="pre-forget", retrieval_query=query,
            )
            self.assertFalse(unrelated_pointer["ok"])
            self.assertFalse(unrelated_pointer["preForget"]["candidateGovernanceReady"])
            with self.store.connect() as db, self.store.transaction(db):
                db.execute(
                    "UPDATE candidates SET registry_candidate_id=? WHERE id=?",
                    (candidate["registry_candidate_id"], candidate["id"]),
                )

            with sqlite3.connect(self.dispatcher_root / "dispatcher.sqlite3") as db:
                db.execute(
                    """UPDATE visibility_outbox
                         SET route_verified=1,route_class='local-deterministic'
                         WHERE lifecycle_work_id=? AND stage='receipt_ready'""",
                    (work_id,),
                )
                db.commit()
            early_route = adapter.human_canary_status(
                work_id, stage="pre-forget", retrieval_query=query,
            )
            self.assertFalse(early_route["ok"])
            self.assertFalse(early_route["preForget"]["earlyVisibilityStagesClean"])
            with sqlite3.connect(self.dispatcher_root / "dispatcher.sqlite3") as db:
                db.execute(
                    """UPDATE visibility_outbox SET route_verified=0,route_class=''
                         WHERE lifecycle_work_id=? AND stage='receipt_ready'""",
                    (work_id,),
                )
                db.commit()

            with sqlite3.connect(self.dispatcher_root / "dispatcher.sqlite3") as db:
                db.execute(
                    """UPDATE visibility_outbox SET state='pending'
                         WHERE lifecycle_work_id=? AND stage='processing'""",
                    (work_id,),
                )
                db.commit()
            visibility = adapter.human_canary_status(
                work_id, stage="pre-forget", retrieval_query=query,
            )
            self.assertFalse(visibility["ok"])
            self.assertEqual(visibility["preForget"]["visibilityStageCount"], 4)

    def test_human_canary_cleanup_retains_journal_on_unknown_delete(self) -> None:
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            work_id = self.begin_verified_human_canary("182", privacy="private")
            adapter = self.activate_canary(work_id)
            adapter.handle_event(self.envelope(
                "Privacy: dashboard-safe", message="300", reply="182",
            ))
            with adapter.connect() as db:
                privacy_preview = str(db.execute(
                    "SELECT message_ref FROM message_mappings WHERE mapping_kind='privacy-preview'",
                ).fetchone()[0])
            adapter.handle_event(self.envelope(
                "CONFIRM PRIVACY", message="301", reply=privacy_preview,
            ))
            adapter.handle_event(self.envelope("Forget", message="294", reply="182"))
            with adapter.connect() as db:
                preview_ref = str(db.execute(
                    "SELECT message_ref FROM message_mappings WHERE mapping_kind='forget-preview'",
                ).fetchone()[0])
            adapter.handle_event(self.envelope(
                "CONFIRM FORGET", message="295", reply=preview_ref,
            ))
        journal = adapter._human_canary(work_id)
        self.allow_delete_permission()
        self.transport.responses.extend({
            "ok": False,
            "state": "indeterminate",
            "errorClass": "telegram-result-unknown",
        } for _ in range(3))
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            cleanup = adapter.cleanup_human_canary_telegram(
                work_id,
                max_attempts=3,
                retrieval_query="JCU10 human canary validates cleanup journal",
            )
        self.assertFalse(cleanup["ok"])
        self.assertEqual(cleanup["unresolved"], 1)
        self.assertTrue(journal.db_path.is_file())
        self.assertFalse(journal.receipt_path.exists())

    def test_human_canary_cleanup_is_serialized_across_full_delete_order(self) -> None:
        journal, targets = self.ready_isolated_journal("concurrent-cleanup")
        start = threading.Barrier(2)
        calls: list[str] = []
        calls_lock = threading.Lock()

        def transport(
            method: str, payload: dict[str, object], _timeout: int,
        ) -> dict[str, object]:
            self.assertEqual(method, "deleteMessage")
            with calls_lock:
                calls.append(str(payload["message_id"]))
            return {"ok": True, "state": "delivered", "result": {}}

        def cleanup() -> dict[str, object]:
            start.wait(timeout=5)
            return journal.cleanup(transport)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in (pool.submit(cleanup), pool.submit(cleanup))]
        self.assertTrue(all(result["ok"] for result in results))
        self.assertEqual(
            calls,
            [targets[target_class] for target_class in actions_module.HUMAN_CANARY_DELETE_ORDER],
        )

    def test_human_canary_atomic_receipt_replaces_partial_private_temp(self) -> None:
        journal, _targets = self.ready_isolated_journal("partial-receipt")
        journal.receipt_temp_path.write_text("{", encoding="utf-8")
        journal.receipt_temp_path.chmod(0o600)
        result = journal.cleanup(
            lambda _method, _payload, _timeout: {
                "ok": True, "state": "delivered", "result": {},
            },
        )
        self.assertTrue(result["ok"])
        self.assertFalse(journal.receipt_temp_path.exists())
        self.assertTrue(json.loads(journal.receipt_path.read_text(encoding="utf-8"))["cleanupConfirmed"])

    def test_human_canary_complete_status_rejects_partial_or_corrupt_receipt(self) -> None:
        journal, _targets = self.ready_isolated_journal("corrupt-receipt")
        journal.cleanup(
            lambda _method, _payload, _timeout: {
                "ok": True, "state": "delivered", "result": {},
            },
        )
        valid = json.loads(journal.receipt_path.read_text(encoding="utf-8"))
        corruptions = {
            "partial-json": "{",
            "wrong-schema": {**valid, "journalSchemaVersion": 1},
            "wrong-count": {**valid, "targetCount": 10},
            "missing-class": {
                **valid,
                "classCounts": {
                    key: value
                    for key, value in valid["classCounts"].items()
                    if key != "source_media"
                },
            },
            "unverified-forget": {**valid, "postForgetVerified": False},
        }
        for label, payload in corruptions.items():
            with self.subTest(label=label):
                journal.receipt_path.write_text(
                    payload if isinstance(payload, str) else json.dumps(payload),
                    encoding="utf-8",
                )
                journal.receipt_path.chmod(0o600)
                with self.assertRaises(actions_module.BrainActionError):
                    journal.status()
        journal.receipt_path.write_text(json.dumps(valid), encoding="utf-8")
        journal.receipt_path.chmod(0o600)
        self.assertTrue(journal.status()["ok"])

    def test_human_canary_receipt_and_unlink_crashes_replay_without_remnants(self) -> None:
        for ordinal, fault_point in enumerate((
            "receipt_renamed",
            "journal_wal_unlinked",
            "journal_shm_unlinked",
            "journal_db_unlinked",
        )):
            with self.subTest(fault_point=fault_point):
                journal, _targets = self.ready_isolated_journal(f"unlink-crash-{ordinal}")
                calls: list[str] = []

                def transport(
                    _method: str, payload: dict[str, object], _timeout: int,
                ) -> dict[str, object]:
                    calls.append(str(payload["message_id"]))
                    return {"ok": True, "state": "delivered", "result": {}}

                def inject(point: str) -> None:
                    if point == fault_point:
                        raise RuntimeError(fault_point)

                with mock.patch.object(journal, "_fault", side_effect=inject):
                    with self.assertRaisesRegex(RuntimeError, fault_point):
                        journal.cleanup(transport)
                call_count = len(calls)
                replay = journal.cleanup(transport)
                self.assertTrue(replay["ok"])
                self.assertEqual(len(calls), call_count)
                self.assertTrue(journal.receipt_path.is_file())
                self.assertTrue(all(
                    not path.exists() and not path.is_symlink()
                    for path in journal.journal_paths
                ))

    def test_human_canary_sealed_binding_scrub_recovers_after_every_commit_boundary(self) -> None:
        self.adapter()._ensure_schema()
        fault_points = (
            "scrub_after_seal",
            "scrub_after_dispatcher_commit",
            "scrub_after_lifecycle_commit",
            "scrub_after_adapter_commit",
            "scrub_before_mark_bindings_scrubbed",
        )
        for ordinal, fault_point in enumerate(fault_points):
            with self.subTest(fault_point=fault_point):
                source_ref = str(410 + ordinal)
                work_id = self.begin(source_ref)
                adapter = self.activate_canary(work_id)
                journal = adapter._human_canary(work_id)
                for target_offset, target_class in enumerate(
                    actions_module.HUMAN_CANARY_FORGET_CLASSES,
                ):
                    journal.record(
                        target_class=target_class,
                        direction=(
                            "inbound"
                            if target_class in {"forget_command", "forget_confirm"}
                            else "outbound"
                        ),
                        chat_ref="-100123",
                        topic_ref="77",
                        message_ref=str(2100 + ordinal * 10 + target_offset),
                        delivery_state=(
                            "received"
                            if target_class in {"forget_command", "forget_confirm"}
                            else "delivered"
                        ),
                    )

                def inject(point: str) -> None:
                    if point == fault_point:
                        raise RuntimeError(fault_point)

                with mock.patch.object(adapter, "_human_canary_fault", side_effect=inject):
                    with self.assertRaisesRegex(RuntimeError, fault_point):
                        adapter._scrub_forget_bindings(work_id)
                self.assertEqual(journal.status()["state"], "sealed")
                read_only = adapter.human_canary_status(work_id, stage="journal")
                self.assertEqual(read_only["state"], "sealed")
                post_read_only = adapter.human_canary_status(
                    work_id,
                    stage="post-forget",
                    retrieval_query="JCU10 human canary validates cleanup journal",
                )
                self.assertEqual(post_read_only["state"], "sealed")
                self.assertFalse(post_read_only["ok"])
                self.assertEqual(journal.status()["state"], "sealed")
                adapter._recover_human_canary_bindings(work_id)
                self.assertEqual(journal.status()["state"], "ready")
                adapter._verify_binding_scrub(work_id)
                adapter._verify_binding_deletion_receipt(work_id)
                with adapter.connect() as db:
                    self.assertEqual(db.execute(
                        "SELECT COUNT(*) FROM deletion_receipts WHERE work_id_hash=?",
                        (hashlib.sha256(work_id.encode()).hexdigest(),),
                    ).fetchone()[0], 1)

    def test_confirmed_cleanup_recovers_real_sealed_forget_before_deleting(self) -> None:
        query = "JCU10 human canary validates cleanup journal"
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            work_id = self.begin_verified_human_canary("187", privacy="private")
            adapter = self.activate_canary(work_id)
            adapter.handle_event(self.envelope(
                "Privacy: dashboard-safe", message="308", reply="187",
            ))
            with adapter.connect() as db:
                privacy_preview = str(db.execute(
                    "SELECT message_ref FROM message_mappings WHERE mapping_kind='privacy-preview'",
                ).fetchone()[0])
            adapter.handle_event(self.envelope(
                "CONFIRM PRIVACY", message="309", reply=privacy_preview,
            ))
            self.assertTrue(adapter.human_canary_status(
                work_id, stage="pre-forget", retrieval_query=query,
            )["ok"])
            adapter.handle_event(self.envelope("Forget", message="310", reply="187"))
            with adapter.connect() as db:
                forget_preview = str(db.execute(
                    "SELECT message_ref FROM message_mappings WHERE mapping_kind='forget-preview'",
                ).fetchone()[0])

            def inject(point: str) -> None:
                if point == "scrub_before_mark_bindings_scrubbed":
                    raise RuntimeError(point)

            with mock.patch.object(adapter, "_human_canary_fault", side_effect=inject):
                failed = adapter.handle_event(self.envelope(
                    "CONFIRM FORGET", message="311", reply=forget_preview,
                ))
            self.assertFalse(failed["ok"])
            journal = adapter._human_canary(work_id)
            sealed = adapter.human_canary_status(
                work_id, stage="post-forget", retrieval_query=query,
            )
            self.assertEqual(sealed["state"], "sealed")
            self.assertFalse(sealed["ok"])
            self.assertEqual(journal.status()["state"], "sealed")

            self.allow_delete_permission()
            cleaned = adapter.cleanup_human_canary_telegram(
                work_id, retrieval_query=query,
            )
        self.assertTrue(cleaned["ok"])
        self.assertEqual(cleaned["targetCount"], 11)
        self.assertFalse(journal.db_path.exists())
        adapter._verify_binding_deletion_receipt(work_id)

    def test_human_canary_preflight_fails_closed_without_delete_authority(self) -> None:
        adapter = self.adapter()
        self.transport.responses.extend((
            {"ok": True, "state": "delivered", "result": {"id": 42}},
            {
                "ok": True,
                "state": "delivered",
                "result": {"status": "administrator", "can_manage_topics": True},
            },
        ))
        result = adapter.human_canary_preflight()
        self.assertFalse(result["ok"])
        self.assertTrue(result["identityVerified"])
        self.assertFalse(result["canDeleteMessages"])
        self.assertNotIn("-100123", json.dumps(result))

    def test_human_canary_preflight_rejects_live_bot_identity_mismatch(self) -> None:
        adapter = self.adapter()
        self.transport.responses.append({
            "ok": True, "state": "delivered", "result": {"id": 43},
        })
        result = adapter.human_canary_preflight()
        self.assertFalse(result["ok"])
        self.assertFalse(result["identityVerified"])
        self.assertEqual(len(self.transport.calls), 1)
        self.assertNotIn("43", json.dumps(result))

    def test_human_canary_cli_failure_exits_nonzero(self) -> None:
        with (
            mock.patch.object(sys, "argv", ["brain_gateway_actions.py", "human-canary-preflight"]),
            mock.patch.object(
                actions_module.BrainGatewayActions,
                "human_canary_preflight",
                return_value={"ok": False, "errorClass": "permission-missing"},
            ),
            mock.patch.object(sys, "stdout", io.StringIO()),
        ):
            self.assertEqual(actions_module.main(), 1)

    def test_human_canary_mutating_cli_requires_explicit_production_confirmation(self) -> None:
        for command in ("human-canary-activate", "human-canary-cleanup-telegram"):
            with self.subTest(command=command):
                with (
                    mock.patch.object(
                        sys, "argv", ["brain_gateway_actions.py", command, "--private-stdin"],
                    ),
                    mock.patch.object(sys, "stdin", io.StringIO(json.dumps({
                        "workId": "private-work",
                        "retrievalQuery": "private-query",
                    }))),
                    mock.patch.object(sys, "stdout", io.StringIO()) as output,
                ):
                    self.assertEqual(actions_module.main(), 1)
                    result = json.loads(output.getvalue())
                    self.assertEqual(
                        result["errorClass"], "production-canary-confirmation-required",
                    )

    def test_concurrent_forget_confirmation_has_exactly_one_winner(self) -> None:
        self.begin("161")
        adapter = self.adapter()
        with (
            mock.patch.object(memory_registry, "DB_PATH", self.registry_path),
            mock.patch.object(memory_registry, "STATUS_PATH", self.registry_status),
        ):
            preview = adapter.handle_event(self.envelope(
                "Forget", message="272", reply="161",
            ))
            self.assertEqual(preview["actionState"], "delivered")
            with adapter.connect() as db:
                preview_ref = str(db.execute(
                    "SELECT message_ref FROM message_mappings WHERE mapping_kind='forget-preview'",
                ).fetchone()[0])

            def confirm(message: str) -> dict[str, object]:
                return self.adapter().handle_event(self.envelope(
                    "CONFIRM FORGET", message=message, reply=preview_ref,
                ))

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(confirm, ("273", "274")))
        states = sorted(str(result.get("actionState")) for result in results)
        self.assertEqual(states, ["delivered", "ignored"])
        # One preview and one final acknowledgement; the losing confirmation
        # never mutates Brain state or creates an acknowledgement effect.
        self.assertEqual(len(self.transport.calls), 2)

    def test_control_outbox_replays_stable_terminal_truth_with_inherited_route(self) -> None:
        work_id = self.begin("170")
        self._surface(work_id, source="170", route_verified=True)
        gateway = self.gateway()
        receipt = gateway.read_work(work_id)
        gateway.commit_terminal(
            work_id,
            "failed",
            expected_sequence=int(receipt["sequence"]),
            fencing_epoch=int(receipt["fencingEpoch"]),
            private_payload={"error": "bounded"},
        )
        self.publisher.accept = False
        adapter = self.adapter()
        result = adapter.handle_event(self.envelope(
            "Reference only", message="280", reply="170",
        ))
        self.assertEqual(result["actionState"], "delivered")
        with adapter.connect() as db, adapter.transaction(db):
            event_ids = [row[0] for row in db.execute(
                "SELECT event_id FROM control_outbox ORDER BY event_id",
            ).fetchall()]
            db.execute(
                "UPDATE control_outbox SET available_at='1970-01-01T00:00:00Z' WHERE state='pending'",
            )
        self.assertEqual(len(event_ids), len(set(event_ids)))
        self.publisher.accept = True
        drained = adapter.drain_outbox(max_events=16)
        self.assertTrue(drained["ok"])
        terminal_events = [event for event in self.publisher.events if event["workId"] == work_id]
        self.assertTrue(terminal_events)
        self.assertTrue(all(event["workEvent"] == "terminal" for event in terminal_events))
        self.assertTrue(all(event["status"] == "error" for event in terminal_events))
        self.assertTrue(all(event["routeVerified"] for event in terminal_events))
        self.assertTrue(all(event["routeClass"] == "local-deterministic" for event in terminal_events))


if __name__ == "__main__":
    unittest.main()
