from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from todays_jobs_projection import (
        discover_codex_automations,
        discover_hermes_definitions,
        discover_launchd_definitions,
        discover_qa_definitions,
        materialize_today_jobs,
        parse_crontab_definitions,
    )
except ModuleNotFoundError:
    from scripts.todays_jobs_projection import (
        discover_codex_automations,
        discover_hermes_definitions,
        discover_launchd_definitions,
        discover_qa_definitions,
        materialize_today_jobs,
        parse_crontab_definitions,
    )


ET = ZoneInfo("America/New_York")


class TodaysJobsProjectionTests(unittest.TestCase):
    def test_malformed_launchd_plist_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as raw:
            invalid = Path(raw) / "com.josh20.invalid.plist"
            invalid.write_text("<plist></plist>junk", encoding="utf-8")
            self.assertEqual(discover_launchd_definitions([invalid]), [])

    def test_inventory_comes_from_native_definitions(self) -> None:
        overrides = [
            {"pattern": "real.py", "name": "Friendly Real", "schedule": "Daily 1:00 AM ET"},
            {"pattern": "phantom.py", "name": "Phantom", "schedule": "Daily 2:00 AM ET"},
        ]
        rows = parse_crontab_definitions(
            "30 8 * * * /usr/bin/python3 /jobs/real.py\n45 9 * * * /jobs/new.py",
            owner="josh2",
            agent="JOSH 2.0",
            overrides=overrides,
        )
        self.assertEqual([row["name"] for row in rows], ["Friendly Real", "New"])
        self.assertEqual(rows[0]["schedule"], "Daily 8:30 AM ET")
        self.assertNotIn("Phantom", {row["name"] for row in rows})

    def test_add_delete_keeps_unchanged_definition_identity(self) -> None:
        old = parse_crontab_definitions(
            "0 8 * * * /jobs/alpha.py\n0 9 * * * /jobs/beta.py",
            owner="jain",
            agent="J.A.I.N",
        )
        new = parse_crontab_definitions(
            "0 9 * * * /jobs/beta.py\n0 10 * * * /jobs/gamma.py",
            owner="jain",
            agent="J.A.I.N",
        )
        old_by_name = {row["name"]: row["definitionId"] for row in old}
        new_by_name = {row["name"]: row["definitionId"] for row in new}
        self.assertEqual(set(old_by_name), {"Alpha", "Beta"})
        self.assertEqual(set(new_by_name), {"Beta", "Gamma"})
        self.assertEqual(old_by_name["Beta"], new_by_name["Beta"])

    def test_codex_discovery_never_projects_prompts(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as raw:
            automation = Path(raw) / "digest"
            automation.mkdir()
            (automation / "automation.toml").write_text(
                'id = "digest"\nname = "Daily Digest"\nprompt = "private prompt"\n'
                'status = "ACTIVE"\nrrule = "FREQ=DAILY;BYHOUR=8;BYMINUTE=15"\n'
            )
            rows = discover_codex_automations(Path(raw))
        self.assertEqual(len(rows), 1)
        self.assertNotIn("private prompt", str(rows))

    def test_codex_status_sidecar_can_supply_native_schedule(self) -> None:
        rows = discover_codex_automations(
            Path("/private/tmp/no-such-codex-automations"),
            status_payload={"automations": {"remote-digest": {
                "present": True,
                "active": True,
                "name": "Remote Digest",
                "rrule": "FREQ=DAILY;BYHOUR=7;BYMINUTE=30",
            }}},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Remote Digest")
        self.assertEqual(rows[0]["scheduleSpec"]["kind"], "rrule")

    def test_hermes_native_schedule_filters_non_today_jobs(self) -> None:
        rows = discover_hermes_definitions([
            {
                "id": "weekly-audit",
                "name": "JAIMES Cron Brain Feed Audit",
                "enabled": True,
                "schedule": {"kind": "cron", "expr": "0 6 * * 1", "display": "0 6 * * 1"},
                "last_run_at": "2026-07-13T06:12:54-04:00",
                "last_status": "error",
                "next_run_at": "2026-07-20T06:00:00-04:00",
            },
            {
                "id": "monthly-cleanup",
                "name": "Monthly cleanup",
                "enabled": True,
                "schedule": {"kind": "cron", "expr": "0 4 1 * *"},
                "last_run_at": "2026-07-01T04:00:00-04:00",
                "last_status": "ok",
            },
            {
                "id": "six-hour-watchdog",
                "name": "Agent Account Auth Watchdog",
                "enabled": True,
                "schedule": {"kind": "interval", "minutes": 360, "display": "every 360m"},
                "last_run_at": "2026-07-17T18:45:37-04:00",
                "last_status": "ok",
                "next_run_at": "2026-07-18T00:45:37-04:00",
            },
        ])
        projected, _ = materialize_today_jobs(
            rows, now=dt.datetime(2026, 7, 17, 22, 30, tzinfo=ET)
        )
        self.assertNotIn("JAIMES Cron Brain Feed Audit", {row["name"] for row in projected})
        self.assertNotIn("Monthly cleanup", {row["name"] for row in projected})
        watchdog = [row for row in projected if row["name"] == "Agent Account Auth Watchdog"]
        self.assertEqual(len(watchdog), 1)
        self.assertEqual(watchdog[0]["scheduledTime"], "Coverage")
        self.assertEqual(watchdog[0]["outcome"], "complete")
        self.assertEqual(watchdog[0]["runStatus"], "coverage-current")

    def test_historical_failure_is_not_reused_but_missing_today_becomes_overdue(self) -> None:
        historical = {
            "definitionId": "hermes:jaimes:daily-audit",
            "name": "Daily audit",
            "agent": "JAIMES",
            "source": "hermes",
            "schedule": "Daily 6:00 AM ET",
            "scheduleSpec": {"kind": "cron", "expression": "0 6 * * *"},
            "status": "ok",
            "rawRunStatus": "error",
            "errors": 0,
            "lastRun": "2026-07-13T06:12:54-04:00",
        }
        current = {**historical, "definitionId": "hermes:jaimes:current", "name": "Current failure", "errors": 1, "lastRun": "2026-07-17T06:02:00-04:00"}
        future = {**historical, "definitionId": "hermes:jaimes:future", "name": "Future run", "schedule": "Daily 11:00 PM ET", "scheduleSpec": {"kind": "cron", "expression": "0 23 * * *"}, "rawRunStatus": "ok", "runStatus": "done", "lastRun": "2026-07-17T22:00:00-04:00"}
        rows, _ = materialize_today_jobs(
            [historical, current, future],
            now=dt.datetime(2026, 7, 17, 22, 30, tzinfo=ET),
        )
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["Daily audit"]["outcome"], "broken")
        self.assertEqual(by_name["Daily audit"]["runStatus"], "overdue")
        self.assertIsNone(by_name["Daily audit"]["lastRun"])
        self.assertEqual(by_name["Daily audit"]["previousRun"], "2026-07-13T06:12:54-04:00")
        self.assertEqual(by_name["Current failure"]["outcome"], "broken")
        self.assertEqual(by_name["Future run"]["outcome"], "pending")

    def test_daemons_and_unknown_schedules_are_not_daily_occurrences(self) -> None:
        rows, meta = materialize_today_jobs([
            {"definitionId": "launchd:josh2:gateway", "name": "Gateway", "agent": "JOSH 2.0", "source": "launchd", "scheduleSpec": {"kind": "daemon"}, "status": "ok"},
            {"definitionId": "hermes:jaimes:unknown", "name": "Unknown", "agent": "JAIMES", "source": "hermes", "scheduleSpec": {"kind": "unknown"}, "status": "ok"},
        ], now=dt.datetime(2026, 7, 17, 22, 30, tzinfo=ET))
        self.assertEqual(rows, [])
        self.assertEqual(meta["occurrenceCount"], 0)

    def test_statuses_rollups_and_uncapped_occurrences(self) -> None:
        def daily(name: str, hour: int, **extra):
            return {
                "definitionId": f"test:josh2:{name}",
                "name": name,
                "agent": "JOSH 2.0",
                "source": "cron",
                "schedule": f"Daily {hour}:00 ET",
                "scheduleSpec": {"kind": "cron", "expression": f"0 {hour} * * *"},
                "status": "ok",
                "runStatus": "upcoming",
                **extra,
            }

        high_frequency = discover_qa_definitions({"jobs": [{
            "id": "refresh",
            "owner": "josh2",
            "team": "Refresh",
            "schedule": {"intervalMinutes": 2, "offset": 0},
        }]}, {"jobs": {"refresh": {
            "status": "ok",
            "completedAt": "2026-07-17T15:58:00Z",
            "durationMs": 1200,
        }}})[0]
        definitions = [
            high_frequency,
            daily("complete", 8, runStatus="done", lastRun="2026-07-17T08:01:00-04:00"),
            daily("skipped", 9, status="paused", runStatus="paused", enabled=False),
            daily("broken", 10, status="error", runStatus="missed", errors=1),
            daily("running", 12, runStatus="running"),
            *[daily(f"job-{index:03d}", index % 24) for index in range(100)],
        ]
        rows, meta = materialize_today_jobs(
            definitions,
            now=dt.datetime(2026, 7, 17, 12, 0, tzinfo=ET),
        )
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["complete"]["outcome"], "complete")
        self.assertEqual(by_name["complete"]["status"], "complete")
        self.assertEqual(by_name["skipped"]["outcome"], "skipped")
        self.assertEqual(by_name["broken"]["outcome"], "broken")
        self.assertEqual(by_name["running"]["outcome"], "pending")
        self.assertEqual(by_name["running"]["runStatus"], "running")
        self.assertTrue(by_name["Refresh"]["rolledUp"])
        self.assertEqual(by_name["Refresh"]["expectedRuns"], 720)
        self.assertEqual(len(rows), 105)
        self.assertEqual(meta["occurrenceCount"], 105)
        self.assertEqual(meta["definitionCount"], 105)
        self.assertEqual(meta["timezone"], "America/New_York")


if __name__ == "__main__":
    unittest.main()
