#!/usr/bin/env python3
"""Silent consistency loop for Control Tower Today's Jobs.

Updated 2026-06-26 by JAIMES: contracts updated to match night mode refactor.
Old contracts referenced removed CSS/DOM patterns. New contracts verify the
calendar-based job view and agent ops grid that replaced the old table layout.
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
    "JobTableHeader",
    "job-table-head",
    "job-row",
    "job-status",
    "JobsRail",
    "AgentOpsHealth",
]
REQUIRED_CSS = [
    "kiosk-grid",
    "night-mode-screen",
    "job-table-head",
    "job-row",
    "job-status",
    "agent-ops",
]


def run(cmd, timeout=120):
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)


def publish(status, title, detail):
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


def load_dashboard():
    if not DATA.exists():
        return {}
    return json.loads(DATA.read_text())


def check_contract():
    issues = []
    main = MAIN.read_text(errors="ignore") if MAIN.exists() else ""
    css = CSS.read_text(errors="ignore") if CSS.exists() else ""
    for needle in REQUIRED_MAIN:
        if needle not in main:
            issues.append("main missing " + needle)
    for needle in REQUIRED_CSS:
        if needle not in css:
            issues.append("css missing " + needle)
    return issues


def check_data():
    issues = []
    try:
        data = load_dashboard()
    except Exception as exc:
        return ["dashboard JSON parse failed: " + str(exc)]
    jobs = data.get("crons") or data.get("jobs") or []
    if not isinstance(jobs, list):
        return ["dashboard jobs payload is not a list"]
    visible = [j for j in jobs if j]
    if len(visible) < 8:
        issues.append("too few jobs visible: " + str(len(visible)))
    missing_core = []
    text = json.dumps(visible).lower()
    for label in ("sorare", "gmail", "brain", "breaking"):
        if label not in text:
            missing_core.append(label)
    if missing_core:
        issues.append("missing ecosystem lanes: " + ", ".join(missing_core))
    return issues


def main():
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
        publish("blocked", "Today Jobs consistency needs attention", "; ".join(issues[:5]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
