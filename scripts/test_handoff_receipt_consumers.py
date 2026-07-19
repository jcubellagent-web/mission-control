#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_context_registry
import update_mission_control


def receipt(receipt_id: str, status: str) -> dict[str, str]:
    return {
        "id": receipt_id,
        "kind": "terminal",
        "agent": "jaimes",
        "status": status,
        "recordedAt": "2026-07-18T16:00:00Z",
    }


class HandoffReceiptConsumerTests(unittest.TestCase):
    def test_dashboard_prefers_receipts_and_exact_work_ids_over_legacy_text(self) -> None:
        handoffs = [{
            "id": "h-done",
            "workId": "work-done-receipt",
            "status": "open",
            "privacy": "dashboard-safe",
            "receipts": [receipt("hreceipt-result-done", "done")],
        }, {
            "id": "h-blocked",
            "workId": "work-blocked-receipt",
            "status": "open",
            "privacy": "dashboard-safe",
            "receipts": [receipt("hreceipt-result-blocked", "blocked")],
        }, {
            "id": "h-canonical-no-fuzzy",
            "workId": "work-still-open",
            "title": "Contains task-closed but belongs to different exact work",
            "status": "open",
            "privacy": "dashboard-safe",
        }, {
            "id": "h-exact-task",
            "workId": "work-closed",
            "status": "open",
            "privacy": "dashboard-safe",
        }, {
            "id": "h-blocked-receipt-cancelled-task",
            "workId": "work-cancelled-after-block",
            "status": "open",
            "privacy": "dashboard-safe",
            "receipts": [receipt("hreceipt-result-old-block", "blocked")],
        }]
        tasks = [{
            "id": "task-closed",
            "workId": "work-closed",
            "status": "done",
        }, {
            "id": "task-cancelled-after-block",
            "workId": "work-cancelled-after-block",
            "status": "cancelled",
        }]

        def fake_load(path: Path, default):
            if path == update_mission_control.HANDOFF_QUEUE_PATH:
                return {"handoffs": handoffs}
            if path == update_mission_control.AGENT_TASK_QUEUE_PATH:
                return {"tasks": tasks}
            return default

        with (
            mock.patch.object(update_mission_control, "load_json_file", side_effect=fake_load),
            mock.patch.object(update_mission_control, "fetch_shared_events", return_value=[]),
        ):
            shared = update_mission_control.fetch_shared_operating_layer(
                "2026-07-18T16:05:00Z"
            )
        self.assertEqual(shared["counts"]["openHandoffs"], 2)
        self.assertEqual(shared["counts"]["attentionHandoffs"], 1)
        by_id = {row["id"]: row for row in shared["openHandoffs"]}
        self.assertNotIn("h-done", by_id)
        self.assertNotIn("h-exact-task", by_id)
        self.assertNotIn("h-blocked-receipt-cancelled-task", by_id)
        self.assertEqual(by_id["h-blocked"]["status"], "blocked")
        self.assertEqual(
            by_id["h-blocked"]["receiptState"]["terminalResultReceiptId"],
            "hreceipt-result-blocked",
        )
        self.assertIn("h-canonical-no-fuzzy", by_id)

    def test_context_registry_excludes_closed_and_private_handoffs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            data_dir = Path(raw)
            payload = {"handoffs": [{
                "id": "h-done",
                "from": "joshex",
                "to": "jaimes",
                "status": "open",
                "privacy": "dashboard-safe",
                "receipts": [receipt("hreceipt-result-done", "done")],
            }, {
                "id": "h-blocked",
                "workId": "work-blocked",
                "runId": "run-blocked",
                "from": "joshex",
                "to": "jaimes",
                "status": "open",
                "privacy": "dashboard-safe",
                "receipts": [receipt("hreceipt-result-blocked", "blocked")],
            }, {
                "id": "h-private",
                "from": "joshex",
                "to": "jaimes",
                "status": "open",
                "privacy": "agent-private",
            }]}
            (data_dir / "handoff-queue.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with mock.patch.object(agent_context_registry, "DATA_DIR", data_dir):
                rows = agent_context_registry.handoff_rows("jaimes")
        self.assertEqual([row["id"] for row in rows], ["h-blocked"])
        self.assertEqual(rows[0]["status"], "blocked")
        self.assertEqual(rows[0]["workId"], "work-blocked")
        self.assertEqual(
            rows[0]["receiptState"]["terminalResultReceiptId"],
            "hreceipt-result-blocked",
        )


if __name__ == "__main__":
    unittest.main()
