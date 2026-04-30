#!/usr/bin/env python3
"""Run a cron command while publishing Mission Control Brain Feed heartbeats.

This wrapper is intentionally secret-safe: it never prints environment values,
never logs the child command with expanded secrets, and treats Brain Feed publish
failures as non-fatal so monitoring cannot break the underlying cron job.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "scripts" / "supabase_brain_feed_publish.py"
INSTALLED_PUBLISHER = Path.home() / "scripts" / "mission_control_brain_feed_publish.py"


def compact(value: str, limit: int = 220) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def publish(agent: str, status: str, cron: str, objective: str, step: str, tool: str, detail: str = "") -> None:
    publisher = PUBLISHER if PUBLISHER.exists() else INSTALLED_PUBLISHER
    if not publisher.exists():
        print("mission-control heartbeat: publisher missing; continuing", file=sys.stderr)
        return
    cmd = [
        sys.executable,
        str(publisher),
        "--agent",
        agent,
        "--status",
        status,
        "--tool",
        tool,
        "--cron",
        compact(cron, 120),
        "--objective",
        compact(objective, 220),
        "--step",
        compact(step, 180),
    ]
    if detail:
        cmd.extend(["--detail", compact(detail, 220)])
    try:
        subprocess.run(cmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    except Exception as exc:  # non-fatal by design
        print(f"mission-control heartbeat: publish skipped ({type(exc).__name__})", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish start/finish Brain Feed rows around a cron command.")
    parser.add_argument("--agent", default="jaimes", help="josh, jaimes, jain, or joshex")
    parser.add_argument("--cron", required=True, help="Human-readable cron/job name")
    parser.add_argument("--objective", required=True, help="Visible objective while the job runs")
    parser.add_argument("--done-objective", default="", help="Objective to publish on success")
    parser.add_argument("--tool", default="cron", help="Tool/lane label")
    parser.add_argument("--start-step", default="Started cron workflow")
    parser.add_argument("--done-step", default="Finished cron workflow")
    parser.add_argument("--error-step", default="Cron workflow failed")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after --")
    args = parser.parse_args()

    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("missing command after --")

    publish(args.agent, "active", args.cron, args.objective, args.start_step, args.tool)
    proc = subprocess.run(command, cwd=os.getcwd())
    if proc.returncode == 0:
        publish(
            args.agent,
            "done",
            args.cron,
            args.done_objective or f"{args.cron} complete",
            args.done_step,
            args.tool,
            detail=f"exit_code={proc.returncode}",
        )
    else:
        publish(
            args.agent,
            "blocked",
            args.cron,
            f"{args.cron} failed",
            args.error_step,
            args.tool,
            detail=f"exit_code={proc.returncode}",
        )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
