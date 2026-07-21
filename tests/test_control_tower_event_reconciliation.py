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


def test_new_generation_terminal_event_closes_old_blocker_for_same_work() -> None:
    blocked = {
        "id": "blocked-generation-1",
        "time": "2026-07-21T22:52:17Z",
        "agent": "joshex",
        "type": "blocked",
        "status": "blocked",
        "tool": "Control Tower change guard",
        "title": "Upgrade awaits push approval",
        "workId": "stable-upgrade-work",
        "generation": 1,
    }
    completed = {
        "id": "complete-generation-2",
        "time": "2026-07-21T22:55:01Z",
        "agent": "joshex",
        "type": "complete",
        "status": "done",
        "tool": "Git push and Control Tower guard",
        "title": "Upgrade released",
        "workId": "stable-upgrade-work",
        "generation": 2,
    }

    assert MODULE.superseded_blocked_event_ids([completed, blocked]) == {"blocked-generation-1"}


def test_terminal_event_for_other_work_does_not_clear_blocker() -> None:
    blocked = {
        "id": "blocked-a",
        "time": "2026-07-21T22:52:17Z",
        "agent": "joshex",
        "type": "blocked",
        "status": "blocked",
        "tool": "guard",
        "title": "Upgrade blocked",
        "workId": "work-a",
    }
    unrelated = {
        "id": "complete-b",
        "time": "2026-07-21T22:55:01Z",
        "agent": "joshex",
        "type": "complete",
        "status": "done",
        "tool": "guard",
        "title": "Upgrade done",
        "workId": "work-b",
    }

    assert MODULE.superseded_blocked_event_ids([unrelated, blocked]) == set()


def test_shared_event_projection_preserves_lifecycle_keys(monkeypatch, tmp_path) -> None:
    event_path = tmp_path / "shared-events.json"
    event_path.write_text(
        __import__("json").dumps({"events": [{
            "id": "event-1",
            "time": "2026-07-21T22:55:01Z",
            "privacy": "dashboard-safe",
            "title": "Upgrade released",
            "status": "done",
            "workId": "stable-upgrade-work",
            "runId": "run-2",
            "generation": 2,
            "sequence": 2,
        }]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(MODULE, "SHARED_EVENTS_PATH", event_path)

    rows = MODULE.fetch_shared_events("2026-07-21T23:00:00Z")

    assert rows[0]["workId"] == "stable-upgrade-work"
    assert rows[0]["runId"] == "run-2"
    assert rows[0]["generation"] == 2
    assert rows[0]["sequence"] == 2


def test_all_safe_skip_states_preserve_an_open_scheduler_incident() -> None:
    for run_state in ("skipped_precondition", "skipped_change_lease", "skipped_locked"):
        assert MODULE.qa_run_needs_attention(run_state, 1) is True
        assert MODULE.qa_run_needs_attention(run_state, 0) is False
    assert MODULE.qa_run_needs_attention("ok", 3) is False
