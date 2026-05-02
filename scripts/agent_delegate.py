#!/usr/bin/env python3
"""Delegate dashboard-safe work and make both sides visible in Brain Feed."""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TASK_QUEUE = DATA_DIR / "agent-task-queue.json"

AGENT_LABELS = {
    "josh": "Josh 2.0",
    "jaimes": "JAIMES",
    "jain": "J.A.I.N",
    "joshex": "JOSHeX",
}
REMOTE_HOSTS = {
    "josh": {
        "ssh": "josh2-lan",
        "path": "/Users/josh2.0/.openclaw/workspace/mission-control",
        "python": "/opt/homebrew/bin/python3",
    },
    "jaimes": {
        "ssh": "jaimes-via-josh",
        "path": "/Users/jc_agent/.openclaw/workspace/mission-control",
        "python": "/opt/homebrew/bin/python3",
    },
    "jain": {
        "ssh": "jaimes-via-josh",
        "path": "/Users/jc_agent/.openclaw/workspace/mission-control",
        "python": "/opt/homebrew/bin/python3",
    },
}


def compact(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or proc.stdout.strip() or f"{cmd[0]} failed: {proc.returncode}")
    return proc


def publish(agent: str, event_type: str, status: str, title: str, detail: str, job: bool) -> None:
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
    ]
    if job:
        cmd.append("--job")
    run(cmd, check=False)


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
    ]
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
    destination = f"{remote['ssh']}:{remote['path']}/data/agent-task-queue.json"
    run(["scp", str(TASK_QUEUE), destination])


def publish_remote_receipt(agent: str, task: dict[str, Any]) -> tuple[str, str]:
    remote = REMOTE_HOSTS.get(agent)
    if not remote:
        return "", ""
    title = f"Instruction received: {task['title']}"
    detail = f"Received JOSHeX request. Task id: {task['id']}. Objective: {task.get('objective') or task['title']}"
    base_args = [
        remote["python"],
        "scripts/agent_publish.py",
        "--agent", agent,
        "--type", "handoff",
        "--status", "active",
        "--title", compact(title, 180),
        "--tool", "agent_delegate.py",
        "--detail", compact(detail, 500),
    ]
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
    parser = argparse.ArgumentParser(description="Delegate work with visible Mission Control Brain Feed updates.")
    parser.add_argument("--to", required=True, choices=sorted(AGENT_LABELS))
    parser.add_argument("--requester", default="joshex", choices=sorted(AGENT_LABELS))
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
    args = parser.parse_args()

    target_label = AGENT_LABELS[args.to]
    publish(
        args.requester,
        "handoff",
        "active",
        f"Requesting {target_label}: {args.title}",
        f"Sending {target_label} a dashboard-safe task: {args.objective}",
        args.job,
    )
    task = create_task(args)

    receipt = {"attempted": False, "ok": False, "error": ""}
    if args.to in REMOTE_HOSTS and not args.no_remote_receipt:
        receipt["attempted"] = True
        try:
            sync_task_queue(REMOTE_HOSTS[args.to])
            receipt_title, receipt_detail = publish_remote_receipt(args.to, task)
            if receipt_title:
                publish(args.to, "handoff", "active", receipt_title, receipt_detail, True)
            receipt["ok"] = True
        except SystemExit as exc:
            receipt["error"] = str(exc)
            publish(
                args.requester,
                "blocked",
                "blocked",
                f"{target_label} receipt not confirmed",
                f"Task {task['id']} was queued locally, but remote receipt failed: {receipt['error']}",
                True,
            )
            if not args.allow_offline:
                raise

    print(json.dumps({"ok": True, "task": task, "remoteReceipt": receipt}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
