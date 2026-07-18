#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import ecosystem_state_reconciler as subject


class ReconcilerTests(unittest.TestCase):
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
