from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "remote_qa_sidecar_ingest.py"
SPEC = importlib.util.spec_from_file_location("remote_qa_sidecar_ingest", MODULE_PATH)
assert SPEC and SPEC.loader
ingest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ingest)


NOW = dt.datetime(2026, 7, 18, 21, 30, tzinfo=dt.timezone.utc)


def blackbox_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "owner": "jaimes",
        "team": "Independent Control Tower black-box QA",
        "privacy": "dashboard-safe",
        "checkedAt": "2026-07-18T21:29:00Z",
        "status": "ok",
        "ok": True,
        "issues": [],
        "metrics": {"latencyMs": 421.0, "sourceAgeMinutes": 1.5, "generatedAgeMinutes": 1.5},
        "contract": "Read-only HTTP verification; JAIMES never writes Josh 2.0 canonical dashboard data.",
    }
    payload.update(updates)
    return payload


def completion_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "owner": "jaimes",
        "privacy": "dashboard-safe",
        "checkedAt": "2026-07-18T21:29:00Z",
        "status": "ok",
        "ok": True,
        "scope": "counts-only completed work-card audit over the last 24 hours",
        "completedRuns": 2,
        "identityBoundRuns": 2,
        "finalMessagesRequired": 2,
        "finalMessagesLinked": 2,
        "deliveryVerifiedRuns": 2,
        "mismatches": 0,
        "unverifiedCompletions": 0,
        "staleEvidenceDetected": 0,
        "issues": [],
        "contentPolicy": "No task IDs, message IDs, objectives, prompts, account data, or raw evidence leave JAIMES.",
    }
    payload.update(updates)
    return payload


class RemoteQaSidecarValidationTests(unittest.TestCase):
    def test_accepts_current_blackbox_and_completion_watch_status(self) -> None:
        watch = completion_payload(
            status="watch",
            ok=False,
            completedRuns=0,
            identityBoundRuns=0,
            finalMessagesRequired=0,
            finalMessagesLinked=0,
            deliveryVerifiedRuns=0,
            issues=["no-recent-completed-samples"],
        )
        self.assertEqual([], ingest.validate_blackbox(blackbox_payload(), now=NOW))
        self.assertEqual([], ingest.validate_completion(watch, now=NOW))

    def test_rejects_stale_and_future_timestamps(self) -> None:
        stale = completion_payload(checkedAt="2026-07-18T20:59:59Z")
        future = completion_payload(checkedAt="2026-07-18T21:32:01Z")
        self.assertIn("sidecar is older than 30 minutes", ingest.validate_completion(stale, now=NOW))
        self.assertIn(
            "sidecar checkedAt is more than 2 minutes in the future",
            ingest.validate_completion(future, now=NOW),
        )

    def test_rejects_identifiers_raw_private_keys_and_unallowlisted_fields(self) -> None:
        payload = completion_payload()
        payload["workId"] = "must-not-cross-hosts"
        payload["rawEvidence"] = {"privateNotes": "must-not-cross-hosts"}
        issues = ingest.validate_completion(payload, now=NOW)
        self.assertIn("forbidden raw/private-content keys detected", issues)
        self.assertTrue(any("non-allowlisted fields" in issue for issue in issues))

    def test_rejects_non_integer_or_inconsistent_completion_aggregates(self) -> None:
        payload = completion_payload(
            completedRuns=1,
            identityBoundRuns=1,
            finalMessagesRequired=1,
            finalMessagesLinked=2,
            mismatches=True,
        )
        issues = ingest.validate_completion(payload, now=NOW)
        self.assertIn(
            "completion aggregate mismatches must be a bounded non-negative integer",
            issues,
        )
        self.assertIn("completion aggregates violate required count ordering", issues)

    def test_rejects_arbitrary_issue_text_and_unbounded_blackbox_fields(self) -> None:
        payload = blackbox_payload(
            status="attention",
            ok=False,
            issues=["failure included /Users/private/account.txt"],
            metrics={
                "latencyMs": 421.0,
                "sourceAgeMinutes": 1.5,
                "generatedAgeMinutes": 1.5,
                "privatePayload": 1,
            },
        )
        issues = ingest.validate_blackbox(payload, now=NOW)
        self.assertIn("blackbox issues must use fixed dashboard-safe templates", issues)
        self.assertIn("blackbox metrics must contain only allowlisted aggregate fields", issues)
        self.assertIn("forbidden raw/private-content keys detected", issues)

    def test_accepts_current_today_jobs_contract_issue_template(self) -> None:
        payload = blackbox_payload(
            status="attention",
            ok=False,
            issues=["live payload missing fields: todayJobs"],
        )
        self.assertEqual([], ingest.validate_blackbox(payload, now=NOW))


class RemoteQaSidecarPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name)
        self.status_path = self.data_dir / "remote-qa-ingest-status.json"
        self.specs = ingest.build_specs(
            data_dir=self.data_dir,
            blackbox_remote="/remote/blackbox.json",
            completion_remote="/remote/completion.json",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fake_fetch(self, payloads: dict[str, tuple[object, list[str]]]):
        def fetch(_host: str, remote: str, _candidate: Path) -> tuple[object, list[str]]:
            payload, issues = payloads[remote]
            return copy.deepcopy(payload), list(issues)

        return fetch

    def test_valid_sources_are_promoted_and_status_is_aggregate_only(self) -> None:
        fake = self.fake_fetch(
            {
                "/remote/blackbox.json": (blackbox_payload(), []),
                "/remote/completion.json": (completion_payload(), []),
            }
        )
        with mock.patch.object(ingest, "fetch_payload", side_effect=fake):
            status = ingest.ingest_sources("jaimes-lan", self.specs, self.status_path, now=NOW)
        self.assertTrue(status["ok"])
        self.assertTrue(status["sources"]["blackbox"]["promoted"])
        self.assertTrue(status["sources"]["completionEvidence"]["promoted"])
        self.assertEqual(blackbox_payload(), json.loads((self.data_dir / ingest.BLACKBOX_OUTPUT.name).read_text()))
        self.assertEqual(completion_payload(), json.loads((self.data_dir / ingest.COMPLETION_OUTPUT.name).read_text()))
        serialized_status = self.status_path.read_text(encoding="utf-8")
        self.assertNotIn("/remote/", serialized_status)
        self.assertNotIn("jaimes-lan", serialized_status)

    def test_invalid_source_preserves_its_last_good_while_other_source_advances(self) -> None:
        old_completion = completion_payload(checkedAt="2026-07-18T21:00:00Z")
        completion_path = self.data_dir / ingest.COMPLETION_OUTPUT.name
        completion_path.write_text(json.dumps(old_completion), encoding="utf-8")
        invalid_completion = completion_payload(owner="not-jaimes")
        new_blackbox = blackbox_payload(metrics={"latencyMs": 99.0, "sourceAgeMinutes": 1.0, "generatedAgeMinutes": 1.0})
        fake = self.fake_fetch(
            {
                "/remote/blackbox.json": (new_blackbox, []),
                "/remote/completion.json": (invalid_completion, []),
            }
        )
        with mock.patch.object(ingest, "fetch_payload", side_effect=fake):
            status = ingest.ingest_sources("jaimes-lan", self.specs, self.status_path, now=NOW)
        self.assertFalse(status["ok"])
        self.assertTrue(status["lastGoodPreserved"])
        self.assertTrue(status["sources"]["blackbox"]["promoted"])
        self.assertFalse(status["sources"]["completionEvidence"]["promoted"])
        self.assertTrue(status["sources"]["completionEvidence"]["lastGoodPreserved"])
        self.assertEqual(old_completion, json.loads(completion_path.read_text()))
        self.assertEqual(new_blackbox, json.loads((self.data_dir / ingest.BLACKBOX_OUTPUT.name).read_text()))

    def test_fetch_failure_does_not_create_a_false_last_good(self) -> None:
        fake = self.fake_fetch(
            {
                "/remote/blackbox.json": (blackbox_payload(), []),
                "/remote/completion.json": (None, ["remote fetch failed with exit 1"]),
            }
        )
        with mock.patch.object(ingest, "fetch_payload", side_effect=fake):
            status = ingest.ingest_sources("jaimes-lan", self.specs, self.status_path, now=NOW)
        completion = status["sources"]["completionEvidence"]
        self.assertFalse(completion["ok"])
        self.assertFalse(completion["lastGoodPreserved"])
        self.assertFalse((self.data_dir / ingest.COMPLETION_OUTPUT.name).exists())

    def test_status_never_echoes_rejected_remote_keys_or_values(self) -> None:
        unsafe = completion_payload()
        unsafe["rawPrivateCustomerName_Acme"] = "private-value-must-not-appear"
        fake = self.fake_fetch(
            {
                "/remote/blackbox.json": (blackbox_payload(), []),
                "/remote/completion.json": (unsafe, []),
            }
        )
        with mock.patch.object(ingest, "fetch_payload", side_effect=fake):
            ingest.ingest_sources("jaimes-lan", self.specs, self.status_path, now=NOW)
        serialized = self.status_path.read_text(encoding="utf-8")
        self.assertNotIn("Acme", serialized)
        self.assertNotIn("private-value-must-not-appear", serialized)


if __name__ == "__main__":
    unittest.main()
