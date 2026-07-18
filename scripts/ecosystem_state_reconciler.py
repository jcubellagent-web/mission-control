#!/usr/bin/env python3
"""Reconcile dashboard-safe lifecycle state without deleting audit history.

Terminal task truth supersedes older active publications with the same topic.
Uncorrelated old work becomes explicit ``stale``/``blocked`` rather than being
shown as currently running. Queued approvals remain queued and are annotated.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TERMINAL = {"done", "complete", "completed", "cancelled", "canceled", "failed", "error"}
ACTIVE = {
    "accepted", "planned", "routed", "active", "verifying", "running",
    "in_progress", "in-progress", "claimed", "queued", "open",
}
BLOCKED = {"blocked", "error"}
NOISE = {
    "task", "active", "queued", "instruction", "received", "requesting", "josh", "josh2",
    "jaimes", "joshex", "agent", "ecosystem", "status", "handoff",
}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


def parse_ts(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def topic(value: Any) -> set[str]:
    tokens = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split()
    return {token for token in tokens if len(token) > 3 and token not in NOISE}


def same_topic(left: Any, right: Any) -> bool:
    a, b = topic(left), topic(right)
    if not a or not b:
        return False
    shared = a & b
    return len(shared) >= min(2, len(a), len(b)) or (len(shared) >= 3 and len(shared) / min(len(a), len(b)) >= 0.5)


def exact_work_id(row: dict[str, Any]) -> str:
    return str(row.get("workId") or row.get("work_id") or "").strip()


def exact_task_id(row: dict[str, Any]) -> str:
    return str(
        row.get("taskId")
        or row.get("receivingTaskId")
        or row.get("terminalTaskId")
        or ""
    ).strip()


def matching_task(
    row: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    """Match canonical rows by identity; use title similarity only for legacy rows.

    Once a publication has a ``workId`` it must never be associated with a
    different lifecycle merely because their words look similar.  Rows created
    before stable identity existed may still be reconciled by the old heuristic
    and are explicitly counted as legacy fuzzy matches.
    """
    work_id = exact_work_id(row)
    task_id = exact_task_id(row)
    if work_id:
        for task in tasks:
            if exact_work_id(task) == work_id:
                return task, "workId"
        return None, ""
    if task_id:
        for task in tasks:
            if str(task.get("id") or "") == task_id:
                return task, "taskId"
        return None, ""
    for task in tasks:
        if same_topic(row.get("title"), task.get("title")):
            return task, "legacy-fuzzy-title"
    return None, ""


def row_stamp(row: dict[str, Any]) -> dt.datetime | None:
    for key in ("updatedAt", "completedAt", "time", "createdAt", "startedAt"):
        stamp = parse_ts(row.get(key))
        if stamp:
            return stamp.astimezone(dt.timezone.utc)
    return None


def mark(row: dict[str, Any], status: str, reason: str, now_iso: str) -> None:
    row["status"] = status
    row["reconciledAt"] = now_iso
    row["reconciliationReason"] = reason
    if status in TERMINAL and not row.get("completedAt"):
        row["completedAt"] = now_iso


def reconcile(data_dir: Path, now: dt.datetime) -> dict[str, Any]:
    now_iso = iso(now)
    task_path = data_dir / "agent-task-queue.json"
    handoff_path = data_dir / "handoff-queue.json"
    jobs_path = data_dir / "codex-jobs.json"
    events_path = data_dir / "shared-events.json"
    tasks_doc = read_json(task_path, {"tasks": []})
    handoffs_doc = read_json(handoff_path, {"handoffs": []})
    jobs_doc = read_json(jobs_path, {"jobs": []})
    events_doc = read_json(events_path, {"events": []})
    tasks = [row for row in tasks_doc.get("tasks", []) if isinstance(row, dict)]
    handoffs = [row for row in handoffs_doc.get("handoffs", []) if isinstance(row, dict)]
    jobs = [row for row in jobs_doc.get("jobs", []) if isinstance(row, dict)]
    events = [row for row in events_doc.get("events", []) if isinstance(row, dict)]
    terminal_tasks = [row for row in tasks if str(row.get("status") or "").lower() in TERMINAL]
    open_tasks = [row for row in tasks if str(row.get("status") or "").lower() not in TERMINAL]
    changes = {"tasksAttention": 0, "handoffsClosed": 0, "handoffsBlocked": 0, "jobsSuperseded": 0, "jobsStale": 0, "eventsSuperseded": 0, "eventsStale": 0, "legacyFuzzyMatches": 0}

    for task in tasks:
        status = str(task.get("status") or "").lower()
        stamp = row_stamp(task)
        if status == "queued" and stamp and now - stamp > dt.timedelta(hours=24):
            if not task.get("attention"):
                changes["tasksAttention"] += 1
            task["attention"] = True
            task["attentionReason"] = "Queued for more than 24 hours; review approval, ownership, or dependencies."
            task["reconciledAt"] = now_iso

    for handoff in handoffs:
        if str(handoff.get("status") or "").lower() not in {"open", "active", "queued"}:
            continue
        matching_terminal, match_kind = matching_task(handoff, terminal_tasks)
        if matching_terminal:
            mark(handoff, "done", f"Receiving work is terminal in {matching_terminal.get('id')} ({match_kind}).", now_iso)
            handoff["receivingTaskId"] = matching_terminal.get("id")
            handoff["reconciliationMatch"] = match_kind
            changes["handoffsClosed"] += 1
            if match_kind == "legacy-fuzzy-title":
                changes["legacyFuzzyMatches"] += 1
            continue
        matching_open, open_match_kind = matching_task(handoff, open_tasks)
        stamp = row_stamp(handoff)
        if not matching_open and stamp and now - stamp > dt.timedelta(hours=6):
            mark(handoff, "blocked", "No receiving task or fresh execution evidence for more than 6 hours.", now_iso)
            changes["handoffsBlocked"] += 1
        elif matching_open and open_match_kind == "legacy-fuzzy-title":
            changes["legacyFuzzyMatches"] += 1

    def reconcile_activity(rows: list[dict[str, Any]], kind: str) -> None:
        for row in rows:
            status = str(row.get("status") or "").lower()
            if status not in ACTIVE | BLOCKED:
                continue
            matching_terminal, match_kind = matching_task(row, terminal_tasks)
            if matching_terminal:
                mark(row, "superseded", f"Terminal task {matching_terminal.get('id')} supersedes this publication ({match_kind}).", now_iso)
                row["terminalTaskId"] = matching_terminal.get("id")
                row["reconciliationMatch"] = match_kind
                changes[f"{kind}Superseded"] += 1
                if match_kind == "legacy-fuzzy-title":
                    changes["legacyFuzzyMatches"] += 1
                continue
            # Unresolved blocked/error publications remain visible until a
            # terminal task explicitly closes the same topic.  Age alone must
            # never hide a blocker that may still require operator action.
            if status in BLOCKED:
                continue
            matching_open, open_match_kind = matching_task(row, open_tasks)
            stamp = row_stamp(row)
            if not matching_open and stamp and now - stamp > dt.timedelta(hours=6):
                mark(row, "stale", "No nonterminal task or fresh execution evidence for more than 6 hours.", now_iso)
                changes[f"{kind}Stale"] += 1
            elif matching_open and open_match_kind == "legacy-fuzzy-title":
                changes["legacyFuzzyMatches"] += 1

    reconcile_activity(jobs, "jobs")
    reconcile_activity(events, "events")
    tasks_doc["tasks"], handoffs_doc["handoffs"] = tasks, handoffs
    # Publishers keep newest records first; retain the fresh head, not the old
    # tail, when bounding compatibility ledgers.
    jobs_doc["jobs"], events_doc["events"] = jobs[:1000], events[:500]
    for document in (tasks_doc, handoffs_doc, jobs_doc, events_doc):
        document["reconciledAt"] = now_iso
    return {
        "documents": {task_path: tasks_doc, handoff_path: handoffs_doc, jobs_path: jobs_doc, events_path: events_doc},
        "summary": {"checkedAt": now_iso, "ok": True, **changes},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = args.data_dir / ".ecosystem-state-reconciler.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"ok": True, "status": "skipped_locked"}))
            return 0
        result = reconcile(args.data_dir, utc_now())
        if not args.dry_run:
            for path, document in result["documents"].items():
                atomic_write(path, document)
            atomic_write(args.data_dir / "ecosystem-lifecycle-qc.json", result["summary"])
        print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
