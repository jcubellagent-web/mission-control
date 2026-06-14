#!/usr/bin/env python3
"""Repair and verify local Mission Control visibility sidecars."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
DATA = ROOT / "data"
WATCH_FILES = {
    "brainFeed": DATA / "brain-feed.json",
    "jaimesBrainFeed": DATA / "jaimes-brain-feed.json",
    "jainBrainFeed": DATA / "jain-brain-feed.json",
    "heartbeats": DATA / "agent-heartbeats.json",
    "dashboard": DATA / "dashboard-data.json",
}
STALE_MINUTES = 20


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 120) -> dict:
    proc = subprocess.run(
        cmd,
        cwd=cwd or WORKSPACE,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-1200:],
    }


def control_tower_issues() -> list[str]:
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    issues = []
    for name, path in WATCH_FILES.items():
        if not path.exists():
            issues.append(f"{name} missing")
            continue
        age_minutes = (now - path.stat().st_mtime) / 60
        if age_minutes > STALE_MINUTES:
            issues.append(f"{name} stale")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--remote-jaimes", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()

    actions: list[dict] = []
    if args.repair and args.remote_jaimes:
        actions.append(run([sys.executable, "scripts/jaimes_brain_feed_poller.py"], cwd=WORKSPACE, timeout=90))

    if args.repair:
        actions.append(run([sys.executable, "scripts/update_mission_control.py"], cwd=ROOT, timeout=120))

    issues = control_tower_issues()
    actions.append({"kind": "control_tower_freshness", "ok": not issues, "issues": issues})

    if args.publish:
        status = "ok" if all(action.get("ok") for action in actions) else "error"
        summary = (
            "Control Tower visibility guard ok."
            if status == "ok"
            else "Control Tower visibility guard needs attention."
        )
        actions.append(
            run(
                [
                    sys.executable,
                    "scripts/agent_heartbeat.py",
                    "write",
                    "--agent",
                    "josh2",
                    "--node",
                    "state-visibility-guard",
                    "--status",
                    status,
                    "--summary",
                    summary,
                ],
                cwd=ROOT,
                timeout=45,
            )
        )

    ok = all(action.get("ok") for action in actions)
    print(json.dumps({"ok": ok, "actions": actions}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
