#!/usr/bin/env python3
"""Write and check shared agent heartbeats."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
HEARTBEATS_PATH = DATA_DIR / "agent-heartbeats.json"
AGENTS = {"joshex", "josh2", "jaimes", "jain"}
AGENT_LABELS = {
    "joshex": "JOSHeX",
    "josh2": "Josh 2.0",
    "jaimes": "JAIMES",
    "jain": "J.A.I.N",
}
STATUS_LABELS = {
    "ready": "online and ready",
    "ok": "online and ready",
    "active": "working now",
    "queued": "queued",
    "blocked": "needs attention",
    "error": "needs attention",
    "idle": "standing by",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(value: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def agent_label(agent: str) -> str:
    return AGENT_LABELS.get(agent, agent)


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status.lower(), "checked in")


def canonical_agent(agent: str) -> str:
    normalized = str(agent or "").strip().lower().replace("_", "").replace(" ", "")
    if normalized in {"josh", "josh20", "josh2", "josh2.0"}:
        return "josh2"
    if normalized in AGENTS:
        return normalized
    raise SystemExit(f"Unknown agent: {agent}")


def heartbeat_title(agent: str, status: str) -> str:
    label = status_label(status)
    if label == "needs attention":
        return f"{agent_label(agent)} needs attention"
    return f"{agent_label(agent)} is {label}"


def heartbeat_detail(agent: str, status: str, summary: str) -> str:
    clean_summary = summary.strip()
    if clean_summary:
        return clean_summary
    return f"{agent_label(agent)} status check completed; the agent is {status_label(status)}."


def refresh_stale(data: dict[str, Any]) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    stale_after = int(data.get("staleAfterMinutes") or 120)
    for beat in data.get("heartbeats", []):
        if beat.get("agent") == "josh":
            beat["agent"] = "josh2"
        stamp = parse_ts(beat.get("updatedAt"))
        beat["stale"] = not bool(stamp and (now - stamp) <= dt.timedelta(minutes=stale_after))
    return data


def write_heartbeat(args: argparse.Namespace) -> dict[str, Any]:
    agent = canonical_agent(args.agent)
    now = utc_now()
    record = {
        "agent": agent,
        "node": args.node,
        "status": args.status,
        "summary": args.summary,
        "updatedAt": now,
        "stale": False,
    }
    lock_path = HEARTBEATS_PATH.with_suffix(".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_json(HEARTBEATS_PATH, {"updatedAt": now, "staleAfterMinutes": args.stale_after, "heartbeats": []})
        rows = [
            row
            for row in data.get("heartbeats", [])
            if row.get("node") != args.node or canonical_agent(str(row.get("agent") or "")) != agent
        ]
        rows.insert(0, record)
        data["heartbeats"] = rows[:100]
        data["updatedAt"] = now
        data["staleAfterMinutes"] = args.stale_after
        write_json(HEARTBEATS_PATH, refresh_stale(data))
        fcntl.flock(lock, fcntl.LOCK_UN)
    if args.brain_feed:
        publish_status = "active" if args.status == "active" else "ready" if args.status in {"ok", "ready"} else "info"
        cmd = [
            sys.executable, str(ROOT / "scripts" / "agent_publish.py"),
            "--agent", agent,
            "--type", "status",
            "--status", publish_status,
            "--title", heartbeat_title(agent, args.status),
            "--tool", "status check",
            "--detail", heartbeat_detail(agent, args.status, args.summary),
            "--brain-feed",
            "--rollup",
        ]
        if args.v2:
            cmd.append("--v2")
        subprocess.run(cmd, cwd=ROOT, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Update or check shared agent heartbeats.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    write_p = sub.add_parser("write")
    write_p.add_argument("--agent", required=True)
    write_p.add_argument("--node", required=True)
    write_p.add_argument("--status", default="ok")
    write_p.add_argument("--summary", default="")
    write_p.add_argument("--stale-after", type=int, default=120)
    write_p.add_argument("--brain-feed", action="store_true")
    write_p.add_argument("--v2", action="store_true", help="Also mirror heartbeat status to dashboard-safe Mission Control tables through agent_publish.py")
    check_p = sub.add_parser("check")
    check_p.add_argument("--stale-after", type=int, default=120)
    args = parser.parse_args()
    if args.cmd == "write":
        result = write_heartbeat(args)
        print(json.dumps({"ok": True, "heartbeat": result}, indent=2))
    else:
        data = read_json(HEARTBEATS_PATH, {"updatedAt": utc_now(), "staleAfterMinutes": args.stale_after, "heartbeats": []})
        data["staleAfterMinutes"] = args.stale_after
        data = refresh_stale(data)
        write_json(HEARTBEATS_PATH, data)
        stale = [beat for beat in data.get("heartbeats", []) if beat.get("stale")]
        print(json.dumps({"ok": not stale, "stale": stale, "heartbeats": data.get("heartbeats", [])}, indent=2))
        return 1 if stale else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
