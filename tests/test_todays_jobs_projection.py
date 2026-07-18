from __future__ import annotations

import datetime as dt
import json
import plistlib
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.todays_jobs_projection import (
    discover_codex_automations,
    discover_hermes_definitions,
    discover_launchd_definitions,
    discover_qa_definitions,
    materialize_today_jobs,
    parse_crontab_definitions,
)


ET = ZoneInfo("America/New_York")
NOW = dt.datetime(2026, 7, 17, 12, 0, tzinfo=ET)


def test_crontab_inventory_is_native_and_metadata_is_not_an_allowlist() -> None:
    overrides = [{
        "name": "Friendly First",
        "pattern": "first_job.py",
        "schedule": "Wrong hand-authored schedule",
        "description": "Presentation metadata",
        "category": "Maintenance",
        "agent": "JOSH 2.0",
    }, {
        "name": "Phantom",
        "pattern": "not-installed.py",
        "schedule": "Daily 1:00 AM ET",
        "agent": "JOSH 2.0",
    }]
    listing = "\n".join((
        "30 8 * * * /usr/bin/python3 /srv/first_job.py",
        "45 9 * * * /usr/bin/python3 /srv/new_job.py",
    ))

    definitions = parse_crontab_definitions(
        listing, owner="josh2", agent="JOSH 2.0", overrides=overrides
    )

    assert [row["name"] for row in definitions] == ["Friendly First", "New Job"]
    assert definitions[0]["schedule"] == "Daily 8:30 AM ET"
    assert all(row["name"] != "Phantom" for row in definitions)
    assert all("/srv/" not in json.dumps(row) for row in definitions)


def test_crontab_add_delete_changes_inventory_without_code_edits() -> None:
    original = parse_crontab_definitions(
        "0 8 * * * /jobs/alpha.py\n0 9 * * * /jobs/beta.py",
        owner="jain",
        agent="J.A.I.N",
    )
    changed = parse_crontab_definitions(
        "0 9 * * * /jobs/beta.py\n0 10 * * * /jobs/gamma.py",
        owner="jain",
        agent="J.A.I.N",
    )

    original_ids = {row["name"]: row["definitionId"] for row in original}
    changed_ids = {row["name"]: row["definitionId"] for row in changed}
    assert set(original_ids) == {"Alpha", "Beta"}
    assert set(changed_ids) == {"Beta", "Gamma"}
    assert original_ids["Beta"] == changed_ids["Beta"]


def test_codex_automation_discovery_keeps_prompt_private_and_disabled_visible(tmp_path: Path) -> None:
    active = tmp_path / "active"
    paused = tmp_path / "paused"
    active.mkdir()
    paused.mkdir()
    (active / "automation.toml").write_text(
        'id = "active"\nname = "Active Digest"\nprompt = "private raw prompt"\n'
        'status = "ACTIVE"\nrrule = "FREQ=DAILY;BYHOUR=8;BYMINUTE=15"\n'
        "created_at = 1784260800000\n"
    )
    (paused / "automation.toml").write_text(
        'id = "paused"\nname = "Paused Digest"\nstatus = "PAUSED"\n'
        'rrule = "FREQ=WEEKLY;BYDAY=FR;BYHOUR=9;BYMINUTE=0"\n'
    )

    rows = discover_codex_automations(tmp_path)

    assert {row["name"] for row in rows} == {"Active Digest", "Paused Digest"}
    assert next(row for row in rows if row["name"] == "Active Digest")["enabled"] is True
    assert next(row for row in rows if row["name"] == "Paused Digest")["enabled"] is False
    assert "private raw prompt" not in json.dumps(rows)


def test_launchd_and_launchctl_discovery_uses_definition_schedule(tmp_path: Path) -> None:
    plist_path = tmp_path / "com.josh20.example.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump({
            "Label": "com.josh20.example",
            "ProgramArguments": ["/usr/bin/python3", "/workspace/example.py"],
            "StartCalendarInterval": {"Hour": 6, "Minute": 5},
        }, handle)

    rows = discover_launchd_definitions(
        [plist_path], active_labels=["com.josh20.example"]
    )

    assert len(rows) == 1
    assert rows[0]["definitionId"].startswith("launchd:josh2:")
    assert rows[0]["schedule"] == "6:05 AM ET"
    assert rows[0]["active"] is True


