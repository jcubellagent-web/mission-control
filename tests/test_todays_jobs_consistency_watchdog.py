from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_module(relative: str, name: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dashboard_jobs():
    return [
        {"name": "Sorare Daily Missions", "category": "Sorare MLB"},
        {"name": "Gmail Morning Inbox Triage", "category": "Personal Inbox"},
        {"name": "Live Work Board Server", "category": "Maintenance"},
        {"name": "Breaking News Scanner", "category": "J.A.I.N Alerts"},
        {"name": "Job five"},
        {"name": "Job six"},
        {"name": "Job seven"},
        {"name": "Job eight"},
    ]


def write_events(path: Path, events: list[dict]):
    import json

    path.write_text(json.dumps({"events": events}))


def test_check_data_uses_canonical_lane_markers(tmp_path, monkeypatch):
    watchdog = load_module(
        "scripts/todays_jobs_consistency_watchdog.py",
        "todays_jobs_consistency_watchdog_lanes",
    )
    data = tmp_path / "dashboard-data.json"
    import json

    data.write_text(json.dumps({"crons": dashboard_jobs()}))
    monkeypatch.setattr(watchdog, "DATA", data)

    assert watchdog.REQUIRED_ECOSYSTEM_LANES == (
        "sorare",
        "gmail",
        "maintenance",
        "breaking",
    )
    assert watchdog.check_data() == []


def test_check_data_reports_missing_canonical_lane(tmp_path, monkeypatch):
    watchdog = load_module(
        "scripts/todays_jobs_consistency_watchdog.py",
        "todays_jobs_consistency_watchdog_missing_lane",
    )
    data = tmp_path / "dashboard-data.json"
    import json

    rows = dashboard_jobs()
    rows[2] = {"name": "Routine job", "category": "Other"}
    data.write_text(json.dumps({"crons": rows}))
    monkeypatch.setattr(watchdog, "DATA", data)

    assert watchdog.check_data() == ["missing ecosystem lanes: maintenance"]


def test_recovery_requires_latest_exact_lane_event_to_be_blocked(tmp_path, monkeypatch):
    watchdog = load_module(
        "scripts/todays_jobs_consistency_watchdog.py",
        "todays_jobs_consistency_watchdog_recovery",
    )
    events = tmp_path / "shared-events.json"
    monkeypatch.setattr(watchdog, "EVENTS", events)
    blocked = {
        "time": "2026-07-15T06:39:11Z",
        "agent": watchdog.WATCHDOG_AGENT,
        "tool": watchdog.WATCHDOG_TOOL,
        "title": watchdog.WATCHDOG_TITLE,
        "status": "blocked",
        "type": "blocked",
    }
    write_events(events, [blocked])
    assert watchdog.recovery_publish_needed() is True

    completed = {
        **blocked,
        "time": "2026-07-15T08:00:00Z",
        "status": "done",
        "type": "complete",
    }
    write_events(events, [blocked, completed])
    assert watchdog.recovery_publish_needed() is False

    wrong_title = {**blocked, "title": "Different watchdog lane"}
    write_events(events, [wrong_title])
    assert watchdog.recovery_publish_needed() is False


def test_recovery_event_supersedes_blocked_event_with_current_event_key():
    watchdog = load_module(
        "scripts/todays_jobs_consistency_watchdog.py",
        "todays_jobs_consistency_watchdog_event_key",
    )
    dashboard = load_module(
        "scripts/update_mission_control.py",
        "update_mission_control_event_key",
    )
    blocked = {
        "id": "blocked-event",
        "time": "2026-07-15T06:39:11Z",
        "agent": watchdog.WATCHDOG_AGENT,
        "tool": watchdog.WATCHDOG_TOOL,
        "title": watchdog.WATCHDOG_TITLE,
        "status": "blocked",
        "type": "blocked",
    }
    recovered = {
        **blocked,
        "id": "recovery-event",
        "time": "2026-07-15T08:00:00Z",
        "status": "done",
        "type": "complete",
    }

    assert dashboard.event_key(blocked) == dashboard.event_key(recovered)
    assert dashboard.superseded_blocked_event_ids([recovered, blocked]) == {
        "blocked-event"
    }


def test_main_publishes_one_correlated_recovery_only_after_failure(monkeypatch):
    watchdog = load_module(
        "scripts/todays_jobs_consistency_watchdog.py",
        "todays_jobs_consistency_watchdog_main",
    )
    published = []
    monkeypatch.setattr(watchdog, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(watchdog, "check_contract", lambda: [])
    monkeypatch.setattr(watchdog, "check_data", lambda: [])
    monkeypatch.setattr(watchdog, "publish", lambda *args: published.append(args))
    monkeypatch.setattr(watchdog, "recovery_publish_needed", lambda: True)

    assert watchdog.main() == 0
    assert published == [
        (
            "done",
            watchdog.WATCHDOG_TITLE,
            "Today's Jobs consistency recovered; source, data, and regression checks pass.",
        )
    ]

    published.clear()
    monkeypatch.setattr(watchdog, "recovery_publish_needed", lambda: False)
    assert watchdog.main() == 0
    assert published == []


def test_live_work_board_server_is_launchd_owned():
    source = (ROOT / "scripts" / "update_mission_control.py").read_text()
    tree = ast.parse(source)
    targets = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "CRON_TARGETS"
            for target in node.targets
        ):
            targets = ast.literal_eval(node.value)
            break
    assert targets is not None
    target = next(row for row in targets if row.get("name") == "Live Work Board Server")
    assert target["pattern"] == "com.josh20.brain-feed-server"
    assert target["source"] == "launchd"
    assert target["logPath"].endswith("/logs/brain-feed-server.launchd.log")
