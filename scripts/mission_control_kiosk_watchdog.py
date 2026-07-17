#!/usr/bin/env python3
"""Keep the live Control Tower kiosk screen-check sidecar fresh."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from control_tower_foreground import ensure_foreground


ROOT = Path(__file__).resolve().parents[1]
QA_PYTHON = ROOT / ".venv-qa" / "bin" / "python"
PLAYWRIGHT_PYTHON = Path("/opt/homebrew/bin/python3")


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

    ok, payload, detail = runtime_check()
    repaired = False
    if not ok and args.repair:
        opener = ROOT / "scripts" / "open_mission_control_kiosk.sh"
        if opener.exists():
            run([str(opener)], timeout=75)
            repaired = True
            time.sleep(3)
            ok, payload, detail = runtime_check()

    foreground = ensure_foreground(repair=args.repair)
    foreground_ok = bool(foreground.get("ok"))
    foreground_action = foreground.get("status") == "focused"
    if not foreground_ok:
        ok = False
        detail = f"{detail}; physical foreground check: {foreground.get('reason', 'unknown failure')}"

    update = run([sys.executable, str(ROOT / "scripts" / "update_mission_control.py")], timeout=120)
    if update.returncode != 0:
        ok = False
        detail = f"{detail}; Control Tower refresh failed"

    if not args.no_publish:
        if ok and (repaired or foreground_action):
            actions = []
            if repaired:
                actions.append("reopened Chrome kiosk")
            if foreground_action:
                actions.append("restored the exact kiosk window to the foreground")
            publish("done", "Josh 2.0 screen restored", f"{detail}; {' and '.join(actions)}")
        else:
            if not ok:
                publish("blocked", "Josh 2.0 screen check needs attention", detail)

    result = {
        "ok": ok,
        "status": "ok" if ok else "attention",
        "detail": detail,
        "runtime": payload,
        "foreground": foreground,
        "repaired": repaired,
        "dashboardRefreshOk": update.returncode == 0,
    }
    print(json.dumps(result, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
