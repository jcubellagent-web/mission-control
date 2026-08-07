#!/usr/bin/env python3
"""Safely defer and redispatch shared-source work after edit leases clear.

This is intentionally a queueing mechanism, not an unattended source editor.
It creates one fresh, dashboard-safe continuation task only when the global and
scoped lease registries are clear and a new canonical-source preflight passes.
"""
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
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("CONTROL_TOWER_DATA_DIR", ROOT / "data"))
QUEUE_PATH = DATA_DIR / "shared-source-resume-queue.json"
TASKS_PATH = DATA_DIR / "agent-task-queue.json"
LOCK_PATH = Path(os.environ.get("CONTROL_TOWER_STATE_DIR", Path.home() / ".openclaw" / "state")) / "shared-source-resume.lock"
GLOBAL_LEASE_PATH = Path.home() / ".openclaw" / "state" / "control-tower-change-lock.json"
SCOPED_LEASE_PATH = Path.home() / ".openclaw" / "state" / "scoped-change-leases.json"
AGENTS = {"joshex", "josh2", "jaimes", "jain"}
PRIORITIES = {"low", "normal", "high", "urgent"}
APPROVALS = {"none", "required", "approved", "rejected"}
SECRET_PATTERN = re.compile(r"(?i)(?:password|secret|token|cookie|oauth|authorization|private key)\s*[:=]|\bsk-[A-Za-z0-9_-]{12,}")