def test_hermes_and_qa_definitions_keep_stable_ids_and_run_evidence() -> None:
    hermes = discover_hermes_definitions([{
        "id": "daily-prep",
        "name": "Daily Prep",
        "schedule": "0 7 * * *",
        "enabled": True,
        "last_run_at": "2026-07-17T10:59:00Z",
        "last_status": "ok",
        "duration_ms": 3300,
    }])
    qa = discover_qa_definitions({"jobs": [{
        "id": "layout-check",
        "owner": "josh2",
        "team": "Runtime UI QA",
        "schedule": {"minutes": [7], "hours": [6]},
    }]}, {"jobs": {"layout-check": {
        "status": "ok",
        "completedAt": "2026-07-17T10:08:00Z",
        "durationMs": 2400,
    }}})

    assert hermes[0]["definitionId"].startswith("hermes:jaimes:")
    assert hermes[0]["lastRun"] == "2026-07-17T10:59:00Z"
    assert qa[0]["definitionId"].startswith("ecosystem-qa-scheduler:josh2:")
    assert qa[0]["runStatus"] == "done"


def _definition(
    name: str,
    hour: int,
    *,
    status: str = "ok",
    run_status: str = "upcoming",
    last_run: str | None = None,
    errors: int = 0,
    enabled: bool = True,
) -> dict:
    return {
        "definitionId": f"test:josh2:{name.lower()}",
        "name": name,
        "agent": "JOSH 2.0",
        "source": "cron",
        "sourceLabel": "Josh Local Cron",
        "schedule": f"Daily {hour}:00 ET",
        "scheduleSpec": {"kind": "cron", "expression": f"0 {hour} * * *"},
        "status": status,
        "runStatus": run_status,
        "lastRun": last_run,
        "errors": errors,
        "enabled": enabled,
    }


def test_materialized_occurrences_are_chronological_and_use_exact_outcomes() -> None:
    definitions = [
        _definition("Pending", 13),
        _definition("Broken", 11, status="error", run_status="missed", errors=1),
        _definition("Complete", 8, run_status="done", last_run="2026-07-17T08:01:00-04:00"),
        _definition("Skipped", 9, status="paused", run_status="paused", enabled=False),
        _definition("Running", 12, run_status="running"),
    ]

    rows, meta = materialize_today_jobs(definitions, now=NOW)

    assert [row["name"] for row in rows] == ["Complete", "Skipped", "Broken", "Running", "Pending"]
    assert {row["name"]: row["outcome"] for row in rows} == {
        "Complete": "complete",
        "Skipped": "skipped",
        "Broken": "broken",
        "Running": "pending",
        "Pending": "pending",
    }
    assert next(row for row in rows if row["name"] == "Running")["runStatus"] == "running"
    assert all(row["status"] == row["outcome"] for row in rows)
    assert meta["nowIndex"] == 4
    assert meta["nextOccurrenceId"] == rows[4]["occurrenceId"]
    assert meta["counts"] == {"complete": 1, "skipped": 1, "broken": 1, "pending": 2}


def test_high_frequency_jobs_roll_up_but_normal_rows_have_no_silent_cap() -> None:
    high_frequency = {
        "definitionId": "qa:josh2:refresh",
        "name": "Refresh",
        "agent": "JOSH 2.0",
        "source": "ecosystem_qa_scheduler",
        "schedule": "Every 2 min",
        "scheduleSpec": {"kind": "qa", "intervalMinutes": 2, "offset": 0},
        "status": "ok",
        "runStatus": "done",
        "lastRun": "2026-07-17T11:58:00-04:00",
    }
    daily = [_definition(f"Job {index:03d}", index % 24) for index in range(100)]

    rows, meta = materialize_today_jobs([high_frequency, *daily], now=NOW)

    rollup = next(row for row in rows if row["name"] == "Refresh")
    assert rollup["rolledUp"] is True
    assert rollup["expectedRuns"] == 720
    assert rollup["outcome"] == "complete"
    assert len(rows) == 101
    assert meta["occurrenceCount"] == 101
    assert meta["definitionCount"] == 101
    assert meta["rolledUpDefinitionCount"] == 1


def test_occurrence_ids_are_stable_for_same_definition_and_day() -> None:
    definition = _definition("Stable", 8, run_status="done", last_run="2026-07-17T08:00:00-04:00")

    first, _ = materialize_today_jobs([definition], now=NOW)
    second, _ = materialize_today_jobs([definition], now=NOW + dt.timedelta(minutes=1))

    assert first[0]["occurrenceId"] == second[0]["occurrenceId"]
