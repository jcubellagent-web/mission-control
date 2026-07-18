#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import control_tower_work_store as subject


class WorkStoreTests(unittest.TestCase):
    def make_store(self, root: Path) -> subject.WorkStore:
        return subject.WorkStore(root / "work.sqlite3", root / "control-tower-hot.json")

    def start(self, store: subject.WorkStore, **overrides):
        payload = {
            "kind": "start",
            "event_id": "event-start",
            "work_id": "work-ledger",
            "run_id": "run-one",
            "generation": 1,
            "sequence": 1,
            "agent": "joshex",
            "objective": "Build the canonical live work ledger",
            "phase": "implementation",
            "tool": "Codex",
            "origin": "joshex",
            "origin_claim": "private-origin-claim",
            "model_family": "gemini",
            "model_id": "gemini-2.5-pro",
            "route_verified": True,
            "lease_seconds": 180,
        }
        payload.update(overrides)
        return store.publish(payload)

    def test_lifecycle_projection_and_verified_route_are_one_revision(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = self.make_store(root)
            started = self.start(store)
            self.assertEqual(started["revision"], 1)
            self.assertEqual(started["work"]["modelFamily"], "antigravity")
            self.assertEqual((root / "work.sqlite3").stat().st_mode & 0o777, 0o600)
            with closing(sqlite3.connect(str(root / "work.sqlite3"))) as connection:
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            hot = json.loads((root / "control-tower-hot.json").read_text())
            self.assertEqual(hot["revision"], 1)
            self.assertEqual(hot["activeWorks"][0]["workId"], "work-ledger")
            self.assertEqual(hot["activeModelRoutes"][0]["modelFamily"], "antigravity")

            meaningful = started["work"]["lastMeaningfulAt"]
            heartbeat = store.publish({
                "kind": "heartbeat",
                "event_id": "event-heartbeat",
                "work_id": "work-ledger",
                "run_id": "run-one",
                "agent": "joshex",
            })
            self.assertEqual(heartbeat["work"]["sequence"], 2)
            self.assertEqual(heartbeat["work"]["lastMeaningfulAt"], meaningful)

            terminal = store.publish({
                "kind": "terminal",
                "event_id": "event-terminal",
                "work_id": "work-ledger",
                "run_id": "run-one",
                "agent": "joshex",
                "status": "done",
                "phase": "complete",
            })
            self.assertEqual(terminal["work"]["status"], "done")
            hot = json.loads((root / "control-tower-hot.json").read_text())
            self.assertEqual(hot["revision"], 3)
            self.assertEqual(hot["activeWorks"], [])
            self.assertEqual(hot["activeModelRoutes"], [])
            with closing(sqlite3.connect(str(root / "work.sqlite3"))) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM work_events").fetchone()[0], 3)
                self.assertEqual(connection.execute("SELECT count(*) FROM model_route_events").fetchone()[0], 3)
                self.assertEqual(connection.execute("SELECT count(*) FROM current_works").fetchone()[0], 1)

    def test_idempotency_ordering_and_new_generation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = self.make_store(Path(raw))
            first = self.start(store)
            duplicate = self.start(store)
            self.assertTrue(duplicate["idempotent"])
            self.assertEqual(duplicate["revision"], first["revision"])
            with self.assertRaises(subject.OutOfOrderEvent):
                store.publish({
                    "kind": "update",
                    "event_id": "event-stale",
                    "work_id": "work-ledger",
                    "run_id": "run-one",
                    "generation": 1,
                    "sequence": 1,
                    "agent": "joshex",
                })
            store.publish({
                "kind": "terminal",
                "event_id": "event-done",
                "work_id": "work-ledger",
                "run_id": "run-one",
                "agent": "joshex",
                "status": "done",
            })
            with self.assertRaises(subject.OutOfOrderEvent):
                store.publish({
                    "kind": "update",
                    "event_id": "event-reopen-bad",
                    "work_id": "work-ledger",
                    "run_id": "run-one",
                    "agent": "joshex",
                    "status": "active",
                })
            reopened = store.publish({
                "kind": "start",
                "event_id": "event-generation-two",
                "work_id": "work-ledger",
                "run_id": "run-two",
                "generation": 2,
                "sequence": 1,
                "agent": "joshex",
                "objective": "Build the canonical live work ledger",
                "phase": "follow-up",
            })
            self.assertEqual(reopened["work"]["generation"], 2)
            self.assertEqual(reopened["work"]["sequence"], 1)

    def test_origin_claim_deduplicates_duplicate_intake_across_work_ids(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = self.make_store(root)
            first = self.start(store)
            duplicate = self.start(
                store,
                event_id="event-duplicate-delivery",
                work_id="work-duplicate-delivery",
                run_id="run-duplicate-delivery",
            )
            self.assertTrue(duplicate["duplicateClaim"])
            self.assertTrue(duplicate["idempotent"])
            self.assertEqual(duplicate["work"]["workId"], first["work"]["workId"])
            self.assertEqual(duplicate["revision"], first["revision"])
            hot = json.loads((root / "control-tower-hot.json").read_text())
            self.assertEqual(hot["counts"]["currentWorks"], 1)

    def test_origin_claim_retry_after_terminal_returns_terminal_canonical_work(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = self.make_store(Path(raw))
            self.start(store)
            done = store.publish({
                "kind": "terminal",
                "event_id": "event-finished-before-retry",
                "work_id": "work-ledger",
                "run_id": "run-one",
                "agent": "joshex",
                "status": "done",
            })
            retry = self.start(store, event_id="event-late-duplicate")
            self.assertTrue(retry["duplicateClaim"])
            self.assertEqual(retry["work"]["status"], "done")
            self.assertEqual(retry["revision"], done["revision"])

    def test_origin_claim_is_hashed_and_secret_text_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = self.make_store(root)
            result = self.start(store)
            expected = hashlib.sha256(b"private-origin-claim").hexdigest()
            self.assertEqual(result["work"]["originClaimHash"], expected)
            with closing(sqlite3.connect(str(root / "work.sqlite3"))) as connection:
                stored = connection.execute(
                    "SELECT origin_claim_hash FROM work_events"
                ).fetchone()[0]
            self.assertEqual(stored, expected)
            self.assertNotIn("private-origin-claim", (root / "control-tower-hot.json").read_text())
            with self.assertRaises(subject.WorkStoreError):
                self.start(
                    store,
                    event_id="event-secret",
                    work_id="work-secret",
                    run_id="run-secret",
                    objective="Use password: definitely-not-dashboard-safe",
                )
            with self.assertRaises(subject.WorkStoreError):
                self.start(
                    store,
                    event_id="event-private",
                    work_id="work-private",
                    run_id="run-private",
                    privacy="agent-private",
                )

    def test_expired_lease_is_never_projected_as_active(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = self.make_store(root)
            result = self.start(
                store,
                occurred_at="2020-01-01T00:00:00Z",
                lease_seconds=15,
            )
            self.assertTrue(result["work"]["stale"])
            hot = json.loads((root / "control-tower-hot.json").read_text())
            self.assertEqual(hot["activeWorks"], [])
            self.assertEqual(hot["activeModelRoutes"], [])

    def test_unverified_route_clears_active_finops_route(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = self.make_store(root)
            self.start(store)
            result = store.publish({
                "kind": "update",
                "event_id": "event-route-unverified",
                "work_id": "work-ledger",
                "run_id": "run-one",
                "agent": "joshex",
                "route_verified": False,
                "phase": "route-unverified",
            })
            self.assertFalse(result["work"]["routeVerified"])
            hot = json.loads((root / "control-tower-hot.json").read_text())
            self.assertEqual(hot["activeModelRoutes"], [])

    def test_concurrent_auto_sequence_allocation_has_no_lost_updates(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = self.make_store(root)
            self.start(store)

            def update(index: int) -> int:
                result = self.make_store(root).publish({
                    "kind": "update",
                    "event_id": f"event-update-{index}",
                    "work_id": "work-ledger",
                    "run_id": "run-one",
                    "agent": "joshex",
                    "phase": f"parallel-{index}",
                })
                return result["event"]["sequence"]

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                sequences = list(executor.map(update, range(12)))
            self.assertEqual(sorted(sequences), list(range(2, 14)))
            self.assertEqual(self.make_store(root).get("work-ledger")["sequence"], 13)
            hot = json.loads((root / "control-tower-hot.json").read_text())
            self.assertEqual(hot["revision"], 13)

    def test_cli_start_heartbeat_terminal_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            script = Path(subject.__file__)
            common = [
                sys.executable,
                "-B",
                str(script),
            ]
            start = subprocess.run(
                common + [
                    "start",
                    "--db-path", str(root / "work.db"),
                    "--hot-path", str(root / "hot.json"),
                    "--work-id", "work-cli",
                    "--run-id", "run-cli",
                    "--agent", "josh2",
                    "--objective", "Verify the command line contract",
                    "--phase", "starting",
                    "--model-family", "codex",
                    "--model-id", "gpt-5.6-terra",
                    "--route-verified",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(start.stdout)["revision"], 1)
            for command in ("heartbeat", "terminal"):
                extra = ["--status", "done"] if command == "terminal" else []
                subprocess.run(
                    common + [
                        command,
                        "--db-path", str(root / "work.db"),
                        "--hot-path", str(root / "hot.json"),
                        "--work-id", "work-cli",
                        "--run-id", "run-cli",
                        "--agent", "josh2",
                    ] + extra,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            self.assertEqual(json.loads((root / "hot.json").read_text())["activeWorks"], [])


if __name__ == "__main__":
    unittest.main()
