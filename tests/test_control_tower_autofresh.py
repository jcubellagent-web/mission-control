from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import control_tower_autofresh_review as review
import control_tower_priority_autofix as autofix


def test_repaired_alert_does_not_become_unresolved_when_other_work_fails(monkeypatch, tmp_path) -> None:
    state = tmp_path / "ops.json"
    monkeypatch.setattr(autofix, "STATE_PATH", state)
    alert = {"title": "Runtime SRE", "detail": "contract drift"}

    autofix.update_ops_state([alert], [], fixed=False, ok=False)
    result = autofix.update_ops_state([alert], [], fixed=False, ok=False)

    recurring = result["recurringAlerts"]
    assert recurring[0]["unresolvedCount"] == 0
    assert recurring[0]["active"] is False
    assert result["recommendations"] == []


def test_only_alerts_surviving_repair_are_active_and_recommended(monkeypatch, tmp_path) -> None:
    state = tmp_path / "ops.json"
    monkeypatch.setattr(autofix, "STATE_PATH", state)
    alert = {"title": "Control Tower layout issue", "detail": "overflow"}

    autofix.update_ops_state([alert], [alert], fixed=False, ok=False)
    result = autofix.update_ops_state([alert], [alert], fixed=False, ok=False)

    assert result["activeAlertKeys"] == [autofix.alert_key(alert)]
    assert result["recurringAlerts"][0]["active"] is True
    assert result["recurringAlerts"][0]["unresolvedCount"] == 2
    assert result["recommendations"]


def test_historical_alert_absent_from_current_dashboard_never_publishes(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "ops.json"
    current = {"title": "Different current issue", "detail": "current"}
    stale = {"title": "Old issue", "detail": "recovered"}
    payload = {
        "schema": 2,
        "checkedAt": "2026-07-19T20:00:00Z",
        "activeAlertKeys": [autofix.alert_key(stale)],
        "recurringAlerts": [{
            "key": autofix.alert_key(stale),
            "title": stale["title"],
            "active": True,
            "unresolvedCount": 4,
            "recommendation": "Old recommendation",
        }],
    }
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(review, "STATE_PATH", state_path)
    monkeypatch.setattr(review, "load_dashboard", lambda: {})
    monkeypatch.setattr(review, "priority_alerts", lambda data: [current])
    monkeypatch.setattr(review, "parse_ts", lambda value: review.dt.datetime.now(review.dt.timezone.utc))
    published = []
    monkeypatch.setattr(review, "publish", lambda *args: published.append(args) or True)
    monkeypatch.setattr(sys, "argv", ["control_tower_autofresh_review.py", "--publish-current-only"])

    assert review.main() == 0
    assert published == []


def test_identical_active_incident_publishes_once(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "ops.json"
    alert = {"title": "Control Tower layout issue", "detail": "overflow"}
    key = autofix.alert_key(alert)
    payload = {
        "schema": 2,
        "checkedAt": "2026-07-19T20:00:00Z",
        "activeAlertKeys": [key],
        "recurringAlerts": [{
            "key": key,
            "title": alert["title"],
            "active": True,
            "unresolvedCount": 3,
            "recommendation": "Review layout",
        }],
    }
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(review, "STATE_PATH", state_path)
    monkeypatch.setattr(review, "load_dashboard", lambda: {})
    monkeypatch.setattr(review, "priority_alerts", lambda data: [alert])
    monkeypatch.setattr(review, "parse_ts", lambda value: review.dt.datetime.now(review.dt.timezone.utc))
    published = []
    monkeypatch.setattr(review, "publish", lambda *args: published.append(args) or True)
    monkeypatch.setattr(sys, "argv", ["control_tower_autofresh_review.py", "--publish-current-only"])

    assert review.main() == 0
    assert review.main() == 0
    assert len(published) == 1
