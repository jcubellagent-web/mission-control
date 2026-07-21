from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brain_gateway_dispatcher as dispatcher_module
import brain_media_intake as brain
from telegram_gateway_lifecycle import GatewayLifecycle, RolloutPolicy


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.next_message_id = 700
        self.responses: list[dict[str, object]] = []

    def __call__(self, method: str, payload: dict[str, object], _timeout: int) -> dict[str, object]:
        self.calls.append((method, dict(payload)))
        if self.responses:
            return self.responses.pop(0)
        if method == "setMessageReaction":
            return {"ok": True, "state": "delivered", "result": True}
        if method == "sendMessage":
            self.next_message_id += 1
            return {"ok": True, "state": "delivered", "result": {"message_id": self.next_message_id}}
        return {"ok": True, "state": "delivered", "result": {}}


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.accept = True

    def __call__(self, event: dict[str, object]) -> bool:
        self.events.append(dict(event))
        return self.accept


class BrainGatewayDispatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="brain-gateway-test-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.store_root = self.folder / "private" / "brain"
        self.lifecycle_root = self.folder / "private" / "lifecycle"
        self.state_root = self.folder / "private" / "dispatcher"
        self.rollout = self.folder / "rollout.json"
        self.config = self.folder / "lanes.json"
        self.topic_receipt = self.folder / "private" / "brain-topic-creation.json"
        self.sender_receipt = self.folder / "private" / "brain-authorized-sender.json"
        self.rollout.write_text(json.dumps({
            "masterState": "josh2",
            "globalKillSwitch": False,
            "brainKillSwitch": False,
            "hosts": {"josh2": True},
            "writerLifecycleVersion": 3,
            "readerLifecycleVersions": [2, 3],
        }))
        self.config.write_text(json.dumps({
            "dynamicTopics": {"brain": {
                "label": "Brain", "owner": "josh2", "lane": "brain-intake",
                "topicIdSource": "private-confirmed-receipt", "enabled": True,
            }},
        }))
        self.topic_receipt.parent.mkdir(parents=True, mode=0o700)
        self.topic_receipt.write_text(json.dumps({
            "state": "confirmed", "topicName": "Brain", "attemptCount": 1,
            "chatId": "-100123", "topicId": "77",
        }))
        self.topic_receipt.chmod(0o600)
        self.sender_receipt.write_text(json.dumps({
            "state": "confirmed", "owner": "josh2", "authorizedSenderId": "9001",
            "chatId": "-100123", "topicId": "77",
        }))
        self.sender_receipt.chmod(0o600)
        self.store = brain.BrainStore(
            self.store_root, authorized_sender_receipt=self.sender_receipt,
        )
        self.transport = RecordingTransport()
        self.publisher = RecordingPublisher()

    def dispatcher(self, *, transport: RecordingTransport | None = None) -> dispatcher_module.BrainGatewayDispatcher:
        return dispatcher_module.BrainGatewayDispatcher(
            self.store_root,
            lifecycle_root=self.lifecycle_root,
            rollout_path=self.rollout,
            config_path=self.config,
            topic_receipt_path=self.topic_receipt,
            state_root=self.state_root,
            transport=transport or self.transport,
            visibility_publisher=self.publisher,
            dispatcher_id="test-brain-gateway",
            lease_seconds=30,
        )

    def gateway(self) -> GatewayLifecycle:
        return GatewayLifecycle(
            self.lifecycle_root,
            rollout=RolloutPolicy.load(self.rollout),
            owner="josh2",
        )

    def begin(self, message: str = "100") -> tuple[str, str]:
        receipt = self.store.begin_submission({
            "chatId": "-100123",
            "threadId": "77",
            "messageId": message,
            "senderId": "9001",
            "senderIsBot": False,
            "mediaGroupId": "",
            "caption": "private caption that must not render",
            "attachments": [{
                "sourceMessageId": message,
                "fileId": f"private-file-{message}",
                "kind": "document",
                "mime": "text/plain",
                "size": 0,
            }],
        })
        brain.ensure_brain_lifecycle(
            self.store,
            str(receipt["workId"]),
            lifecycle_root=self.lifecycle_root,
            rollout_path=self.rollout,
        )
        binding = self.store.lifecycle_binding(str(receipt["workId"]))
        assert binding
        return str(receipt["workId"]), str(binding["lifecycle_work_id"])

    @staticmethod
    def terminal_payload(outcome: str = "succeeded") -> dict[str, object]:
        return {
            "handoffSchemaVersion": 1,
            "surfaceContract": "brain-intake",
            "deliveryTier": 3,
            "owner": "josh2",
            "brainWorkRef": "private-opaque-ref",
            "sourceRevision": 1,
            "terminalStatus": outcome,
            "errorClass": "n/a",
            "receipt": {
                "Stored": "Yes",
                "Extracted": {
                    "types": ["text"],
                    "coverage": ["full"],
                    "routes": ["local-deterministic"],
                },
                "Learned": {"count": 1, "types": ["fact"]},
                "Source indexed": "Yes",
                "Pending review": 0,
                "Duplicates": "n/a",
                "Unsupported": ["n/a"],
                "Privacy": "private",
                "Retention": "privately retained",
                "How to correct": "Reply with a correction.",
                "How to forget": "Reply with Forget, then confirm.",
                "Approval needed": "n/a",
            },
        }

    def commit_terminal(self, brain_work_id: str, lifecycle_work_id: str, *, payload: dict[str, object] | None = None) -> None:
        private_payload = payload or self.terminal_payload()
        encoded = json.dumps(private_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                """INSERT INTO intake_terminal_prepares(
                     work_id,outcome,payload_hash,private_payload_json,
                     attempt_fence,lease_owner_hash,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    brain_work_id, private_payload["terminalStatus"], digest,
                    encoded, 1, "test-worker-fence", brain.utc_now(),
                ),
            )
        gateway = self.gateway()
        receipt = gateway.read_work(lifecycle_work_id)
        assert receipt
        gateway.commit_terminal(
            lifecycle_work_id,
            str(private_payload["terminalStatus"]),
            expected_sequence=int(receipt["sequence"]),
            fencing_epoch=int(receipt["fencingEpoch"]),
            private_payload=private_payload,
        )

    def attempts(self) -> list[sqlite3.Row]:
        with self.dispatcher().connect() as db:
            return list(db.execute("SELECT * FROM attempts ORDER BY created_at,kind").fetchall())

    def test_bootstrap_sends_exactly_one_reaction_and_one_card_and_duplicate_is_silent(self) -> None:
        work_id, lifecycle_work_id = self.begin()
        self.assertEqual(work_id, lifecycle_work_id)
        dispatcher = self.dispatcher()
        self.assertEqual(dispatcher.process_work(work_id), "active")
        receipt = self.gateway().read_work(lifecycle_work_id)
        assert receipt
        self.assertTrue(receipt["reactionDelivered"])
        self.assertTrue(receipt["cardCreated"])
        self.assertEqual([method for method, _ in self.transport.calls], ["setMessageReaction", "sendMessage"])
        rendered = str(self.transport.calls[1][1]["text"])
        self.assertNotIn("private caption", rendered)
        self.assertNotIn("private-file", rendered)
        self.assertNotIn("-100123", rendered)
        self.assertIn("Pending/unverified", rendered)
        self.assertNotIn("deterministic local extractors", rendered)
        self.assertNotIn("No external model", rendered)
        self.assertEqual(dispatcher.process_work(work_id), "active")
        self.assertEqual(len(self.transport.calls), 2)
        published = json.dumps(self.publisher.events, sort_keys=True)
        self.assertNotIn("private caption", published)
        self.assertNotIn("private-file", published)
        self.assertNotIn("-100123", published)
        self.assertIn("receipt_ready", published)
        receipt_event = next(item for item in self.publisher.events if item["stage"] == "receipt_ready")
        self.assertFalse(receipt_event["routeVerified"])

    def test_card_uses_only_bounded_private_store_derived_objective(self) -> None:
        work_id, _ = self.begin("108")
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "UPDATE submissions SET objective_private=? WHERE work_id=?",
                (
                    "Govern verified document evidence about quarterly operating plan",
                    work_id,
                ),
            )
        dispatcher = self.dispatcher()
        self.assertEqual(dispatcher.process_work(work_id), "active")
        rendered = str(self.transport.calls[1][1]["text"])
        self.assertIn(
            "Govern verified document evidence about quarterly operating plan",
            rendered,
        )
        self.assertNotIn("private caption", rendered)
        self.assertNotIn("private-file-108", rendered)

        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "UPDATE submissions SET objective_private=? WHERE work_id=?",
                ("<b>untrusted filename private-file-108</b>", work_id),
            )
        source = dispatcher._source(work_id)
        self.assertNotIn("private-file-108", source["objective"])
        self.assertTrue(source["objective"].startswith("Govern a captioned verified "))

    def test_visibility_route_truth_stays_unverified_until_executed_extraction_evidence(self) -> None:
        work_id, lifecycle_work_id = self.begin("109")
        dispatcher = self.dispatcher()
        self.assertEqual(dispatcher.process_work(work_id), "active")
        current = self.gateway().read_work(lifecycle_work_id)
        assert current
        self.assertTrue(dispatcher._ensure_visibility(current, "processing"))
        processing = next(item for item in self.publisher.events if item["stage"] == "processing")
        self.assertFalse(processing["routeVerified"])
        self.assertEqual(processing["routeClass"], "")
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                """INSERT INTO extractions(
                     id,work_id,digest,version,method,status,private_path,text_hash,
                     confidence,coverage,warnings_json,prompt_injection,created_at,
                     model_route,tool_version
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "extract-route-evidence", work_id, "digest-route-evidence", "v3",
                    "text", "indexed", "", "hash", 1.0, "full", "[]", 0,
                    brain.utc_now(), "local-deterministic", "python-test",
                ),
            )
        self.assertTrue(dispatcher._ensure_visibility(current, "verifying"))
        verifying = next(item for item in self.publisher.events if item["stage"] == "verifying")
        self.assertTrue(verifying["routeVerified"])
        self.assertEqual(verifying["routeClass"], "local-deterministic")

    def test_default_publisher_marks_only_evidence_backed_routes_verified(self) -> None:
        accepted = type("Result", (), {
            "returncode": 0,
            "stdout": json.dumps({"ok": True, "workLedger": {"accepted": True}}),
        })()
        base = {
            "stage": "receipt_ready", "workId": "work-telegram-0123456789abcdef01234567",
            "runId": "run-safe", "originClaimHash": "a" * 64,
            "routeVerified": False, "routeClass": "",
            "eventId": "brain-visibility-stable-test-event",
        }
        with mock.patch.object(dispatcher_module.Path, "exists", return_value=True):
            with mock.patch.object(dispatcher_module.subprocess, "run", return_value=accepted) as publish:
                self.assertTrue(dispatcher_module.default_visibility_publisher(base))
                first_command = publish.call_args.args[0]
                self.assertIn("--route-unverified", first_command)
                self.assertNotIn("--route-verified", first_command)
                self.assertNotIn("--model-family", first_command)
                self.assertNotIn("--model-id", first_command)
                verified = {
                    **base, "stage": "verifying", "routeVerified": True,
                    "routeClass": "local-deterministic",
                }
                self.assertTrue(dispatcher_module.default_visibility_publisher(verified))
                second_command = publish.call_args.args[0]
                self.assertIn("--route-verified", second_command)
                self.assertNotIn("--route-unverified", second_command)
                self.assertEqual(
                    second_command[second_command.index("--model-family") + 1],
                    "local",
                )
                self.assertEqual(
                    second_command[second_command.index("--model-id") + 1],
                    "local-deterministic",
                )
                detail = second_command[second_command.index("--detail") + 1]
                self.assertIn("local-deterministic", detail)
                self.assertEqual(
                    second_command[second_command.index("--event-id") + 1],
                    "brain-visibility-stable-test-event",
                )

    def test_visibility_replay_reuses_stable_event_id_after_acceptance_crash(self) -> None:
        class AcceptThenCrash:
            def __init__(self) -> None:
                self.calls = 0
                self.ledger: set[str] = set()
                self.event_ids: list[str] = []

            def __call__(self, event: dict[str, object]) -> bool:
                self.calls += 1
                event_id = str(event["eventId"])
                self.event_ids.append(event_id)
                already_accepted = event_id in self.ledger
                self.ledger.add(event_id)
                if self.calls == 1:
                    # Canonical ledger accepted the event, but the local
                    # publisher lost its acknowledgement before returning.
                    raise RuntimeError("simulated-post-acceptance-crash")
                return already_accepted

        publisher = AcceptThenCrash()
        dispatcher = dispatcher_module.BrainGatewayDispatcher(
            self.store_root,
            lifecycle_root=self.lifecycle_root,
            rollout_path=self.rollout,
            config_path=self.config,
            topic_receipt_path=self.topic_receipt,
            state_root=self.state_root,
            transport=self.transport,
            visibility_publisher=publisher,
            dispatcher_id="test-brain-gateway",
            lease_seconds=30,
        )
        _, lifecycle_work_id = self.begin("112")
        receipt = self.gateway().read_work(lifecycle_work_id)
        assert receipt
        self.assertFalse(dispatcher._ensure_visibility(receipt, "receipt_ready"))
        with dispatcher.connect() as db, dispatcher.transaction(db):
            db.execute(
                "UPDATE visibility_outbox SET available_at='2000-01-01T00:00:00Z' WHERE stage='receipt_ready'",
            )
        self.assertTrue(dispatcher._ensure_visibility(receipt, "receipt_ready"))
        self.assertEqual(publisher.calls, 2)
        self.assertEqual(len(publisher.ledger), 1)
        self.assertEqual(len(set(publisher.event_ids)), 1)
        with dispatcher.connect() as db:
            outbox = db.execute(
                "SELECT state,event_key FROM visibility_outbox WHERE stage='receipt_ready'",
            ).fetchone()
        self.assertEqual(outbox["state"], "accepted")
        self.assertEqual(outbox["event_key"], publisher.event_ids[0])

    def test_crash_after_lifecycle_intent_before_local_reservation_is_safely_adopted(self) -> None:
        work_id, lifecycle_work_id = self.begin("101")
        gateway = self.gateway()
        receipt = gateway.read_work(lifecycle_work_id)
        assert receipt
        claim = gateway.claim_effect(
            lifecycle_work_id, "reaction",
            sequence=int(receipt["sequence"]), fencing_epoch=int(receipt["fencingEpoch"]),
        )
        self.assertTrue(claim["allowed"])
        self.assertEqual(self.dispatcher().process_work(work_id), "active")
        self.assertEqual([method for method, _ in self.transport.calls].count("setMessageReaction"), 1)
        self.assertTrue(self.gateway().read_work(lifecycle_work_id)["reactionDelivered"])

    def test_restart_after_api_attempt_without_receipt_marks_indeterminate_and_never_retries(self) -> None:
        work_id, lifecycle_work_id = self.begin("102")
        dispatcher = self.dispatcher()
        source = dispatcher._source(work_id)
        dispatcher._bind_surface(source)
        receipt = self.gateway().read_work(lifecycle_work_id)
        assert receipt
        claim = self.gateway().claim_effect(
            lifecycle_work_id, "reaction",
            sequence=int(receipt["sequence"]), fencing_epoch=int(receipt["fencingEpoch"]),
        )
        with dispatcher.connect() as db, dispatcher.transaction(db):
            db.execute(
                """INSERT INTO attempts(
                     idempotency_key,work_id,kind,lifecycle_sequence,fencing_epoch,stage,created_at,updated_at
                   ) VALUES(?,?,?,?,?,'attempting',?,?)""",
                (
                    claim["idempotencyKey"], work_id, "reaction", receipt["sequence"],
                    receipt["fencingEpoch"], brain.utc_now(), brain.utc_now(),
                ),
            )
        self.assertEqual(dispatcher.process_work(work_id), "surface_pending")
        self.assertEqual(self.transport.calls, [])
        with self.gateway().connect() as db:
            effect = db.execute("SELECT state FROM effects WHERE idempotency_key=?", (claim["idempotencyKey"],)).fetchone()
        self.assertEqual(effect["state"], "indeterminate")
        self.assertEqual(dispatcher.process_work(work_id), "surface_pending")
        self.assertEqual(self.transport.calls, [])

    def test_terminal_consumption_closes_card_then_sends_one_separate_final(self) -> None:
        work_id, lifecycle_work_id = self.begin("103")
        dispatcher = self.dispatcher()
        self.assertEqual(dispatcher.process_work(work_id), "active")
        self.transport.calls.clear()
        self.commit_terminal(work_id, lifecycle_work_id)
        self.assertEqual(dispatcher.process_work(work_id), "delivered")
        self.assertEqual([method for method, _ in self.transport.calls], ["editMessageText", "sendMessage"])
        terminal_card = str(self.transport.calls[0][1]["text"])
        self.assertIn("Route: Verified local-deterministic", terminal_card)
        final_text = str(self.transport.calls[-1][1]["text"])
        for field in dispatcher_module.FINAL_RECEIPT_FIELDS:
            self.assertIn(field, final_text)
        self.assertIn("Model:", final_text)
        self.assertIn("Route: Verified local-deterministic", final_text)
        self.assertIn("Why: Frozen worker receipt", final_text)
        self.assertNotIn("private-opaque-ref", final_text)
        self.assertEqual(self.gateway().read_work(lifecycle_work_id)["deliveryState"], "delivered")
        self.assertIn("terminal_committed", {str(item["stage"]) for item in self.publisher.events})
        self.assertIn("delivered", {str(item["stage"]) for item in self.publisher.events})
        self.transport.calls.clear()
        self.assertEqual(dispatcher.process_work(work_id), "delivered")
        self.assertEqual(self.transport.calls, [])

    def test_indeterminate_final_is_fenced_across_restart(self) -> None:
        work_id, lifecycle_work_id = self.begin("104")
        dispatcher = self.dispatcher()
        self.assertEqual(dispatcher.process_work(work_id), "active")
        self.commit_terminal(work_id, lifecycle_work_id)
        self.transport.calls.clear()
        self.transport.responses.extend([
            {"ok": True, "state": "delivered", "result": {}},
            {"ok": False, "state": "indeterminate", "errorClass": "telegram-result-unknown"},
        ])
        self.assertEqual(dispatcher.process_work(work_id), "indeterminate")
        self.assertEqual(self.gateway().read_work(lifecycle_work_id)["deliveryState"], "indeterminate")
        prior = list(self.transport.calls)
        restarted = self.dispatcher()
        self.assertEqual(restarted.process_work(work_id), "indeterminate")
        self.assertEqual(self.transport.calls, prior)

    def test_indeterminate_card_close_blocks_final_and_stays_fenced(self) -> None:
        work_id, lifecycle_work_id = self.begin("110")
        dispatcher = self.dispatcher()
        self.assertEqual(dispatcher.process_work(work_id), "active")
        self.commit_terminal(work_id, lifecycle_work_id)
        self.transport.calls.clear()
        self.transport.responses.append({
            "ok": False, "state": "indeterminate", "errorClass": "telegram-result-unknown",
        })
        self.assertEqual(dispatcher.process_work(work_id), "incident")
        self.assertEqual([method for method, _ in self.transport.calls], ["editMessageText"])
        self.assertEqual(self.gateway().read_work(lifecycle_work_id)["deliveryState"], "pending")
        prior = list(self.transport.calls)
        self.assertEqual(self.dispatcher().process_work(work_id), "incident")
        self.assertEqual(self.transport.calls, prior)

    def test_dead_letter_card_close_blocks_final(self) -> None:
        work_id, lifecycle_work_id = self.begin("111")
        dispatcher = self.dispatcher()
        self.assertEqual(dispatcher.process_work(work_id), "active")
        self.commit_terminal(work_id, lifecycle_work_id)
        self.transport.calls.clear()
        self.transport.responses.append({
            "ok": False, "state": "dead_letter", "errorClass": "telegram-api-rejected",
        })
        self.assertEqual(dispatcher.process_work(work_id), "incident")
        self.assertEqual([method for method, _ in self.transport.calls], ["editMessageText"])
        self.assertEqual(self.gateway().read_work(lifecycle_work_id)["deliveryState"], "pending")

    def test_terminal_race_after_edit_reservation_is_fenced_before_api(self) -> None:
        work_id, lifecycle_work_id = self.begin("105")
        dispatcher = self.dispatcher()
        self.assertEqual(dispatcher.process_work(work_id), "active")
        with self.store.connect() as db, self.store.transaction(db):
            db.execute("UPDATE submissions SET phase='stored',updated_at=? WHERE work_id=?", (brain.utc_now(), work_id))
        payload = self.terminal_payload()
        original_reserve = dispatcher._reserve_effect
        terminalized = False

        def reserve_then_terminal(*args: object, **kwargs: object) -> sqlite3.Row:
            nonlocal terminalized
            attempt = original_reserve(*args, **kwargs)
            if str(attempt["kind"]) == "card_edit" and not terminalized:
                terminalized = True
                self.commit_terminal(work_id, lifecycle_work_id, payload=payload)
            return attempt

        self.transport.calls.clear()
        with mock.patch.object(dispatcher, "_reserve_effect", side_effect=reserve_then_terminal):
            state = dispatcher.process_work(work_id)
        self.assertIn(state, {"delivered", "dead_letter"})
        # The active-progress edit was fenced. Any calls are terminal close/final only.
        self.assertFalse(any(
            method == "editMessageText" and "Stored ·" in str(payload_data.get("text") or "")
            for method, payload_data in self.transport.calls
        ))
        with dispatcher.connect() as db:
            raced = db.execute(
                "SELECT stage,error_class FROM attempts WHERE kind='card_edit' ORDER BY created_at LIMIT 1",
            ).fetchone()
        self.assertEqual(raced["stage"], "dead_letter")
        self.assertEqual(raced["error_class"], "lifecycle-race-fenced")

    def test_brain_kill_switch_blocks_all_telegram_calls(self) -> None:
        work_id, _ = self.begin("106")
        rollout = json.loads(self.rollout.read_text())
        rollout["brainKillSwitch"] = True
        self.rollout.write_text(json.dumps(rollout))
        self.assertEqual(self.dispatcher().process_work(work_id), "killed")
        self.assertEqual(self.transport.calls, [])

    def test_public_status_and_run_output_never_include_private_identifiers(self) -> None:
        self.begin("107")
        dispatcher = self.dispatcher()
        result = dispatcher.run_once()
        status = dispatcher.status()
        encoded = json.dumps({"result": result, "status": status}, sort_keys=True)
        self.assertNotIn("-100123", encoded)
        self.assertNotIn("private-file", encoded)
        self.assertNotIn("107", encoded)
        self.assertTrue(result["privacy"]["countsOnly"])

    def test_terminal_history_cannot_starve_new_terminal_work_before_limit(self) -> None:
        dispatcher = self.dispatcher()
        for index in range(9):
            work_id, lifecycle_work_id = self.begin(str(200 + index))
            self.assertEqual(dispatcher.process_work(work_id), "active")
            self.commit_terminal(work_id, lifecycle_work_id)
            self.assertEqual(dispatcher.process_work(work_id), "delivered")

        new_work_id, new_lifecycle_work_id = self.begin("299")
        self.assertEqual(dispatcher.process_work(new_work_id), "active")
        self.commit_terminal(new_work_id, new_lifecycle_work_id)
        self.transport.calls.clear()

        candidates = dispatcher._candidate_work(8)
        self.assertIn(new_work_id, candidates)
        result = dispatcher.run_once(max_work=8)

        self.assertEqual(result["counts"]["delivered"], 1)
        self.assertEqual(self.gateway().read_work(new_lifecycle_work_id)["deliveryState"], "delivered")
        self.assertEqual(
            [method for method, _ in self.transport.calls],
            ["editMessageText", "sendMessage"],
        )

    def test_mutating_transport_treats_http_5xx_as_indeterminate_without_retry(self) -> None:
        error = urllib.error.HTTPError(
            "https://private.invalid", 502, "bad gateway", None, io.BytesIO(b"{}"),
        )
        with mock.patch.object(dispatcher_module.urllib.request, "urlopen", side_effect=error) as send:
            with mock.patch.dict(sys.modules, {
                "send_josh_reply": type("Reply", (), {"API_BASE": "https://private.invalid"}),
            }):
                result = dispatcher_module.default_transport(
                    "sendMessage", {"chat_id": "private", "text": "safe"}, 1,
                )
        self.assertEqual(result["state"], "indeterminate")
        self.assertEqual(send.call_count, 1)

    def test_mutating_transport_treats_api_5xx_as_indeterminate_without_retry(self) -> None:
        class Response:
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"ok": False, "error_code": 503}).encode()

        with mock.patch.object(dispatcher_module.urllib.request, "urlopen", return_value=Response()) as send:
            with mock.patch.dict(sys.modules, {
                "send_josh_reply": type("Reply", (), {"API_BASE": "https://private.invalid"}),
            }):
                result = dispatcher_module.default_transport(
                    "editMessageText", {"chat_id": "private", "message_id": 1, "text": "safe"}, 1,
                )
        self.assertEqual(result["state"], "indeterminate")
        self.assertEqual(send.call_count, 1)

    def test_delete_transport_reconciles_definitive_message_not_found_as_absent(self) -> None:
        error = urllib.error.HTTPError(
            "https://private.invalid",
            400,
            "bad request",
            None,
            io.BytesIO(json.dumps({
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message to delete not found",
            }).encode()),
        )
        with mock.patch.object(dispatcher_module.urllib.request, "urlopen", side_effect=error) as send:
            with mock.patch.dict(sys.modules, {
                "send_josh_reply": type("Reply", (), {"API_BASE": "https://private.invalid"}),
            }):
                result = dispatcher_module.default_transport(
                    "deleteMessage", {"chat_id": "private", "message_id": 1}, 1,
                )
        self.assertTrue(result["ok"])
        self.assertTrue(result["alreadyAbsent"])
        self.assertEqual(send.call_count, 1)

    def test_delete_transport_does_not_reconcile_http_5xx_not_found_description(self) -> None:
        error = urllib.error.HTTPError(
            "https://private.invalid",
            503,
            "service unavailable",
            None,
            io.BytesIO(json.dumps({
                "ok": False,
                "error_code": 503,
                "description": "Bad Request: message to delete not found",
            }).encode()),
        )
        with mock.patch.object(dispatcher_module.urllib.request, "urlopen", side_effect=error):
            with mock.patch.dict(sys.modules, {
                "send_josh_reply": type("Reply", (), {"API_BASE": "https://private.invalid"}),
            }):
                result = dispatcher_module.default_transport(
                    "deleteMessage", {"chat_id": "private", "message_id": 1}, 1,
                )
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "indeterminate")
        self.assertNotIn("alreadyAbsent", result)

    def test_delete_transport_requires_api_400_for_not_found_reconciliation(self) -> None:
        class Response:
            def __init__(self, code: int) -> None:
                self.code = code

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({
                    "ok": False,
                    "error_code": self.code,
                    "description": "Bad Request: message to delete not found",
                }).encode()

        with mock.patch.dict(sys.modules, {
            "send_josh_reply": type("Reply", (), {"API_BASE": "https://private.invalid"}),
        }):
            with mock.patch.object(
                dispatcher_module.urllib.request, "urlopen", return_value=Response(503),
            ):
                rejected = dispatcher_module.default_transport(
                    "deleteMessage", {"chat_id": "private", "message_id": 1}, 1,
                )
            with mock.patch.object(
                dispatcher_module.urllib.request, "urlopen", return_value=Response(400),
            ):
                reconciled = dispatcher_module.default_transport(
                    "deleteMessage", {"chat_id": "private", "message_id": 1}, 1,
                )
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["state"], "indeterminate")
        self.assertNotIn("alreadyAbsent", rejected)
        self.assertTrue(reconciled["ok"])
        self.assertTrue(reconciled["alreadyAbsent"])

    def test_final_waits_for_durable_control_tower_acceptance_and_replays_outbox(self) -> None:
        work_id, lifecycle_work_id = self.begin("108")
        dispatcher = self.dispatcher()
        self.assertEqual(dispatcher.process_work(work_id), "active")
        self.commit_terminal(work_id, lifecycle_work_id)
        self.transport.calls.clear()
        self.publisher.accept = False
        self.assertEqual(dispatcher.process_work(work_id), "visibility_pending")
        self.assertFalse(any(method == "sendMessage" for method, _ in self.transport.calls))
        self.assertEqual(self.gateway().read_work(lifecycle_work_id)["deliveryState"], "pending")
        with dispatcher.connect() as db, dispatcher.transaction(db):
            db.execute(
                "UPDATE visibility_outbox SET available_at='2000-01-01T00:00:00Z' WHERE state='pending'",
            )
        self.publisher.accept = True
        self.assertEqual(dispatcher.process_work(work_id), "delivered")
        self.assertEqual([method for method, _ in self.transport.calls], ["editMessageText", "sendMessage"])


if __name__ == "__main__":
    unittest.main()
