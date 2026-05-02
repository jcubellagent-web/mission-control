#!/usr/bin/env python3
"""Create and update shared agent tasks for Mission Control."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TASKS_PATH = DATA_DIR / "agent-task-queue.json"
CAPABILITIES_PATH = DATA_DIR / "agent-capabilities.json"

AGENTS = {"joshex", "josh", "jaimes", "jain"}
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
    value = str(agent or "").strip().lower()
    if value not in AGENTS:
        raise SystemExit(f"Unknown agent '{agent}'. Use one of: {', '.join(sorted(AGENTS))}")
    return value


def validate_requester(requester: str) -> str:
    value = str(requester or "joshex").strip().lower()
    if value not in REQUESTERS:
        raise SystemExit(f"Unknown requester '{requester}'.")
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


def publish_event(agent: str, event_type: str, status: str, title: str, detail: str, brain_feed: bool, job: bool = False) -> None:
    publish_status = (
        "active"
        if status in {"queued", "accepted", "active"}
        else status
        if status in {"done", "blocked", "error"}
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
    ]
    if brain_feed:
        cmd.append("--brain-feed")
    if job:
        cmd.append("--job")
    subprocess.run(cmd, cwd=ROOT, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    task = {
        "id": args.id or task_id(owner, args.title, now),
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
            args.brain_feed,
            args.job,
        )
    publish_event(owner, "status", "queued", f"Task queued: {task['title']}", task["objective"], args.brain_feed, args.job)
    return result


def set_status(args: argparse.Namespace, status: str) -> dict[str, Any]:
    agent = validate_agent(args.agent)
    now = utc_now()

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        task = find_task(data, args.id)
        task["status"] = status
        task["updatedAt"] = now
        if status in {"done", "cancelled", "error"}:
            task["completedAt"] = now
        if args.owner:
            task["owner"] = validate_agent(args.owner)
        if args.artifact:
            task.setdefault("artifacts", [])
            for item in args.artifact:
                if item not in task["artifacts"]:
                    task["artifacts"].append(item)
        if args.summary:
            task["summary"] = compact(args.summary, 800)
        add_note(task, agent, args.note or args.summary or status, status)
        return task

    result = locked_tasks(mutate)
    title = f"Task {status}: {result['title']}"
    detail = args.summary or args.note or result.get("objective") or title
    publish_event(result["owner"], "complete" if status == "done" else "blocked" if status in {"blocked", "error"} else "status", status, title, detail, args.brain_feed, args.job)
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
    parser = argparse.ArgumentParser(description="Manage shared Mission Control agent tasks.")
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
    create_p.add_argument("--brain-feed", action="store_true")
    create_p.add_argument("--job", action="store_true")

    for name, status in [("accept", "accepted"), ("start", "active"), ("block", "blocked"), ("complete", "done"), ("error", "error"), ("cancel", "cancelled")]:
        p = sub.add_parser(name)
        p.set_defaults(status=status)
        p.add_argument("--id", required=True)
        p.add_argument("--agent", required=True)
        p.add_argument("--owner", default="")
        p.add_argument("--note", default="")
        p.add_argument("--summary", default="")
        p.add_argument("--artifact", action="append", default=[])
        p.add_argument("--brain-feed", action="store_true")
        p.add_argument("--job", action="store_true")

    handoff_p = sub.add_parser("handoff")
    handoff_p.set_defaults(status="accepted")
    handoff_p.add_argument("--id", required=True)
    handoff_p.add_argument("--agent", required=True)
    handoff_p.add_argument("--to", required=True)
    handoff_p.add_argument("--note", default="")
    handoff_p.add_argument("--summary", default="")
    handoff_p.add_argument("--artifact", action="append", default=[])
    handoff_p.add_argument("--brain-feed", action="store_true")
    handoff_p.add_argument("--job", action="store_true")

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
