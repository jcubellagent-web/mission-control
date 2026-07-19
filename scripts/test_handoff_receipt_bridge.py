#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import control_tower_work_store
import handoff_receipt_bridge as subject


class HandoffReceiptBridgeTests(unittest.TestCase):
    def record(self, **overrides):
        row = {
            "id": "event-handoff-1",
            "workId": "work-handoff-1",
            "runId": "run-handoff-1",
            "originClaimHash": "a" * 64,
            "senderEventId": "event-handoff-1",
            "time": "2026-07-18T16:00:00Z",
            "from": "joshex",
            "fromLabel": "JOSHeX",
            "to": "jaimes",
            "title": "Dashboard-safe handoff title",
            "status": "open",
            "detail": "Dashboard-safe handoff detail",
            "path": "docs/handoffs/example.md",
            "privacy": "dashboard-safe",
        }
        row.update(overrides)
        return row

    def test_new_handoff_is_additive_and_retry_does_not_rewrite_history(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            queue = Path(raw) / "handoff-queue.json"
            legacy = {
                "handoffs": [
                    {"id": "legacy-one", "title": "Keep one", "status": "done"},
                    {"id": "legacy-two", "title": "Keep two", "status": "done"},
                ],
                "preserveTopLevel": True,
            }
            queue.write_text(json.dumps(legacy), encoding="utf-8")

            first = subject.write_new_handoff(queue, self.record())
            self.assertTrue(first["created"])
            document = json.loads(queue.read_text(encoding="utf-8"))
            self.assertEqual(len(document["handoffs"]), 3)
            self.assertTrue(document["preserveTopLevel"])
            handoff = document["handoffs"][0]
            self.assertEqual(handoff["handoffSchemaVersion"], 2)
            self.assertEqual(handoff["receiptSchemaVersion"], 1)
            self.assertEqual(handoff["workId"], "work-handoff-1")
            self.assertEqual(handoff["runId"], "run-handoff-1")
            self.assertEqual(handoff["originClaimHash"], "a" * 64)
            self.assertEqual(handoff["senderEventId"], "event-handoff-1")
            self.assertEqual(handoff["senderReceiptId"], handoff["receipts"][0]["id"])
            self.assertEqual(handoff["receipts"][0]["kind"], "sent")

            retry = self.record(title="A retry must not rewrite the original title")
            second = subject.write_new_handoff(queue, retry)
            self.assertFalse(second["created"])
            document = json.loads(queue.read_text(encoding="utf-8"))
            self.assertEqual(len(document["handoffs"]), 3)
            self.assertEqual(
                document["handoffs"][0]["title"], "Dashboard-safe handoff title"
            )
            self.assertEqual(len(document["handoffs"][0]["receipts"]), 1)

    def test_acknowledgement_and_result_writes_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            queue = Path(raw) / "handoff-queue.json"
            subject.write_new_handoff(queue, self.record())

            first_ack = subject.record_receipt(
                queue,
                kind="acknowledged",
                agent="jaimes",
                work_id="work-handoff-1",
                run_id="run-handoff-1",
                origin_claim_hash="a" * 64,
                event_id="event-ack-1",
                task_id="task-handoff-1",
                recorded_at="2026-07-18T16:01:00Z",
            )
            retry_ack = subject.record_receipt(
                queue,
                kind="acknowledged",
                agent="jaimes",
                work_id="work-handoff-1",
                run_id="run-handoff-1",
                origin_claim_hash="a" * 64,
                event_id="event-ack-retry",
                task_id="task-handoff-1",
                recorded_at="2026-07-18T16:02:00Z",
            )
            self.assertTrue(first_ack["created"])
            self.assertFalse(retry_ack["created"])
            self.assertEqual(first_ack["receipt"]["id"], retry_ack["receipt"]["id"])
            self.assertEqual(retry_ack["receipt"]["eventId"], "event-ack-1")

            first_result = subject.record_receipt(
                queue,
                kind="terminal",
                agent="jaimes",
                work_id="work-handoff-1",
                run_id="run-handoff-1",
                origin_claim_hash="a" * 64,
                event_id="event-result-1",
                task_id="task-handoff-1",
                status="done",
                recorded_at="2026-07-18T16:03:00Z",
            )
            retry_result = subject.record_receipt(
                queue,
                kind="terminal",
                agent="jaimes",
                work_id="work-handoff-1",
                run_id="run-handoff-1",
                origin_claim_hash="a" * 64,
                event_id="event-result-retry",
                task_id="task-handoff-1",
                status="done",
                recorded_at="2026-07-18T16:04:00Z",
            )
            self.assertTrue(first_result["created"])
            self.assertFalse(retry_result["created"])
            document = json.loads(queue.read_text(encoding="utf-8"))
            handoff = document["handoffs"][0]
            self.assertEqual(len(handoff["receipts"]), 3)
            self.assertEqual(handoff["terminalResultStatus"], "done")

            with self.assertRaises(subject.HandoffReceiptError):
                subject.record_receipt(
                    queue,
                    kind="acknowledged",
                    agent="jain",
                    handoff_id="event-handoff-1",
                    event_id="event-wrong-agent",
                    task_id="task-handoff-1",
                )

    def test_read_only_report_links_exact_task_and_ledger_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            queue = root / "handoff-queue.json"
            tasks = root / "agent-task-queue.json"
            db = root / "control-tower-work.sqlite3"
            hot = root / "control-tower-hot.json"
            store = control_tower_work_store.WorkStore(db, hot)
            event = store.publish({
                "kind": "start",
                "event_id": "event-handoff-1",
                "work_id": "work-handoff-1",
                "run_id": "run-handoff-1",
                "generation": 1,
                "sequence": 1,
                "agent": "joshex",
                "objective": "Safe work ledger objective",
                "phase": "delegating",
                "tool": "agent_task.py",
                "origin": "agent-task",
                "origin_claim_hash": "a" * 64,
            })
            self.assertEqual(event["event"]["eventId"], "event-handoff-1")
            subject.write_new_handoff(queue, self.record())
            tasks.write_text(
                json.dumps({
                    "tasks": [{
                        "id": "task-handoff-1",
                        "workId": "work-handoff-1",
                        "runId": "run-handoff-1",
                        "originClaimHash": "a" * 64,
                        "status": "queued",
                        "title": "PRIVATE-TITLE-MUST-NOT-ENTER-REPORT",
                        "objective": "PRIVATE-OBJECTIVE-MUST-NOT-ENTER-REPORT",
                    }]
                }),
                encoding="utf-8",
            )
            queue_before = queue.read_bytes()
            tasks_before = tasks.read_bytes()
            db_before = db.read_bytes()

            report = subject.build_reconciliation_report(
                queue,
                tasks,
                db,
                generated_at="2026-07-18T16:10:00Z",
                include_row_identifiers=True,
            )

            self.assertEqual(queue.read_bytes(), queue_before)
            self.assertEqual(tasks.read_bytes(), tasks_before)
            self.assertEqual(db.read_bytes(), db_before)
            self.assertEqual(report["mode"], "read-only-reconciliation")
            self.assertEqual(report["counts"]["canonicalIdentity"], 1)
            self.assertEqual(report["counts"]["exactTaskMatches"], 1)
            self.assertEqual(report["counts"]["exactLedgerMatches"], 1)
            self.assertEqual(report["rows"][0]["ledgerMatch"], "exact")
            rendered = json.dumps(report)
            self.assertNotIn("PRIVATE-TITLE", rendered)
            self.assertNotIn("PRIVATE-OBJECTIVE", rendered)
            self.assertNotIn("Dashboard-safe handoff detail", rendered)
            self.assertNotIn("a" * 64, rendered)
            aggregate = subject.build_reconciliation_report(
                queue,
                tasks,
                db,
                generated_at="2026-07-18T16:10:00Z",
            )
            self.assertNotIn("rows", aggregate)
            aggregate_rendered = json.dumps(aggregate)
            self.assertNotIn("event-handoff-1", aggregate_rendered)
            self.assertNotIn("task-handoff-1", aggregate_rendered)

    def test_privacy_safe_golden_receipt_contract(self) -> None:
        prepared = subject.attach_sender_receipt(self.record())
        allowed = {
            "id", "kind", "agent", "recordedAt", "eventId", "taskId",
            "workId", "runId", "status",
        }
        receipt = prepared["receipts"][0]
        self.assertLessEqual(set(receipt), allowed)
        rendered = json.dumps(receipt)
        for forbidden in (
            "title", "detail", "objective", "prompt", "chatId", "messageId",
            "oauth", "cookie", "password", "PRIVATE-SECRET",
        ):
            self.assertNotIn(forbidden, rendered)
        with self.assertRaises(subject.HandoffReceiptError):
            subject.deterministic_receipt_id(
                handoff_id="unsafe id with spaces",
                kind="acknowledged",
                agent="jaimes",
            )
        with self.assertRaises(subject.HandoffReceiptError):
            subject.attach_sender_receipt(
                self.record(originClaimHash="raw-telegram-message-123")
            )

    def test_report_uses_explicit_legacy_task_id_without_fuzzy_matching(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            queue = root / "handoff-queue.json"
            tasks = root / "agent-task-queue.json"
            queue.write_text(json.dumps({"handoffs": [{
                "id": "legacy-handoff-1",
                "receivingTaskId": "task-legacy-1",
                "title": "Wording intentionally unrelated to the task",
                "status": "done",
            }]}), encoding="utf-8")
            tasks.write_text(json.dumps({"tasks": [{
                "id": "task-legacy-1",
                "title": "Different wording",
                "status": "done",
            }]}), encoding="utf-8")
            report = subject.build_reconciliation_report(
                queue,
                tasks,
                generated_at="2026-07-18T16:10:00Z",
                include_row_identifiers=True,
            )
        self.assertEqual(report["counts"]["exactTaskMatches"], 1)
        self.assertEqual(report["counts"]["exactTaskIdMatches"], 1)
        self.assertEqual(report["counts"]["exactWorkIdMatches"], 0)
        self.assertEqual(report["rows"][0]["taskMatch"], "exact-task-id")

    def test_concurrent_ack_retries_create_one_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            queue = Path(raw) / "handoff-queue.json"
            subject.write_new_handoff(queue, self.record())
            command = [
                sys.executable,
                "-B",
                str(Path(subject.__file__)),
                "--handoff-path", str(queue),
                "acknowledge",
                "--agent", "jaimes",
                "--work-id", "work-handoff-1",
                "--run-id", "run-handoff-1",
                "--origin-claim-hash", "a" * 64,
                "--event-id", "event-ack-concurrent",
                "--task-id", "task-handoff-1",
            ]

            def invoke(_index: int) -> dict:
                result = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                return json.loads(result.stdout)

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(invoke, range(8)))
            self.assertEqual(sum(bool(result["created"]) for result in results), 1)
            document = json.loads(queue.read_text(encoding="utf-8"))
            kinds = [row["kind"] for row in document["handoffs"][0]["receipts"]]
            self.assertEqual(kinds, ["sent", "acknowledged"])


if __name__ == "__main__":
    unittest.main()
