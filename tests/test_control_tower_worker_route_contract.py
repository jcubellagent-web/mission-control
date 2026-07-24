from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "control_tower_work_store.py"
SPEC = importlib.util.spec_from_file_location("control_tower_work_store_worker_contract", MODULE_PATH)
assert SPEC and SPEC.loader
WORK_STORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORK_STORE)


def publish(store, *, work_id: str, run_id: str, agent: str = "joshex", **extra):
    payload = {
        "kind": "start",
        "work_id": work_id,
        "run_id": run_id,
        "agent": agent,
        "objective": work_id,
        "status": "active",
        "model_family": "codex",
        "model_id": "gpt-5.6-terra",
        "route_verified": True,
        "lease_seconds": 300,
    }
    payload.update(extra)
    return store.publish(payload)


def test_worker_projection_requires_exact_active_controller(tmp_path: Path) -> None:
    store = WORK_STORE.WorkStore(tmp_path / "work.sqlite3", tmp_path / "hot.json")
    publish(store, work_id="controller-work", run_id="controller-run")
    publish(
        store,
        work_id="worker-work",
        run_id="worker-run",
        model_family="ollama",
        model_id="glm-5.2:cloud",
        execution_role="worker",
        controller_work_id="controller-work",
        controller_run_id="controller-run",
    )

    worker = next(row for row in store.projection()["activeModelRoutes"] if row["workId"] == "worker-work")
    assert worker["executionRole"] == "worker"
    assert worker["controllerWorkId"] == "controller-work"
    assert worker["controllerRunId"] == "controller-run"
    active_worker = next(row for row in store.projection()["activeWorks"] if row["workId"] == "worker-work")
    assert active_worker["executionRole"] == "worker"
    assert active_worker["controllerWorkId"] == "controller-work"


def test_worker_route_rejects_missing_or_cross_owner_controller(tmp_path: Path) -> None:
    store = WORK_STORE.WorkStore(tmp_path / "work.sqlite3", tmp_path / "hot.json")
    publish(store, work_id="controller-work", run_id="controller-run", agent="josh2")

    with pytest.raises(WORK_STORE.WorkStoreError, match="same agent"):
        publish(
            store,
            work_id="worker-work",
            run_id="worker-run",
            execution_role="worker",
            controller_work_id="controller-work",
            controller_run_id="controller-run",
        )

    with pytest.raises(WORK_STORE.WorkStoreError, match="requires controller"):
        publish(
            store,
            work_id="worker-without-parent",
            run_id="worker-without-parent-run",
            execution_role="worker",
        )
