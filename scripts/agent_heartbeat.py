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
AGENTS = {"joshex", "josh", "jaimes", "jain"}


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


def refresh_stale(data: dict[str, Any]) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    stale_after = int(data.get("staleAfterMinutes") or 30)
    for beat in data.get("heartbeats", []):
        stamp = parse_ts(beat.get("updatedAt"))
        beat["stale"] = not bool(stamp and (now - stamp) <= dt.timedelta(minutes=stale_after))
    return data


def write_heartbeat(args: argparse.Namespace) -> dict[str, Any]:
    agent = args.agent.lower()
    if agent not in AGENTS:
        raise SystemExit(f"Unknown agent: {args.agent}")
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
        rows = [row for row in data.get("heartbeats", []) if row.get("agent") != agent or row.get("node") != args.node]
        rows.insert(0, record)
        data["heartbeats"] = rows[:100]
        data["updatedAt"] = now
        data["staleAfterMinutes"] = args.stale_after
        write_json(HEARTBEATS_PATH, refresh_stale(data))
        fcntl.flock(lock, fcntl.LOCK_UN)
    if args.brain_feed:
        cmd = [
            sys.executable, str(ROOT / "scripts" / "agent_publish.py"),
            "--agent", agent,
            "--type", "status",
            "--status", "active" if args.status in {"ok", "active", "ready"} else "info",
            "--title", f"Heartbeat: {args.node}",
            "--tool", "agent_heartbeat.py",
            "--detail", args.summary or f"{agent} heartbeat is {args.status}",
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
    write_p.add_argument("--stale-after", type=int, default=30)
    write_p.add_argument("--brain-feed", action="store_true")
    write_p.add_argument("--v2", action="store_true", help="Also mirror heartbeat status to Mission Control v2 through agent_publish.py")
    check_p = sub.add_parser("check")
    check_p.add_argument("--stale-after", type=int, default=30)
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
