#!/usr/bin/env python3
"""Create and update shared agent tasks for Control Tower."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from control_tower_work_store import (
    canonical_model_family,
    new_id,
    origin_digest,
    safe_identifier,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("CONTROL_TOWER_DATA_DIR", ROOT / "data"))
TASKS_PATH = DATA_DIR / "agent-task-queue.json"
CAPABILITIES_PATH = DATA_DIR / "agent-capabilities.json"

AGENTS = {"joshex", "josh", "jaimes", "jain"}
AGENT_ALIASES = {
    "codex": "joshex",
    "josh2": "josh",
    "josh2.0": "josh",
    "josh 2.0": "josh",
    "j.a.i.n": "jain",
}
AGENT_LABELS = {
    "joshex": "JOSHeX",
    "josh": "Josh 2.0",
    "jaimes": "JAIMES",
    "jain": "J.A.I.N",
}
REQUESTERS = AGENTS | {"josh-user"}
STATUSES = {"queued", "accepted", "active", "blocked", "done", "cancelled", "error"}
PRIORITIES = {"low", "normal", "high", "urgent"}
PRIVACY_TIERS = {"dashboard-safe", "agent-private", "josh-approval", "sensitive-account", "destructive"}
APPROVALS = {"none", "required", "approved", "rejected"}
REQUIRES_APPROVAL = {"josh-approval", "sensitive-account", "destructive"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compact(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:54] or "task"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def load_tasks() -> dict[str, Any]:
    return read_json(TASKS_PATH, {"updatedAt": None, "tasks": []})


def save_tasks(data: dict[str, Any]) -> None:
    data["updatedAt"] = utc_now()
    write_json(TASKS_PATH, data)


def locked_tasks(fn):
    TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = TASKS_PATH.with_suffix(".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load_tasks()
        result = fn(data)
        save_tasks(data)
        fcntl.flock(lock, fcntl.LOCK_UN)
    return result


def validate_agent(agent: str) -> str:
    value = " ".join(str(agent or "").strip().lower().replace("_", " ").split())
    value = AGENT_ALIASES.get(value, value.replace(" ", ""))
    if value not in AGENTS:
        raise SystemExit(f"Unknown agent '{agent}'. Use joshex, josh2, jaimes, or jain.")
    return value


def validate_requester(requester: str) -> str:
    raw = " ".join(str(requester or "joshex").strip().lower().replace("_", " ").split())
    if raw == "josh-user":
        return raw
    value = AGENT_ALIASES.get(raw, raw.replace(" ", ""))
    if value not in REQUESTERS:
        raise SystemExit(f"Unknown requester '{requester}'. Use joshex, josh2, jaimes, jain, or josh-user.")
    return value


def task_id(owner: str, title: str, now: str) -> str:
    stamp = now.replace("-", "").replace(":", "").replace("Z", "").replace("T", "-")
    return f"task-{owner}-{stamp}-{slug(title)}"


def find_task(data: dict[str, Any], task_id_value: str) -> dict[str, Any]:
    for task in data.get("tasks", []):
        if task.get("id") == task_id_value:
            return task
    raise SystemExit(f"Task not found: {task_id_value}")


def add_note(task: dict[str, Any], agent: str, note: str, status: str | None = None) -> None:
    if not note and not status:
        return
    rows = task.setdefault("notes", [])
    rows.insert(0, {
        "time": utc_now(),
        "agent": agent,
        "status": status or task.get("status"),
        "note": compact(note or status or "updated", 400),
    })
    del rows[50:]


def publish_event(
    agent: str,
    event_type: str,
    status: str,
    title: str,
    detail: str,
    brain_feed: bool,
    job: bool = False,
    *,
    task: dict[str, Any] | None = None,
    phase: str = "",
    work_event: str = "auto",
) -> None:
    publish_status = (
        "planned"
        if status == "queued"
        else "accepted"
        if status == "accepted"
        else "active"
        if status == "active"
        else status
        if status in {"done", "blocked", "error"}
        else "cancelled"
        if status == "cancelled"
        else "info"
    )
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_publish.py"),
        "--agent", agent,
        "--type", event_type,
        "--status", publish_status,
        "--title", compact(title, 150),
        "--tool", "agent_task.py",
        "--detail", compact(detail, 500),
        "--rollup",
        "--phase", compact(phase or status, 120),
        "--work-event", work_event,
    ]
    if task:
        cmd.extend([
            "--work-id", str(task["workId"]),
            "--run-id", str(task["runId"]),
            "--generation", str(task.get("generation") or 1),
            "--origin", str(task.get("origin") or "agent-task"),
            "--origin-claim-hash", str(task["originClaimHash"]),
        ])
        if task.get("modelFamily"):
            cmd.extend(["--model-family", str(task["modelFamily"])])
        if task.get("modelId"):
            cmd.extend(["--model-id", str(task["modelId"])])
        if task.get("routeVerified"):
            cmd.append("--route-verified")
        else:
            cmd.append("--route-unverified")
    if brain_feed:
        cmd.append("--brain-feed")
    if job:
        cmd.append("--job")
    result = subprocess.run(cmd, cwd=ROOT, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            compact(result.stderr.strip() or result.stdout.strip() or "Canonical work publish failed", 500)
        )


def publish_to_brain_feed(args: argparse.Namespace) -> bool:
    """Brain Feed is mandatory for meaningful shared tasks unless explicitly suppressed."""
    return not bool(getattr(args, "no_brain_feed", False))


def task_summary(task: dict[str, Any]) -> str:
    return f"{task.get('id')} [{task.get('status')}] {task.get('owner')}: {task.get('title')}"


def create(args: argparse.Namespace) -> dict[str, Any]:
    owner = validate_agent(args.owner)
    requester = validate_requester(args.requester)
    privacy = args.privacy
    approval = args.approval
    if privacy in REQUIRES_APPROVAL and approval == "none":
        approval = "required"
    if privacy == "destructive" and approval != "approved":
        raise SystemExit("Destructive tasks require --approval approved.")
    now = utc_now()
    identity = args.id or task_id(owner, args.title, now)
    work_id = safe_identifier(args.work_id or identity, "work_id")
    run_id = safe_identifier(args.run_id or new_id("run"), "run_id")
    generation = int(args.generation or 1)
    if generation < 1:
        raise SystemExit("generation must be positive.")
    origin = compact(args.origin or "agent-task", 80)
    claim_hash = origin_digest(
        origin_claim=args.origin_claim,
        origin_claim_hash=args.origin_claim_hash,
        fallback=f"{origin}|{work_id}|{run_id}|{generation}",
    )
    model_family = canonical_model_family(args.model_family) if args.model_family else ""
    if args.route_verified and (not model_family or not args.model_id):
        raise SystemExit("--route-verified requires --model-family and --model-id.")
    task = {
        "id": identity,
        "workId": work_id,
        "runId": run_id,
        "generation": generation,
        "origin": origin,
        "originClaimHash": claim_hash,
        "modelFamily": model_family or None,
        "modelId": compact(args.model_id, 120) or None,
        "routeVerified": bool(args.route_verified),
        "title": compact(args.title, 160),
        "objective": compact(args.objective, 600),
        "owner": owner,
        "requester": requester,
        "status": "queued",
        "priority": args.priority,
        "privacy": privacy,
        "approval": approval,
        "requiredCapabilities": args.capability or [],
        "dependencies": args.depends_on or [],
        "artifacts": args.artifact or [],
        "notes": [],
        "createdAt": now,
        "updatedAt": now,
        "dueAt": args.due_at,
        "completedAt": None,
        "summary": "",
    }
    add_note(task, requester, args.note or "Task created", "queued")

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        tasks = data.setdefault("tasks", [])
        if any(t.get("id") == task["id"] for t in tasks):
            raise SystemExit(f"Task already exists: {task['id']}")
        tasks.insert(0, task)
        return task

    result = locked_tasks(mutate)
    if requester != owner:
        publish_event(
            requester,
            "handoff",
            "active",
            f"Requesting {AGENT_LABELS[owner]}: {task['title']}",
            f"Created task {task['id']} for {AGENT_LABELS[owner]}: {task['objective']}",
            publish_to_brain_feed(args),
            args.job,
            task=task,
            phase="delegating",
            work_event="start",
        )
    publish_event(
        owner,
        "status",
        "queued",
        f"Task queued: {task['title']}",
        task["objective"],
        publish_to_brain_feed(args),
        args.job,
        task=task,
        phase="queued",
        work_event="update" if requester != owner else "start",
    )
    return result


def set_status(args: argparse.Namespace, status: str) -> dict[str, Any]:
    agent = validate_agent(args.agent)
    now = utc_now()

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        task = find_task(data, args.id)
        task.setdefault("workId", task.get("id") or new_id("work-task"))
        task.setdefault("runId", new_id("run"))
        task.setdefault("generation", 1)
        task.setdefault("origin", "legacy-agent-task")
        task.setdefault(
            "originClaimHash",
            origin_digest(
                fallback=f"{task['origin']}|{task['workId']}|{task['runId']}|{task['generation']}"
            ),
        )
        previous_status = str(task.get("status") or "queued")
        effective_status = previous_status if getattr(args, "work_event", "") == "heartbeat" else status
        if previous_status in {"done", "blocked", "error", "cancelled"} and effective_status not in {"done", "blocked", "error", "cancelled"}:
            task["generation"] = int(task.get("generation") or 1) + 1
            task["runId"] = new_id("run")
        task["status"] = effective_status
        task["updatedAt"] = now
        if effective_status in {"done", "cancelled", "error", "blocked"}:
            task["completedAt"] = now
        else:
            task["completedAt"] = None
        if args.owner:
            task["owner"] = validate_agent(args.owner)
        if args.artifact:
            task.setdefault("artifacts", [])
            for item in args.artifact:
                if item not in task["artifacts"]:
                    task["artifacts"].append(item)
        if args.summary:
            task["summary"] = compact(args.summary, 800)
        if getattr(args, "phase", ""):
            task["phase"] = compact(args.phase, 120)
        if getattr(args, "model_family", None):
            task["modelFamily"] = canonical_model_family(args.model_family)
        if getattr(args, "model_id", None):
            task["modelId"] = compact(args.model_id, 120)
        if getattr(args, "route_verified", None) is not None:
            task["routeVerified"] = bool(args.route_verified)
        if task.get("routeVerified") and (not task.get("modelFamily") or not task.get("modelId")):
            raise SystemExit("A verified route requires modelFamily and modelId.")
        add_note(task, agent, args.note or args.summary or effective_status, effective_status)
        return task

    result = locked_tasks(mutate)
    effective_status = result["status"]
    title = f"Task {effective_status}: {result['title']}"
    detail = args.summary or args.note or result.get("objective") or title
    publish_event(
        result["owner"],
        "complete" if effective_status == "done" else "blocked" if effective_status in {"blocked", "error"} else "status",
        effective_status,
        title,
        detail,
        publish_to_brain_feed(args),
        args.job,
        task=result,
        phase=getattr(args, "phase", "") or effective_status,
        work_event=getattr(args, "work_event", "update"),
    )
    return result


def list_tasks(args: argparse.Namespace) -> list[dict[str, Any]]:
    data = load_tasks()
    tasks = [task for task in data.get("tasks", []) if isinstance(task, dict)]
    if args.owner:
        owner = validate_agent(args.owner)
        tasks = [task for task in tasks if task.get("owner") == owner]
    if args.status:
        tasks = [task for task in tasks if task.get("status") == args.status]
    return tasks[: args.limit]


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage shared Control Tower agent tasks.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    create_p = sub.add_parser("create")
    create_p.add_argument("--id", default="")
    create_p.add_argument("--owner", required=True)
    create_p.add_argument("--requester", default="joshex")
    create_p.add_argument("--title", required=True)
    create_p.add_argument("--objective", required=True)
    create_p.add_argument("--priority", default="normal", choices=sorted(PRIORITIES))
    create_p.add_argument("--privacy", default="dashboard-safe", choices=sorted(PRIVACY_TIERS))
    create_p.add_argument("--approval", default="none", choices=sorted(APPROVALS))
    create_p.add_argument("--capability", action="append", default=[])
    create_p.add_argument("--depends-on", action="append", default=[])
    create_p.add_argument("--artifact", action="append", default=[])
    create_p.add_argument("--due-at", default=None)
    create_p.add_argument("--note", default="")
    create_p.add_argument("--brain-feed", action="store_true", help="Accepted for compatibility; Brain Feed publishing is on by default")
    create_p.add_argument("--no-brain-feed", action="store_true", help="Suppress Brain Feed only for dry-runs or local render tests")
    create_p.add_argument("--job", action="store_true")
    create_p.add_argument("--work-id", default="")
    create_p.add_argument("--run-id", default="")
    create_p.add_argument("--generation", type=int, default=1)
    create_p.add_argument("--origin", default="agent-task")
    create_origin = create_p.add_mutually_exclusive_group()
    create_origin.add_argument("--origin-claim", default="")
    create_origin.add_argument("--origin-claim-hash", default="")
    create_p.add_argument("--model-family", default="")
    create_p.add_argument("--model-id", default="")
    create_p.add_argument("--route-verified", action="store_true")

    for name, status in [("accept", "accepted"), ("start", "active"), ("heartbeat", "active"), ("block", "blocked"), ("complete", "done"), ("error", "error"), ("cancel", "cancelled")]:
        p = sub.add_parser(name)
        p.set_defaults(status=status)
        p.set_defaults(
            work_event="heartbeat"
            if name == "heartbeat"
            else "terminal"
            if status in {"done", "blocked", "error", "cancelled"}
            else "update"
        )
        p.add_argument("--id", required=True)
        p.add_argument("--agent", required=True)
        p.add_argument("--owner", default="")
        p.add_argument("--note", default="")
        p.add_argument("--summary", default="")
        p.add_argument("--artifact", action="append", default=[])
        p.add_argument("--brain-feed", action="store_true", help="Accepted for compatibility; Brain Feed publishing is on by default")
        p.add_argument("--no-brain-feed", action="store_true", help="Suppress Brain Feed only for dry-runs or local render tests")
        p.add_argument("--job", action="store_true")
        p.add_argument("--phase", default="")
        p.add_argument("--model-family", default=None)
        p.add_argument("--model-id", default=None)
        route = p.add_mutually_exclusive_group()
        route.add_argument("--route-verified", action="store_true", default=None)
        route.add_argument("--route-unverified", action="store_false", dest="route_verified")

    handoff_p = sub.add_parser("handoff")
    handoff_p.set_defaults(status="accepted")
    handoff_p.add_argument("--id", required=True)
    handoff_p.add_argument("--agent", required=True)
    handoff_p.add_argument("--to", required=True)
    handoff_p.add_argument("--note", default="")
    handoff_p.add_argument("--summary", default="")
    handoff_p.add_argument("--artifact", action="append", default=[])
    handoff_p.add_argument("--brain-feed", action="store_true", help="Accepted for compatibility; Brain Feed publishing is on by default")
    handoff_p.add_argument("--no-brain-feed", action="store_true", help="Suppress Brain Feed only for dry-runs or local render tests")
    handoff_p.add_argument("--job", action="store_true")
    handoff_p.add_argument("--phase", default="handoff")
    handoff_p.add_argument("--model-family", default=None)
    handoff_p.add_argument("--model-id", default=None)
    handoff_route = handoff_p.add_mutually_exclusive_group()
    handoff_route.add_argument("--route-verified", action="store_true", default=None)
    handoff_route.add_argument("--route-unverified", action="store_false", dest="route_verified")
    handoff_p.set_defaults(work_event="update")

    list_p = sub.add_parser("list")
    list_p.add_argument("--owner", default="")
    list_p.add_argument("--status", default="")
    list_p.add_argument("--limit", type=int, default=20)
    list_p.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.cmd == "create":
        result = create(args)
        print(json.dumps({"ok": True, "task": result}, indent=2))
    elif args.cmd == "list":
        tasks = list_tasks(args)
        if args.json:
            print(json.dumps({"tasks": tasks}, indent=2))
        else:
            for task in tasks:
                print(task_summary(task))
    elif args.cmd == "handoff":
        args.owner = args.to
        args.note = args.note or f"Handed off to {args.to}"
        result = set_status(args, "accepted")
        print(json.dumps({"ok": True, "task": result}, indent=2))
    else:
        result = set_status(args, args.status)
        print(json.dumps({"ok": True, "task": result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
