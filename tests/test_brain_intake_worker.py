from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brain_intake_worker as worker_module
import brain_media_intake as brain
from telegram_gateway_lifecycle import GatewayLifecycle, RolloutPolicy


class FakeRegistry(types.ModuleType):
    class Connection:
        def close(self) -> None:
            return None

    def __init__(self) -> None:
        super().__init__("memory_registry")
        self.forgets: list[object] = []

    def connect(self) -> Connection:
        return self.Connection()

    def forget_source(self, _db: object, _args: object) -> dict[str, object]:
        self.forgets.append(_args)
        return {"status": "forgotten", "recordCount": 0, "candidateCount": 0, "ftsDeleted": 0}


class BrainIntakeWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="brain-intake-worker-test-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.chat_id = "-1001234567890"
        self.topic_id = "777"
        self.authorized_sender = "123456789"
        self.downloads = self.folder / "downloads"
        self.downloads.mkdir(mode=0o700)
        self.store_root = self.folder / "private" / "brain"
        self.lifecycle_root = self.folder / "private" / "lifecycle"
        self.rollout = self.folder / "rollout.json"
        self.config = self.folder / "lanes.json"
        self.topic_receipt = self.folder / "brain-topic.json"
        self.topic_receipt.write_text(json.dumps({
            "state": "confirmed", "topicName": "Brain", "attemptCount": 1,
            "chatId": self.chat_id, "topicId": self.topic_id,
        }))
        self.topic_receipt.chmod(0o600)
        self.authorized_sender_receipt = self.folder / "brain-authorized-sender.json"
        self.authorized_sender_receipt.write_text(json.dumps({
            "state": "confirmed", "owner": "josh2",
            "chatId": self.chat_id, "topicId": self.topic_id,
            "authorizedSenderId": self.authorized_sender,
        }))
        self.authorized_sender_receipt.chmod(0o600)
        self._write_enabled()
        self.store = brain.BrainStore(
            self.store_root,
            download_roots=[self.downloads],
            authorized_sender_receipt=self.authorized_sender_receipt,
        )

    def _write_enabled(self) -> None:
        self.rollout.write_text(json.dumps({
            "masterState": "josh2",
            "globalKillSwitch": False,
            "brainKillSwitch": False,
            "hosts": {"josh2": True},
            "writerLifecycleVersion": 3,
            "readerLifecycleVersions": [2, 3],
        }))
        self.config.write_text(json.dumps({
            "groups": {},
            "dynamicTopics": {"brain": {
                "label": "Brain", "owner": "josh2", "lane": "brain-intake",
                "topicIdSource": "private-confirmed-receipt", "enabled": True,
            }},
        }))

    def _write_disabled(self) -> None:
        rollout = json.loads(self.rollout.read_text())
        rollout.update({"masterState": "off", "brainKillSwitch": True})
        self.rollout.write_text(json.dumps(rollout))
        config = json.loads(self.config.read_text())
        config["dynamicTopics"]["brain"]["enabled"] = False
        self.config.write_text(json.dumps(config))

    def worker(self, *, worker_id: str = "test-worker") -> worker_module.BrainIntakeWorker:
        return worker_module.BrainIntakeWorker(
            self.store_root,
            lifecycle_root=self.lifecycle_root,
            rollout_path=self.rollout,
            config_path=self.config,
            download_roots=[self.downloads],
            worker_id=worker_id,
            retry_floor_seconds=0,
        )

    def envelope(
        self,
        message: str,
        *,
        attachments: list[dict[str, object]] | None = None,
        media_group: str = "",
    ) -> dict[str, object]:
        return {
            "chatId": self.chat_id,
            "threadId": self.topic_id,
            "messageId": message,
            "senderId": self.authorized_sender,
            "senderIsBot": False,
            "mediaGroupId": media_group,
            "caption": "private caption must never enter lifecycle",
            "attachments": attachments or [{
                "sourceMessageId": message,
                "fileId": f"private-file-{message}",
                "kind": "document",
                "mime": "text/plain",
                "size": 0,
            }],
        }

    def source(self, name: str, data: bytes) -> Path:
        path = self.downloads / name
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def begin_bound(self, envelope: dict[str, object]) -> dict[str, object]:
        receipt = self.store.begin_submission(envelope)
        binding = brain.ensure_brain_lifecycle(
            self.store,
            str(receipt["workId"]),
            lifecycle_root=self.lifecycle_root,
            rollout_path=self.rollout,
        )
        gateway = GatewayLifecycle(
            self.lifecycle_root,
            rollout=RolloutPolicy.load(self.rollout),
            owner="josh2",
        )
        lifecycle_work_id = str(receipt["workId"])
        for kind in ("reaction", "card"):
            current = gateway.read_work(lifecycle_work_id)
            effect = gateway.claim_effect(
                lifecycle_work_id,
                kind,
                sequence=int(current["sequence"]),
                fencing_epoch=int(current["fencingEpoch"]),
            )
            self.assertTrue(effect["allowed"])
            gateway.finish_effect(str(effect["idempotencyKey"]), state="delivered")
        return receipt

    def enqueue(
        self,
        message: str,
        data: bytes,
        *,
        mime: str = "text/plain",
        kind: str = "document",
    ) -> tuple[dict[str, object], dict[str, object]]:
        envelope = self.envelope(message)
        envelope["attachments"][0]["mime"] = mime
        envelope["attachments"][0]["kind"] = kind
        receipt = self.begin_bound(envelope)
        capability = receipt["downloadTokens"][0]
        accepted = self.store.accept_download(
            work_id=str(receipt["workId"]),
            attachment_id=str(capability["attachmentId"]),
            token=str(capability["token"]),
            source_path=self.source(f"{message}.bin", data),
        )
        return receipt, accepted

    def brain_count(self, table: str) -> int:
        self.assertIn(table, {"intake_jobs", "intake_results"})
        with self.store.connect() as db:
            return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def lifecycle_rows(self, query: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        gateway = GatewayLifecycle(
            self.lifecycle_root,
            rollout=RolloutPolicy.load(self.rollout),
            owner="josh2",
        )
        with gateway.connect() as db:
            return list(db.execute(query, params).fetchall())

    def test_album_enqueues_only_after_last_store_and_never_extracts_inline(self) -> None:
        attachments = [
            {"sourceMessageId": "100", "fileId": "file-a", "kind": "document", "mime": "text/plain", "size": 0},
            {"sourceMessageId": "101", "fileId": "file-b", "kind": "document", "mime": "text/plain", "size": 0},
        ]
        receipt = self.begin_bound(self.envelope("100", attachments=attachments, media_group="album-100"))
        first_path = self.source("album-a.txt", b"first album source")
        second_path = self.source("album-b.txt", b"second album source")
        with mock.patch.object(brain, "extract_local") as extract:
            first = self.store.accept_download(
                work_id=str(receipt["workId"]),
                attachment_id=str(receipt["downloadTokens"][0]["attachmentId"]),
                token=str(receipt["downloadTokens"][0]["token"]),
                source_path=first_path,
            )
            self.assertFalse(first["queued"])
            self.assertEqual(self.brain_count("intake_jobs"), 0)
            second = self.store.accept_download(
                work_id=str(receipt["workId"]),
                attachment_id=str(receipt["downloadTokens"][1]["attachmentId"]),
                token=str(receipt["downloadTokens"][1]["token"]),
                source_path=second_path,
            )
            self.assertTrue(second["queued"])
            extract.assert_not_called()
        self.assertEqual(self.brain_count("intake_jobs"), 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM extractions").fetchone()[0], 0)

    def test_restart_recovers_expired_lease_and_terminal_handoff_is_exactly_once(self) -> None:
        receipt, _ = self.enqueue("200", b"durable restart marker")
        first_worker = self.worker(worker_id="worker-before-restart")
        claimed = first_worker._claim(brain.MAX_SUBMISSION_BYTES)
        self.assertIsNotNone(claimed)
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "UPDATE intake_jobs SET lease_expires_at='2000-01-01T00:00:00Z' WHERE work_id=?",
                (receipt["workId"],),
            )
        with mock.patch.object(GatewayLifecycle, "claim_terminal_delivery") as forbidden:
            result = self.worker(worker_id="worker-after-restart").run_once(max_jobs=2)
            forbidden.assert_not_called()
        self.assertEqual(result["recoveredLeases"], 1)
        self.assertEqual(result["counts"]["succeeded"], 1)
        replay = self.store.begin_submission(self.envelope("200"))
        self.assertTrue(replay["duplicate"])
        self.assertEqual(self.worker().run_once()["counts"]["claimed"], 0)
        self.assertEqual(self.brain_count("intake_results"), 1)
        with self.store.connect() as db:
            identity = db.execute(
                """SELECT s.work_id,j.work_id AS job_work_id,r.work_id AS result_work_id,
                          r.lifecycle_work_id,b.lifecycle_work_id AS binding_work_id
                     FROM submissions s JOIN intake_jobs j ON j.work_id=s.work_id
                     JOIN intake_results r ON r.work_id=s.work_id
                     JOIN lifecycle_bindings b ON b.work_id=s.work_id
                    WHERE s.work_id=?""",
                (receipt["workId"],),
            ).fetchone()
        self.assertEqual(set(dict(identity).values()), {receipt["workId"]})
        retrieval = self.store.search_source(
            query="durable restart marker", agent="josh2",
        )
        self.assertEqual(retrieval["results"][0]["workId"], receipt["workId"])
        outbox = self.lifecycle_rows("SELECT * FROM terminal_outbox")
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["state"], "pending")
        payload = str(outbox[0]["payload_json"])
        self.assertNotIn("private caption", payload)
        self.assertNotIn("private-chat", payload)
        self.assertNotIn(str(receipt["workId"]), payload)

    def test_restart_after_outbox_commit_reuses_frozen_result(self) -> None:
        receipt, _ = self.enqueue("250", b"terminal crash recovery marker")
        original_commit = GatewayLifecycle.commit_terminal
        crashed = False

        def commit_then_crash(gateway: GatewayLifecycle, *args: object, **kwargs: object) -> dict[str, object]:
            nonlocal crashed
            result = original_commit(gateway, *args, **kwargs)
            if not crashed:
                crashed = True
                raise RuntimeError("simulated process loss after durable outbox commit")
            return result

        worker = self.worker()
        with mock.patch.object(GatewayLifecycle, "commit_terminal", autospec=True, side_effect=commit_then_crash):
            first = worker.run_once(max_jobs=1)
        self.assertEqual(first["counts"]["retried"], 1)
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "UPDATE intake_jobs SET available_at='2000-01-01T00:00:00Z' WHERE work_id=?",
                (receipt["workId"],),
            )
        second = worker.run_once(max_jobs=1)
        self.assertEqual(second["counts"]["succeeded"], 1)
        self.assertEqual(self.brain_count("intake_results"), 1)
        self.assertEqual(len(self.lifecycle_rows("SELECT 1 FROM terminal_outbox")), 1)

    def test_unsupported_and_quarantine_finish_without_fabricated_learning(self) -> None:
        audio, _ = self.enqueue("300", b"OggS" + b"\x00" * 64, mime="audio/ogg", kind="voice")
        executable, rejected = self.enqueue("301", b"#!/bin/sh\necho unsafe\n", mime="text/plain")
        self.assertTrue(rejected["quarantined"])
        with mock.patch.object(brain, "extract_local", wraps=brain.extract_local) as extract:
            result = self.worker().run_once(max_jobs=4)
        self.assertEqual(result["counts"]["partial"], 1)
        self.assertEqual(result["counts"]["failed"], 1)
        called_paths = [str(call.args[0]) for call in extract.call_args_list]
        self.assertFalse(any("quarantine" in value for value in called_paths))
        with self.store.connect() as db:
            learned = db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
            outcomes = {
                row["work_id"]: row["outcome"]
                for row in db.execute("SELECT work_id,outcome FROM intake_results")
            }
        self.assertEqual(learned, 0)
        self.assertEqual(outcomes[audio["workId"]], "partial")
        self.assertEqual(outcomes[executable["workId"]], "failed")

    def test_metadata_only_attachment_failures_finish_failed_or_partial_without_learning(self) -> None:
        oversize_envelope = self.envelope("310")
        oversize_envelope["attachments"][0]["size"] = brain.MAX_FILE_BYTES + 1
        oversize = self.begin_bound(oversize_envelope)
        oversize_token = oversize["downloadTokens"][0]
        oversize_failed = self.store.fail_attachment(
            work_id=str(oversize["workId"]),
            attachment_id=str(oversize_token["attachmentId"]),
            token=str(oversize_token["token"]),
            reason="oversize",
        )
        self.assertTrue(oversize_failed["queued"])

        attachments = [
            {"sourceMessageId": "311", "fileId": "mixed-a", "kind": "document", "mime": "text/plain", "size": 8},
            {"sourceMessageId": "312", "fileId": "mixed-b", "kind": "document", "mime": "text/plain", "size": 8},
        ]
        mixed = self.begin_bound(self.envelope("311", attachments=attachments, media_group="album-311"))
        first, second = mixed["downloadTokens"]
        stored = self.store.accept_download(
            work_id=str(mixed["workId"]),
            attachment_id=str(first["attachmentId"]),
            token=str(first["token"]),
            source_path=self.source("mixed-a.txt", b"safe mixed album evidence"),
        )
        self.assertFalse(stored["queued"])
        unavailable = self.store.fail_attachment(
            work_id=str(mixed["workId"]),
            attachment_id=str(second["attachmentId"]),
            token=str(second["token"]),
            reason="download-unavailable",
        )
        self.assertTrue(unavailable["queued"])

        with mock.patch.object(brain, "extract_local", wraps=brain.extract_local) as extract:
            result = self.worker().run_once(max_jobs=2)
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(result["counts"]["partial"], 1)
        self.assertEqual(extract.call_count, 1)
        with self.store.connect() as db:
            outcomes = {
                row["work_id"]: row["outcome"]
                for row in db.execute("SELECT work_id,outcome FROM intake_results")
            }
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM candidates WHERE work_id=?", (oversize["workId"],),
            ).fetchone()[0], 0)
        self.assertEqual(outcomes[oversize["workId"]], "failed")
        self.assertEqual(outcomes[mixed["workId"]], "partial")
        self.assertIn("oversize", self.store.final_receipt(str(oversize["workId"]))["Unsupported"])
        self.assertIn("download-unavailable", self.store.final_receipt(str(mixed["workId"]))["Unsupported"])

    def test_forget_cancels_before_extraction_and_commits_once(self) -> None:
        receipt, _ = self.enqueue("400", b"private content that must be forgotten")
        preview = self.store.forget_preview(str(receipt["workId"]), authorized_user=self.authorized_sender)
        with mock.patch.dict(sys.modules, {"memory_registry": FakeRegistry()}):
            forgotten = self.store.forget(
                str(receipt["workId"]),
                authorized_user=self.authorized_sender,
                confirmation_token=str(preview["confirmationToken"]),
            )
        self.assertTrue(forgotten["forgotten"])
        with mock.patch.object(brain, "extract_local") as extract:
            first = self.worker().run_once(max_jobs=2)
            second = self.worker().run_once(max_jobs=2)
            extract.assert_not_called()
        self.assertEqual(first["counts"]["cancelled"], 1)
        self.assertEqual(second["counts"]["claimed"], 0)
        self.assertEqual(self.brain_count("intake_results"), 1)
        terminal = self.lifecycle_rows("SELECT outcome,COUNT(*) AS count FROM terminal_outbox GROUP BY outcome")
        self.assertEqual([(row["outcome"], row["count"]) for row in terminal], [("cancelled", 1)])

    def test_forget_after_success_prepare_supersedes_uncommitted_terminal_intent(self) -> None:
        receipt, _ = self.enqueue("450", b"cancel must supersede prepared success")
        worker = self.worker()
        claimed = worker._claim(brain.MAX_SUBMISSION_BYTES)
        self.assertIsNotNone(claimed)
        binding = self.store.lifecycle_binding(str(receipt["workId"]))
        self.assertIsNotNone(binding)
        success_receipt = self.store.final_receipt(str(receipt["workId"]))
        prepared_payload = worker._terminal_payload(
            str(receipt["workId"]), binding, "succeeded", success_receipt,
        )
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "INSERT INTO intake_terminal_prepares VALUES(?,?,?,?,?,?,?)",
                (
                    receipt["workId"], "succeeded",
                    worker_module.payload_hash(prepared_payload),
                    json.dumps(prepared_payload, sort_keys=True, separators=(",", ":")),
                    int(claimed["attempt_count"]),
                    brain.stable_id("brain-lease-owner", worker.worker_id, length=32),
                    brain.utc_now(),
                ),
            )
        preview = self.store.forget_preview(str(receipt["workId"]), authorized_user=self.authorized_sender)
        with mock.patch.dict(sys.modules, {"memory_registry": FakeRegistry()}):
            self.store.forget(
                str(receipt["workId"]),
                authorized_user=self.authorized_sender,
                confirmation_token=str(preview["confirmationToken"]),
            )
        resumed_worker = self.worker(worker_id="post-forget-worker")
        resumed = resumed_worker._claim(brain.MAX_SUBMISSION_BYTES)
        self.assertIsNotNone(resumed)
        terminal = resumed_worker._terminalize(
            str(receipt["workId"]), binding, "succeeded", success_receipt,
            expected_attempt=int(resumed["attempt_count"]),
        )
        self.assertEqual(terminal["outcome"], "cancelled")
        with self.store.connect() as db:
            prepare = db.execute(
                "SELECT outcome FROM intake_terminal_prepares WHERE work_id=?", (receipt["workId"],),
            ).fetchone()
            result = db.execute(
                "SELECT outcome FROM intake_results WHERE work_id=?", (receipt["workId"],),
            ).fetchone()
        self.assertEqual(prepare["outcome"], "cancelled")
        self.assertEqual(result["outcome"], "cancelled")

    def test_owner_cancel_before_extraction_retains_source_and_is_idempotent(self) -> None:
        receipt, _ = self.enqueue("460", b"retained source after cancellation")
        with self.store.connect() as db:
            stored_path = Path(db.execute(
                """SELECT a.stored_path FROM submission_artifacts sa
                     JOIN artifacts a ON a.digest=sa.digest WHERE sa.work_id=?""",
                (receipt["workId"],),
            ).fetchone()[0])
        registry = FakeRegistry()
        with mock.patch.dict(sys.modules, {"memory_registry": registry}):
            cancelled = self.store.cancel_submission(
                str(receipt["workId"]), authorized_user=self.authorized_sender,
            )
        self.assertTrue(cancelled["cancelled"])
        self.assertTrue(cancelled["sourceRetained"])
        self.assertTrue(stored_path.exists())
        with mock.patch.object(brain, "extract_local") as extract:
            result = self.worker().run_once(max_jobs=1)
            extract.assert_not_called()
        self.assertEqual(result["counts"]["cancelled"], 1)
        self.assertTrue(stored_path.exists())
        with mock.patch.dict(sys.modules, {"memory_registry": registry}):
            replay = self.store.cancel_submission(
                str(receipt["workId"]), authorized_user=self.authorized_sender,
            )
        self.assertTrue(replay["tooLate"])
        self.assertEqual(replay["terminalStatus"], "cancelled")
        with self.store.connect() as db:
            payload = json.loads(db.execute(
                "SELECT private_payload_json FROM intake_results WHERE work_id=?",
                (receipt["workId"],),
            ).fetchone()[0])
        self.assertEqual(payload["terminalStatus"], "cancelled")
        self.assertEqual(payload["receipt"]["Retention"], "privately retained")

    def test_cancel_revokes_promoted_candidate_and_restart_fences_late_success(self) -> None:
        receipt, _ = self.enqueue("461", b"candidate cancellation source")
        self.store.extract_submission(str(receipt["workId"]))
        candidate_id = brain.stable_id("test-candidate", receipt["workId"], length=28)
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                """INSERT INTO candidates(
                     id,work_id,candidate_type,subject,predicate,value_private,privacy_class,
                     confidence,provenance_ref,status,eligibility_reason,registry_candidate_id,
                     registry_memory_id,created_at,duplicate_of,conflicts_with,semantic_score
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    candidate_id, receipt["workId"], "fact", "subject", "predicate", "value",
                    "private", 0.99, "source-ref", "active", "approved",
                    "registry-candidate", "registry-memory", brain.utc_now(), "", "", 0.0,
                ),
            )
        stale_worker = self.worker(worker_id="cancel-stale-worker")
        claimed = stale_worker._claim(brain.MAX_SUBMISSION_BYTES)
        self.assertIsNotNone(claimed)
        binding = self.store.lifecycle_binding(str(receipt["workId"]))
        success_receipt = self.store.final_receipt(str(receipt["workId"]))
        prepared = stale_worker._terminal_payload(
            str(receipt["workId"]), binding, "succeeded", success_receipt,
        )
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "INSERT INTO intake_terminal_prepares VALUES(?,?,?,?,?,?,?)",
                (
                    receipt["workId"], "succeeded", worker_module.payload_hash(prepared),
                    json.dumps(prepared, sort_keys=True, separators=(",", ":")),
                    int(claimed["attempt_count"]),
                    brain.stable_id("brain-lease-owner", stale_worker.worker_id, length=32),
                    brain.utc_now(),
                ),
            )
        registry = FakeRegistry()
        with mock.patch.dict(sys.modules, {"memory_registry": registry}):
            cancelled = self.store.cancel_submission(
                str(receipt["workId"]), authorized_user=self.authorized_sender,
            )
        self.assertEqual(cancelled["revokedPending"], 1)
        self.assertEqual(len(registry.forgets), 1)
        with self.store.connect() as db, self.store.transaction(db):
            self.assertEqual(db.execute(
                "SELECT status FROM candidates WHERE id=?", (candidate_id,),
            ).fetchone()[0], "cancelled")
            db.execute(
                "UPDATE intake_jobs SET lease_expires_at='2000-01-01T00:00:00Z' WHERE work_id=?",
                (receipt["workId"],),
            )
        resumed_worker = self.worker(worker_id="cancel-resumed-worker")
        self.assertEqual(resumed_worker.recover_expired_leases(), 1)
        resumed = resumed_worker._claim(brain.MAX_SUBMISSION_BYTES)
        terminal = resumed_worker._terminalize(
            str(receipt["workId"]), binding, "succeeded", success_receipt,
            expected_attempt=int(resumed["attempt_count"]),
        )
        self.assertEqual(terminal["outcome"], "cancelled")
        with self.assertRaises(worker_module.LeaseLostError):
            stale_worker._terminalize(
                str(receipt["workId"]), binding, "succeeded", success_receipt,
                expected_attempt=int(claimed["attempt_count"]),
            )

    def test_cancel_retries_registry_tombstone_after_local_revocation(self) -> None:
        receipt, _ = self.enqueue("4611", b"registry cancellation retry source")
        candidate_id = brain.stable_id("test-candidate", receipt["workId"], length=28)
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                """INSERT INTO candidates(
                     id,work_id,candidate_type,subject,predicate,value_private,privacy_class,
                     confidence,provenance_ref,status,eligibility_reason,registry_candidate_id,
                     registry_memory_id,created_at,duplicate_of,conflicts_with,semantic_score
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    candidate_id, receipt["workId"], "fact", "subject", "predicate", "value",
                    "private", 0.99, "source-ref", "active", "approved",
                    "registry-candidate", "registry-memory", brain.utc_now(), "", "", 0.0,
                ),
            )
        registry = FakeRegistry()
        successful_tombstone = {
            "status": "forgotten", "recordCount": 1, "candidateCount": 1, "ftsDeleted": 1,
        }
        with (
            mock.patch.object(
                registry, "forget_source",
                side_effect=[RuntimeError("temporary registry outage"), successful_tombstone],
            ) as forget_source,
            mock.patch.dict(sys.modules, {"memory_registry": registry}),
        ):
            with self.assertRaisesRegex(RuntimeError, "temporary registry outage"):
                self.store.cancel_submission(
                    str(receipt["workId"]), authorized_user=self.authorized_sender,
                )
            with self.store.connect() as db:
                local = db.execute(
                    """SELECT s.user_cancel_requested,c.status
                         FROM submissions s JOIN candidates c ON c.work_id=s.work_id
                        WHERE s.work_id=?""",
                    (receipt["workId"],),
                ).fetchone()
            self.assertEqual(local["user_cancel_requested"], 1)
            self.assertEqual(local["status"], "cancelled")
            retry = self.store.cancel_submission(
                str(receipt["workId"]), authorized_user=self.authorized_sender,
            )
        self.assertTrue(retry["duplicate"])
        self.assertEqual(retry["memoryCandidateTombstones"], 1)
        self.assertEqual(retry["memoryRecordTombstones"], 1)
        self.assertEqual(forget_source.call_count, 2)

    def test_cancel_after_completed_success_is_too_late_and_does_not_rewrite_outcome(self) -> None:
        receipt, _ = self.enqueue("462", b"already completed source")
        self.assertEqual(self.worker().run_once(max_jobs=1)["counts"]["succeeded"], 1)
        result = self.store.cancel_submission(
            str(receipt["workId"]), authorized_user=self.authorized_sender,
        )
        self.assertTrue(result["tooLate"])
        self.assertEqual(result["terminalStatus"], "succeeded")
        with self.store.connect() as db:
            submission = db.execute(
                "SELECT user_cancel_requested FROM submissions WHERE work_id=?",
                (receipt["workId"],),
            ).fetchone()
            outcome = db.execute(
                "SELECT outcome FROM intake_results WHERE work_id=?",
                (receipt["workId"],),
            ).fetchone()[0]
        self.assertEqual(submission["user_cancel_requested"], 0)
        self.assertEqual(outcome, "succeeded")

    def test_active_job_drains_after_kill_but_unbound_new_job_does_not_start(self) -> None:
        active, _ = self.enqueue("500", b"accepted before emergency stop")
        self._write_disabled()
        worker = self.worker()
        self.assertFalse(worker.accepting_new())
        drained = worker.run_once(max_jobs=1)
        self.assertEqual(drained["counts"]["succeeded"], 1)

        # Direct store access is not the live intake path, but proves a queued
        # row cannot silently gain lifecycle authority after the gate closes.
        unbound = self.store.begin_submission(self.envelope("501"))
        capability = unbound["downloadTokens"][0]
        self.store.accept_download(
            work_id=str(unbound["workId"]),
            attachment_id=str(capability["attachmentId"]),
            token=str(capability["token"]),
            source_path=self.source("501.txt", b"must remain queued while disabled"),
        )
        stopped = worker.run_once(max_jobs=1)
        self.assertEqual(stopped["counts"]["retried"], 1)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT state,error_class FROM intake_jobs WHERE work_id=?", (unbound["workId"],),
            ).fetchone()
        self.assertEqual(row["state"], "retry_wait")
        self.assertEqual(row["error_class"], "lifecycle-binding-pending")
        self.assertEqual(
            len(self.lifecycle_rows("SELECT 1 FROM work_receipts")),
            1,
        )
        self.assertEqual(active["phase"], "receipt_pending")

    def test_retries_are_bounded_failures_are_redacted_and_resources_defer(self) -> None:
        receipt, _ = self.enqueue("600", b"bounded failure source")
        worker = self.worker()
        with mock.patch.object(brain, "extract_local", side_effect=RuntimeError("raw secret failure detail")):
            for _ in range(brain.WORKER_MAX_ATTEMPTS):
                worker.run_once(max_jobs=1)
                with self.store.connect() as db, self.store.transaction(db):
                    db.execute(
                        "UPDATE intake_jobs SET available_at='2000-01-01T00:00:00Z' WHERE work_id=? AND state='retry_wait'",
                        (receipt["workId"],),
                    )
        with self.store.connect() as db:
            job = db.execute("SELECT state,attempt_count,error_class FROM intake_jobs").fetchone()
            payload = db.execute("SELECT private_payload_json FROM intake_results").fetchone()[0]
        self.assertEqual(job["state"], "completed")
        self.assertEqual(job["attempt_count"], brain.WORKER_MAX_ATTEMPTS)
        self.assertEqual(job["error_class"], "runtimeerror")
        self.assertNotIn("raw secret", payload)

        deferred, _ = self.enqueue("601", b"larger than a one-byte worker budget")
        limited = worker.run_once(max_jobs=1, max_bytes=1)
        self.assertEqual(limited["counts"]["claimed"], 0)
        self.assertEqual(limited["resourceDeferred"], 1)
        with self.store.connect() as db:
            state = db.execute(
                "SELECT state FROM intake_jobs WHERE work_id=?", (deferred["workId"],),
            ).fetchone()[0]
        self.assertEqual(state, "queued")
        rendered = json.dumps(worker.status())
        self.assertNotIn(str(deferred["workId"]), rendered)
        self.assertNotIn("private-chat", rendered)

    def test_expired_max_attempt_lease_terminalizes_once_without_reclaim_loop(self) -> None:
        receipt, _ = self.enqueue("605", b"expired lease exhaustion source")
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                """UPDATE intake_jobs SET state='running',attempt_count=max_attempts,
                          lease_owner='dead-worker',lease_expires_at='2000-01-01T00:00:00Z'
                     WHERE work_id=?""",
                (receipt["workId"],),
            )
        first = self.worker(worker_id="lease-exhaustion-worker").run_once(max_jobs=2)
        self.assertEqual(first["recoveredLeases"], 1)
        self.assertEqual(first["counts"]["failed"], 1)
        self.assertEqual(first["counts"]["deadLetter"], 0)
        second = self.worker(worker_id="lease-exhaustion-replay").run_once(max_jobs=2)
        self.assertEqual(second["counts"]["claimed"], 0)
        with self.store.connect() as db:
            job = db.execute(
                "SELECT state,attempt_count,error_class FROM intake_jobs WHERE work_id=?",
                (receipt["workId"],),
            ).fetchone()
        self.assertEqual(job["state"], "completed")
        self.assertEqual(job["attempt_count"], brain.WORKER_MAX_ATTEMPTS)
        self.assertEqual(job["error_class"], "worker-lease-exhausted")
        self.assertEqual(len(self.lifecycle_rows(
            "SELECT 1 FROM terminal_outbox WHERE work_id=?", (receipt["workId"],),
        )), 1)

    def test_public_readiness_is_required_before_any_extraction(self) -> None:
        envelope = self.envelope("606")
        receipt = self.store.begin_submission(envelope)
        brain.ensure_brain_lifecycle(
            self.store, str(receipt["workId"]),
            lifecycle_root=self.lifecycle_root, rollout_path=self.rollout,
        )
        capability = receipt["downloadTokens"][0]
        self.store.accept_download(
            work_id=str(receipt["workId"]),
            attachment_id=str(capability["attachmentId"]),
            token=str(capability["token"]),
            source_path=self.source("606.txt", b"must await public readiness"),
        )
        worker = self.worker()
        with mock.patch.object(brain, "extract_local") as extract:
            result = worker.run_once(max_jobs=1)
            extract.assert_not_called()
        self.assertEqual(result["counts"]["retried"], 1)
        with self.store.connect() as db:
            job = db.execute(
                "SELECT state,error_class FROM intake_jobs WHERE work_id=?",
                (receipt["workId"],),
            ).fetchone()
        self.assertEqual((job["state"], job["error_class"]), (
            "retry_wait", "lifecycle-readiness-pending",
        ))

    def test_run_wide_extraction_character_budget_defers_second_job(self) -> None:
        first, _ = self.enqueue("607", b"a" * 200)
        second, _ = self.enqueue("608", b"b" * 200)
        result = self.worker().run_once(
            max_jobs=2, max_extracted_chars=32,
        )
        self.assertEqual(result["counts"]["claimed"], 1)
        self.assertEqual(result["used"]["extractedChars"], 32)
        with self.store.connect() as db:
            states = {
                row["work_id"]: row["state"]
                for row in db.execute("SELECT work_id,state FROM intake_jobs")
            }
            extracted = db.execute(
                "SELECT SUM(LENGTH(evidence_text)) FROM source_fts",
            ).fetchone()[0]
        self.assertEqual(sorted(states.values()), ["completed", "queued"])
        self.assertLessEqual(int(extracted or 0), 32)

    def test_deadline_after_candidate_stage_retries_before_review_or_terminal(self) -> None:
        receipt, _ = self.enqueue("610", b"deadline candidate stage source")
        worker = self.worker(worker_id="deadline-candidate-worker")
        clock = {"now": 0.0}

        def slow_candidate(*_args: object, **_kwargs: object) -> dict[str, object]:
            clock["now"] = 2.0
            return {"ok": True, "candidateCount": 0}

        with (
            mock.patch.object(worker_module.time, "monotonic", side_effect=lambda: clock["now"]),
            mock.patch.object(worker.store, "synthesize_candidates", side_effect=slow_candidate),
            mock.patch.object(worker.store, "review_candidates") as review,
            mock.patch.object(worker, "_terminalize", wraps=worker._terminalize) as terminalize,
        ):
            result = worker.run_once(max_jobs=1, max_seconds=1)
        self.assertEqual(result["counts"]["retried"], 1)
        review.assert_not_called()
        terminalize.assert_not_called()
        with self.store.connect() as db:
            job = db.execute(
                "SELECT state,error_class FROM intake_jobs WHERE work_id=?",
                (receipt["workId"],),
            ).fetchone()
            prepares = db.execute(
                "SELECT COUNT(*) FROM intake_terminal_prepares WHERE work_id=?",
                (receipt["workId"],),
            ).fetchone()[0]
        self.assertEqual((job["state"], job["error_class"]), ("retry_wait", "worker-time-budget"))
        self.assertEqual(prepares, 0)
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "UPDATE intake_jobs SET available_at='2000-01-01T00:00:00Z' WHERE work_id=?",
                (receipt["workId"],),
            )
        self.assertEqual(self.worker().run_once(max_jobs=1)["counts"]["succeeded"], 1)

    def test_deadline_after_terminal_prepare_does_not_cross_lifecycle_boundary(self) -> None:
        receipt, _ = self.enqueue("611", b"terminal prepare deadline source")
        worker = self.worker(worker_id="deadline-terminal-worker")
        claimed = worker._claim(brain.MAX_SUBMISSION_BYTES)
        binding = self.store.lifecycle_binding(str(receipt["workId"]))
        with mock.patch.object(
            worker,
            "_check_deadline",
            side_effect=[None, worker_module.WorkerBudgetExceeded("worker-time-budget")],
        ):
            with self.assertRaises(worker_module.WorkerBudgetExceeded):
                worker._terminalize(
                    str(receipt["workId"]), binding, "succeeded",
                    self.store.final_receipt(str(receipt["workId"])),
                    expected_attempt=int(claimed["attempt_count"]),
                    deadline_monotonic=1.0,
                )
        with self.store.connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM intake_terminal_prepares WHERE work_id=?",
                (receipt["workId"],),
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM intake_results WHERE work_id=?",
                (receipt["workId"],),
            ).fetchone()[0], 0)
        self.assertEqual(len(self.lifecycle_rows(
            "SELECT 1 FROM terminal_outbox WHERE work_id=?", (receipt["workId"],),
        )), 0)
        self.assertTrue(worker._retry(claimed, "worker-time-budget"))
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "UPDATE intake_jobs SET available_at='2000-01-01T00:00:00Z' WHERE work_id=?",
                (receipt["workId"],),
            )
        self.assertEqual(self.worker().run_once(max_jobs=1)["counts"]["succeeded"], 1)
        self.assertEqual(len(self.lifecycle_rows(
            "SELECT 1 FROM terminal_outbox WHERE work_id=?", (receipt["workId"],),
        )), 1)

    def test_fairness_rotates_media_lane_before_second_job_in_same_lane(self) -> None:
        first, _ = self.enqueue("700", b"first text lane")
        second, _ = self.enqueue("701", b"second text lane")
        audio, _ = self.enqueue("702", b"OggS" + b"\x00" * 64, mime="audio/ogg", kind="voice")
        with self.store.connect() as db, self.store.transaction(db):
            db.execute(
                "UPDATE intake_jobs SET created_at='2026-01-01T00:00:00Z' WHERE work_id=?",
                (first["workId"],),
            )
            db.execute(
                "UPDATE intake_jobs SET created_at='2026-01-01T00:00:01Z' WHERE work_id=?",
                (second["workId"],),
            )
            db.execute(
                "UPDATE intake_jobs SET created_at='2026-01-01T00:00:02Z' WHERE work_id=?",
                (audio["workId"],),
            )
        result = self.worker().run_once(max_jobs=2)
        self.assertEqual(result["counts"]["claimed"], 2)
        self.assertEqual(result["counts"]["succeeded"], 1)
        self.assertEqual(result["counts"]["partial"], 1)
        with self.store.connect() as db:
            states = {
                row["work_id"]: row["state"]
                for row in db.execute("SELECT work_id,state FROM intake_jobs")
            }
        self.assertEqual(states[first["workId"]], "completed")
        self.assertEqual(states[audio["workId"]], "completed")
        self.assertEqual(states[second["workId"]], "queued")


if __name__ == "__main__":
    unittest.main()
