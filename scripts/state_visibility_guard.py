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
HOT_WORK_PATH = DATA / "control-tower-hot.json"
WATCH_FILES = {
    "brainFeed": DATA / "brain-feed.json",
    "joshexBrainFeed": DATA / "joshex-brain-feed.json",
    "jaimesBrainFeed": DATA / "jaimes-brain-feed.json",
    "jainBrainFeed": DATA / "jain-brain-feed.json",
    "heartbeats": DATA / "agent-heartbeats.json",
    "dashboard": DATA / "dashboard-data.json",
    "projectContext": DATA / "project-context-registry.json",
    "agentContext": DATA / "agent-context-registry.json",
}
AGENT_FEED_NAMES = {"brainFeed", "joshexBrainFeed", "jaimesBrainFeed", "jainBrainFeed"}
STALE_MINUTES = 20


def parse_timestamp(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def active_feed_age_minutes(path: Path, now: dt.datetime) -> float | None:
    """Return age only for a feed that claims live work.

    Inactive terminal/ready/info feeds are valid durable state and should not
    require no-op heartbeat writes merely to keep their file mtime young.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return float("inf")
    status = str(payload.get("status") or "").strip().lower()
    active = payload.get("active") is True or status in {"active", "working", "running", "queued", "waiting"}
    if not active:
        return None
    updated_at = parse_timestamp(payload.get("updatedAt") or payload.get("timestamp"))
    if updated_at is None:
        return float("inf")
    feed_age = max(0.0, (now - updated_at).total_seconds() / 60)
    work_id = str(payload.get("workId") or "").strip()
    run_id = str(payload.get("runId") or "").strip()
    if not work_id or not run_id:
        return feed_age
    try:
        hot = json.loads(HOT_WORK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return feed_age
    active_works = hot.get("activeWorks") if isinstance(hot, dict) else []
    for work in active_works if isinstance(active_works, list) else []:
        if not isinstance(work, dict):
            continue
        if str(work.get("workId") or "") != work_id or str(work.get("runId") or "") != run_id:
            continue
        work_updated_at = parse_timestamp(work.get("updatedAt"))
        if work_updated_at is None:
            continue
        work_age = max(0.0, (now - work_updated_at).total_seconds() / 60)
        return min(feed_age, work_age)
    return feed_age


def active_feed_has_current_work(path: Path) -> bool:
    """Require a fresh JOSHeX claim to have an exact leased-work projection.

    JOSHeX can publish a local status sidecar independently of the dedicated
    host feeds.  That must not be treated as visible work unless its exact
    work/run identity is also present in the canonical live-work ledger.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        hot = json.loads(HOT_WORK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or not isinstance(hot, dict):
        return False
    status = str(payload.get("status") or "").strip().lower()
    active = payload.get("active") is True or status in {"active", "working", "running", "queued", "waiting"}
    if not active:
        return True
    work_id = str(payload.get("workId") or "").strip()
    run_id = str(payload.get("runId") or "").strip()
    if not work_id or not run_id:
        return False
    active_works = hot.get("activeWorks") if isinstance(hot.get("activeWorks"), list) else []
    return any(
        isinstance(work, dict)
        and str(work.get("workId") or "") == work_id
        and str(work.get("runId") or "") == run_id
        and work.get("stale") is not True
        for work in active_works
    )


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
    now = dt.datetime.now(dt.timezone.utc)
    issues = []
    for name, path in WATCH_FILES.items():
        if not path.exists():
            issues.append(f"{name} missing")
            continue
        if name in AGENT_FEED_NAMES:
            age_minutes = active_feed_age_minutes(path, now)
            if age_minutes is None:
                continue
        else:
            age_minutes = (now.timestamp() - path.stat().st_mtime) / 60
        if age_minutes > STALE_MINUTES:
            issues.append(f"{name} stale")
        if name == "joshexBrainFeed" and not active_feed_has_current_work(path):
            issues.append("joshexBrainFeed active without current leased work")
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
        actions.append(run([sys.executable, "scripts/project_context_registry.py"], cwd=ROOT, timeout=45))
        actions.append(run([sys.executable, "scripts/agent_context_registry.py"], cwd=ROOT, timeout=45))
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
