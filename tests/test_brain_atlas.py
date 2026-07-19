from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import brain_atlas as subject  # noqa: E402


AS_OF = dt.datetime(2026, 7, 18, 12, 0, tzinfo=dt.timezone.utc)


class BrainAtlasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="brain-atlas-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "work.sqlite3"
        self.create_database(self.database)

    @staticmethod
    def create_database(path: Path, *, schema_version: int = 1, revision: int = 1000) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE store_meta (
              singleton INTEGER PRIMARY KEY,
              schema_version INTEGER NOT NULL,
              revision INTEGER NOT NULL,
              updated_at TEXT
            );
            CREATE TABLE work_events (
              event_id TEXT PRIMARY KEY,
              work_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              generation INTEGER NOT NULL,
              sequence INTEGER NOT NULL,
              kind TEXT NOT NULL,
              status TEXT NOT NULL,
              owner_agent TEXT NOT NULL,
              objective TEXT NOT NULL,
              phase TEXT NOT NULL,
              tool TEXT NOT NULL,
              detail TEXT NOT NULL,
              origin TEXT NOT NULL,
              origin_claim_hash TEXT NOT NULL,
              model_family TEXT NOT NULL,
              model_id TEXT NOT NULL,
              route_verified INTEGER NOT NULL,
              lease_until TEXT,
              occurred_at TEXT NOT NULL,
              accepted_revision INTEGER NOT NULL,
              UNIQUE(work_id,generation,sequence)
            );
            """
        )
        connection.execute(
            "INSERT INTO store_meta VALUES (1,?,?,?)",
            (schema_version, revision, subject.iso(AS_OF)),
        )
        connection.commit()
        connection.close()

    def insert_event(self, index: int, **overrides: object) -> None:
        payload: dict[str, object] = {
            "event_id": f"event-{index}",
            "work_id": f"work-{index}",
            "run_id": f"run-{index}",
            "generation": 1,
            "sequence": 1,
            "kind": "start",
            "status": "active",
            "owner_agent": "josh2",
            "objective": "private objective must never be read",
            "phase": "working",
            "tool": "test",
            "detail": "private detail must never be read",
            "origin": "private-origin",
            "origin_claim_hash": hashlib.sha256(f"claim-{index}".encode()).hexdigest(),
            "model_family": "codex",
            "model_id": f"gpt-5.6-terra-{index}",
            "route_verified": 1,
            "lease_until": None,
            "occurred_at": subject.iso(AS_OF - dt.timedelta(hours=index % 24)),
            "accepted_revision": index + 1,
        }
        payload.update(overrides)
        columns = list(payload)
        placeholders = ",".join("?" for _ in columns)
        connection = sqlite3.connect(self.database)
        connection.execute(
            f"INSERT INTO work_events({','.join(columns)}) VALUES ({placeholders})",
            [payload[column] for column in columns],
        )
        connection.commit()
        connection.close()

    def test_output_excludes_private_content_and_raw_operational_ids(self) -> None:
        self.insert_event(
            1,
            event_id="event-raw-secret-marker",
            work_id="work-private-account-marker",
            run_id="run-private-session-marker",
            objective="raw prompt marker sk-abcdefghijklmnopqrstuvwxyz",
            detail="private-account@example.com",
            origin="private-browser-account-marker",
        )
        before_hash = hashlib.sha256(self.database.read_bytes()).hexdigest()
        before_files = {path.name for path in self.root.iterdir()}
        atlas = subject.generate_atlas(self.database, as_of=AS_OF)
        after_hash = hashlib.sha256(self.database.read_bytes()).hexdigest()
        after_files = {path.name for path in self.root.iterdir()}
        rendered = json.dumps(atlas)

        for marker in (
            "event-raw-secret-marker",
            "work-private-account-marker",
            "run-private-session-marker",
            "raw prompt marker",
            "sk-abcdefghijklmnopqrstuvwxyz",
            "private-account@example.com",
            "private-browser-account-marker",
        ):
            self.assertNotIn(marker, rendered)
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(before_files, after_files)
        self.assertEqual(atlas["status"], "ready")
        self.assertTrue(atlas["source"]["verified"])
        self.assertEqual(atlas["counts"]["receipts"], 1)

    def test_ids_and_full_payload_are_deterministic_for_fixed_receipts(self) -> None:
        self.insert_event(1, owner_agent="jaimes")
        self.insert_event(2, owner_agent="jain", route_verified=0, model_family="", model_id="")
        first = subject.generate_atlas(self.database, as_of=AS_OF)
        second = subject.generate_atlas(self.database, as_of=AS_OF)
        self.assertEqual(first, second)
        self.assertEqual(subject.validate_atlas(first), [])
        self.assertEqual(
            [row["id"] for row in first["nodes"]],
            [row["id"] for row in second["nodes"]],
        )
        self.assertEqual(len({row["id"] for row in first["nodes"]}), len(first["nodes"]))
        self.assertEqual(len({row["id"] for row in first["edges"]}), len(first["edges"]))

    def test_node_cap_is_hard_and_graph_has_no_dangling_edges(self) -> None:
        for index in range(60):
            self.insert_event(index)
        atlas = subject.generate_atlas(self.database, as_of=AS_OF, max_nodes=10)
        node_ids = {row["id"] for row in atlas["nodes"]}
        self.assertLessEqual(len(node_ids), 10)
        self.assertEqual(atlas["limits"]["hardMaxNodes"], 100)
        self.assertGreater(atlas["counts"]["excluded"]["capacityReceipts"], 0)
        for edge in atlas["edges"]:
            self.assertIn(edge["source"], node_ids)
            self.assertIn(edge["target"], node_ids)
            self.assertIn(edge["evidenceReceipt"], node_ids)
        with self.assertRaises(ValueError):
            subject.generate_atlas(self.database, as_of=AS_OF, max_nodes=101)

    def test_stale_legacy_and_unverified_relationships_are_excluded(self) -> None:
        self.insert_event(1)
        self.insert_event(2, route_verified=0, model_family="codex", model_id="unverified-model")
        self.insert_event(3, route_verified=1, model_family="codex", model_id="unsafe model id")
        self.insert_event(4, occurred_at=subject.iso(AS_OF - dt.timedelta(days=8)))
        self.insert_event(5, owner_agent="josh")
        self.insert_event(6, accepted_revision=0)
        self.insert_event(7, origin_claim_hash="legacy-not-a-hash")
        atlas = subject.generate_atlas(self.database, as_of=AS_OF)
        exclusions = atlas["counts"]["excluded"]

        self.assertEqual(atlas["window"]["days"], 7)
        self.assertEqual(atlas["counts"]["sourceRowsInWindow"], 6)
        self.assertEqual(atlas["counts"]["receipts"], 3)
        self.assertEqual(exclusions["timeOutOfWindow"], 1)
        self.assertEqual(exclusions["legacyOrInvalid"], 3)
        self.assertEqual(exclusions["unverifiedRoutes"], 1)
        self.assertEqual(exclusions["unsafeVerifiedRoutes"], 1)
        route_edges = [edge for edge in atlas["edges"] if edge["kind"] == "verified-route"]
        self.assertEqual(len(route_edges), 1)

    def test_edges_are_only_exact_receipt_relationships(self) -> None:
        self.insert_event(1, owner_agent="joshex")
        atlas = subject.generate_atlas(self.database, as_of=AS_OF)
        nodes = {row["id"]: row for row in atlas["nodes"]}
        for edge in atlas["edges"]:
            self.assertEqual(nodes[edge["evidenceReceipt"]]["kind"], "receipt")
            if edge["kind"] == "owns":
                self.assertEqual(nodes[edge["source"]]["kind"], "agent")
                self.assertEqual(nodes[edge["target"]]["kind"], "work")
            elif edge["kind"] == "emitted":
                self.assertEqual(nodes[edge["source"]]["kind"], "work")
                self.assertEqual(nodes[edge["target"]]["kind"], "receipt")
            else:
                self.assertEqual(edge["kind"], "verified-route")
                self.assertEqual(nodes[edge["source"]]["kind"], "receipt")
                self.assertEqual(nodes[edge["target"]]["kind"], "model")
        self.assertIn("no inferred or fuzzy relationships", atlas["policy"]["edges"])

    def test_empty_and_unavailable_states_are_truthful(self) -> None:
        empty = subject.generate_atlas(self.database, as_of=AS_OF)
        self.assertEqual(empty["status"], "empty")
        self.assertTrue(empty["empty"])
        self.assertEqual(empty["emptyReason"], "no-receipts-in-window")
        self.assertEqual(empty["nodes"], [])
        self.assertEqual(empty["edges"], [])
        self.assertTrue(empty["source"]["verified"])

        unsupported = self.root / "unsupported.sqlite3"
        sqlite3.connect(unsupported).close()
        unavailable = subject.generate_safely(unsupported, as_of=AS_OF)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertTrue(unavailable["empty"])
        self.assertEqual(unavailable["emptyReason"], "unsupported-source-schema")
        self.assertFalse(unavailable["source"]["verified"])
        self.assertEqual(unavailable["nodes"], [])
        self.assertNotIn(str(unsupported), json.dumps(unavailable))

        missing = subject.generate_safely(self.root / "missing.sqlite3", as_of=AS_OF)
        self.assertEqual(missing["emptyReason"], "source-missing")

        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a sqlite database")
        unavailable_corrupt = subject.generate_safely(corrupt, as_of=AS_OF)
        self.assertEqual(unavailable_corrupt["status"], "unavailable")
        self.assertEqual(unavailable_corrupt["emptyReason"], "source-unavailable")
        self.assertNotIn(str(corrupt), json.dumps(unavailable_corrupt))

    def test_schema_contract_is_bounded_and_excludes_content_fields(self) -> None:
        schema = json.loads((ROOT / "schemas" / "brain-atlas.schema.json").read_text())
        self.assertEqual(schema["properties"]["nodes"]["maxItems"], 100)
        self.assertEqual(schema["properties"]["limits"]["properties"]["hardMaxNodes"]["const"], 100)
        node_properties = schema["$defs"]["node"]["properties"]
        for field in ("objective", "detail", "origin", "workId", "runId", "eventId", "memoryContent"):
            self.assertNotIn(field, node_properties)

    def test_internal_validator_fails_closed_and_validate_only_is_content_free(self) -> None:
        self.insert_event(1)
        atlas = subject.generate_atlas(self.database, as_of=AS_OF)
        private_mutation = json.loads(json.dumps(atlas))
        private_mutation["nodes"][0]["objective"] = "raw prompt"
        self.assertIn("node-fields", subject.validate_atlas(private_mutation))
        dangling = json.loads(json.dumps(atlas))
        dangling["edges"][0]["target"] = "work:missing"
        self.assertIn("dangling-edge", subject.validate_atlas(dangling))

        output = self.root / "must-not-be-written.json"
        process = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "brain_atlas.py"),
                "--db", str(self.database),
                "--as-of", subject.iso(AS_OF),
                "--output", str(output),
                "--validate-only",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        gate = json.loads(process.stdout)
        self.assertEqual(set(gate), {"ok", "status", "sourceVerified", "windowDays", "nodes", "edges", "problems", "emptyReason"})
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["problems"], [])
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
