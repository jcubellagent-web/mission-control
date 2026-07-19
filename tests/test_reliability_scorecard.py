from __future__ import annotations

import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "reliability_scorecard.py"
SPEC = importlib.util.spec_from_file_location("reliability_scorecard", MODULE_PATH)
assert SPEC and SPEC.loader
scorecard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scorecard)


NOW = dt.datetime(2026, 7, 18, 21, 0, tzinfo=dt.timezone.utc)


class ReliabilityScorecardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, name: str, payload: object) -> None:
        (self.data_dir / name).write_text(json.dumps(payload), encoding="utf-8")

    def rows(self, payload: dict) -> dict[str, dict]:
        return {row["id"]: row for row in payload["items"]}

    def test_missing_sources_never_render_as_ready(self) -> None:
        payload = scorecard.build_scorecard(self.data_dir, NOW)
        self.assertTrue(all(row["status"] == "watch" for row in payload["items"]))
        self.assertIn("0 ready", payload["summary"])
        self.assertFalse(payload["policy"]["compositeScore"])

    def test_ignored_feedback_is_not_counted_as_quality(self) -> None:
        self.write(
            "memory-operations.json",
            {
                "retrieval": {
                    "helpful30d": 32,
                    "ignored30d": 35,
                    "corrected30d": 0,
                    "harmful30d": 0,
                }
            },
        )
        payload = scorecard.build_scorecard(self.data_dir, NOW)
        row = self.rows(payload)["memory-reuse"]
        self.assertEqual("watch", row["status"])
        self.assertIn("47.8%", row["signal"])
        metric = next(value for value in payload["metrics"] if value["label"] == "Helpful reuse")
        self.assertEqual("47.8%", metric["value"])

    def test_legacy_handoffs_are_not_rewritten_or_claimed_traceable(self) -> None:
        self.write(
            "handoff-queue.json",
            {"handoffs": [{"id": "legacy-1", "status": "done", "receivingTaskId": "task-1"}]},
        )
        payload = scorecard.build_scorecard(self.data_dir, NOW)
        row = self.rows(payload)["traceable-handoffs"]
        self.assertEqual("watch", row["status"])
        self.assertIn("historical", row["signal"])
        self.assertIn("1 legacy", next(v for v in payload["metrics"] if v["label"] == "Receipt-complete")["detail"])

    def test_modern_terminal_handoff_requires_terminal_receipt(self) -> None:
        incomplete = {
            "id": "h-1",
            "status": "done",
            "workId": "w-1",
            "runId": "r-1",
            "senderEventId": "send-1",
            "recipientAckId": "ack-1",
        }
        self.write("handoff-queue.json", {"handoffs": [incomplete]})
        payload = scorecard.build_scorecard(self.data_dir, NOW)
        self.assertEqual("watch", self.rows(payload)["traceable-handoffs"]["status"])
        complete = dict(incomplete, terminalResultReceiptId="terminal-1")
        self.write("handoff-queue.json", {"handoffs": [complete]})
        payload = scorecard.build_scorecard(self.data_dir, NOW)
        self.assertEqual("ready", self.rows(payload)["traceable-handoffs"]["status"])

    def test_canonical_receipt_bridge_fields_are_counted(self) -> None:
        self.write(
            "handoff-queue.json",
            {"handoffs": [{
                "workId": "w-canonical",
                "runId": "r-canonical",
                "senderReceiptId": "send-canonical",
                "recipientAckReceiptId": "ack-canonical",
                "terminalResultReceiptId": "terminal-canonical",
                "terminalResultStatus": "done",
            }]},
        )
        payload = scorecard.build_scorecard(self.data_dir, NOW)
        row = self.rows(payload)["traceable-handoffs"]
        self.assertEqual("ready", row["status"])
        self.assertIn("1/1", row["signal"])

    def test_stale_green_sources_are_downgraded(self) -> None:
        self.write(
            "telegram-inbox-qa.json",
            {
                "updatedAt": "2026-07-11T20:00:00Z",
                "lanes": {"stress": {"lastSample": {"checkedAt": "2026-07-11T20:00:00Z", "problemCount": 0, "stress": {"renderedCards": 100, "problemCount": 0}}}},
                "rolling": {"contractStress": {"samples": 30, "minimumSamples": 20, "p95Ms": 100, "sloMs": 2000, "status": "ok"}},
            },
        )
        self.write(
            "ecosystem-qa-scheduler.json",
            {"jobs": {"runtime-service-probe": {"status": "ok", "completedAt": "2026-07-11T20:00:00Z", "failureStreak": 0}}},
        )
        payload = scorecard.build_scorecard(self.data_dir, NOW)
        rows = self.rows(payload)
        self.assertEqual("watch", rows["telegram-final-contract"]["status"])
        self.assertEqual("watch", rows["runtime-recovery"]["status"])

    def test_unverified_completion_cannot_hide_behind_zero_mismatches(self) -> None:
        self.write(
            "jaimes-completion-evidence.json",
            {
                "checkedAt": "2026-07-18T20:56:00Z",
                "ok": True,
                "status": "ok",
                "mismatches": 0,
                "unverifiedCompletions": 1,
                "finalMessagesLinked": 2,
                "finalMessagesRequired": 2,
            },
        )
        payload = scorecard.build_scorecard(self.data_dir, NOW)
        row = self.rows(payload)["verified-completion"]
        self.assertEqual("watch", row["status"])
        self.assertIn("1 mismatch", row["signal"])

    def test_completion_requires_a_sample_and_labels_legacy_history_truthfully(self) -> None:
        self.write(
            "jaimes-completion-evidence.json",
            {
                "checkedAt": "2026-07-18T20:56:00Z",
                "ok": False,
                "status": "watch",
                "completedRuns": 2,
                "identityBoundRuns": 0,
                "finalMessagesRequired": 0,
                "finalMessagesLinked": 0,
                "deliveryVerifiedRuns": 0,
                "mismatches": 0,
                "unverifiedCompletions": 0,
            },
        )
        payload = scorecard.build_scorecard(self.data_dir, NOW)
        row = self.rows(payload)["verified-completion"]
        self.assertEqual("watch", row["status"])
        self.assertIn("predate exact work/run binding", row["signal"])

        self.write(
            "jaimes-completion-evidence.json",
            {
                "checkedAt": "2026-07-18T20:56:00Z",
                "ok": True,
                "status": "ok",
                "completedRuns": 0,
                "identityBoundRuns": 0,
                "finalMessagesRequired": 0,
                "finalMessagesLinked": 0,
                "mismatches": 0,
            },
        )
        payload = scorecard.build_scorecard(self.data_dir, NOW)
        self.assertEqual("watch", self.rows(payload)["verified-completion"]["status"])

    def test_all_supported_controls_can_be_independently_ready(self) -> None:
        self.write(
            "reliability-reuse-eval.json",
            {"checkedAt": "2026-07-18T20:55:00Z", "ok": True, "status": "ok", "checksPassed": 12, "checksTotal": 12},
        )
        self.write(
            "handoff-queue.json",
            {"handoffs": [{"status": "done", "workId": "w", "runId": "r", "receiptId": "s", "recipientAckId": "a", "terminalReceiptId": "t"}]},
        )
        self.write(
            "memory-operations.json",
            {
                "privacy": {"checkedAt": "2026-07-18T20:55:00Z", "unsafeShared": 0},
                "retrieval": {"helpful30d": 18, "ignored30d": 2, "corrected30d": 0, "harmful30d": 0, "selected30d": 4, "used30d": 3},
            },
        )
        self.write(
            "telegram-inbox-qa.json",
            {
                "updatedAt": "2026-07-18T20:55:00Z",
                "lanes": {"stress": {"lastSample": {"checkedAt": "2026-07-18T20:55:00Z", "problemCount": 0, "stress": {"renderedCards": 1100, "problemCount": 0}}}},
                "rolling": {"contractStress": {"samples": 30, "minimumSamples": 20, "p95Ms": 1700, "sloMs": 2000, "status": "ok"}},
            },
        )
        self.write(
            "ecosystem-qa-scheduler.json",
            {"jobs": {"runtime-service-probe": {"status": "ok", "completedAt": "2026-07-18T20:56:00Z", "failureStreak": 0}}},
        )
        self.write(
            "completion-evidence.json",
            {"checkedAt": "2026-07-18T20:56:00Z", "ok": True, "status": "ok", "mismatches": 0, "finalMessagesLinked": 2, "finalMessagesRequired": 2},
        )
        payload = scorecard.build_scorecard(self.data_dir, NOW)
        self.assertTrue(all(row["status"] == "ready" for row in payload["items"]))
        self.assertEqual(6, len(payload["items"]))
        self.assertEqual(5, len(payload["metrics"]))


if __name__ == "__main__":
    unittest.main()
