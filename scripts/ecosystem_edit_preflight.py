#!/usr/bin/env python3
"""Read-only shared-repository preflight for ecosystem agents."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


RUNTIME_PREFIXES = ("data/", "dist/", "logs/", "tmp/")
OPEN_STATES = {"active", "assigned", "claimed", "in_progress", "pending", "queued", "running", "working"}


def run(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(args, cwd=root, text=True, capture_output=True)
    if check and proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    return proc.stdout.strip()


def count_open(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return -1
    rows = payload if isinstance(payload, list) else next((v for v in payload.values() if isinstance(v, list)), [])
    return sum(1 for row in rows if isinstance(row, dict) and str(row.get("status", "")).lower() in OPEN_STATES)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=("joshex", "josh2", "jaimes", "jain"), required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    if args.fetch:
        run(root, "git", "fetch", "origin")

    status = run(root, "git", "status", "--porcelain").splitlines()
    paths = [line[3:] for line in status if len(line) > 3]
    source_changes = [p for p in paths if not p.startswith(RUNTIME_PREFIXES)]
    branch = run(root, "git", "branch", "--show-current")
    head = run(root, "git", "rev-parse", "HEAD")
    remote = run(root, "git", "rev-parse", "origin/main")
    counts = run(root, "git", "rev-list", "--left-right", "--count", "HEAD...origin/main").split()
    ahead, behind = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (-1, -1)

    guard_raw = run(root, "python3", "scripts/control_tower_change_guard.py", "status", check=False)
    try:
        guard = json.loads(guard_raw)
    except Exception:
        guard = {"ok": False, "error": guard_raw[:300]}
    lease = guard.get("lease") if isinstance(guard, dict) else None
    lease_owner = lease.get("agent") if isinstance(lease, dict) else None

    reasons: list[str] = []
    if branch != "main":
        reasons.append(f"canonical branch is {branch}, expected main")
    if ahead or behind:
        reasons.append(f"local/remote divergence: ahead={ahead}, behind={behind}")
    if source_changes:
        reasons.append("canonical source already has uncommitted changes")
    if lease_owner and lease_owner != args.agent:
        reasons.append(f"shared change lease is owned by {lease_owner}")

    result = {
        "ok": not reasons,
        "agent": args.agent,
        "objective": args.objective,
        "branch": branch,
        "head": head[:12],
        "originMain": remote[:12],
        "ahead": ahead,
        "behind": behind,
        "leaseOwner": lease_owner,
        "sourceChanges": source_changes,
        "runtimeChurn": [p for p in paths if p not in source_changes],
        "openTasks": count_open(root / "data/agent-task-queue.json"),
        "openHandoffs": count_open(root / "data/handoff-queue.json"),
        "reasons": reasons,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
