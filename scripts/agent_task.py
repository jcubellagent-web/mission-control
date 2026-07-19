#!/usr/bin/env python3
"""Create and update shared agent tasks for Control Tower."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
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
from handoff_receipt_bridge import HandoffReceiptError, record_receipt
from linear_work_intent import enqueue_task_intent, linear_metadata


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
STATUSES = {"queued", "accepted", "active", "blocked", "verifying", "done", "cancelled", "error"}
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
    handoff_to: str = "",
) -> dict[str, Any]:
    publish_status = (
        "planned"
        if status == "queued"
        else "accepted"
        if status == "accepted"
        else "active"
        if status == "active"
        else status
        if status in {"done", "blocked", "error", "verifying"}
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
    if handoff_to:
        cmd.extend(["--handoff-to", handoff_to])
    if brain_feed:
        cmd.append("--brain-feed")
    if job:
        cmd.append("--job")
    result = subprocess.run(cmd, cwd=ROOT, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            compact(result.stderr.strip() or result.stdout.strip() or "Canonical work publish failed", 500)
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise SystemExit("Canonical work publish returned an invalid receipt.") from exc
    return payload if isinstance(payload, dict) else {}


def record_task_handoff_receipt(
    task: dict[str, Any],
    *,
    kind: str,
    agent: str,
    event: dict[str, Any],
    status: str = "",
) -> dict[str, Any] | None:
    """Bridge a task lifecycle event to its exact cross-agent handoff row.

    Tasks created before the receipt bridge may not have an exact handoff row;
    those rows remain untouched and are surfaced by the read-only report.
    """
    if str(task.get("requester") or "") == str(task.get("owner") or ""):
        return None
    try:
        return record_receipt(
            DATA_DIR / "handoff-queue.json",
            kind=kind,
            agent=agent,
            work_id=task.get("workId"),
            run_id=task.get("runId"),
            origin_claim_hash=task.get("originClaimHash"),
            event_id=event.get("id"),
            task_id=task.get("id"),
            status=status,
            recorded_at=event.get("time"),
        )
    except HandoffReceiptError as exc:
        if "No handoff matches" in str(exc):
            return None
        raise


def publish_to_brain_feed(args: argparse.Namespace) -> bool:
    """Brain Feed is mandatory for meaningful shared tasks unless explicitly suppressed."""
    return not bool(getattr(args, "no_brain_feed", False))


def task_summary(task: dict[str, Any]) -> str:
    return f"{task.get('id')} [{task.get('status')}] {task.get('owner')}: {task.get('title')}"


def upsert_linear_connector_task(
    task: dict[str, Any],
    *,
    brain_feed: bool,
    job: bool = False,
) -> dict[str, Any] | None:
    """Wake one stable direct-connector task for each delegated durable work ID."""
    metadata = task.get("linear") if isinstance(task.get("linear"), dict) else {}
    owner = str(task.get("owner") or "")
    direct_owner = "josh2" if owner == "josh" else owner
    route_to = str(metadata.get("routeTo") or "")
    intent_id = str(metadata.get("lastIntentId") or "")
    work_id = str(task.get("workId") or task.get("id") or "")
    if not metadata.get("durable") or not intent_id or not work_id:
        return None

    digest = hashlib.sha256(work_id.encode("utf-8")).hexdigest()[:20]
    connector_id = f"task-linear-connector-{digest}"
    connector_work_id = f"work-linear-connector-{digest}"
    now = utc_now()
    boundary = str(metadata.get("lastBoundary") or task.get("status") or "planned")
    note = compact(f"Latest delegated boundary is {boundary}; canonical intent {intent_id}.", 400)
    source_position = (
        int(task.get("generation") or 1),
        int(metadata.get("revision") or 1),
    )

    def mutate(data: dict[str, Any]) -> dict[str, Any] | None:
        source = find_task(data, str(task.get("id") or ""))
        source_metadata = source.get("linear") if isinstance(source.get("linear"), dict) else {}
        rows = data.setdefault("tasks", [])
        connector = next((row for row in rows if row.get("id") == connector_id), None)
        if not route_to or route_to == direct_owner:
            if connector is None:
                return None
            source_metadata["connectorTaskId"] = connector_id
            source["linear"] = source_metadata
            metadata["connectorTaskId"] = connector_id
            if connector.get("status") not in {"done", "cancelled"}:
                connector["status"] = "done"
                connector["completedAt"] = now
                connector["updatedAt"] = now
                connector["summary"] = "Direct Linear ownership superseded the delegated connector wake."
                add_note(connector, owner, connector["summary"], "done")
                return {"task": connector, "action": "retired"}
            return None
        if metadata.get("syncState") not in {"pending", "failed"}:
            return None
        source_metadata["connectorTaskId"] = connector_id
        source["linear"] = source_metadata
        metadata["connectorTaskId"] = connector_id
        created = connector is None
        action = "created" if created else "refreshed"
        if connector is None:
            connector = {
                "id": connector_id,
                "workId": connector_work_id,
                "runId": f"run-linear-connector-{digest}",
                "generation": 1,
                "origin": "linear-intent-delegation",
                "originClaimHash": origin_digest(
                    fallback=f"linear-intent-delegation|{connector_work_id}|{digest}|1"
                ),
                "modelFamily": None,
                "modelId": None,
                "routeVerified": False,
                "title": compact(f"Sync {AGENT_LABELS.get(owner, owner)} durable work to Linear", 160),
                "objective": "",
                "owner": route_to,
                "requester": owner,
                "status": "queued",
                "priority": "high",
                "privacy": "dashboard-safe",
                "approval": "none",
                "requiredCapabilities": [],
                "dependencies": [str(task.get("id") or "")],
                "artifacts": [],
                "notes": [],
                "createdAt": now,
                "updatedAt": now,
                "dueAt": None,
                "completedAt": None,
                "summary": "",
            }
            rows.insert(0, connector)
        else:
            connector_metadata = (
                connector.get("linearConnector")
                if isinstance(connector.get("linearConnector"), dict)
                else {}
            )
            connector_position = (
                int(connector_metadata.get("sourceGeneration") or 0),
                int(connector_metadata.get("sourceRevision") or 0),
            )
            if connector_position > source_position:
                return None
            if connector_position == source_position and connector_metadata.get("latestIntentId") == intent_id:
                return None
            if connector.get("status") in {"done", "blocked", "error", "cancelled"}:
                connector["generation"] = int(connector.get("generation") or 1) + 1
                connector["runId"] = new_id("run")
                action = "reopened"
        connector["owner"] = route_to
        connector["requester"] = owner
        connector["status"] = "queued"
        connector["completedAt"] = None
        connector["updatedAt"] = now
        connector["objective"] = compact(
            f"Run flush-local, resolve the latest pending sanitized Linear intent for stable work "
            f"{work_id}, update its single issue, and acknowledge the canonical outbox. "
            f"Do not rely on a previously captured intent ID.",
            600,
        )
        connector["summary"] = note
        connector["linearConnector"] = {
            "sourceWorkId": work_id,
            "sourceTaskId": str(task.get("id") or ""),
            "latestIntentId": intent_id,
            "sourceGeneration": source_position[0],
            "sourceRevision": source_position[1],
            "boundary": boundary,
        }
        add_note(connector, owner, note, "queued")
        return {"task": connector, "action": action}

    refreshed = locked_tasks(mutate)
    if not refreshed:
        return None
    connector = refreshed["task"]
    action = str(refreshed["action"])
    if action == "retired":
        publish_event(
            str(connector.get("owner") or "jaimes"),
            "complete",
            "done",
            connector["title"],
            connector["summary"],
            brain_feed,
            job,
            task=connector,
            phase="linear-sync-retired",
            work_event="terminal",
        )
        return connector
    publish_event(
        route_to,
        "handoff" if action == "created" else "status",
        "queued",
        connector["title"],
        f"{note} Process the latest intent for work {work_id}.",
        brain_feed,
        job,
        task=connector,
        phase=f"linear-sync-{boundary}",
        work_event="start" if action == "created" else "update",
        handoff_to=route_to if action == "created" else "",
    )
    return connector


def create(args: argparse.Namespace) -> dict[str, Any]:
    owner = validate_agent(args.owner)
    requester = validate_requester(args.requester)
    privacy = args.privacy
    approval = args.approval
    if privacy in REQUIRES_APPROVAL and approval == "none":
        approval = "required"
    if privacy == "destructive" and approval != "approved":
        raise SystemExit("Destructive tasks require --approval approved.")
    if not args.durable and (args.area or args.acceptance_criterion):
        raise SystemExit("--area and --acceptance-criterion require --durable.")
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
    if args.durable:
        if privacy != "dashboard-safe":
            raise SystemExit("Durable Linear tasks require --privacy dashboard-safe.")
        task["linear"] = linear_metadata(
            area=args.area,
            acceptance_criteria=args.acceptance_criterion,
        )
    add_note(task, requester, args.note or "Task created", "queued")

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        tasks = data.setdefault("tasks", [])
        if any(t.get("id") == task["id"] for t in tasks):
            raise SystemExit(f"Task already exists: {task['id']}")
        enqueue_task_intent(task)
        tasks.insert(0, task)
        return task

    result = locked_tasks(mutate)
    upsert_linear_connector_task(
        result,
        brain_feed=publish_to_brain_feed(args),
        job=args.job,
    )
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
            handoff_to=owner,
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
        terminal_statuses = {"done", "blocked", "error", "cancelled"}
        if (
            previous_status in terminal_statuses
            and effective_status in terminal_statuses
            and effective_status != previous_status
        ):
            raise SystemExit(
                f"Task is already terminal as {previous_status}; reopen it with a non-terminal "
                f"transition before changing the terminal outcome to {effective_status}."
            )
        reopened = previous_status in terminal_statuses and effective_status not in terminal_statuses
        if reopened:
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
        if getattr(args, "work_event", "") != "heartbeat":
            metadata = task.get("linear") if isinstance(task.get("linear"), dict) else {}
            if metadata.get("durable"):
                metadata["revision"] = 1 if reopened else int(metadata.get("revision") or 1) + 1
                task["linear"] = metadata
            enqueue_task_intent(task)
        return task

    result = locked_tasks(mutate)
    if getattr(args, "work_event", "") != "heartbeat":
        upsert_linear_connector_task(
            result,
            brain_feed=publish_to_brain_feed(args),
            job=args.job,
        )
    effective_status = result["status"]
    title = f"Task {effective_status}: {result['title']}"
    detail = args.summary or args.note or result.get("objective") or title
    publish_result = publish_event(
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
    published_event = publish_result.get("event", {}) if isinstance(publish_result, dict) else {}
    if getattr(args, "cmd", "") != "handoff" and effective_status in {"accepted", "active"}:
        record_task_handoff_receipt(
            result,
            kind="acknowledged",
            agent=result["owner"],
            event=published_event,
        )
    elif effective_status in {"done", "blocked", "error", "cancelled"}:
        record_task_handoff_receipt(
            result,
            kind="terminal",
            agent=result["owner"],
            event=published_event,
            status=effective_status,
        )
    return result


def enable_linear_tracking(args: argparse.Namespace) -> dict[str, Any]:
    agent = validate_agent(args.agent)

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        task = find_task(data, args.id)
        if task.get("privacy") != "dashboard-safe":
            raise SystemExit("Durable Linear tasks require dashboard-safe privacy.")
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
        existing = task.get("linear") if isinstance(task.get("linear"), dict) else {}
        if existing.get("issueId"):
            raise SystemExit(f"Task already has Linear issue {existing['issueId']}.")
        task["linear"] = linear_metadata(
            area=args.area,
            acceptance_criteria=args.acceptance_criterion,
        )
        add_note(task, agent, "Durable Linear tracking enabled", task.get("status"))
        task["updatedAt"] = utc_now()
        enqueue_task_intent(task)
        return task

    result = locked_tasks(mutate)
    upsert_linear_connector_task(result, brain_feed=True)
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
    create_p.add_argument("--durable", action="store_true", help="Opt this task into durable Linear tracking")
    create_p.add_argument("--area", default="", help="Exactly one configured Linear Area label")
    create_p.add_argument(
        "--acceptance-criterion",
        action="append",
        default=[],
        help="Dashboard-safe acceptance criterion; repeat for multiple criteria",
    )

    for name, status in [("plan", "queued"), ("accept", "accepted"), ("start", "active"), ("heartbeat", "active"), ("block", "blocked"), ("verify", "verifying"), ("complete", "done"), ("error", "error"), ("cancel", "cancelled")]:
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

    track_p = sub.add_parser("track")
    track_p.add_argument("--id", required=True)
    track_p.add_argument("--agent", required=True)
    track_p.add_argument("--area", required=True)
    track_p.add_argument("--acceptance-criterion", action="append", default=[], required=True)

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
    elif args.cmd == "track":
        result = enable_linear_tracking(args)
        print(json.dumps({"ok": True, "task": result}, indent=2))
    else:
        result = set_status(args, args.status)
        print(json.dumps({"ok": True, "task": result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
