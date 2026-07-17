#!/usr/bin/env python3
"""Single deterministic dispatcher for Control Tower QA and product support."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "ecosystem-qa-schedule.json"
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "ecosystem-qa-scheduler.json"
LOCK_DIR = DATA_DIR / ".ecosystem-qa-locks"
TICK_LOCK = LOCK_DIR / "scheduler.lock"
CHANGE_LOCK = Path.home() / ".openclaw" / "state" / "control-tower-change-lock.json"
HOST_TOOL_PATHS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin")


def iso(value: dt.datetime | None = None) -> str:
    return (value or dt.datetime.now(dt.timezone.utc)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def slot(now: dt.datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M")


def is_due(job: dict[str, Any], now: dt.datetime) -> bool:
    schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
    interval = int(schedule.get("intervalMinutes") or 0)
    if interval:
        return now.minute % interval == int(schedule.get("offset") or 0) % interval
    minutes = schedule.get("minutes")
    hours = schedule.get("hours")
    weekdays = schedule.get("weekdays")
    return (
        (not minutes or now.minute in minutes)
        and (not hours or now.hour in hours)
        and (not weekdays or now.weekday() in weekdays)
    )


def compact(value: Any, limit: int = 1600) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def change_lease_active(now: dt.datetime) -> bool:
    lease = read_json(CHANGE_LOCK, {})
    try:
        expires = dt.datetime.fromisoformat(str(lease.get("expiresAt") or "").replace("Z", "+00:00"))
        return expires.astimezone(dt.timezone.utc) > now.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return False


def scheduler_environment() -> dict[str, str]:
    """Keep launchd jobs able to resolve host tools such as npm and node."""
    env = os.environ.copy()
    inherited = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    env["PATH"] = os.pathsep.join(dict.fromkeys((*HOST_TOOL_PATHS, *inherited)))
    return env


def run_job(job: dict[str, Any], shadow: bool = False) -> dict[str, Any]:
    job_id = str(job["id"])
    command = [str(item) for item in job.get("command", [])]
    started = dt.datetime.now(dt.timezone.utc)
    if shadow:
        return {"id": job_id, "status": "shadow_due", "command": command, "startedAt": iso(started), "durationMs": 0}
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    with (LOCK_DIR / f"{job_id}.lock").open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"id": job_id, "status": "skipped_locked", "startedAt": iso(started), "durationMs": 0}
        try:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=scheduler_environment(),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            stdout, stderr = process.communicate(timeout=int(job.get("timeoutSeconds") or 120))
            returncode = process.returncode
            skip_return_codes = {
                int(value)
                for value in job.get("skipReturnCodes", [])
                if str(value).lstrip("-").isdigit()
            }
            if returncode in skip_return_codes:
                status = "skipped_precondition"
            else:
                status = "ok" if returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            returncode, status = 124, "timeout"
        except Exception as exc:
            stdout, stderr, returncode, status = "", f"{type(exc).__name__}: {exc}", 125, "failed"
        ended = dt.datetime.now(dt.timezone.utc)
        return {
            "id": job_id,
            "owner": job.get("owner"),
            "team": job.get("team"),
            "severity": job.get("severity"),
            "status": status,
            "returncode": returncode,
            "startedAt": iso(started),
            "completedAt": iso(ended),
            "durationMs": round((ended - started).total_seconds() * 1000),
            "stdout": compact(stdout),
            "stderr": compact(stderr),
        }


def publish_transition(job: dict[str, Any], current: dict[str, Any], previous: dict[str, Any]) -> None:
    previous_status = str(previous.get("status") or "unknown")
    current_status = str(current.get("status") or "unknown")
    failed = current_status in {"failed", "timeout"}
    recovered = current_status == "ok" and int(previous.get("failureStreak") or 0) > 0
    streak = int(current.get("failureStreak") or 0)
    immediate = job.get("severity") == "p0"
    if not recovered and not (failed and (immediate or streak >= 2)):
        return
    title = f"{job.get('team')}: {job.get('id')} {'recovered' if recovered else 'needs attention'}"
    detail = "Recovered after a prior QA failure." if recovered else compact(current.get("stderr") or current.get("stdout") or "Scheduled QA failed.", 500)
    command = [
        sys.executable, str(ROOT / "scripts" / "agent_publish.py"),
        "--agent", str(job.get("owner") or "josh2"),
        "--type", "complete" if recovered else "blocked",
        "--status", "done" if recovered else "blocked",
        "--title", title,
        "--tool", "ecosystem QA scheduler",
        "--detail", detail,
        "--privacy", "dashboard-safe",
        "--brain-feed",
    ]
    subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30, check=False)


def tick(config_path: Path, now: dt.datetime, shadow: bool = False, only: str | None = None) -> dict[str, Any]:
    config = read_json(config_path, {})
    timezone = ZoneInfo(str(config.get("timezone") or "America/New_York"))
    local_now = now.astimezone(timezone)
    state = read_json(STATE_PATH, {"jobs": {}})
    prior_jobs = state.get("jobs") if isinstance(state.get("jobs"), dict) else {}
    new_jobs = dict(prior_jobs)
    results = []
    current_slot = slot(local_now)
    for job in config.get("jobs", []):
        if not isinstance(job, dict) or not job.get("id"):
            continue
        job_id = str(job["id"])
        if only and job_id != only:
            continue
        previous = prior_jobs.get(job_id, {}) if isinstance(prior_jobs.get(job_id), dict) else {}
        forced = bool(only)
        if not forced and (not is_due(job, local_now) or previous.get("lastSlot") == current_slot):
            continue
        if job.get("skipDuringChangeLease") and change_lease_active(now):
            result = {"id": job_id, "status": "skipped_change_lease", "startedAt": iso(now), "durationMs": 0}
        else:
            result = run_job(job, shadow=shadow)
        if result["status"] in {"failed", "timeout"}:
            result["failureStreak"] = int(previous.get("failureStreak") or 0) + 1
        elif result["status"] == "ok":
            result["failureStreak"] = 0
        else:
            result["failureStreak"] = int(previous.get("failureStreak") or 0)
        result["lastSlot"] = current_slot
        new_jobs[job_id] = result
        results.append(result)
        if not shadow:
            publish_transition(job, result, previous)
    output = {
        "version": 1,
        "checkedAt": iso(now),
        "localTime": local_now.isoformat(),
        "timezone": str(timezone),
        "shadow": shadow,
        "jobs": new_jobs,
        "ran": [row["id"] for row in results],
        "status": "attention" if any(row["status"] in {"failed", "timeout"} for row in results) else "ok",
    }
    if not shadow:
        atomic_write(STATE_PATH, output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--run-job")
    parser.add_argument("--shadow", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    config = read_json(args.config, {})
    if args.list:
        print(json.dumps(config, indent=2))
        return 0
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    with TICK_LOCK.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"ok": True, "status": "skipped_locked"}))
            return 0
        output = tick(args.config, dt.datetime.now(dt.timezone.utc), shadow=args.shadow, only=args.run_job)
    print(json.dumps(output, indent=2))
    return 1 if output["status"] == "attention" else 0


if __name__ == "__main__":
    raise SystemExit(main())
