#!/usr/bin/env python3
"""Keep the live Control Tower kiosk screen-check sidecar fresh."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from control_tower_foreground import ensure_foreground


ROOT = Path(__file__).resolve().parents[1]
QA_PYTHON = ROOT / ".venv-qa" / "bin" / "python"
PLAYWRIGHT_PYTHON = Path("/opt/homebrew/bin/python3")
SHARED_EVENTS_PATH = ROOT / "data" / "shared-events.json"
WATCHDOG_STATE_PATH = ROOT / "data" / "mission-control-kiosk-watchdog-state.json"
CHANGE_LOCK = Path.home() / ".openclaw" / "state" / "control-tower-change-lock.json"
SCREEN_CHECK_FAILURE_STATUSES = {"attention", "blocked", "error", "failed"}


def run(cmd: list[str], timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)


def publish(status: str, title: str, detail: str) -> None:
    event_type = "complete" if status == "done" else "blocked" if status in {"blocked", "error"} else "status"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "agent_publish.py"),
            "--agent",
            "josh2",
            "--type",
            event_type,
            "--status",
            status,
            "--title",
            title,
            "--tool",
            "Control Tower screen check",
            "--detail",
            detail,
            "--privacy",
            "dashboard-safe",
            "--brain-feed",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def change_lease_active(now: dt.datetime | None = None) -> bool:
    lease = read_json(CHANGE_LOCK, {})
    try:
        expires = dt.datetime.fromisoformat(str(lease.get("expiresAt") or "").replace("Z", "+00:00"))
        return expires.astimezone(dt.timezone.utc) > (now or dt.datetime.now(dt.timezone.utc))
    except (TypeError, ValueError):
        return False


def latest_screen_check_status(path: Path = SHARED_EVENTS_PATH) -> str:
    """Return the newest published Josh 2.0 screen-check status, if any."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    events = payload.get("events", []) if isinstance(payload, dict) else []
    matching = [
        event
        for event in events
        if isinstance(event, dict)
        and str(event.get("agent") or "").lower() == "josh2"
        and "screen check"
        in f"{event.get('tool') or ''} {event.get('title') or ''}".lower()
    ]
    if not matching:
        return ""
    latest = max(matching, key=lambda event: str(event.get("time") or ""))
    return str(latest.get("status") or latest.get("type") or "").lower()


def publication_for_result(
    *,
    ok: bool,
    detail: str,
    prior_status: str,
    repaired: bool,
    foreground_action: bool,
) -> tuple[str, str, str] | None:
    """Publish only incident transitions; routine healthy polls stay quiet."""
    prior_failed = prior_status.lower() in SCREEN_CHECK_FAILURE_STATUSES
    if not ok:
        if prior_failed:
            return None
        return "blocked", "Josh 2.0 screen check needs attention", detail

    if prior_failed:
        actions = []
        if repaired:
            actions.append("repaired the dedicated kiosk")
        if foreground_action:
            actions.append("restored the exact kiosk window to the foreground")
        action_detail = f"{detail.rstrip().rstrip(';')}; {' and '.join(actions)}" if actions else detail
        return "done", "Josh 2.0 screen check recovered", action_detail
    return None


