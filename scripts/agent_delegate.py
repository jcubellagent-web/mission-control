#!/usr/bin/env python3
"""Delegate dashboard-safe work and make both sides visible in Brain Feed."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import time
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("CONTROL_TOWER_DATA_DIR", ROOT / "data"))
TASK_QUEUE = DATA_DIR / "agent-task-queue.json"

AGENT_LABELS = {
    "josh": "Josh 2.0",
    "jaimes": "JAIMES",
    "jain": "J.A.I.N",
    "joshex": "JOSHeX",
}
AGENT_ALIASES = {
    "codex": "joshex",
    "josh2": "josh",
    "josh2.0": "josh",
    "josh 2.0": "josh",
    "j.a.i.n": "jain",
}
REMOTE_HOSTS = {
    "josh": {
        "ssh": "josh2",
        "path": "/Users/josh2.0/.openclaw/workspace/mission-control",
        "python": "/opt/homebrew/bin/python3",
    },
    "jaimes": {
        "ssh": "jc_agent@100.121.89.84",
        "path": "/Users/jc_agent/.openclaw/workspace/mission-control",
        "python": "/opt/homebrew/bin/python3",
    },
    "jain": {
        "ssh": "jc_agent@100.121.89.84",
        "path": "/Users/jc_agent/.openclaw/workspace/mission-control",
        "python": "/opt/homebrew/bin/python3",
    },
}


def is_local_remote(remote: dict[str, str]) -> bool:
    """Return true when the requested receiving host is this checkout."""
    try:
        return Path(remote["path"]).resolve() == ROOT.resolve()
    except Exception:
        return False


def compact(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def canonical_agent(value: str) -> str:
    raw = " ".join(str(value or "").strip().lower().replace("_", " ").split())
    agent = AGENT_ALIASES.get(raw, raw.replace(" ", ""))
    if agent not in AGENT_LABELS:
        raise SystemExit(f"Unknown agent '{value}'. Use joshex, josh2, jaimes, or jain.")
    return agent


def run(cmd: list[str], *, check: bool = True, retries: int = 1, retry_delay: float = 2.0) -> subprocess.CompletedProcess[str]:
    # Retry transient ssh/scp failures (Tailscale name-resolution / connect blips)
    # so a single hiccup does not emit a permanent "receipt not confirmed" blocker.
    is_remote = bool(cmd) and cmd[0] in ("ssh", "scp")
    attempts = (retries + 1) if is_remote else 1
    proc = None
    for attempt in range(attempts):
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            return proc
        transient = is_remote and re.search(
            r"could not resolve|connection (timed out|refused|reset)|operation timed out|temporary failure|no route to host",
            (proc.stderr or "") + (proc.stdout or ""),
            re.IGNORECASE,
        )
        if transient and attempt < attempts - 1:
            time.sleep(retry_delay)
            continue
        break
    if check and proc is not None and proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or proc.stdout.strip() or f"{cmd[0]} failed: {proc.returncode}")
    return proc


def publish(
    agent: str,
    event_type: str,
    status: str,
    title: str,
    detail: str,
    job: bool,
    *,
    task: dict[str, Any] | None = None,
    phase: str = "handoff",
) -> None:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_publish.py"),
        "--agent", agent,
        "--type", event_type,
        "--status", status,
        "--title", compact(title, 180),
        "--tool", "agent_delegate.py",
        "--detail", compact(detail, 500),
        "--brain-feed",
        "--phase", phase,
    ]
    if task:
        cmd.extend([
            "--work-id", str(task["workId"]),
            "--run-id", str(task["runId"]),
            "--generation", str(task.get("generation") or 1),
            "--origin", str(task.get("origin") or "agent-delegate"),
            "--origin-claim-hash", str(task["originClaimHash"]),
            "--work-event", "update",
        ])
        if task.get("modelFamily"):
            cmd.extend(["--model-family", str(task["modelFamily"])])
        if task.get("modelId"):
            cmd.extend(["--model-id", str(task["modelId"])])
        if task.get("routeVerified"):
            cmd.append("--route-verified")
        else:
            cmd.append("--route-unverified")
    if job:
        cmd.append("--job")
    run(cmd, check=True)


def create_task(args: argparse.Namespace) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_task.py"),
        "create",
        "--owner", args.to,
        "--requester", args.requester,
        "--title", args.title,
        "--objective", args.objective,
        "--priority", args.priority,
        "--privacy", args.privacy,
        "--approval", args.approval,
        "--note", compact(f"Delegated through agent_delegate.py. Type={args.task_type}", 400),
        "--brain-feed",
        "--origin", args.origin,
    ]
    if args.origin_claim:
        cmd.extend(["--origin-claim", args.origin_claim])
    elif args.origin_claim_hash:
        cmd.extend(["--origin-claim-hash", args.origin_claim_hash])
    if args.model_family:
        cmd.extend(["--model-family", args.model_family])
    if args.model_id:
        cmd.extend(["--model-id", args.model_id])
    if args.route_verified:
        cmd.append("--route-verified")
    if args.job:
        cmd.append("--job")
    for cap in args.capability or []:
        cmd += ["--capability", cap]
    for artifact in args.artifact or []:
        cmd += ["--artifact", artifact]
    if args.due_at:
        cmd += ["--due-at", args.due_at]
    proc = run(cmd)
    return json.loads(proc.stdout)["task"]


def sync_task_queue(remote: dict[str, str]) -> None:
    if not TASK_QUEUE.exists():
        return
    if is_local_remote(remote):
        return
    destination = f"{remote['ssh']}:{remote['path']}/data/agent-task-queue.json"
    run(["scp", str(TASK_QUEUE), destination])


def block_task_receipt(task: dict[str, Any], requester: str, error: str) -> None:
    """Keep queue and canonical work truth on the same terminal generation."""
    run([
        sys.executable,
        str(ROOT / "scripts" / "agent_task.py"),
        "block",
        "--id", str(task["id"]),
        "--agent", requester,
        "--note", compact(f"Remote receipt not confirmed: {error}", 400),
        "--phase", "receipt-blocked",
        "--brain-feed",
    ])


def publish_remote_receipt(agent: str, task: dict[str, Any]) -> tuple[str, str]:
    remote = REMOTE_HOSTS.get(agent)
    if not remote:
        return "", ""
    title = f"Instruction received: {task['title']}"
    detail = f"Received JOSHeX request. Task id: {task['id']}. Objective: {task.get('objective') or task['title']}"
    if is_local_remote(remote):
        return title, detail
    base_args = [
        remote["python"],
        "scripts/agent_publish.py",
        "--agent", agent,
        "--type", "handoff",
        "--status", "active",
        "--title", compact(title, 180),
        "--tool", "agent_delegate.py",
        "--detail", compact(detail, 500),
        "--work-id", str(task["workId"]),
        "--run-id", str(task["runId"]),
        "--generation", str(task.get("generation") or 1),
        "--origin", str(task.get("origin") or "agent-delegate"),
        "--origin-claim-hash", str(task["originClaimHash"]),
        "--work-event", "update",
        "--phase", "instruction-received",
    ]
    if task.get("modelFamily"):
        base_args.extend(["--model-family", str(task["modelFamily"])])
    if task.get("modelId"):
        base_args.extend(["--model-id", str(task["modelId"])])
    if task.get("routeVerified"):
        base_args.append("--route-verified")
    else:
        base_args.append("--route-unverified")
    brain_args = [
        *base_args,
        "--brain-feed",
        "--job",
        "--rollup",
    ]
    event_args = [*base_args, "--job", "--rollup"]
    remote_cmd = (
        f"cd {shlex.quote(remote['path'])} && "
        f"({' '.join(shlex.quote(part) for part in brain_args)}) || "
        f"({' '.join(shlex.quote(part) for part in event_args)})"
    )
    run(["ssh", remote["ssh"], remote_cmd])
    return title, detail


def main() -> int:
    parser = argparse.ArgumentParser(description="Delegate work with visible Control Tower Brain Feed updates.")
    parser.add_argument("--to", required=True)
    parser.add_argument("--requester", default="joshex")
    parser.add_argument("--task-type", default="delegated-work")
    parser.add_argument("--title", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--priority", default="normal", choices=["low", "normal", "high", "urgent"])
    parser.add_argument("--privacy", default="dashboard-safe")
    parser.add_argument("--approval", default="none", choices=["none", "required", "approved", "rejected"])
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--due-at", default="")
    parser.add_argument("--job", action="store_true")
    parser.add_argument("--no-remote-receipt", action="store_true")
    parser.add_argument("--allow-offline", action="store_true")
    parser.add_argument("--origin", default="agent-delegate")
    origin = parser.add_mutually_exclusive_group()
    origin.add_argument("--origin-claim", default="")
    origin.add_argument("--origin-claim-hash", default="")
    parser.add_argument("--model-family", default="")
    parser.add_argument("--model-id", default="")
    parser.add_argument("--route-verified", action="store_true")
    args = parser.parse_args()
    args.to = canonical_agent(args.to)
    args.requester = canonical_agent(args.requester)

    task = create_task(args)

    receipt = {"attempted": False, "ok": False, "error": ""}
    if args.to in REMOTE_HOSTS and not args.no_remote_receipt:
        receipt["attempted"] = True
        try:
            sync_task_queue(REMOTE_HOSTS[args.to])
            receipt_title, receipt_detail = publish_remote_receipt(args.to, task)
            if receipt_title:
                publish(args.to, "handoff", "active", receipt_title, receipt_detail, True, task=task, phase="instruction-received")
            receipt["ok"] = True
        except SystemExit as exc:
            receipt["error"] = str(exc)
            block_task_receipt(task, args.requester, receipt["error"])
            if not args.allow_offline:
                raise

    print(json.dumps({"ok": True, "task": task, "remoteReceipt": receipt}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
