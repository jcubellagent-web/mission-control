from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_mission_control.py"
SPEC = importlib.util.spec_from_file_location("update_mission_control_event_tests", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_scheduler_recovery_supersedes_matching_attention_event() -> None:
    blocked = {
        "id": "blocked-1",
        "time": "2026-07-15T08:21:42Z",
        "agent": "josh2",
        "type": "blocked",
        "status": "blocked",
        "tool": "ecosystem QA scheduler",
        "title": "Runtime SRE: runtime-service-probe needs attention",
    }
    recovered = {
        "id": "complete-1",
        "time": "2026-07-15T08:27:04Z",
        "agent": "josh2",
        "type": "complete",
        "status": "done",
        "tool": "ecosystem QA scheduler",
        "title": "Runtime SRE: runtime-service-probe recovered",
    }

    assert MODULE.superseded_blocked_event_ids([blocked, recovered]) == {"blocked-1"}


def test_scheduler_recovery_does_not_clear_a_different_job() -> None:
    blocked = {
        "id": "blocked-1",
        "time": "2026-07-15T08:21:42Z",
        "agent": "josh2",
        "type": "blocked",
        "status": "blocked",
        "tool": "ecosystem QA scheduler",
        "title": "Runtime SRE: runtime-service-probe needs attention",
    }
    unrelated = {
        "id": "complete-2",
        "time": "2026-07-15T08:27:04Z",
        "agent": "josh2",
        "type": "complete",
        "status": "done",
        "tool": "ecosystem QA scheduler",
        "title": "Route QA: route-benchmark recovered",
    }

    assert MODULE.superseded_blocked_event_ids([unrelated, blocked]) == set()


def test_newer_blocker_is_not_cleared_by_an_older_recovery() -> None:
    recovered = {
        "id": "complete-1",
        "time": "2026-07-15T08:20:00Z",
        "agent": "josh2",
        "type": "complete",
        "status": "done",
        "tool": "ecosystem QA scheduler",
        "title": "Runtime SRE: runtime-service-probe recovered",
    }
    blocked = {
        "id": "blocked-2",
        "time": "2026-07-15T08:21:42Z",
        "agent": "josh2",
        "type": "blocked",
        "status": "blocked",
        "tool": "ecosystem QA scheduler",
        "title": "Runtime SRE: runtime-service-probe needs attention",
    }

    assert MODULE.superseded_blocked_event_ids([recovered, blocked]) == set()
