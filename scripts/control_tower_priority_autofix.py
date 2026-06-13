#!/usr/bin/env python3
"""Auto-repair refreshable Control Tower Priority Queue alerts.

Runs silent when alerts are clear or successfully repaired. Prints a concise
Josh-facing summary only when a Priority Queue alert remains unresolved.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "dashboard-data.json"
MIN_EXPECTED_OPERATOR_JOBS = 30
MIN_EXPECTED_AGENT_ROWS = 3
RUNTIME_STALE_MINUTES = 45


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def age_minutes(value: Any) -> float | None:
    parsed = parse_ts(value)
    if not parsed:
        return None
    return max(0.0, (utc_now() - parsed).total_seconds() / 60)


def run(cmd: list[str], timeout: int = 180) -> dict[str, Any]:
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
    return {
        "cmd": " ".join(cmd),
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
        "ok": proc.returncode == 0,
    }


def load_dashboard() -> dict[str, Any]:
    try:
        data = json.loads(DATA.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def job_title(job: dict[str, Any]) -> str:
    return str(job.get("title") or job.get("name") or job.get("id") or "Scheduled job")


def job_attention_items(data: dict[str, Any]) -> list[dict[str, str]]:
    jobs = data.get("crons") or data.get("jobs") or []
    if not isinstance(jobs, list):
        return [{"title": "Job data payload invalid", "detail": "Control Tower jobs payload is not a list."}]
    items: list[dict[str, str]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        status = str(job.get("status") or "").lower()
        run_status = str(job.get("runStatus") or job.get("run_status") or "").lower()
        if status == "paused":
            continue
        if run_status in {"missed", "error", "failed", "blocked"} or status in {"error", "failed", "blocked"}:
            items.append({
                "title": job_title(job),
                "detail": f"status={status or 'unknown'} runStatus={run_status or 'unknown'}",
            })
    return items[:6]


def priority_alerts(data: dict[str, Any]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    for item in data.get("actionRequired") or []:
        if isinstance(item, dict):
            alerts.append({
                "title": str(item.get("title") or "Action required"),
                "detail": str(item.get("detail") or item.get("priority") or ""),
            })

    runtime = data.get("runtimeLayout") if isinstance(data.get("runtimeLayout"), dict) else {}
    runtime_age = age_minutes(runtime.get("checkedAt"))
    if runtime and (runtime.get("ok") is False or runtime.get("status") == "attention" or runtime.get("issues")):
        alerts.append({"title": "Control Tower layout issue", "detail": str(runtime.get("summary") or runtime.get("issues") or "")})
    elif runtime_age is None or runtime_age >= RUNTIME_STALE_MINUTES:
        alerts.append({"title": "Josh 2.0 screen check is stale", "detail": f"runtime layout age={runtime_age!r}m"})

    jobs = data.get("crons") or data.get("jobs") or []
    if not isinstance(jobs, list) or len(jobs) < MIN_EXPECTED_OPERATOR_JOBS:
        alerts.append({"title": "Job data needs refresh", "detail": f"{len(jobs) if isinstance(jobs, list) else 0} jobs loaded"})

    statuses = data.get("agentBrainFeeds") or data.get("activeAgents") or []
    if isinstance(statuses, list) and len(statuses) < MIN_EXPECTED_AGENT_ROWS:
        alerts.append({"title": "Agent status coverage is low", "detail": f"{len(statuses)}/{MIN_EXPECTED_AGENT_ROWS} core agent rows loaded"})

    shared = data.get("sharedOperatingLayer") if isinstance(data.get("sharedOperatingLayer"), dict) else {}
    if shared.get("status") == "attention":
        blocked = shared.get("blockedEvents") or []
        handoffs = shared.get("attentionHandoffs") or []
        tasks = (shared.get("tasks") or {}) if isinstance(shared.get("tasks"), dict) else {}
        title = "Shared layer needs attention"
        detail = ""
        for row in list(blocked or []) + list(handoffs or []) + list(tasks.get("blocked") or []) + list(tasks.get("approvalNeeded") or []):
            if isinstance(row, dict):
                title = str(row.get("title") or title)
                detail = str(row.get("detail") or row.get("status") or "")
                break
        alerts.append({"title": title, "detail": detail})

    alerts.extend(job_attention_items(data))

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for alert in alerts:
        key = (alert.get("title", "") + "|" + alert.get("detail", ""))[:220]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(alert)
    return deduped[:8]


def run_repairs(before: dict[str, Any], before_alerts: list[dict[str, str]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    runtime = before.get("runtimeLayout") if isinstance(before.get("runtimeLayout"), dict) else {}
    runtime_age = age_minutes(runtime.get("checkedAt"))
    needs_runtime = any("layout" in a["title"].lower() or "screen check" in a["title"].lower() for a in before_alerts)
    if needs_runtime or runtime_age is None or runtime_age >= 30:
        steps.append(run([sys.executable, "scripts/mission_control_runtime_layout_check.py"], timeout=90))
        steps.append(run([sys.executable, "scripts/mission_control_kiosk_watchdog.py", "--repair", "--no-publish"], timeout=150))

    if any("agent status coverage" in a["title"].lower() for a in before_alerts):
        jain = ROOT / "scripts" / "jain_visibility_heartbeat.py"
        if jain.exists():
            steps.append(run([sys.executable, str(jain), "--brain-feed"], timeout=90))

    router = ROOT / "scripts" / "agent_auto_delegate.py"
    if router.exists():
        samples = [
            ("Mission Control refresh", "Refresh Mission Control and repair stale Brain Feed visibility."),
            ("Sorare pre-lock review", "Check Sorare lineups, daily missions, starters, DNP risk, and lock timing."),
            ("Personal Gmail reply", "Use my personal Gmail browser session to draft a reply."),
        ]
        for title, objective in samples:
            steps.append(run([
                sys.executable, str(router), "--title", title, "--objective", objective,
                "--requester", "joshex", "--privacy", "dashboard-safe", "--dry-run",
            ], timeout=60))

    steps.append(run([sys.executable, "scripts/update_mission_control.py"], timeout=180))
    steps.append(run([sys.executable, "scripts/mission_control_regression_check.py"], timeout=120))
    return steps


def main() -> int:
    before = load_dashboard()
    before_alerts = priority_alerts(before)
    steps = run_repairs(before, before_alerts)
    after = load_dashboard()
    # update_mission_control writes dashboard; reload after repairs
    after = load_dashboard()
    after_alerts = priority_alerts(after)
    failed_steps = [s for s in steps if not s.get("ok")]

    result = {
        "checkedAt": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "beforeAlertCount": len(before_alerts),
        "afterAlertCount": len(after_alerts),
        "fixed": bool(before_alerts and not after_alerts and not failed_steps),
        "ok": not after_alerts and not failed_steps,
        "repairs": [
            {"cmd": s["cmd"], "ok": s["ok"], "returncode": s["returncode"]}
            for s in steps
        ],
        "unresolved": after_alerts,
        "failedSteps": [
            {"cmd": s["cmd"], "returncode": s["returncode"], "stderr": s.get("stderr", "")[-800:], "stdout": s.get("stdout", "")[-800:]}
            for s in failed_steps[:3]
        ],
    }
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
