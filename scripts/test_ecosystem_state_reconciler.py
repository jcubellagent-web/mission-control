#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ecosystem_state_reconciler as subject


class ReconcilerTests(unittest.TestCase):
    def test_main_holds_publisher_locks_across_snapshot_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name, key in (
                ("agent-task-queue.json", "tasks"),
                ("handoff-queue.json", "handoffs"),
                ("codex-jobs.json", "jobs"),
                ("shared-events.json", "events"),
            ):
                (root / name).write_text(json.dumps({key: []}), encoding="utf-8")
            attempted = root / "writer-attempted"
            acquired = root / "writer-acquired"
            handoff_lock = root / "handoff-queue.lock"
            child_code = (
                "import fcntl, pathlib, sys; "
                "attempted=pathlib.Path(sys.argv[1]); "
                "acquired=pathlib.Path(sys.argv[2]); "
                "lock_path=pathlib.Path(sys.argv[3]); "
                "attempted.write_text('yes'); "
                "handle=lock_path.open('a+'); "
                "fcntl.flock(handle.fileno(), fcntl.LOCK_EX); "
                "acquired.write_text('yes'); "
                "handle.close()"
            )
            child: subprocess.Popen[str] | None = None

            def fake_reconcile(data_dir: Path, _now: dt.datetime):
                nonlocal child
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        child_code,
                        str(attempted),
                        str(acquired),
                        str(handoff_lock),
                    ],
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                deadline = time.monotonic() + 3
                while not attempted.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(attempted.exists())
                self.assertFalse(acquired.exists())
                return {"documents": {}, "summary": {"ok": True}}

            argv = [
                "ecosystem_state_reconciler.py",
                "--data-dir", str(root),
                "--dry-run",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(subject, "reconcile", side_effect=fake_reconcile),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(subject.main(), 0)
            self.assertIsNotNone(child)
            child.wait(timeout=5)
            self.assertEqual(child.returncode, 0)
            self.assertTrue(acquired.exists())

    def test_canonical_work_ids_never_fuzzy_match_a_different_task(self) -> None:
        now = dt.datetime(2026, 7, 15, 12, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixtures = {
                "agent-task-queue.json": {
                    "tasks": [{
                        "id": "task-new-theme",
                        "workId": "work-new-theme",
                        "title": "Implement Control Tower model route theme",
                        "status": "done",
                        "completedAt": "2026-07-15T11:00:00Z",
                    }]
                },
                "handoff-queue.json": {"handoffs": []},
                "codex-jobs.json": {"jobs": [{
                    "id": "job-architecture",
                    "workId": "work-architecture",
                    "title": "Plan Control Tower model route architecture",
                    "status": "active",
                    "time": "2026-07-15T11:30:00Z",
                }]},
                "shared-events.json": {"events": []},
            }
            for name, payload in fixtures.items():
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            result = subject.reconcile(root, now)
            job = result["documents"][root / "codex-jobs.json"]["jobs"][0]
            self.assertEqual(job["status"], "active")
            self.assertNotIn("terminalTaskId", job)

    def test_exact_work_id_supersedes_even_when_titles_differ(self) -> None:
        now = dt.datetime(2026, 7, 15, 12, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixtures = {
                "agent-task-queue.json": {"tasks": [{
                    "id": "task-exact",
                    "workId": "work-exact",
                    "title": "Final operator wording",
                    "status": "done",
                    "completedAt": "2026-07-15T11:00:00Z",
                }]},
                "handoff-queue.json": {"handoffs": []},
                "codex-jobs.json": {"jobs": [{
                    "id": "job-exact",
                    "workId": "work-exact",
                    "title": "Completely different intake wording",
                    "status": "active",
                    "time": "2026-07-15T10:00:00Z",
                }]},
                "shared-events.json": {"events": []},
            }
            for name, payload in fixtures.items():
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            result = subject.reconcile(root, now)
            job = result["documents"][root / "codex-jobs.json"]["jobs"][0]
            self.assertEqual(job["status"], "superseded")
            self.assertEqual(job["reconciliationMatch"], "workId")

    def test_terminal_truth_supersedes_old_activity_and_closes_handoff(self) -> None:
        now = dt.datetime(2026, 7, 15, 12, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixtures = {
                "agent-task-queue.json": {"tasks": [{"id": "task-1", "title": "Repair Control Tower route telemetry", "status": "done", "completedAt": "2026-07-15T11:00:00Z"}]},
                "handoff-queue.json": {"handoffs": [{"id": "h-1", "title": "Instruction received: repair Control Tower route telemetry", "status": "open", "time": "2026-07-15T09:00:00Z"}]},
                "codex-jobs.json": {"jobs": [{"id": "j-1", "title": "Task active: repair Control Tower route telemetry", "status": "active", "time": "2026-07-15T09:00:00Z"}]},
                "shared-events.json": {"events": [{"id": "e-1", "title": "Task queued: repair Control Tower route telemetry", "status": "active", "time": "2026-07-15T09:00:00Z"}]},
            }
            for name, payload in fixtures.items():
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            result = subject.reconcile(root, now)
            docs = result["documents"]
            self.assertEqual(docs[root / "handoff-queue.json"]["handoffs"][0]["status"], "done")
            self.assertEqual(docs[root / "codex-jobs.json"]["jobs"][0]["status"], "superseded")
            self.assertEqual(docs[root / "shared-events.json"]["events"][0]["status"], "superseded")

    def test_unowned_old_activity_is_stale_not_done(self) -> None:
        now = dt.datetime(2026, 7, 15, 12, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "agent-task-queue.json").write_text('{"tasks": []}', encoding="utf-8")
            (root / "handoff-queue.json").write_text('{"handoffs": []}', encoding="utf-8")
            (root / "codex-jobs.json").write_text('{"jobs": [{"title":"old orphan","status":"active","time":"2026-07-14T01:00:00Z"}]}', encoding="utf-8")
            (root / "shared-events.json").write_text('{"events": []}', encoding="utf-8")
            result = subject.reconcile(root, now)
            self.assertEqual(result["documents"][root / "codex-jobs.json"]["jobs"][0]["status"], "stale")

    def test_terminal_truth_supersedes_matching_blocker_without_hiding_unresolved_blocker(self) -> None:
        now = dt.datetime(2026, 7, 15, 12, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixtures = {
                "agent-task-queue.json": {
                    "tasks": [
                        {
                            "id": "task-credentials",
                            "title": "Store dedicated Mac administrator credentials in 1Password",
                            "status": "done",
                            "completedAt": "2026-07-15T11:00:00Z",
                        }
                    ]
                },
                "handoff-queue.json": {"handoffs": []},
                "codex-jobs.json": {
                    "jobs": [
                        {
                            "id": "job-resolved",
                            "title": "Task blocked: Store dedicated Mac administrator credentials in 1Password",
                            "status": "blocked",
                            "time": "2026-07-15T09:00:00Z",
                        },
                        {
                            "id": "job-open",
                            "title": "Unrelated production authorization",
                            "status": "blocked",
                            "time": "2026-07-14T01:00:00Z",
                        },
                    ]
                },
                "shared-events.json": {"events": []},
            }
            for name, payload in fixtures.items():
                (root / name).write_text(json.dumps(payload), encoding="utf-8")

            result = subject.reconcile(root, now)
            jobs = result["documents"][root / "codex-jobs.json"]["jobs"]
            self.assertEqual(jobs[0]["status"], "superseded")
            self.assertEqual(jobs[0]["terminalTaskId"], "task-credentials")
            self.assertEqual(jobs[1]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
