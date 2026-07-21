from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "memory_registry.py"


class BrainMemoryRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="brain-memory-registry-test-")
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

    def propose_brain(
        self,
        label: str,
        *,
        eligible: bool = False,
        injection_status: str = "clear",
        memory_type: str = "fact",
        source: str = "brain-source:fixture",
    ) -> dict:
        args = [
            "propose",
            "--agent", "josh2",
            "--type", memory_type,
            "--subject", f"Brain fixture {label}",
            "--predicate", "has state",
            "--value", f"value {label}",
            "--owner", "josh2",
            "--visibility", "shared",
            "--privacy", "dashboard-safe",
            "--source", source,
            "--source-ref", source,
            "--source-kind", "brain-source",
            "--extraction-version", "extractor-v1",
            "--injection-status", injection_status,
            "--evidence", f"evidence {label}",
            "--confidence", "0.99",
        ]
        if eligible:
            args.append("--governance-eligible")
        return self.cli(*args)

    def candidate_status(self, candidate_id: str) -> str:
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT status FROM memory_candidates WHERE id=?", (candidate_id,)).fetchone()
        assert row is not None
        return str(row[0])

    def test_brain_auto_review_is_fail_closed_but_eligible_clear_fact_can_promote(self) -> None:
        ineligible = self.propose_brain("ineligible", eligible=False)
        unclassified = self.propose_brain(
            "unclassified", eligible=True, injection_status="not-applicable"
        )
        flagged = self.propose_brain("flagged", eligible=True, injection_status="flagged")
        governed_type = self.propose_brain("procedure", eligible=True, memory_type="procedure")
        eligible = self.propose_brain("eligible", eligible=True)

        review = self.cli("review", "--apply-safe")

        self.assertEqual(review["promoted"], 1)
        self.assertEqual(self.candidate_status(eligible["id"]), "active")
        for candidate in (ineligible, unclassified, flagged, governed_type):
            self.assertEqual(self.candidate_status(candidate["id"]), "candidate")

    def test_manual_brain_approval_requires_eligibility_and_clear_injection_scan(self) -> None:
        ineligible = self.propose_brain("manual-ineligible", eligible=False)
        unclassified = self.propose_brain(
            "manual-unclassified", eligible=True, injection_status="not-applicable"
        )
        quarantined = self.propose_brain(
            "manual-quarantined", eligible=True, injection_status="quarantined"
        )

        for candidate in (ineligible, unclassified, quarantined):
            process = self.run_raw(
                "approve", "--id", candidate["id"], "--reviewer", "joshex"
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(self.candidate_status(candidate["id"]), "candidate")

        eligible = self.propose_brain("manual-eligible", eligible=True)
        approved = self.cli(
            "approve", "--id", eligible["id"], "--reviewer", "joshex"
        )
        self.assertEqual(approved["status"], "active")
        with sqlite3.connect(self.database) as db:
            record = db.execute(
                "SELECT source_path,source_ref FROM memory_records WHERE id=?",
                (approved["recordId"],),
            ).fetchone()
        self.assertEqual(record, ("brain-source:fixture", "brain-source:fixture"))

    def test_legacy_candidates_keep_existing_review_and_approval_behavior(self) -> None:
        legacy_auto = self.cli(
            "propose",
            "--agent", "jaimes",
            "--type", "fact",
            "--subject", "Legacy automatic fixture",
            "--value", "ready",
            "--owner", "jaimes",
            "--visibility", "shared",
            "--privacy", "dashboard-safe",
            "--source", "legacy:test:auto",
            "--confidence", "0.99",
        )
        review = self.cli("review", "--apply-safe")
        self.assertEqual(review["promoted"], 1)
        self.assertEqual(self.candidate_status(legacy_auto["id"]), "active")

        legacy_manual = self.cli(
            "propose",
            "--agent", "jaimes",
            "--type", "preference",
            "--subject", "Legacy manual fixture",
            "--value", "ready",
            "--owner", "jaimes",
            "--source", "legacy:test:manual",
        )
        approved = self.cli(
            "approve", "--id", legacy_manual["id"], "--reviewer", "joshex"
        )
        self.assertEqual(approved["status"], "active")

    def test_forget_source_tombstones_content_removes_fts_and_writes_safe_receipt(self) -> None:
        source = "brain-source:forget-fixture"
        active = self.propose_brain("forget-active", eligible=True, source=source)
        approved = self.cli("approve", "--id", active["id"], "--reviewer", "joshex")
        pending = self.propose_brain("forget-pending", eligible=False, source=source)

        with sqlite3.connect(self.database) as db:
            db.execute(
                "UPDATE memory_candidates SET metadata_json=? WHERE source_ref=?",
                (json.dumps({"private": "candidate marker"}), source),
            )
            db.execute(
                "UPDATE memory_records SET metadata_json=? WHERE id=?",
                (json.dumps({"private": "record marker"}), approved["recordId"]),
            )
            db.commit()

        without_confirmation = self.run_raw(
            "forget-source", "--source", source, "--actor", "josh2"
        )
        self.assertNotEqual(without_confirmation.returncode, 0)
        self.assertEqual(self.candidate_status(pending["id"]), "candidate")

        forgotten = self.cli(
            "forget-source", "--source", source, "--actor", "josh2", "--confirm"
        )
        self.assertEqual(forgotten["status"], "forgotten")
        self.assertEqual(forgotten["candidateCount"], 2)
        self.assertEqual(forgotten["recordCount"], 1)
        self.assertEqual(forgotten["ftsDeleted"], 1)

        with sqlite3.connect(self.database) as db:
            candidates = db.execute(
                """SELECT status,source_state,subject,predicate,object_text,evidence,metadata_json
                   FROM memory_candidates WHERE source_ref=? ORDER BY id""",
                (source,),
            ).fetchall()
            record = db.execute(
                """SELECT status,subject,predicate,object_text,evidence,metadata_json
                   FROM memory_records WHERE id=?""",
                (approved["recordId"],),
            ).fetchone()
            fts_count = db.execute(
                "SELECT COUNT(*) FROM memory_fts WHERE id=?", (approved["recordId"],)
            ).fetchone()[0]
            deletion = db.execute(
                """SELECT source_hash,actor,candidate_count,record_count,fts_count,status
                   FROM memory_deletions WHERE id=?""",
                (forgotten["id"],),
            ).fetchone()

        self.assertEqual(len(candidates), 2)
        for row in candidates:
            self.assertEqual(row, ("forgotten", "forgotten", "", "", "", "", "{}"))
        self.assertEqual(record, ("forgotten", "", "", "", "", "{}"))
        self.assertEqual(fts_count, 0)
        self.assertEqual(deletion[1:], ("josh2", 2, 1, 1, "forgotten"))
        self.assertNotEqual(deletion[0], source)
        self.assertNotIn(source, json.dumps(forgotten))

        retrieval = self.cli(
            "retrieve", "--agent", "joshex", "--query", "Brain fixture forget active"
        )
        self.assertEqual(retrieval["results"], [])

    def test_existing_candidate_schema_migrates_additively_and_new_sql_remains_valid(self) -> None:
        legacy_database = self.folder / "legacy-candidates.sqlite"
        with sqlite3.connect(legacy_database) as db:
            db.execute(
                """CREATE TABLE memory_candidates (
                  id TEXT PRIMARY KEY, proposed_by TEXT NOT NULL, memory_type TEXT NOT NULL,
                  subject TEXT NOT NULL, predicate TEXT NOT NULL, object_text TEXT NOT NULL,
                  owner TEXT NOT NULL, visibility TEXT NOT NULL, privacy TEXT NOT NULL,
                  source_path TEXT NOT NULL, evidence TEXT, confidence REAL NOT NULL,
                  status TEXT NOT NULL, proposed_at TEXT NOT NULL, reviewed_at TEXT,
                  review_reason TEXT, content_hash TEXT NOT NULL UNIQUE
                )"""
            )
            db.execute(
                "INSERT INTO memory_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "candidate-legacy", "jaimes", "fact", "Legacy row", "states", "ready",
                    "jaimes", "shared", "dashboard-safe", "legacy:test", "fixture", 0.99,
                    "candidate", "2026-01-01T00:00:00Z", None, None, "legacy-hash",
                ),
            )

        env = dict(self.env)
        env["MEMORY_REGISTRY_DB"] = str(legacy_database)
        env["MEMORY_OPERATIONS_PATH"] = str(self.folder / "legacy-status.json")
        self.cli("init", env=env)
        proposal = self.cli(
            "propose", "--agent", "josh2", "--type", "fact",
            "--subject", "Migrated Brain row", "--value", "ready",
            "--owner", "josh2", "--source", "brain-source:migration",
            "--source-ref", "brain-source:migration", "--source-kind", "brain-source",
            "--extraction-version", "extractor-v1", "--governance-eligible",
            "--injection-status", "clear", env=env,
        )
        self.assertEqual(proposal["status"], "candidate")

        with sqlite3.connect(legacy_database) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(memory_candidates)")}
            legacy = db.execute(
                """SELECT source_ref,source_kind,extraction_version,governance_eligible,
                          injection_status,source_state,metadata_json
                   FROM memory_candidates WHERE id='candidate-legacy'"""
            ).fetchone()
            deletion_table = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_deletions'"
            ).fetchone()
        self.assertTrue(
            {
                "source_ref", "source_kind", "extraction_version", "governance_eligible",
                "injection_status", "source_state", "metadata_json",
            }.issubset(columns)
        )
        self.assertEqual(legacy, ("", "legacy", "", 1, "not-applicable", "active", "{}"))
        self.assertIsNotNone(deletion_table)


if __name__ == "__main__":
    unittest.main()
