#!/usr/bin/env python3
"""Focused regression tests for guarded Control Tower lease recovery."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("control_tower_change_guard", Path(__file__).with_name("control_tower_change_guard.py"))
guard = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(guard)


class OrphanRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.tasks = Path(self.temp.name) / "tasks.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def lease(self, *, renewed_minutes_ago: int, task_id: str = "task-orphan") -> dict:
        return {
            "ownerPid": 999_999,
            "startedAt": guard.iso(guard.now() - timedelta(minutes=30)),
            "lastRenewedAt": guard.iso(guard.now() - timedelta(minutes=renewed_minutes_ago)),
            "expiresAt": guard.iso(guard.now() + timedelta(minutes=15)),
            "taskBinding": {"taskId": task_id, "workId": "work-orphan", "runId": "run-orphan"},
        }

    def test_recent_renewal_blocks_early_recovery(self) -> None:
        self.tasks.write_text(json.dumps({"tasks": []}))
        with patch.object(guard, "TASKS_PATH", self.tasks), patch.object(guard, "process_is_alive", return_value=False), patch.object(guard, "source_changes", return_value=[]):
            allowed, reason = guard.recovery_ready(self.lease(renewed_minutes_ago=1), allow_early_orphan=True)
        self.assertFalse(allowed)
        self.assertIn("recent owner renewal", reason)

    def test_stale_clean_dead_lease_can_recover_early(self) -> None:
        self.tasks.write_text(json.dumps({"tasks": []}))
        with patch.object(guard, "TASKS_PATH", self.tasks), patch.object(guard, "process_is_alive", return_value=False), patch.object(guard, "source_changes", return_value=[]):
            allowed, reason = guard.recovery_ready(self.lease(renewed_minutes_ago=11), allow_early_orphan=True)
        self.assertTrue(allowed)
        self.assertIn("source is clean", reason)

    def test_dirty_source_blocks_early_recovery(self) -> None:
        self.tasks.write_text(json.dumps({"tasks": []}))
        with patch.object(guard, "TASKS_PATH", self.tasks), patch.object(guard, "process_is_alive", return_value=False), patch.object(guard, "source_changes", return_value=["v2-react/src/main.tsx"]):
            allowed, reason = guard.recovery_ready(self.lease(renewed_minutes_ago=11), allow_early_orphan=True)
        self.assertFalse(allowed)
        self.assertIn("unresolved changes", reason)

    def test_control_tower_closeout_requires_strict_rendered_visual_qc(self) -> None:
        source = Path(guard.__file__).read_text(encoding="utf-8")
        self.assertIn('"--strict-browser", "--strict-visual"', source)
        self.assertIn('"/tmp/control-tower-change-guard.png"', source)


if __name__ == "__main__":
    unittest.main()
