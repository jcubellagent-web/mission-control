from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "memory_registry.py"
sys.path.insert(0, str(ROOT / "scripts"))
import memory_registry  # noqa: E402


class MemoryRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memory-registry-test-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.database = self.folder / "registry.sqlite"
        self.env = dict(os.environ)
        self.env["MEMORY_REGISTRY_DB"] = str(self.database)
        self.env["MEMORY_OPERATIONS_PATH"] = str(self.folder / "status.json")
        self.cli("init")

    def run_raw(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=ROOT,
            env=env or self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def cli(self, *args: str, env: dict[str, str] | None = None) -> dict:
        process = self.run_raw(*args, env=env)
        if process.returncode != 0:
            self.fail(f"command failed ({process.returncode}): {process.stderr or process.stdout}")
        return json.loads(process.stdout)

    def create_memory(self, label: str, *, privacy: str, owner: str = "jaimes") -> str:
        proposal = self.cli(
            "propose",
            "--agent", "jaimes",
            "--type", "fact",
            "--subject", f"Privacy fixture {label}",
            "--predicate", "has state",
            "--value", "ready",
            "--owner", owner,
            "--visibility", "shared",
            "--privacy", privacy,
            "--source", f"test:{label}",
            "--evidence", "unit test",
            "--confidence", "0.99",
        )
        approved = self.cli("approve", "--id", proposal["id"], "--reviewer", "joshex")
        return approved["recordId"]

    def retrieve_ids(self, agent: str) -> set[str]:
        result = self.cli(
            "retrieve", "--agent", agent, "--query", "Privacy fixture", "--limit", "20"
        )
        return {str(row["id"]) for row in result["results"]}

    def test_deterministic_sources_keep_entities_sharp_and_trading_snapshots_historical(self) -> None:
        data = self.folder / "source-data"
        data.mkdir()
        (data / "decisions.json").write_text(json.dumps({"decisions": [
            {
                "id": "jaimes-decision-20260619-trading-monitor",
                "title": "Trading monitor: no trade",
                "detail": "Point-in-time review.",
                "agent": "jaimes",
                "time": "2026-06-19T17:37:44Z",
            },
            {
                "id": "joshex-decision-policy",
                "title": "Keep canonical instructions authoritative",
                "detail": "Checked-in policy wins.",
                "agent": "joshex",
            },
        ]}), encoding="utf-8")
        (data / "agent-task-queue.json").write_text('{"tasks": []}', encoding="utf-8")
        index = data / "semantic-index.json"
        index.write_text(json.dumps({"nodes": [
            {"id": "agent:jaimes", "label": "JAIMES", "type": "agent"},
            {"id": "capability:memory", "label": "memory", "type": "capability"},
            {"id": "topic:detail", "label": "Detail", "type": "topic"},
        ]}), encoding="utf-8")

        with (
            mock.patch.object(memory_registry, "DATA", data),
            mock.patch.object(memory_registry, "INDEX_PATH", index),
        ):
            rows = list(memory_registry.source_rows())

        by_subject = {row["subject"]: row for row in rows}
        trading = by_subject["Trading monitor: no trade"]
        self.assertEqual(trading["memory_type"], "episode")
        self.assertEqual(trading["predicate"], "historical trading decision")
        self.assertTrue(trading["metadata"]["historicalSnapshot"])
        self.assertEqual(by_subject["Keep canonical instructions authoritative"]["memory_type"], "decision")
        self.assertIn("JAIMES", by_subject)
        self.assertIn("memory", by_subject)
        self.assertNotIn("Detail", by_subject)

    def test_retrieval_rejects_weak_single_word_tail_matches(self) -> None:
        target = self.cli(
            "propose", "--agent", "joshex", "--type", "procedure",
            "--subject", "Validated agent ecosystem push authority",
            "--predicate", "authorizes clean source pushes",
            "--value", "JOSHeX JAIMES and Josh 2.0 may push after validation",
            "--owner", "Josh", "--visibility", "shared", "--privacy", "dashboard-safe",
            "--source", "test:sharp-target", "--evidence", "unit test", "--confidence", "1.0",
        )
        target_id = self.cli("approve", "--id", target["id"], "--reviewer", "joshex")["recordId"]
        distractor = self.cli(
            "propose", "--agent", "joshex", "--type", "episode",
            "--subject", "Restore agent authorization",
            "--predicate", "completed",
            "--value", "Reauthorized a service account",
            "--owner", "joshex", "--visibility", "shared", "--privacy", "dashboard-safe",
            "--source", "test:weak-tail", "--evidence", "unit test", "--confidence", "0.92",
        )
        distractor_id = self.cli("approve", "--id", distractor["id"], "--reviewer", "joshex")["recordId"]

        result = self.cli(
            "retrieve", "--agent", "joshex", "--query", "Validated agent ecosystem push authority",
            "--limit", "5",
        )
        ids = {row["id"] for row in result["results"]}
        self.assertEqual(len(result["results"]), 1)
        self.assertIn(target_id, ids)
        self.assertNotIn(distractor_id, ids)

    def test_privacy_is_deny_by_default_outside_owner_and_joshex(self) -> None:
        dashboard = self.create_memory("dashboard", privacy="dashboard-safe")
        public = self.create_memory("public", privacy="public")
        agent_private = self.create_memory("agent private", privacy="agent-private")
        sensitive_account = self.create_memory("sensitive account", privacy="sensitive-account")
        unknown = self.create_memory("unknown", privacy="mystery-internal")

        public_ids = {dashboard, public}
        all_ids = {dashboard, public, agent_private, sensitive_account, unknown}
        self.assertEqual(self.retrieve_ids("jain"), public_ids)
        self.assertEqual(self.retrieve_ids("josh2"), public_ids)
        self.assertEqual(self.retrieve_ids("jaimes"), all_ids)
        self.assertEqual(self.retrieve_ids("joshex"), all_ids)
        privacy = self.cli("privacy-check")
        self.assertTrue(privacy["ok"])
        self.assertRegex(privacy["checkedAt"], r"^\d{4}-\d{2}-\d{2}T.*Z$")
        self.assertEqual(privacy["activePublic"], 2)
        self.assertEqual(privacy["activeOwnerPrivate"], 3)
        self.assertEqual(privacy["unknownLabelsOwnerScoped"], 1)
        self.assertEqual(privacy["crossOwnerPrivateLeaks"], 0)

    def test_owner_private_candidate_is_not_auto_promoted(self) -> None:
        self.cli(
            "propose", "--agent", "jaimes", "--type", "fact",
            "--subject", "Private candidate", "--predicate", "has state", "--value", "ready",
            "--owner", "jaimes", "--visibility", "shared", "--privacy", "agent-private",
            "--source", "test:private", "--evidence", "unit test", "--confidence", "0.99",
        )
        review = self.cli("review", "--apply-safe")
        self.assertEqual(review["promoted"], 0)
        self.assertEqual(review["pending"], 1)

    def test_default_proposals_are_shared_while_personal_account_access_stays_private(self) -> None:
        shared = self.cli(
            "propose", "--agent", "jaimes", "--type", "fact",
            "--subject", "Privacy fixture shared-by-default", "--predicate", "has state", "--value", "ready",
            "--owner", "jaimes", "--visibility", "shared", "--source", "test:default", "--confidence", "0.99",
        )
        shared_id = self.cli("approve", "--id", shared["id"], "--reviewer", "joshex")["recordId"]
        personal = self.create_memory("personal account access", privacy="personal-account-access")

        self.assertIn(shared_id, self.retrieve_ids("jain"))
        self.assertNotIn(personal, self.retrieve_ids("jain"))
        self.assertIn(personal, self.retrieve_ids("joshex"))

    def test_preflight_hashes_context_and_tracks_selected_then_used(self) -> None:
        memory_id = self.create_memory("preflight", privacy="dashboard-safe")
        work_id = "work-private-marker"
        run_id = "run-private-marker"
        session_id = "session-private-marker"
        preflight = self.cli(
            "preflight", "--agent", "jain", "--query", "Privacy fixture private-query-marker",
            "--work-id", work_id, "--run-id", run_id, "--session-id", session_id,
        )
        rendered = json.dumps(preflight)
        self.assertTrue(preflight["proceed"])
        self.assertFalse(preflight["failOpen"])
        self.assertNotIn("private-query-marker", rendered)
        self.assertNotIn(work_id, rendered)
        self.assertNotIn(run_id, rendered)
        self.assertNotIn(session_id, rendered)

        selected = self.cli(
            "reuse-outcome", "--agent", "jain", "--retrieval-id", preflight["retrievalId"],
            "--memory-id", memory_id, "--outcome", "selected", "--reason-code", "context-only",
            "--work-id", work_id, "--run-id", run_id, "--session-id", session_id,
        )
        used = self.cli(
            "reuse-outcome", "--agent", "jain", "--retrieval-id", preflight["retrievalId"],
            "--memory-id", memory_id, "--outcome", "used", "--reason-code", "applied",
            "--work-id", work_id, "--run-id", run_id, "--session-id", session_id,
        )
        duplicate = self.cli(
            "reuse-outcome", "--agent", "jain", "--retrieval-id", preflight["retrievalId"],
            "--memory-id", memory_id, "--outcome", "used", "--reason-code", "applied",
        )
        self.assertEqual(selected["outcome"], "selected")
        self.assertEqual(used["outcome"], "used")
        self.assertTrue(duplicate["duplicate"])

        connection = sqlite3.connect(self.database)
        row = connection.execute(
            "SELECT query_hash,work_id_hash,run_id_hash,session_id_hash,preflight FROM retrieval_events WHERE id=?",
            (preflight["retrievalId"],),
        ).fetchone()
        reuse_rows = connection.execute(
            "SELECT outcome,reason_code,work_id_hash,run_id_hash,session_id_hash FROM memory_reuse_events ORDER BY outcome",
        ).fetchall()
        connection.close()
        self.assertEqual(row[4], 1)
        self.assertNotIn(work_id, row)
        self.assertNotIn(run_id, row)
        self.assertNotIn(session_id, row)
        self.assertEqual({item[0] for item in reuse_rows}, {"selected", "used"})
        self.assertTrue(all(item[2] == row[1] and item[3] == row[2] and item[4] == row[3] for item in reuse_rows))

        status = self.cli("status")
        self.assertEqual(status["retrieval"]["preflights7d"], 1)
        self.assertEqual(status["retrieval"]["selected30d"], 1)
        self.assertEqual(status["retrieval"]["used30d"], 1)
        self.assertEqual(status["retrieval"]["selectedUseRate"], 100.0)

    def test_used_requires_selected_and_context_must_match(self) -> None:
        memory_id = self.create_memory("ordered outcome", privacy="dashboard-safe")
        preflight = self.cli(
            "preflight", "--agent", "jain", "--query", "Privacy fixture ordered outcome",
            "--work-id", "work-a",
        )
        used_first = self.run_raw(
            "reuse-outcome", "--agent", "jain", "--retrieval-id", preflight["retrievalId"],
            "--memory-id", memory_id, "--outcome", "used",
        )
        self.assertNotEqual(used_first.returncode, 0)
        mismatch = self.run_raw(
            "reuse-outcome", "--agent", "jain", "--retrieval-id", preflight["retrievalId"],
            "--memory-id", memory_id, "--outcome", "selected", "--work-id", "work-b",
        )
        self.assertNotEqual(mismatch.returncode, 0)

        unbound = self.cli(
            "preflight", "--agent", "jain", "--query", "Privacy fixture ordered outcome"
        )
        self.cli(
            "reuse-outcome", "--agent", "jain", "--retrieval-id", unbound["retrievalId"],
            "--memory-id", memory_id, "--outcome", "selected", "--work-id", "work-a",
        )
        changed_after_selection = self.run_raw(
            "reuse-outcome", "--agent", "jain", "--retrieval-id", unbound["retrievalId"],
            "--memory-id", memory_id, "--outcome", "used", "--work-id", "work-b",
        )
        self.assertNotEqual(changed_after_selection.returncode, 0)

    def test_preflight_fails_open_when_registry_is_unavailable(self) -> None:
        env = dict(self.env)
        env["MEMORY_REGISTRY_DB"] = str(self.folder)
        result = self.cli(
            "preflight", "--agent", "jain", "--query", "safe fail open",
            "--work-id", "work-private-marker", env=env,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["proceed"])
        self.assertTrue(result["failOpen"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["results"], [])
        self.assertNotIn("work-private-marker", json.dumps(result))

    def test_ignored_feedback_reduces_quality_rate(self) -> None:
        memory_id = self.create_memory("quality", privacy="dashboard-safe")
        retrieval = self.cli(
            "retrieve", "--agent", "jain", "--query", "Privacy fixture quality", "--limit", "3"
        )
        for outcome in ("helpful", "ignored"):
            self.cli(
                "feedback", "--agent", "jain", "--retrieval-id", retrieval["retrievalId"],
                "--memory-id", memory_id, "--outcome", outcome, "--reason", "unit-test outcome",
            )
        status = self.cli("status")
        self.assertEqual(status["retrieval"]["feedback30d"], 2)
        self.assertEqual(status["retrieval"]["qualityRate"], 50.0)

    def test_activity_export_is_counts_only_and_updates_on_retrieval(self) -> None:
        memory_id = self.create_memory("live activity", privacy="dashboard-safe")
        private_query = "Privacy fixture live activity raw-query-marker"
        retrieval = self.cli(
            "retrieve", "--agent", "joshex", "--query", private_query, "--limit", "3"
        )
        status_path = Path(self.env["MEMORY_OPERATIONS_PATH"])
        status = json.loads(status_path.read_text(encoding="utf-8"))
        activity = status["activity"]

        self.assertEqual(activity["schemaVersion"], 2)
        self.assertTrue(activity["source"]["verified"])
        self.assertTrue(activity["privacy"]["countsOnly"])
        self.assertFalse(activity["privacy"]["queryIncluded"])
        self.assertFalse(activity["privacy"]["contentIncluded"])
        self.assertFalse(activity["privacy"]["rawIdentifiersIncluded"])
        self.assertGreaterEqual(activity["counts"]["retrievals"], 1)
        self.assertGreaterEqual(activity["counts"]["hits"], 1)
        self.assertIsNotNone(activity["lastObservedAt"]["retrieval"])
        self.assertEqual(
            next(row for row in activity["agents"] if row["agent"] == "joshex")["retrievals"],
            1,
        )

        rendered = json.dumps(status)
        self.assertNotIn(private_query, rendered)
        self.assertNotIn("raw-query-marker", rendered)
        self.assertNotIn(retrieval["retrievalId"], rendered)
        self.assertNotIn(memory_id, rendered)
        self.assertNotIn("events", activity)

    def test_activity_export_attributes_explicit_cross_agent_use_without_memory_identifiers(self) -> None:
        memory_id = self.create_memory("cross agent reuse", privacy="dashboard-safe", owner="jaimes")
        retrieval = self.cli(
            "preflight", "--agent", "jain", "--query", "Privacy fixture cross agent reuse",
            "--work-id", "private-cross-work",
        )
        for outcome in ("selected", "used"):
            self.cli(
                "reuse-outcome", "--agent", "jain", "--retrieval-id", retrieval["retrievalId"],
                "--memory-id", memory_id, "--outcome", outcome, "--reason-code", "applied",
                "--work-id", "private-cross-work",
            )

        status = self.cli("export")
        activity = status["activity"]
        jain = next(row for row in activity["agents"] if row["agent"] == "jain")
        self.assertEqual(activity["counts"]["selected"], 1)
        self.assertEqual(activity["counts"]["used"], 1)
        self.assertEqual(activity["counts"]["crossAgentUsed"], 1)
        self.assertEqual(jain["selected"], 1)
        self.assertEqual(jain["used"], 1)
        self.assertEqual(jain["crossAgentUsed"], 1)
        self.assertEqual(activity["reuseLinks"], [{
            "sourceAgent": "jaimes",
            "consumerAgent": "jain",
            "uses": 1,
            "lastUsedAt": jain["lastCrossAgentUsedAt"],
        }])
        rendered = json.dumps(activity)
        self.assertNotIn(memory_id, rendered)
        self.assertNotIn(retrieval["retrievalId"], rendered)
        self.assertNotIn("private-cross-work", rendered)

    def test_preflight_immediately_exports_activity(self) -> None:
        self.create_memory("preflight activity", privacy="dashboard-safe")
        self.cli(
            "preflight", "--agent", "jain", "--query", "Privacy fixture preflight activity",
            "--work-id", "private-work-marker",
        )
        status = json.loads(Path(self.env["MEMORY_OPERATIONS_PATH"]).read_text(encoding="utf-8"))
        activity = status["activity"]
        self.assertGreaterEqual(activity["counts"]["retrievals"], 1)
        self.assertEqual(next(row for row in activity["agents"] if row["agent"] == "jain")["retrievals"], 1)
        self.assertNotIn("private-work-marker", json.dumps(status))

    def test_existing_retrieval_schema_is_migrated_additively(self) -> None:
        legacy_database = self.folder / "legacy.sqlite"
        connection = sqlite3.connect(legacy_database)
        connection.execute(
            """CREATE TABLE retrieval_events (
              id TEXT PRIMARY KEY,time TEXT NOT NULL,agent TEXT NOT NULL,scope TEXT NOT NULL,
              query_hash TEXT NOT NULL,term_count INTEGER NOT NULL,matched_count INTEGER NOT NULL,
              latency_ms REAL NOT NULL,memory_ids_json TEXT NOT NULL,outcome TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO retrieval_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("retrieval-legacy", "2026-01-01T00:00:00Z", "jain", "ecosystem", "hash", 1, 0, 1.0, "[]", "miss"),
        )
        connection.commit()
        connection.close()

        env = dict(self.env)
        env["MEMORY_REGISTRY_DB"] = str(legacy_database)
        env["MEMORY_OPERATIONS_PATH"] = str(self.folder / "legacy-status.json")
        self.cli("init", env=env)
        connection = sqlite3.connect(legacy_database)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(retrieval_events)")}
        legacy = connection.execute(
            "SELECT id,work_id_hash,run_id_hash,session_id_hash,preflight FROM retrieval_events WHERE id='retrieval-legacy'"
        ).fetchone()
        reuse_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_reuse_events'"
        ).fetchone()
        connection.close()
        self.assertTrue({"work_id_hash", "run_id_hash", "session_id_hash", "preflight"}.issubset(columns))
        self.assertEqual(legacy, ("retrieval-legacy", None, None, None, 0))
        self.assertIsNotNone(reuse_table)


if __name__ == "__main__":
    unittest.main()
