#!/usr/bin/env python3
"""Silent consistency loop for Control Tower Today's Jobs.

Validates the source/build contract that keeps Today's Jobs as a fixed table,
checks generated job data is parseable and has visible same-day/fallback rows,
and emits dashboard-safe Brain Feed alerts only when the contract breaks.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "v2-react" / "src" / "main.tsx"
CSS = ROOT / "v2-react" / "src" / "styles.css"
DATA = ROOT / "data" / "dashboard-data.json"

REQUIRED_MAIN = [
    "jobs-table-head",
    "calendarBlockStateLabel",
    ".slice(0, 10)",
    "Time</span>",
    "State</span>",
    "Job</span>",
    "Owner</span>",
]
REQUIRED_CSS = [
    "TODAY JOBS STABLE TABLE CONTRACT 2026-06-13",
    "TODAY JOBS STABLE TABLE SPECIFICITY LOCK 2026-06-13",
    "TODAY JOBS ROW HEIGHT FINAL LOCK 2026-06-13",
    "grid-template-columns: 48px 72px minmax(0, 1fr) 58px",
    "grid-template-columns: 0 48px 72px minmax(0, 1fr) 58px",
    "height: 30px !important",
]


def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)


def publish(status: str, title: str, detail: str) -> None:
    event_type = "blocked" if status in {"blocked", "error"} else "complete"
    subprocess.run([
        sys.executable, str(ROOT / "scripts" / "agent_publish.py"),
        "--agent", "josh2",
        "--type", event_type,
        "--status", status,
        "--title", title,
        "--tool", "todays-jobs-consistency-watchdog",
        "--detail", detail[:420],
        "--privacy", "dashboard-safe",
        "--brain-feed",
    ], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def load_dashboard() -> dict[str, Any]:
    if not DATA.exists():
        return {}
    return json.loads(DATA.read_text())


def check_contract() -> list[str]:
    issues: list[str] = []
    main = MAIN.read_text(errors="ignore") if MAIN.exists() else ""
    css = CSS.read_text(errors="ignore") if CSS.exists() else ""
    for needle in REQUIRED_MAIN:
        if needle not in main:
            issues.append(f"main missing {needle}")
    for needle in REQUIRED_CSS:
        if needle not in css:
            issues.append(f"css missing {needle}")
    return issues


def check_data() -> list[str]:
    issues: list[str] = []
    try:
        data = load_dashboard()
    except Exception as exc:  # noqa: BLE001
        return [f"dashboard JSON parse failed: {exc}"]
    jobs = data.get("crons") or data.get("jobs") or []
    if not isinstance(jobs, list):
        return ["dashboard jobs payload is not a list"]
    visible = [j for j in jobs if j]
    if len(visible) < 8:
        issues.append(f"too few jobs visible: {len(visible)}")
    missing_core = []
    text = json.dumps(visible).lower()
    for label in ("sorare", "gmail", "brain", "breaking"):
        if label not in text:
            missing_core.append(label)
    if missing_core:
        issues.append("missing ecosystem lanes: " + ", ".join(missing_core))
    return issues


def main() -> int:
    refresh = run([sys.executable, str(ROOT / "scripts" / "update_mission_control.py")], timeout=120)
    issues = []
    if refresh.returncode != 0:
        issues.append("dashboard refresh failed")
    issues.extend(check_contract())
    issues.extend(check_data())
    regression = run([sys.executable, str(ROOT / "scripts" / "mission_control_regression_check.py")], timeout=120)
    if regression.returncode != 0:
        issues.append("regression check failed")
    result = {"ok": not issues, "issues": issues[:12]}
    print(json.dumps(result, indent=2))
    if issues:
        publish("blocked", "Today's Jobs consistency needs attention", "; ".join(issues[:5]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
