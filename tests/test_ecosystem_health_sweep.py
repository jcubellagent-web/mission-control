from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "ecosystem_health_sweep.py"
SPEC = importlib.util.spec_from_file_location("ecosystem_health_sweep_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def _healthy_agents() -> list[dict]:
    return [
        {"agent": agent, "ok": True, "status": "ok", "stale": False}
        for agent in subject.REQUIRED_AGENTS
    ]


def _run_main(monkeypatch, dashboard: dict) -> tuple[int, dict]:
    written: dict = {}

    def fake_read(path: Path, default):
        if path == subject.DASHBOARD_PATH:
            return dashboard
        if path == subject.HEARTBEATS_PATH:
            return {"heartbeats": []}
        return default

    monkeypatch.setattr(subject, "read_json", fake_read)
    monkeypatch.setattr(subject, "latest_agent_rows", lambda *_args: (_healthy_agents(), 120))
    monkeypatch.setattr(subject, "model_status_ok", lambda: True)
    monkeypatch.setattr(subject, "write_json", lambda _path, payload: written.update(payload))
    monkeypatch.setattr(sys, "argv", ["ecosystem_health_sweep.py"])
    return subject.main(), written


def test_qa_aggregate_failures_remain_visible_without_recursing_into_health(monkeypatch) -> None:
    dashboard = {
        "actionRequired": [{
            "priority": "high",
            "title": "Deep release QA needs attention",
            "qaMetaOnly": True,
        }],
        "crons": [{
            "name": "Deep release QA",
            "status": "error",
            "runStatus": "missed",
            "errors": 1,
            "qaJobId": "nightly-control-tower-suite",
            "qaMeta": True,
        }],
    }

    returncode, result = _run_main(monkeypatch, dashboard)

    assert returncode == 0
    assert result["ok"] is True
    assert result["cronAttention"] == []
    assert result["qaMetaAttention"][0]["qaJobId"] == "nightly-control-tower-suite"
    assert result["qaMetaActionRequired"][0]["qaMetaOnly"] is True
    assert result["operationalActionRequiredCount"] == 0
    assert result["nonBlockingActionRequiredCount"] == 0


def test_adaptive_quality_failure_cannot_recursively_fail_its_health_input(monkeypatch) -> None:
    dashboard = {
        "actionRequired": [{
            "priority": "high",
            "title": "Adaptive QA/QC snapshot needs attention",
            "qaMetaOnly": True,
        }],
        "crons": [{
            "name": "Adaptive QA/QC snapshot",
            "status": "error",
            "runStatus": "missed",
            "errors": 1,
            "qaJobId": "adaptive-quality-snapshot",
            "qaMeta": False,
        }],
    }

    returncode, result = _run_main(monkeypatch, dashboard)

    assert returncode == 0
    assert result["ok"] is True
    assert result["operationalCronAttention"] == []
    assert result["qaMetaAttention"][0]["qaJobId"] == "adaptive-quality-snapshot"


def test_real_operational_cron_failure_still_fails_health(monkeypatch) -> None:
    dashboard = {
        "actionRequired": [],
        "crons": [{
            "name": "Telegram delivery",
            "status": "error",
            "runStatus": "missed",
            "errors": 1,
            "qaMeta": False,
        }],
    }

    returncode, result = _run_main(monkeypatch, dashboard)

    assert returncode == 1
    assert result["ok"] is False
    assert result["cronAttention"][0]["name"] == "Telegram delivery"


def test_high_priority_non_aggregate_action_still_fails_health(monkeypatch) -> None:
    dashboard = {
        "actionRequired": [{"priority": "high", "title": "Runtime outage", "qaMetaOnly": False}],
        "crons": [],
    }

    returncode, result = _run_main(monkeypatch, dashboard)

    assert returncode == 1
    assert result["blockingActionRequiredCount"] == 1


def test_medium_operational_action_is_nonblocking_and_disjoint_from_qa_meta(monkeypatch) -> None:
    dashboard = {
        "actionRequired": [
            {"priority": "medium", "title": "Retry inbox surface", "qaMetaOnly": False},
            {"priority": "high", "title": "Deep release QA", "qaMetaOnly": True},
        ],
        "crons": [],
    }

    returncode, result = _run_main(monkeypatch, dashboard)

    assert returncode == 0
    assert result["operationalActionRequiredCount"] == 1
    assert result["blockingActionRequiredCount"] == 0
    assert result["nonBlockingActionRequiredCount"] == 1
    assert len(result["qaMetaActionRequired"]) == 1
