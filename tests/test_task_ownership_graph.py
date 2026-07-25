from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "task_ownership_graph.py"
if str(SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT.parent))


def load_module():
    spec = importlib.util.spec_from_file_location("task_ownership_graph", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


graph = load_module()
NOW = dt.datetime(2026, 7, 25, 3, 0, tzinfo=dt.timezone.utc)


def task(work: str, run: str, *, status: str = "active", owner: str = "joshex", title: str = "Inspect task ownership") -> dict:
    return {
        "id": f"task-{work}",
        "workId": work,
        "runId": run,
        "generation": 1,
        "status": status,
        "owner": owner,
        "title": title,
        "updatedAt": "2026-07-25T02:55:00Z",
    }


def hot(work: str, run: str, *, owner: str = "joshex", lease: str = "2026-07-25T03:05:00Z", role: str = "controller") -> dict:
    return {
        "workId": work,
        "runId": run,
        "generation": 1,
        "status": "active",
        "ownerAgent": owner,
        "executionRole": role,
        "leaseUntil": lease,
        "updatedAt": "2026-07-25T02:59:00Z",
    }


def handoff(work: str, run: str, *, status: str = "open", receipt: str | None = None) -> dict:
    row = {
        "id": f"handoff-{work}",
        "workId": work,
        "runId": run,
        "generation": 1,
        "handoffSchemaVersion": 2,
        "status": status,
        "from": "joshex",
        "to": "jaimes",
        "time": "2026-07-25T02:58:00Z",
    }
    if receipt:
        row["terminalResultReceiptId"] = receipt
        row["terminalResultStatus"] = "done"
    return row


def finding_types(payload: dict) -> set[str]:
    return {row["type"] for row in payload["findings"]}


def test_happy_path_emits_hashed_graph_without_findings() -> None:
    payload = graph.build_graph(
        {"tasks": [task("work-a", "run-a")]},
        {"handoffs": [handoff("work-a", "run-a")]},
        {"activeWorks": [hot("work-a", "run-a")]},
        now=NOW,
    )
    assert payload["status"] == "ready"
    assert payload["findings"] == []
    assert payload["flows"][0]["ownerAgent"] == "joshex"
    assert payload["flows"][0]["toAgent"] == "jaimes"
    serialized = str(payload)
    assert "work-a" not in serialized
    assert "run-a" not in serialized


def test_orphan_requires_no_live_controller_and_no_open_handoff() -> None:
    payload = graph.build_graph(
        {"tasks": [task("work-orphan", "run-orphan")]},
        {"handoffs": []},
        {"activeWorks": []},
        now=NOW,
    )
    assert finding_types(payload) == {"orphaned-work"}


def test_worker_does_not_satisfy_controller_ownership() -> None:
    payload = graph.build_graph(
        {"tasks": [task("work-worker", "run-worker")]},
        {"handoffs": []},
        {"activeWorks": [hot("work-worker", "run-worker", owner="jaimes", role="worker")]},
        now=NOW,
    )
    assert "orphaned-work" in finding_types(payload)


def test_duplicate_active_controllers_and_stale_lease_are_deterministic() -> None:
    payload = graph.build_graph(
        {"tasks": [task("work-conflict", "run-conflict")]},
        {"handoffs": []},
        {"activeWorks": [
            hot("work-conflict", "run-conflict", owner="joshex", lease="2026-07-25T02:59:59Z"),
            hot("work-conflict", "run-conflict", owner="jaimes"),
        ]},
        now=NOW,
    )
    assert finding_types(payload) == {"duplicate-active-owner", "stale-execution"}


def test_modern_terminal_handoff_requires_receipt() -> None:
    missing = graph.build_graph(
        {"tasks": [task("work-terminal", "run-terminal", status="done")]},
        {"handoffs": [handoff("work-terminal", "run-terminal", status="done")]},
        {"activeWorks": []},
        now=NOW,
    )
    present = graph.build_graph(
        {"tasks": [task("work-terminal", "run-terminal", status="done")]},
        {"handoffs": [handoff("work-terminal", "run-terminal", status="done", receipt="receipt-terminal")]},
        {"activeWorks": []},
        now=NOW,
    )
    assert "missing-terminal-receipt" in finding_types(missing)
    assert "missing-terminal-receipt" not in finding_types(present)
    assert present["flows"][0]["terminalReceipt"] is True


def test_private_or_raw_fields_never_serialize() -> None:
    raw_title = "Contact private@example.com with secret=abcdef1234567890"
    payload = graph.build_graph(
        {"tasks": [{**task("work-private", "run-private", title=raw_title), "detail": "raw private detail", "originClaimHash": "abc"}]},
        {"handoffs": []},
        {"activeWorks": []},
        now=NOW,
    )
    serialized = str(payload)
    assert "private@example.com" not in serialized
    assert "raw private detail" not in serialized
    assert "work-private" not in serialized
    assert "run-private" not in serialized


def test_missing_source_fails_closed() -> None:
    payload = graph.unavailable_graph(now=NOW)
    assert payload["status"] == "unavailable"
    assert payload["source"]["verified"] is False
    assert payload["nodes"] == []


def test_dashboard_sanitizer_accepts_generated_graph_and_rejects_raw_ids() -> None:
    payload = graph.build_graph(
        {"tasks": [task("work-safe", "run-safe")]},
        {"handoffs": [handoff("work-safe", "run-safe")]},
        {"activeWorks": [hot("work-safe", "run-safe")]},
        now=NOW,
    )
    assert graph.sanitize_graph(payload, now=NOW) == payload
    tampered = {**payload, "nodes": [*payload["nodes"], {
        "id": "work-raw-identifier",
        "kind": "work",
        "label": "Unsafe work",
        "observedAt": "2026-07-25T03:00:00Z",
    }]}
    assert graph.sanitize_graph(tampered, now=NOW)["status"] == "unavailable"
