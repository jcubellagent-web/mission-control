#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import unittest

import ecosystem_qa_scheduler as subject


class SchedulerTests(unittest.TestCase):
    def test_interval_offset(self) -> None:
        job = {"schedule": {"intervalMinutes": 5, "offset": 1}}
        self.assertTrue(subject.is_due(job, dt.datetime(2026, 7, 15, 10, 11)))
        self.assertFalse(subject.is_due(job, dt.datetime(2026, 7, 15, 10, 12)))

    def test_exact_schedule(self) -> None:
        monday = dt.datetime(2026, 7, 13, 4, 13)
        job = {"schedule": {"minutes": [13], "hours": [4], "weekdays": [0]}}
        self.assertTrue(subject.is_due(job, monday))
        self.assertFalse(subject.is_due(job, monday.replace(hour=5)))

    def test_inbox_cleanup_is_a_safe_daily_terminal_retention_job(self) -> None:
        config = json.loads(subject.CONFIG_PATH.read_text(encoding="utf-8"))
        job = next(row for row in config["jobs"] if row.get("id") == "inbox-coordinator-retention")
        self.assertEqual(job["schedule"], {"minutes": [33], "hours": [3]})
        self.assertTrue(subject.is_due(job, dt.datetime(2026, 7, 15, 3, 33)))
        self.assertFalse(subject.is_due(job, dt.datetime(2026, 7, 15, 3, 34)))
        self.assertEqual(
            job["command"],
            ["python3", "scripts/inbox_coordinator.py", "cleanup", "--max-age-seconds", "86400"],
        )
        self.assertNotIn("--include-queued", job["command"])

    def test_visual_jobs_use_the_browser_capable_python_runtime(self) -> None:
        config = json.loads(subject.CONFIG_PATH.read_text(encoding="utf-8"))
        jobs = {row["id"]: row for row in config["jobs"]}
        for job_id in ("runtime-layout-check", "full-kiosk-visual"):
            self.assertEqual(jobs[job_id]["command"][0], "/opt/homebrew/bin/python3")


if __name__ == "__main__":
    unittest.main()
