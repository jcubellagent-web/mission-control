from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "reliability_reuse_eval.py"
SPEC = importlib.util.spec_from_file_location("reliability_reuse_eval", MODULE_PATH)
assert SPEC and SPEC.loader
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)

NOW = dt.datetime(2026, 7, 18, 21, 0, tzinfo=dt.timezone.utc)
STAMP = subject.iso(NOW - dt.timedelta(minutes=2))


class ReliabilityReuseEvalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="reliability-reuse-eval-")
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)

    def write(self, name: str, payload: object) -> None:
        (self.data_dir / name).write_text(json.dumps(payload), encoding="utf-8")

    def write_happy_sources(self) -> None:
        self.write(
            "memory-operations.json",
            {
                "updatedAt": STAMP,
                "privacy": {"checkedAt": STAMP, "unsafeShared": 0},
                "retrieval": {
                    "helpful30d": 18,
                    "ignored30d": 2,
                    "corrected30d": 0,
                    "harmful30d": 0,
                    "selected30d": 10,
                    "used30d": 8,
                },
            },
        )
        self.write(
            "handoff-queue.json",
            {
                "reconciledAt": STAMP,
                "handoffs": [
                    {
                        "status": "done",
                        "workId": "work-1",
                        "runId": "run-1",
                        "receiptId": "send-1",
                        "recipientAckId": "ack-1",
                        "terminalReceiptId": "terminal-1",
                    }
                ],
            },
        )
        self.write(
            "completion-evidence.json",
            {
                "checkedAt": STAMP,
                "ok": True,
                "status": "ok",
                "finalMessagesRequired": 2,
                "finalMessagesLinked": 2,
                "verifiedCompletions": 2,
                "mismatches": 0,
            },
        )
        self.write(
            "telegram-inbox-qa.json",
            {
                "updatedAt": STAMP,
                "status": "ok",
                "privacy": {
                    "dashboardSafe": True,
                    "messageIdsIncluded": False,
                    "rawPromptsIncluded": False,
                },
                "coverage": {"recurringProductionWrites": False},
                "rolling": {
                    "contractStress": {
                        "samples": 30,
                        "minimumSamples": 20,
                        "p95Ms": 1500,
                        "sloMs": 2000,
                        "status": "ok",
                    }
                },
                "lanes": {
                    "stress": {
                        "lastSample": {
                            "checkedAt": STAMP,
                            "ok": True,
                            "problemCount": 0,
                            "stress": {"renderedCards": 1100, "problemCount": 0},
                        }
                    }
                },
            },
        )
        self.write(
            "ecosystem-qa-scheduler.json",
            {
                "checkedAt": STAMP,
                "jobs": {
                    "runtime-service-probe": {
                        "status": "ok",
                        "returncode": 0,
                        "completedAt": STAMP,
                        "failureStreak": 0,
                    }
                },
            },
        )
        self.write(
            "recovery-proof.json",
            {
                "checkedAt": STAMP,
                "ok": True,
                "status": "ok",
                "recoverySeconds": 45,
                "restartAttempts": 1,
                "cleanProbes": 2,
            },
        )

    @staticmethod
    def rows(payload: dict) -> dict[str, dict]:
        return {row["id"]: row for row in payload["gates"]}

    def test_all_six_gates_pass_independently_without_a_composite_score(self) -> None:
        self.write_happy_sources()
        payload = subject.build_evaluation(self.data_dir, root=ROOT, now=NOW)
        self.assertEqual("pass", payload["status"])
        self.assertTrue(payload["ok"])
        self.assertEqual({"pass": 6, "watch": 0, "fail": 0}, payload["stateCounts"])
        self.assertEqual(6, payload["checksPassed"])
        self.assertEqual(list(subject.GATE_IDS), [row["id"] for row in payload["gates"]])
        self.assertFalse(payload["policy"]["compositeScore"])
        self.assertEqual("worst-state only", payload["policy"]["aggregation"])
        self.assertEqual([], subject.validate_output(payload))

    def test_missing_is_watch_malformed_is_fail_and_neither_infers_success(self) -> None:
        missing = subject.build_evaluation(self.data_dir, root=ROOT, now=NOW)
        rows = self.rows(missing)
        for gate_id in subject.GATE_IDS[:-1]:
            self.assertEqual("watch", rows[gate_id]["state"])
        self.assertEqual("pass", rows["scorecard-semantics"]["state"])
        self.assertFalse(missing["ok"])

        marker = "private-prompt-super-secret-marker"
        (self.data_dir / "memory-operations.json").write_text("{not-json " + marker, encoding="utf-8")
        malformed = subject.build_evaluation(self.data_dir, root=ROOT, now=NOW)
        self.assertEqual("fail", self.rows(malformed)["memory-privacy-reuse"]["state"])
        self.assertNotIn(marker, json.dumps(malformed))

        (self.data_dir / "memory-operations.json").write_text(
            '{"updatedAt":"2026-07-18T20:58:00Z","retrieval":{"helpful30d":NaN}}',
            encoding="utf-8",
        )
        non_finite = subject.build_evaluation(self.data_dir, root=ROOT, now=NOW)
        self.assertEqual("fail", self.rows(non_finite)["memory-privacy-reuse"]["state"])

    def test_memory_gate_uses_explicit_outcomes_privacy_and_selected_to_used(self) -> None:
        self.write_happy_sources()
        memory = json.loads((self.data_dir / "memory-operations.json").read_text())
        memory["retrieval"].update({"helpful30d": 8, "ignored30d": 12})
        self.write("memory-operations.json", memory)
        row = self.rows(subject.build_evaluation(self.data_dir, root=ROOT, now=NOW))["memory-privacy-reuse"]
        self.assertEqual("fail", row["state"])
        self.assertEqual(40.0, row["counts"]["helpfulRatePct"])
        self.assertIn("helpful-reuse-below-threshold", row["reasonCodes"])

        memory["retrieval"].update({"helpful30d": 20, "ignored30d": 0, "used30d": 11})
        memory["privacy"]["unsafeShared"] = 1
        self.write("memory-operations.json", memory)
        row = self.rows(subject.build_evaluation(self.data_dir, root=ROOT, now=NOW))["memory-privacy-reuse"]
        self.assertEqual("fail", row["state"])
        self.assertIn("used-exceeds-selected", row["reasonCodes"])
        self.assertIn("unsafe-shared-memory-observed", row["reasonCodes"])

    def test_handoff_gate_preserves_legacy_and_requires_terminal_receipts(self) -> None:
        self.write_happy_sources()
        self.write(
            "handoff-queue.json",
            {"reconciledAt": STAMP, "handoffs": [{"id": "legacy", "status": "done"}]},
        )
        legacy = self.rows(subject.build_evaluation(self.data_dir, root=ROOT, now=NOW))["handoff-receipts"]
        self.assertEqual("watch", legacy["state"])
        self.assertEqual(1, legacy["counts"]["legacy"])

        self.write(
            "handoff-queue.json",
            {
                "reconciledAt": STAMP,
                "handoffs": [{
                    "status": "done", "workId": "w", "runId": "r",
                    "receiptId": "send", "recipientAckId": "ack",
                }],
            },
        )
        incomplete = self.rows(subject.build_evaluation(self.data_dir, root=ROOT, now=NOW))["handoff-receipts"]
        self.assertEqual("fail", incomplete["state"])
        self.assertIn("terminal-receipt-missing", incomplete["reasonCodes"])

    def test_handoff_gate_reads_canonical_receipt_bridge_fields(self) -> None:
        self.write_happy_sources()
        self.write(
            "handoff-queue.json",
            {
                "reconciledAt": STAMP,
                "handoffs": [{
                    "workId": "w-canonical",
                    "runId": "r-canonical",
                    "senderReceiptId": "send-canonical",
                    "recipientAckReceiptId": "ack-canonical",
                    "terminalResultReceiptId": "terminal-canonical",
                    "terminalResultStatus": "done",
                }],
            },
        )
        row = self.rows(subject.build_evaluation(self.data_dir, root=ROOT, now=NOW))["handoff-receipts"]
        self.assertEqual("pass", row["state"])
        self.assertEqual(1, row["counts"]["receiptComplete"])
        self.assertEqual(1, row["counts"]["terminalModern"])
        self.assertEqual(1, row["counts"]["terminalLinked"])

    def test_missing_ack_does_not_misreport_a_present_terminal_receipt(self) -> None:
        self.write_happy_sources()
        self.write(
            "handoff-queue.json",
            {
                "reconciledAt": STAMP,
                "handoffs": [{
                    "workId": "w-blocked",
                    "runId": "r-blocked",
                    "senderReceiptId": "send-blocked",
                    "terminalResultReceiptId": "terminal-blocked",
                    "terminalResultStatus": "blocked",
                }],
            },
        )
        row = self.rows(subject.build_evaluation(self.data_dir, root=ROOT, now=NOW))["handoff-receipts"]
        self.assertEqual("fail", row["state"])
        self.assertIn("receipt-chain-incomplete", row["reasonCodes"])
        self.assertNotIn("terminal-receipt-missing", row["reasonCodes"])
        self.assertEqual(1, row["counts"]["terminalLinked"])

    def test_completion_gate_binds_evidence_and_final_to_exact_work_run(self) -> None:
        self.write_happy_sources()
        self.write(
            "completion-evidence.json",
            {
                "checkedAt": STAMP,
                "ok": True,
                "records": [{
                    "workId": "work-current",
                    "runId": "run-current",
                    "evidence": {
                        "workId": "work-old",
                        "runId": "run-old",
                        "receiptId": "evidence-1",
                    },
                    "delivery": {
                        "workId": "work-current",
                        "runId": "run-current",
                        "finalMessageId": "final-1",
                    },
                }],
            },
        )
        row = self.rows(subject.build_evaluation(self.data_dir, root=ROOT, now=NOW))["completion-final-linkage"]
        self.assertEqual("fail", row["state"])
        self.assertEqual(1, row["counts"]["mismatches"])
        self.assertIn("work-run-evidence-mismatch", row["reasonCodes"])
        rendered = json.dumps(row)
        self.assertNotIn("work-current", rendered)
        self.assertNotIn("work-old", rendered)
        self.assertNotIn("final-1", rendered)

    def test_completion_gate_accepts_counts_only_sidecar_and_treats_legacy_as_watch(self) -> None:
        self.write_happy_sources()
        ready = {
            "checkedAt": STAMP,
            "ok": True,
            "status": "ok",
            "completedRuns": 2,
            "finalMessagesRequired": 2,
            "finalMessagesLinked": 2,
            "deliveryVerifiedRuns": 2,
            "mismatches": 0,
            "unverifiedCompletions": 0,
        }
        self.write("jaimes-completion-evidence.json", ready)
        (self.data_dir / "completion-evidence.json").unlink()
        row = self.rows(subject.build_evaluation(self.data_dir, root=ROOT, now=NOW))["completion-final-linkage"]
        self.assertEqual("pass", row["state"])
        self.assertEqual(2, row["counts"]["evidenceVerified"])

        legacy = dict(
            ready,
            ok=False,
            status="watch",
            finalMessagesRequired=0,
            finalMessagesLinked=0,
            deliveryVerifiedRuns=0,
        )
        self.write("jaimes-completion-evidence.json", legacy)
        row = self.rows(subject.build_evaluation(self.data_dir, root=ROOT, now=NOW))["completion-final-linkage"]
        self.assertEqual("watch", row["state"])
        self.assertIn("completion-source-declared-watch", row["reasonCodes"])

    def test_telegram_and_recovery_require_fresh_contract_and_drill_proof(self) -> None:
        self.write_happy_sources()
        telegram = json.loads((self.data_dir / "telegram-inbox-qa.json").read_text())
        telegram["lanes"]["stress"]["lastSample"]["stress"]["problemCount"] = 2
        telegram["lanes"]["stress"]["lastSample"]["problemCount"] = 2
        telegram["privacy"]["rawPromptsIncluded"] = True
        self.write("telegram-inbox-qa.json", telegram)
        recovery = json.loads((self.data_dir / "recovery-proof.json").read_text())
        recovery.update({"recoverySeconds": 61, "restartAttempts": 2, "cleanProbes": 1})
        self.write("recovery-proof.json", recovery)
        rows = self.rows(subject.build_evaluation(self.data_dir, root=ROOT, now=NOW))
        self.assertEqual("fail", rows["telegram-contract"]["state"])
        self.assertIn("telegram-contract-violations", rows["telegram-contract"]["reasonCodes"])
        self.assertIn("telegram-privacy-contract-unproven", rows["telegram-contract"]["reasonCodes"])
        self.assertEqual("fail", rows["recovery-proof"]["state"])
        self.assertIn("recovery-time-over-slo", rows["recovery-proof"]["reasonCodes"])
        self.assertIn("restart-attempt-limit-exceeded", rows["recovery-proof"]["reasonCodes"])

    def test_scorecard_semantics_and_optional_contract_tests_are_bounded(self) -> None:
        self.write_happy_sources()
        contract = subject.run_contract_checks(ROOT, 10)
        self.assertEqual("pass", contract["state"])
        self.assertGreater(contract["tests"], 0)
        payload = subject.build_evaluation(
            self.data_dir, root=ROOT, now=NOW, contract_checks=contract,
        )
        row = self.rows(payload)["scorecard-semantics"]
        self.assertEqual("pass", row["state"])
        self.assertEqual(row["counts"]["semanticChecks"], row["counts"]["semanticPassed"])

        with mock.patch.object(subject.subprocess, "run", side_effect=subprocess.TimeoutExpired(["test"], 1)):
            timed_out = subject.run_contract_checks(ROOT, 1)
        self.assertTrue(timed_out["timedOut"])
        timeout_payload = subject.build_evaluation(
            self.data_dir, root=ROOT, now=NOW, contract_checks=timed_out,
        )
        timeout_row = self.rows(timeout_payload)["scorecard-semantics"]
        self.assertEqual("fail", timeout_row["state"])
        self.assertIn("scorecard-contract-tests-timeout", timeout_row["reasonCodes"])

    def test_cli_check_is_read_only_deterministic_and_private(self) -> None:
        self.write_happy_sources()
        output = self.data_dir / "must-not-write.json"
        process = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "--data-dir", str(self.data_dir),
                "--output", str(output),
                "--as-of", subject.iso(NOW),
                "--check",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        summary = json.loads(process.stdout)
        self.assertEqual(subject.iso(NOW), summary["checkedAt"])
        self.assertEqual(6, summary["checksTotal"])
        self.assertFalse(output.exists())
        self.assertNotIn("objective", json.dumps(summary).lower())

    def test_default_cli_atomically_emits_dashboard_safe_artifact(self) -> None:
        self.write_happy_sources()
        output = self.data_dir / "reliability-reuse-eval.json"
        process = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "--data-dir", str(self.data_dir),
                "--as-of", subject.iso(NOW),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertTrue(output.is_file())
        payload = json.loads(output.read_text())
        self.assertEqual("pass", payload["status"])
        self.assertEqual([], subject.validate_output(payload))
        self.assertEqual("pass", json.loads(process.stdout)["status"])


if __name__ == "__main__":
    unittest.main()
