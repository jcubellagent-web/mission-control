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
DEFAULT_CATCH_UP_MINUTES = 15
MAX_RECEIPTS_PER_JOB = 128


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


def due_slot(job: dict[str, Any], now: dt.datetime) -> str | None:
    """Return the newest scheduled minute within the bounded catch-up window."""
    try:
        catch_up_minutes = max(0, int(job.get("catchUpMinutes", DEFAULT_CATCH_UP_MINUTES)))
    except (TypeError, ValueError):
        catch_up_minutes = DEFAULT_CATCH_UP_MINUTES
    for minutes_ago in range(catch_up_minutes + 1):
        candidate = now - dt.timedelta(minutes=minutes_ago)
        if is_due(job, candidate):
            return slot(candidate)
    return None


def slot_at_or_after(value: Any, target: str) -> bool:
    """Compare canonical minute slots without treating legacy text as ordered."""
    try:
        observed = dt.datetime.strptime(str(value), "%Y-%m-%dT%H:%M")
        scheduled = dt.datetime.strptime(target, "%Y-%m-%dT%H:%M")
    except (TypeError, ValueError):
        return str(value or "") == target
    return observed >= scheduled


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


def is_debounced_failure(job: dict[str, Any], result: dict[str, Any]) -> bool:
    if str(result.get("status") or "") not in {"failed", "timeout"}:
        return False
    return int(result.get("returncode") or 0) in {
        int(value)
        for value in job.get("debouncedReturnCodes", [])
        if str(value).lstrip("-").isdigit()
    }


def alert_threshold(job: dict[str, Any], result: dict[str, Any]) -> int:
    if job.get("severity") == "p0" and not is_debounced_failure(job, result):
        return 1
    return max(1, int(job.get("alertAfterFailures") or 2))


def defer_debounced_failure_for_change_lease(
    job: dict[str, Any],
    result: dict[str, Any],
    now: dt.datetime,
    *,
    allow_change_lease: bool,
) -> bool:
    return bool(
        job.get("suppressDebouncedDuringChangeLease")
        and not allow_change_lease
        and is_debounced_failure(job, result)
        and change_lease_active(now)
    )


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
    current_status = str(current.get("status") or "unknown")
    failed = current_status in {"failed", "timeout"}
    prior_open = bool(previous.get("incidentOpen"))
    current_open = bool(current.get("incidentOpen"))
    recovered = current_status == "ok" and prior_open
    opened = failed and current_open and not prior_open
    if not recovered and not opened:
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


def tick(
    config_path: Path,
    now: dt.datetime,
    shadow: bool = False,
    only: str | None = None,
    allow_change_lease: bool = False,
) -> dict[str, Any]:
    config = read_json(config_path, {})
    timezone = ZoneInfo(str(config.get("timezone") or "America/New_York"))
    local_now = now.astimezone(timezone)
    state = read_json(STATE_PATH, {"jobs": {}})
    prior_jobs = state.get("jobs") if isinstance(state.get("jobs"), dict) else {}
    new_jobs = dict(prior_jobs)
    prior_history = state.get("history") if isinstance(state.get("history"), dict) else {}
    new_history = {
        str(job_id): list(rows)[-MAX_RECEIPTS_PER_JOB:]
        for job_id, rows in prior_history.items()
        if isinstance(rows, list)
    }
    results = []
    current_slot = slot(local_now)

    def publish_running(job: dict[str, Any], previous: dict[str, Any], scheduled_slot: str) -> None:
        running_jobs = dict(new_jobs)
        running_jobs[str(job["id"])] = {
            "id": str(job["id"]),
            "owner": job.get("owner"),
            "team": job.get("team"),
            "severity": job.get("severity"),
            "status": "running",
            "startedAt": iso(now),
            "failureStreak": int(previous.get("failureStreak") or 0),
            "incidentOpen": bool(previous.get("incidentOpen")),
            "lastSlot": scheduled_slot,
        }
        atomic_write(STATE_PATH, {
            "version": 1,
            "checkedAt": iso(now),
            "localTime": local_now.isoformat(),
            "timezone": str(timezone),
            "shadow": False,
            "jobs": running_jobs,
            "history": new_history,
            "ran": [str(job["id"])],
            "status": "running",
        })

    for job in config.get("jobs", []):
        if not isinstance(job, dict) or not job.get("id"):
            continue
        job_id = str(job["id"])
        if only and job_id != only:
            continue
        previous = prior_jobs.get(job_id, {}) if isinstance(prior_jobs.get(job_id), dict) else {}
        forced = bool(only)
        scheduled_slot = current_slot if forced else due_slot(job, local_now)
        if not scheduled_slot or (not forced and slot_at_or_after(previous.get("lastSlot"), scheduled_slot)):
            continue
        if job.get("skipDuringChangeLease") and change_lease_active(now) and not allow_change_lease:
            result = {"id": job_id, "status": "skipped_change_lease", "startedAt": iso(now), "durationMs": 0}
        else:
            if not shadow:
                # #JAIMES: publish in-flight truth before long QA so health
                # checks do not mistake the previous terminal state for now.
                publish_running(job, previous, scheduled_slot)
            result = run_job(job, shadow=shadow)
        if defer_debounced_failure_for_change_lease(
            job,
            result,
            now,
            allow_change_lease=allow_change_lease,
        ):
            result["observedStatus"] = result["status"]
            result["status"] = "skipped_change_lease"
            result["failureStreak"] = int(previous.get("failureStreak") or 0)
            result["incidentOpen"] = bool(previous.get("incidentOpen"))
        elif result["status"] in {"failed", "timeout"}:
            result["failureStreak"] = int(previous.get("failureStreak") or 0) + 1
            result["incidentOpen"] = bool(previous.get("incidentOpen")) or (
                int(result["failureStreak"]) >= alert_threshold(job, result)
            )
        elif result["status"] == "ok":
            result["failureStreak"] = 0
            result["incidentOpen"] = False
        else:
            result["failureStreak"] = int(previous.get("failureStreak") or 0)
            result["incidentOpen"] = bool(previous.get("incidentOpen"))
        result["lastSlot"] = scheduled_slot
        new_jobs[job_id] = result
        # #JAIMES: preserve a bounded metadata-only receipt ledger so Today's
        # Jobs can report each scheduled outcome instead of repainting the day
        # from the definition's latest state.
        receipt = {
            "scheduledAt": scheduled_slot,
            "status": result.get("status"),
            "startedAt": result.get("startedAt"),
            "finishedAt": result.get("completedAt") or result.get("startedAt"),
            "durationMs": result.get("durationMs"),
            "returncode": result.get("returncode"),
        }
        receipts = [
            row for row in new_history.get(job_id, [])
            if isinstance(row, dict) and row.get("scheduledAt") != scheduled_slot
        ]
        receipts.append(receipt)
        new_history[job_id] = receipts[-MAX_RECEIPTS_PER_JOB:]
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
        "history": new_history,
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
    parser.add_argument("--allow-change-lease", action="store_true", help="Allow an explicit operator-run job during a held Control Tower lease")
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
        output = tick(
            args.config,
            dt.datetime.now(dt.timezone.utc),
            shadow=args.shadow,
            only=args.run_job,
            allow_change_lease=args.allow_change_lease,
        )
    print(json.dumps(output, indent=2))
    return 1 if output["status"] == "attention" else 0


if __name__ == "__main__":
    raise SystemExit(main())
