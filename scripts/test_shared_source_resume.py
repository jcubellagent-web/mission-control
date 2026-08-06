#!/usr/bin/env python3
"""Focused regression tests for shared_source_resume."""
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("shared_source_resume", Path(__file__).with_name("shared_source_resume.py"))
resume = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(resume)


class SharedSourceResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.queue = Path(self.temp.name) / "queue.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def args(self, **overrides: str) -> argparse.Namespace:
        values = {"owner": "jaimes", "requester": "joshex", "title": "Resume safe source work", "objective": "Apply the prepared dashboard-safe source improvement.", "priority": "high", "approval": "approved", "capability": [], "artifact": ["scripts/example.py"]}
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_defer_is_idempotent_and_rejects_secret_material(self) -> None:
        first = resume.defer(self.args(), path=self.queue)
        second = resume.defer(self.args(), path=self.queue)
        self.assertEqual(first["status"], "deferred")
        self.assertEqual(second["status"], "already_registered")
        with self.assertRaises(SystemExit):
            resume.defer(self.args(objective="token=super-secret"), path=self.queue)

    def test_tick_waits_for_any_active_lease(self) -> None:
        resume.defer(self.args(), path=self.queue)
        with patch.object(resume, "active_leases", return_value={"global": {"expiresAt": "2999-01-01T00:00:00Z"}, "scoped": []}):
            output = resume.tick(path=self.queue)
        self.assertEqual(output["resumed"], [])
        self.assertIn("blockedBy", output["deferred"][0]["reason"])

    def test_tick_resumes_once_after_preflight(self) -> None:
        resume.defer(self.args(), path=self.queue)
        with patch.object(resume, "active_leases", return_value={"global": None, "scoped": []}), patch.object(resume, "run_json", return_value=(0, {"ok": True, "leaseOwner": None}, "")), patch.object(resume, "create_continuation", return_value=(True, "task-resume-test", "created")) as created:
            first = resume.tick(path=self.queue)
            second = resume.tick(path=self.queue)
        self.assertEqual(first["resumed"][0]["taskId"], "task-resume-test")
        self.assertEqual(second["resumed"], [])
        created.assert_called_once()

    def test_preflight_failure_keeps_entry_deferred(self) -> None:
        resume.defer(self.args(), path=self.queue)
        with patch.object(resume, "active_leases", return_value={"global": None, "scoped": []}), patch.object(resume, "run_json", return_value=(1, {"ok": False, "reasons": ["source changes"]}, "")):
            output = resume.tick(path=self.queue)
        self.assertEqual(output["resumed"], [])
        stored = json.loads(self.queue.read_text())["entries"][0]
        self.assertEqual(stored["status"], "deferred")
        self.assertIn("source changes", stored["lastError"])


if __name__ == "__main__":
    unittest.main()
