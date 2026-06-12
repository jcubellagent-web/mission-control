#!/usr/bin/env python3
"""Publish a lightweight J.A.I.N visibility heartbeat."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], timeout: int = 25) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)
        return {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr, "code": proc.returncode}
    except Exception as exc:  # noqa: BLE001 - heartbeat should degrade to status
        return {"ok": False, "stdout": "", "stderr": str(exc), "code": 1}


def compact(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a J.A.I.N visibility heartbeat.")
    parser.add_argument("--brain-feed", action="store_true")
    args = parser.parse_args()

    status = run(["openclaw", "status", "--deep"])
    text = (status.get("stdout") or "") + "\n" + (status.get("stderr") or "")
    lowered = text.lower()
    gateway_ok = "gateway" in lowered and "reachable" in lowered
    telegram_ok = "telegram" in lowered and "ok" in lowered
    task_ok = "0 active" in lowered and "0 queued" in lowered and "0 running" in lowered
    auth_ok = "not authenticated" not in lowered and "expired" not in lowered
    overall = "ok" if status["ok"] and gateway_ok and telegram_ok and auth_ok else "blocked"
    summary = (
        "J.A.I.N visibility heartbeat: OpenCLAW gateway reachable, Telegram OK, auth OK, and no active queued worker tasks."
        if overall == "ok" and task_ok
        else compact(
            f"J.A.I.N visibility heartbeat: gateway={'ok' if gateway_ok else 'attention'}, "
            f"Telegram={'ok' if telegram_ok else 'attention'}, auth={'ok' if auth_ok else 'attention'}, "
            f"tasks={'clear' if task_ok else 'check'}."
        )
    )
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_heartbeat.py"),
        "write",
        "--agent", "jain",
        "--node", "jaimes-via-josh",
        "--status", overall,
        "--summary", summary,
    ]
    if args.brain_feed:
        cmd.append("--brain-feed")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return proc.returncode
    result = json.loads(proc.stdout)
    print(json.dumps({"ok": overall == "ok", "status": overall, "summary": summary, "heartbeat": result.get("heartbeat")}, indent=2))
    return 0 if overall == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