def iso(value: dt.datetime | None = None) -> str:
    return (value or dt.datetime.now(dt.timezone.utc)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if SECRET_PATTERN.search(text):
        raise SystemExit("Deferred-work fields must be dashboard-safe and cannot contain secrets or account material.")
    return text[:limit].rstrip()


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked_queue():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
        fcntl.flock(handle, fcntl.LOCK_UN)


def queue_payload(path: Path = QUEUE_PATH) -> dict[str, Any]:
    payload = read_json(path, {})
    rows = payload.get("entries") if isinstance(payload, dict) else []
    return {"version": 1, "entries": rows if isinstance(rows, list) else []}


def active(lease: dict[str, Any]) -> bool:
    try:
        return dt.datetime.fromisoformat(str(lease.get("expiresAt") or "").replace("Z", "+00:00")) > dt.datetime.now(dt.timezone.utc)
    except ValueError:
        return False


def active_leases() -> dict[str, Any]:
    global_lease = read_json(GLOBAL_LEASE_PATH, {})
    scoped = read_json(SCOPED_LEASE_PATH, {})
    global_active = global_lease if isinstance(global_lease, dict) and active(global_lease) else None
    scoped_active = [row for row in scoped.get("leases", []) if isinstance(row, dict) and active(row)] if isinstance(scoped, dict) else []
    return {"global": global_active, "scoped": scoped_active}


def public_blockers(leases: dict[str, Any]) -> dict[str, int]:
    return {"global": 1 if leases.get("global") else 0, "scoped": len(leases.get("scoped") or [])}


def identity(owner: str, title: str, objective: str) -> str:
    digest = hashlib.sha256(f"{owner}|{title}|{objective}".encode("utf-8")).hexdigest()
    return f"resume-{digest[:20]}"


def validate_args(args: argparse.Namespace) -> dict[str, Any]:
    owner = str(args.owner).strip().lower()
    if owner not in AGENTS:
        raise SystemExit("--owner must be one of joshex, josh2, jaimes, or jain.")
    priority = str(args.priority).strip().lower()
    if priority not in PRIORITIES:
        raise SystemExit("Invalid --priority.")
    approval = str(args.approval).strip().lower()
    if approval not in APPROVALS or approval == "rejected":
        raise SystemExit("Deferred work must have a non-rejected approval state.")
    title, objective = compact(args.title, 160), compact(args.objective, 600)
    if not title or not objective:
        raise SystemExit("--title and --objective are required.")
    return {
        "id": identity(owner, title, objective), "owner": owner,
        "requester": compact(args.requester, 32) or "joshex", "title": title,
        "objective": objective, "priority": priority, "approval": approval,
        "capabilities": [compact(value, 100) for value in args.capability if compact(value, 100)],
        "artifacts": [compact(value, 220) for value in args.artifact if compact(value, 220)],
    }


def defer(args: argparse.Namespace, *, path: Path = QUEUE_PATH) -> dict[str, Any]:
    candidate = validate_args(args)
    with locked_queue():
        payload = queue_payload(path)
        existing = next((row for row in payload["entries"] if isinstance(row, dict) and row.get("id") == candidate["id"]), None)
        if existing and existing.get("status") in {"deferred", "resuming", "resumed"}:
            return {"ok": True, "status": "already_registered", "entry": existing}
        entry = candidate | {
            "status": "deferred", "createdAt": iso(), "updatedAt": iso(),
            "attempts": 0, "lastError": "", "continuationTaskId": "",
            "lastBlocked": public_blockers(active_leases()),
        }
        payload["entries"].append(entry)
        atomic_write(path, payload)
    return {"ok": True, "status": "deferred", "entry": entry}


def run_json(command: list[str]) -> tuple[int, dict[str, Any], str]:
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    try:
        parsed = json.loads(proc.stdout or "{}")
    except ValueError:
        parsed = {}
    return proc.returncode, parsed if isinstance(parsed, dict) else {}, (proc.stderr or proc.stdout or "").strip()


def existing_task(task_id: str, *, tasks_path: Path = TASKS_PATH) -> dict[str, Any] | None:
    rows = read_json(tasks_path, {}).get("tasks", [])
    return next((row for row in rows if isinstance(row, dict) and row.get("id") == task_id), None)


def create_continuation(entry: dict[str, Any]) -> tuple[bool, str, str]:
    task_id = f"task-{entry['id']}"
    prior = existing_task(task_id)
    if prior:
        if prior.get("origin") == "shared-source-resume":
            return True, task_id, "already-created"
        return False, "", "continuation task id collision"
    command = [
        sys.executable, "scripts/agent_task.py", "create", "--id", task_id,
        "--owner", entry["owner"], "--requester", entry["requester"],
        "--title", entry["title"], "--objective", entry["objective"],
        "--priority", entry["priority"], "--privacy", "dashboard-safe",
        "--approval", entry["approval"], "--work-scope", "shared-source",
        "--origin", "shared-source-resume", "--origin-claim", entry["id"],
        "--note", "Automatically redispatched after all shared-source lease and canonical preflight gates passed.",
        "--brain-feed",
    ]
    for value in entry.get("capabilities", []):
        command.extend(["--capability", value])
    for value in entry.get("artifacts", []):
        command.extend(["--artifact", value])
    code, payload, detail = run_json(command)
    if code == 0 and payload.get("ok"):
        return True, task_id, "created"
    return False, "", compact(detail, 300) or "task creation failed"


def resume_one(entry: dict[str, Any]) -> tuple[bool, str, str]:
    leases = active_leases()
    # A dead owner should not hold the whole source queue for the remaining TTL.
    # The guard itself remains authoritative and writes the recovery receipt.
    if leases.get("global") and not leases.get("scoped"):
        run_json([sys.executable, "scripts/control_tower_change_guard.py", "recover-orphan"])
        leases = active_leases()
    if leases.get("global") or leases.get("scoped"):
        return False, "deferred", json.dumps({"blockedBy": public_blockers(leases)})
    code, preflight, detail = run_json([
        sys.executable, "scripts/ecosystem_edit_preflight.py", "--agent", entry["owner"],
        "--objective", f"Auto-resume: {entry['title']}", "--fetch",
    ])
    if code != 0 or not preflight.get("ok"):
        reason = preflight.get("reasons") if isinstance(preflight, dict) else None
        return False, "deferred", compact(reason or detail or "canonical preflight not ready", 300)
    return create_continuation(entry) if preflight.get("leaseOwner") is None else (False, "deferred", "preflight reported an active lease")


def tick(*, path: Path = QUEUE_PATH) -> dict[str, Any]:
    with locked_queue():
        payload = queue_payload(path)
        resumed, deferred = [], []
        for entry in payload["entries"]:
            if not isinstance(entry, dict) or entry.get("status") != "deferred":
                continue
            entry["attempts"] = int(entry.get("attempts") or 0) + 1
            ok, task_id_or_status, detail = resume_one(entry)
            entry["updatedAt"] = iso()
            if ok:
                entry["status"] = "resumed"
                entry["continuationTaskId"] = task_id_or_status
                entry["resumedAt"] = iso()
                entry["lastError"] = ""
                resumed.append({"id": entry["id"], "taskId": task_id_or_status, "result": detail})
            else:
                entry["lastError"] = detail
                deferred.append({"id": entry["id"], "reason": detail})
        atomic_write(path, payload)
    return {"ok": True, "status": "ok", "resumed": resumed, "deferred": deferred, "checkedAt": iso()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=QUEUE_PATH, help="Override only for diagnostics/tests.")
    sub = parser.add_subparsers(dest="command", required=True)
    defer_p = sub.add_parser("defer", help="Register dashboard-safe source work blocked by a lease.")
    defer_p.add_argument("--owner", required=True)
    defer_p.add_argument("--requester", default="joshex")
    defer_p.add_argument("--title", required=True)
    defer_p.add_argument("--objective", required=True)
    defer_p.add_argument("--priority", choices=sorted(PRIORITIES), default="normal")
    defer_p.add_argument("--approval", choices=sorted(APPROVALS), default="approved")
    defer_p.add_argument("--capability", action="append", default=[])
    defer_p.add_argument("--artifact", action="append", default=[])
    sub.add_parser("tick", help="Redispatch eligible deferred work exactly once.")
    sub.add_parser("status", help="Show deferred/resumed records without changing state.")
    args = parser.parse_args()
    if args.command == "defer":
        output = defer(args, path=args.queue)
    elif args.command == "tick":
        output = tick(path=args.queue)
    else:
        payload = queue_payload(args.queue)
        output = {"ok": True, "entries": payload["entries"], "checkedAt": iso()}
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