def runtime_check() -> tuple[bool, dict[str, Any], str]:
    # launchd intentionally starts this lightweight watchdog with /usr/bin
    # Python. The rendered-browser check must use the Homebrew runtime where
    # Playwright is installed; raw Chromium fallback can exit 0 without a DOM
    # and create a false alert from harmless allocator/keychain/GCM stderr.
    if PLAYWRIGHT_PYTHON.is_file():
        layout_python = str(PLAYWRIGHT_PYTHON)
    elif QA_PYTHON.is_file():
        layout_python = str(QA_PYTHON)
    else:
        layout_python = sys.executable
    proc = run([layout_python, str(ROOT / "scripts" / "mission_control_runtime_layout_check.py")])
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(proc.stdout)
    except Exception:
        payload = {"summary": (proc.stdout or proc.stderr or "").strip()[:240]}
    ok = proc.returncode == 0 and bool(payload.get("ok"))
    detail = str(payload.get("summary") or "Control Tower screen check ran.")
    return ok, payload, detail


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Control Tower kiosk screen-check status.")
    parser.add_argument("--repair", action="store_true", help="Try to reopen the kiosk if the live check fails.")
    parser.add_argument("--no-publish", action="store_true", help="Skip Brain Feed status publishing.")
    args = parser.parse_args()

    if change_lease_active():
        print(json.dumps({
            "ok": True,
            "status": "skipped_change_lease",
            "detail": "Control Tower source change lease is active; screen check deferred.",
        }, indent=2))
        return 0

    prior_state = read_json(WATCHDOG_STATE_PATH, {})
    prior_event_status = latest_screen_check_status()
    prior_incident_open = bool(prior_state.get("incidentOpen")) or (
        prior_event_status.lower() in SCREEN_CHECK_FAILURE_STATUSES
    )

    ok, payload, detail = runtime_check()
    repaired = False
    foreground = ensure_foreground(repair=False)
    if foreground.get("status") == "deferred" and foreground.get("reason") == "active-visible-work":
        print(json.dumps({
            "ok": True,
            "status": "skipped_visible_work",
            "detail": "Visible browser work owns the display; kiosk enforcement deferred.",
            "runtime": payload,
            "foreground": foreground,
        }, indent=2))
        return 0
    foreground_ok = bool(foreground.get("ok"))
    foreground_action = foreground.get("status") == "focused"
    if not foreground_ok:
        ok = False
        detail = f"{detail}; physical foreground check: {foreground.get('reason', 'unknown failure')}"

    update = run([sys.executable, str(ROOT / "scripts" / "update_mission_control.py")], timeout=120)
    if update.returncode != 0:
        ok = False
        detail = f"{detail}; Control Tower refresh failed"

    failure_streak = 0 if ok else int(prior_state.get("failureStreak") or 0) + 1
    if not ok and failure_streak >= 2 and args.repair and not foreground_ok:
        foreground = ensure_foreground(repair=True)
        repaired = True
        foreground_ok = bool(foreground.get("ok"))
        foreground_action = foreground.get("status") == "focused"
        if foreground_ok:
            time.sleep(1)
            ok, payload, detail = runtime_check()
            update = run([sys.executable, str(ROOT / "scripts" / "update_mission_control.py")], timeout=120)
            if update.returncode != 0:
                ok = False
                detail = f"{detail}; Control Tower refresh failed"
            failure_streak = 0 if ok else failure_streak

    incident_open = prior_incident_open
    if ok:
        incident_open = False
    elif failure_streak >= 2:
        incident_open = True

    atomic_write(WATCHDOG_STATE_PATH, {
        "checkedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "ok" if ok else "attention",
        "failureStreak": failure_streak,
        "incidentOpen": incident_open,
        "foregroundStatus": foreground.get("status"),
    })

    publication_emitted = False
    if not args.no_publish:
        publication = None
        if ok or failure_streak >= 2:
            publication = publication_for_result(
                ok=ok,
                detail=detail,
                prior_status="blocked" if prior_incident_open else "done",
                repaired=repaired,
                foreground_action=foreground_action,
            )
        if publication:
            publish(*publication)
            publication_emitted = True

    # The first refresh validates the writer before a status transition is
    # published. Refresh once more only on transitions so the new event is
    # visible immediately instead of waiting for the next five-minute poll.
    if publication_emitted:
        update = run([sys.executable, str(ROOT / "scripts" / "update_mission_control.py")], timeout=120)
        if update.returncode != 0:
            ok = False
            detail = f"{detail}; Control Tower post-publication refresh failed"

    result = {
        "ok": ok,
        "status": "ok" if ok else "attention",
        "detail": detail,
        "runtime": payload,
        "foreground": foreground,
        "repaired": repaired,
        "failureStreak": failure_streak,
        "incidentOpen": incident_open,
        "dashboardRefreshOk": update.returncode == 0,
    }
    print(json.dumps(result, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
