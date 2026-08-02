from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import agent_task as closeout  # noqa: E402
import control_tower_change_guard as guard  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(closeout, "SOURCE_STATE_DIR", state)
    monkeypatch.setattr(closeout, "SOURCE_LEASE_PATH", state / "control-tower-change-lock.json")
    monkeypatch.setattr(closeout, "SCOPED_SOURCE_LEASES_PATH", state / "scoped-change-leases.json")
    monkeypatch.setattr(closeout, "SOURCE_CLOSEOUT_DIR", state / "agent-source-closeouts")
    monkeypatch.setattr(closeout, "SOURCE_LIFECYCLE_LOCK_PATH", state / "agent-source-lifecycle.lock")
    monkeypatch.setattr(guard, "TASKS_PATH", data / "agent-task-queue.json")
    monkeypatch.setattr(guard, "CLOSEOUT_DIR", state / "agent-source-closeouts")


def source_task() -> dict:
    return {
        "id": "task-a", "workId": "work-a", "runId": "run-a",
        "status": "active", "workScope": "shared-source",
    }


def test_source_lease_requires_exact_open_task_binding() -> None:
    guard.TASKS_PATH.write_text(json.dumps({"tasks": [source_task()]}), encoding="utf-8")
    assert guard.require_open_source_task("task-a", "work-a", "run-a")["status"] == "active"
    with pytest.raises(SystemExit, match="does not match"):
        guard.require_open_source_task("task-a", "work-a", "wrong-run")


def test_terminal_source_task_fails_without_matching_receipt() -> None:
    with pytest.raises(SystemExit, match="evidence is missing"):
        closeout.validate_terminal_source_closeout(source_task())


def test_active_matching_lease_blocks_terminal_transition() -> None:
    closeout.SOURCE_LEASE_PATH.parent.mkdir(parents=True)
    closeout.SOURCE_LEASE_PATH.write_text(json.dumps({"taskBinding": closeout.source_task_binding(source_task())}))
    with pytest.raises(SystemExit, match="still active"):
        closeout.validate_terminal_source_closeout(source_task())


def test_active_matching_scoped_lease_blocks_terminal_transition() -> None:
    closeout.SCOPED_SOURCE_LEASES_PATH.parent.mkdir(parents=True)
    closeout.SCOPED_SOURCE_LEASES_PATH.write_text(json.dumps({
        "version": 1,
        "leases": [{"taskBinding": closeout.source_task_binding(source_task())}],
    }))
    with pytest.raises(SystemExit, match="scoped source lease is still active"):
        closeout.validate_terminal_source_closeout(source_task())


def test_finished_receipt_satisfies_exact_source_task() -> None:
    task = source_task()
    payload = {"baseCommit": "base", "taskBinding": guard.task_binding(task)}
    guard.write_receipt(
        payload, outcome="finished", detail="verified", recorded_at="2026-08-02T00:00:00Z",
        head_commit="head", source_clean=True, origin_synced=True, evidence="private-evidence",
    )
    receipt = closeout.validate_terminal_source_closeout(task)
    assert receipt and receipt["headCommit"] == "head"
    assert task["sourceClosure"]["status"] == "satisfied"


def test_aborted_receipt_must_still_prove_clean_source() -> None:
    task = source_task()
    payload = {"baseCommit": "base", "taskBinding": guard.task_binding(task)}
    guard.write_receipt(
        payload, outcome="aborted", detail="restored", recorded_at="2026-08-02T00:00:00Z",
        head_commit="head", source_clean=False, origin_synced=False, evidence="private-evidence",
    )
    with pytest.raises(SystemExit, match="incomplete"):
        closeout.validate_terminal_source_closeout(task)


def test_non_source_and_legacy_tasks_remain_compatible() -> None:
    assert closeout.validate_terminal_source_closeout({"id": "legacy"}) is None
