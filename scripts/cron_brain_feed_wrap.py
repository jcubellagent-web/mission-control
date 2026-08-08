#!/usr/bin/env python3
"""Run a cron command while publishing Control Tower Brain Feed heartbeats.

This wrapper is intentionally secret-safe: it never prints environment values,
never logs the child command with expanded secrets, and treats Brain Feed publish
failures as non-fatal so monitoring cannot break the underlying cron job.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "scripts" / "agent_publish.py"
INSTALLED_PUBLISHER = Path.home() / "scripts" / "mission_control_agent_publish.py"


def compact(value: str, limit: int = 220) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def publish(
    agent: str,
    status: str,
    cron: str,
    objective: str,
    step: str,
    tool: str,
    *,
    work_id: str,
    run_id: str,
    work_event: str,
    phase: str,
    lease_seconds: int,
    detail: str = "",
) -> None:
    publisher = PUBLISHER if PUBLISHER.exists() else INSTALLED_PUBLISHER
    if not publisher.exists():
        print("mission-control heartbeat: publisher missing; continuing", file=sys.stderr)
        return
    publish_status = status if status in {"active", "ready", "done", "blocked", "error", "info"} else "info"
    publish_type = "blocked" if publish_status in {"blocked", "error"} else "job"
    publish_detail = compact(" · ".join(part for part in [step, detail, f"Cron: {cron}"] if part), 500)
    cmd = [
        sys.executable,
        str(publisher),
        "--agent",
        agent,
        "--type",
        publish_type,
        "--status",
        publish_status,
        "--tool",
        tool,
        "--title",
        compact(objective, 220),
        "--detail",
        publish_detail,
        "--brain-feed",
        "--job",
        "--work-id",
        work_id,
        "--run-id",
        run_id,
        "--origin",
        "scheduled-cron",
        "--work-event",
        work_event,
        "--phase",
        phase,
        "--lease-seconds",
        str(lease_seconds),
    ]
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
    parser.add_argument("--work-id", default="", help="Stable Control Tower work identity for this cron")
    parser.add_argument("--run-id", default="", help="Exact Control Tower run identity for this invocation")
    parser.add_argument("--heartbeat-seconds", type=int, default=60, help="Active-work heartbeat interval")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after --")
    args = parser.parse_args()

    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("missing command after --")

    work_id = args.work_id.strip() or f"work-cron-{uuid.uuid5(uuid.NAMESPACE_URL, args.cron).hex}"
    run_id = args.run_id.strip() or f"run-{uuid.uuid4().hex}"
    heartbeat_seconds = max(15, args.heartbeat_seconds)
    publish(
        args.agent, "active", args.cron, args.objective, args.start_step, args.tool,
        work_id=work_id, run_id=run_id, work_event="start", phase="executing",
        lease_seconds=heartbeat_seconds * 3,
    )
    proc = subprocess.Popen(command, cwd=os.getcwd())
    stop_heartbeats = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_heartbeats.wait(heartbeat_seconds):
            if proc.poll() is not None:
                return
            publish(
                args.agent, "active", args.cron, args.objective, "Cron workflow still running", args.tool,
                work_id=work_id, run_id=run_id, work_event="heartbeat", phase="executing",
                lease_seconds=heartbeat_seconds * 3,
            )

    heartbeat_thread = threading.Thread(target=heartbeat_loop, name="control-tower-cron-heartbeat", daemon=True)
    heartbeat_thread.start()
    returncode = proc.wait()
    stop_heartbeats.set()
    heartbeat_thread.join(timeout=heartbeat_seconds + 2)
    if returncode == 0:
        publish(
            args.agent,
            "done",
            args.cron,
            args.done_objective or f"{args.cron} complete",
            args.done_step,
            args.tool,
            work_id=work_id,
            run_id=run_id,
            work_event="terminal",
            phase="complete",
            lease_seconds=0,
            detail=f"exit_code={returncode}",
        )
    else:
        publish(
            args.agent,
            "blocked",
            args.cron,
            f"{args.cron} failed",
            args.error_step,
            args.tool,
            work_id=work_id,
            run_id=run_id,
            work_event="terminal",
            phase="failed",
            lease_seconds=0,
            detail=f"exit_code={returncode}",
        )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
