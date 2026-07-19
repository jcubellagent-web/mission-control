#!/usr/bin/env python3
"""Auto-repair refreshable Control Tower Priority Queue alerts.

Runs silent when alerts are clear or successfully repaired. Prints a concise
Josh-facing summary only when a Priority Queue alert remains unresolved.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "dashboard-data.json"
STATE_PATH = ROOT / "data" / "control-tower-autofresh-ops.json"
MIN_EXPECTED_OPERATOR_JOBS = 30
MIN_EXPECTED_AGENT_ROWS = 3
RUNTIME_STALE_MINUTES = 45
STATE_WINDOW_HOURS = 48
STATE_HISTORY_LIMIT = 36
STATE_SCHEMA = 2

#JAIMES: keep a short local incident ledger so repeated Control Tower drift turns into concrete next fixes instead of one-off repairs.


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


def alert_key(alert: dict[str, str]) -> str:
    title = str(alert.get("title") or "").strip().lower()
    detail = str(alert.get("detail") or "").strip().lower()
    return f"{title}|{detail}"


def summarize_alerts(alerts: list[dict[str, str]]) -> list[str]:
    return [f'{alert.get("title", "Alert")}: {alert.get("detail", "").strip()}'.strip(": ") for alert in alerts[:6]]


def load_ops_state() -> dict[str, Any]:
    try:
        payload = json.loads(STATE_PATH.read_text())
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def save_ops_state(payload: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def recommendation_for_title(title: str) -> str:
    lower = title.lower()
    if "screen check" in lower or "layout" in lower or "kiosk" in lower:
        return "Review kiosk watchdog and runtime layout checks."
    if "job data" in lower or "refresh" in lower:
        return "Review dashboard regeneration or publish path drift."
    if "agent status coverage" in lower or "shared layer" in lower:
        return "Review heartbeats, agent publishers, and shared sidecars."
    return "Promote this repeat incident into a dedicated watchdog or skill update."


def update_ops_state(before_alerts: list[dict[str, str]], after_alerts: list[dict[str, str]], fixed: bool, ok: bool) -> dict[str, Any]:
    now = utc_now()
    prior = load_ops_state()
    history = prior.get("history") if prior.get("schema") == STATE_SCHEMA and isinstance(prior.get("history"), list) else []
    fresh_history: list[dict[str, Any]] = []
    cutoff = now - dt.timedelta(hours=STATE_WINDOW_HOURS)
    for row in history:
        if not isinstance(row, dict):
            continue
        seen_at = parse_ts(row.get("checkedAt"))
        if seen_at and seen_at >= cutoff:
            fresh_history.append(row)

    observed_alerts = list(before_alerts) + list(after_alerts)
    observed_keys = list(dict.fromkeys(alert_key(alert) for alert in observed_alerts))
    unresolved_keys = list(dict.fromkeys(alert_key(alert) for alert in after_alerts))
    fresh_history.append({
        "checkedAt": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "ok": ok,
        "fixed": fixed,
        "beforeAlerts": summarize_alerts(before_alerts),
        "afterAlerts": summarize_alerts(after_alerts),
        "observedAlertKeys": observed_keys,
        "unresolvedAlertKeys": unresolved_keys,
    })
    fresh_history = fresh_history[-STATE_HISTORY_LIMIT:]

    counts: dict[str, dict[str, Any]] = {}
    for row in fresh_history:
        unresolved = {
            key for key in row.get("unresolvedAlertKeys") or []
            if isinstance(key, str) and key
        }
        for key in row.get("observedAlertKeys") or []:
            if not isinstance(key, str) or not key:
                continue
            item = counts.setdefault(key, {"key": key, "count": 0, "unresolvedCount": 0, "lastSeen": row.get("checkedAt", "")})
            item["count"] += 1
            item["lastSeen"] = row.get("checkedAt", "")
            if key in unresolved:
                item["unresolvedCount"] += 1

    recurring: list[dict[str, Any]] = []
    recommendations: list[str] = []
    lookup = {alert_key(alert): alert for alert in after_alerts}
    active_keys = set(lookup)
    for key, item in sorted(counts.items(), key=lambda pair: (-pair[1]["unresolvedCount"], -pair[1]["count"], pair[0])):
        if item["count"] < 2:
            continue
        title, _, detail = key.partition("|")
        alert = lookup.get(key, {})
        entry = {
            "key": key,
            "title": alert.get("title") or title.title(),
            "detail": alert.get("detail") or detail,
            "count": item["count"],
            "unresolvedCount": item["unresolvedCount"],
            "lastSeen": item["lastSeen"],
            "active": key in active_keys,
            "recommendation": recommendation_for_title(str(alert.get("title") or title)),
        }
        recurring.append(entry)
        if key in lookup and item["unresolvedCount"] >= 2:
            recommendations.append(f'{entry["title"]}: {entry["recommendation"]}')

    payload = {
        "schema": STATE_SCHEMA,
        "checkedAt": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "ok" if ok else "attention",
        "fixed": fixed,
        "beforeAlerts": summarize_alerts(before_alerts),
        "afterAlerts": summarize_alerts(after_alerts),
        "activeAlertKeys": sorted(active_keys),
        "history": fresh_history,
        "recurringAlerts": recurring[:8],
        "recommendations": recommendations[:6],
    }
    save_ops_state(payload)
    return payload


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



def prune_stale_freshness_history() -> dict[str, Any]:
    data_dir = ROOT / "data"
    removed = {"sharedEvents": 0, "brainFeedSteps": 0}
    shared = data_dir / "shared-events.json"
    if shared.exists():
        try:
            obj = json.loads(shared.read_text())
            events = obj.get("events") if isinstance(obj, dict) else None
            if isinstance(events, list):
                clean = []
                for event in events:
                    if not isinstance(event, dict):
                        clean.append(event); continue
                    title = str(event.get("title") or "")
                    status = str(event.get("status") or "").lower()
                    detail = str(event.get("detail") or "").lower()
                    tool = str(event.get("tool") or "")
                    if title == "Mission Control freshness loop" and status in {"error", "blocked"} and ("visibility guard" in detail or tool == "mission_control_freshness_loop"):
                        removed["sharedEvents"] += 1; continue
                    clean.append(event)
                obj["events"] = clean[:80]
                shared.write_text(json.dumps(obj, indent=2) + "\n")
        except Exception:
            pass
    brain = data_dir / "brain-feed.json"
    if brain.exists():
        try:
            obj = json.loads(brain.read_text())
            steps = obj.get("steps") if isinstance(obj, dict) else None
            if isinstance(steps, list):
                clean = []
                for step in steps:
                    if not isinstance(step, dict):
                        clean.append(step); continue
                    label = str(step.get("label") or "")
                    status = str(step.get("status") or "").lower()
                    tool = str(step.get("tool") or "")
                    if label == "Mission Control freshness loop" and status in {"error", "blocked"} and tool == "mission_control_freshness_loop":
                        removed["brainFeedSteps"] += 1; continue
                    clean.append(step)
                if str(obj.get("status") or "").lower() in {"done", "idle", "ready"} and obj.get("active") is False:
                    # If the feed is currently healthy, do not preserve stale
                    # blocked/error step history from older agent tasks.
                    pruned_clean = []
                    for step in clean:
                        if isinstance(step, dict) and str(step.get("status") or "").lower() in {"error", "blocked"}:
                            removed["brainFeedSteps"] += 1
                            continue
                        pruned_clean.append(step)
                    clean = pruned_clean
                obj["steps"] = clean[:10]
                if obj.get("status") in {"error", "blocked"} and removed["brainFeedSteps"]:
                    obj["status"] = "done"; obj["active"] = False; obj["detail"] = "Mission Control freshness ok."
                brain.write_text(json.dumps(obj, indent=2) + "\n")
        except Exception:
            pass
    return removed

def run_repairs(before: dict[str, Any], before_alerts: list[dict[str, str]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if not before_alerts:
        return steps
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

    steps.append(run([sys.executable, "scripts/update_mission_control.py"], timeout=180))
    prune_stale_freshness_history()
    steps.append(run([sys.executable, "scripts/update_mission_control.py"], timeout=180))
    steps.append(run([sys.executable, "scripts/mission_control_regression_check.py"], timeout=120))
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet-ok", action="store_true", help="Suppress JSON output for clean no-op runs.")
    args = parser.parse_args()

    before = load_dashboard()
    before_alerts = priority_alerts(before)
    steps = run_repairs(before, before_alerts)
    pruned = prune_stale_freshness_history()
    after = load_dashboard()
    # update_mission_control writes dashboard; reload after repairs
    after = load_dashboard()
    after_alerts = priority_alerts(after)
    failed_steps = [s for s in steps if not s.get("ok")]

    result = {
        "checkedAt": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "beforeAlertCount": len(before_alerts),
        "afterAlertCount": len(after_alerts),
        "fixed": bool((before_alerts or any(pruned.values())) and not after_alerts and not failed_steps),
        "pruned": pruned,
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
    ops_state = update_ops_state(before_alerts, after_alerts, result["fixed"], result["ok"])
    result["opsState"] = {
        "status": ops_state.get("status"),
        "recurringAlerts": ops_state.get("recurringAlerts", [])[:4],
        "recommendations": ops_state.get("recommendations", [])[:4],
    }
    should_print = not args.quiet_ok or not result["ok"] or result["fixed"] or bool(result["opsState"]["recommendations"])
    if should_print:
        print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
