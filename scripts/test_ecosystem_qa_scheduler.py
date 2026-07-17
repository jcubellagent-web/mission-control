#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_scheduler_environment_restores_homebrew_tools_for_launchd(self) -> None:
        with mock.patch.dict(subject.os.environ, {"PATH": "/usr/bin"}, clear=True):
            env = subject.scheduler_environment()

        self.assertEqual(env["PATH"].split(":"), ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"])

    def test_recurring_telegram_contract_stress_is_quiet_guarded_and_lease_aware(self) -> None:
        config = json.loads(subject.CONFIG_PATH.read_text(encoding="utf-8"))
        jobs = {row["id"]: row for row in config["jobs"]}
        stress = jobs["telegram-inbox-contract-stress"]

        self.assertEqual(stress["schedule"], {"minutes": [19, 49]})
        self.assertEqual(stress["command"][0], "/opt/homebrew/bin/python3")
        self.assertEqual(stress["command"][stress["command"].index("--iterations") + 1], "100")
        self.assertTrue(stress["skipDuringChangeLease"])
        self.assertNotIn("telegram-inbox-live-canary", jobs)

    def test_configured_precondition_exit_maps_to_skip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(subject, "LOCK_DIR", Path(directory)):
                result = subject.run_job({
                    "id": "precondition-skip",
                    "command": [sys.executable, "-c", "raise SystemExit(75)"],
                    "skipReturnCodes": [75],
                    "timeoutSeconds": 10,
                })

        self.assertEqual(result["status"], "skipped_precondition")
        self.assertEqual(result["returncode"], 75)

    def test_skip_preserves_failure_streak_in_scheduler_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "schedule.json"
            config.write_text(json.dumps({
                "timezone": "UTC",
                "jobs": [{"id": "live", "schedule": {"intervalMinutes": 1}, "command": ["noop"]}],
            }))
            state = root / "state.json"
            state.write_text(json.dumps({
                "jobs": {"live": {"status": "failed", "failureStreak": 2, "lastSlot": "older"}},
            }))
            skipped = {"id": "live", "status": "skipped_precondition", "returncode": 75, "durationMs": 0}
            with mock.patch.object(subject, "STATE_PATH", state), \
                    mock.patch.object(subject, "run_job", return_value=skipped), \
                    mock.patch.object(subject, "publish_transition"):
                result = subject.tick(config, dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc))

        self.assertEqual(result["jobs"]["live"]["status"], "skipped_precondition")
        self.assertEqual(result["jobs"]["live"]["failureStreak"], 2)

    def test_explicit_operator_run_can_verify_during_owned_change_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "schedule.json"
            config.write_text(json.dumps({
                "timezone": "UTC",
                "jobs": [{
                    "id": "release-qa",
                    "schedule": {"intervalMinutes": 1},
                    "command": ["noop"],
                    "skipDuringChangeLease": True,
                }],
            }))
            state = root / "state.json"
            state.write_text(json.dumps({"jobs": {}}))
            completed = {"id": "release-qa", "status": "ok", "returncode": 0, "durationMs": 1}
            with mock.patch.object(subject, "STATE_PATH", state), \
                    mock.patch.object(subject, "change_lease_active", return_value=True), \
                    mock.patch.object(subject, "run_job", return_value=completed) as run, \
                    mock.patch.object(subject, "publish_transition"):
                result = subject.tick(
                    config,
                    dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc),
                    only="release-qa",
                    allow_change_lease=True,
                )

        run.assert_called_once()
        self.assertEqual(result["jobs"]["release-qa"]["status"], "ok")

    def test_scheduler_publishes_running_state_before_job_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "schedule.json"
            config.write_text(json.dumps({
                "timezone": "UTC",
                "jobs": [{"id": "deep-qa", "schedule": {"intervalMinutes": 1}, "command": ["noop"]}],
            }))
            state = root / "state.json"
            state.write_text(json.dumps({"jobs": {"deep-qa": {"status": "failed", "failureStreak": 1}}}))
            observed = {}

            def finish_job(_job, shadow=False):
                observed.update(json.loads(state.read_text()))
                return {"id": "deep-qa", "status": "ok", "returncode": 0, "durationMs": 1}

            with mock.patch.object(subject, "STATE_PATH", state), \
                    mock.patch.object(subject, "run_job", side_effect=finish_job), \
                    mock.patch.object(subject, "publish_transition"):
                subject.tick(config, dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc))

        self.assertEqual(observed["status"], "running")
        self.assertEqual(observed["jobs"]["deep-qa"]["status"], "running")

    def test_clean_run_after_any_safe_skip_publishes_recovery_for_open_streak(self) -> None:
        job = {"id": "live", "owner": "josh2", "team": "Telegram QA", "severity": "p0"}
        current = {"status": "ok", "failureStreak": 0}
        previous = {"status": "skipped_change_lease", "failureStreak": 2}
        with mock.patch.object(subject.subprocess, "run") as publish:
            subject.publish_transition(job, current, previous)

        publish.assert_called_once()
        self.assertIn("complete", publish.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
