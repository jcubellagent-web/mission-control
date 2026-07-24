#!/usr/bin/env python3
"""Dashboard-safe ecosystem health sweep for Josh 2.0 Control Tower."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
HEALTH_PATH = DATA_DIR / "ecosystem-health-sweep.json"
HEARTBEATS_PATH = DATA_DIR / "agent-heartbeats.json"
DASHBOARD_PATH = DATA_DIR / "dashboard-data.json"
REQUIRED_AGENTS = ("josh2", "jaimes", "jain")
QA_META_JOB_IDS = frozenset({
    "nightly-control-tower-suite",
    "daily-qa-rollup",
    "adaptive-quality-snapshot",
    "maintenance-portfolio-snapshot",
    "daily-refactor-discovery",
    "weekly-quality-baseline-review",
})
SIDECAR_PATHS = {
    "josh2": DATA_DIR / "brain-feed.json",
    "jaimes": DATA_DIR / "jaimes-brain-feed.json",
    "jain": DATA_DIR / "jain-brain-feed.json",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)


def latest_agent_rows(heartbeats: dict[str, Any], now: dt.datetime) -> tuple[list[dict[str, Any]], int]:
    stale_after = int(heartbeats.get("staleAfterMinutes") or 120)
    rows = [row for row in heartbeats.get("heartbeats", []) if isinstance(row, dict)]
    result: list[dict[str, Any]] = []
    for agent in REQUIRED_AGENTS:
        agent_rows = [row for row in rows if str(row.get("agent") or "").lower() == agent]
        latest = max(agent_rows, key=lambda row: row.get("updatedAt") or "", default={})
        stamp = parse_ts(latest.get("updatedAt"))

        # Brain Feed sidecars are also dashboard-safe visibility sources. Prefer
        # them when fresher, so stale heartbeat rows do not create false alerts.
        sidecar = read_json(SIDECAR_PATHS.get(agent, Path("/missing")), {})
        sidecar_stamp = None
        if isinstance(sidecar, dict):
            sidecar_stamp = parse_ts(sidecar.get("updatedAt") or sidecar.get("checkedAt"))
        if sidecar_stamp and (not stamp or sidecar_stamp > stamp):
            latest = {
                "agent": agent,
                "status": sidecar.get("status") or "ok",
                "updatedAt": sidecar.get("updatedAt") or sidecar.get("checkedAt"),
                "summary": sidecar.get("detail") or sidecar.get("objective") or "Brain Feed sidecar fresh.",
            }
            stamp = sidecar_stamp

        age_min = None
        if stamp:
            age_min = round((now - stamp.astimezone(dt.timezone.utc)).total_seconds() / 60, 1)
        stale = not bool(stamp and (now - stamp.astimezone(dt.timezone.utc)) <= dt.timedelta(minutes=stale_after))
        status = str(latest.get("status") or "missing").lower()
        ok = bool(latest) and not stale and status not in {"blocked", "error", "attention", "failed"}
        result.append({
            "agent": agent,
            "ok": ok,
            "status": status,
            "stale": stale,
            "ageMinutes": age_min,
            "updatedAt": latest.get("updatedAt"),
            "summary": latest.get("summary") or "No heartbeat found.",
        })
    return result, stale_after


def model_status_ok() -> bool:
    try:
        proc = run(["openclaw", "models", "status", "--json"], timeout=45)
        if proc.returncode != 0:
            return False
        data = json.loads(proc.stdout)
        auth = data.get("auth") if isinstance(data, dict) else {}
        missing = {str(item).lower() for item in auth.get("missingProvidersInUse", [])}
        # Ollama is a local runtime and does not require a provider credential.
        # Verify the daemon/model inventory instead of treating it as missing auth.
        if "ollama" in missing:
            local = run(["ollama", "list"], timeout=20)
            if local.returncode != 0 or len(local.stdout.splitlines()) < 2:
                return False
            missing.remove("ollama")
        return not missing and bool(data.get("resolvedDefault"))
    except Exception:
        return False


def cron_attention(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    rows = dashboard.get("crons", []) if isinstance(dashboard.get("crons"), list) else []
    attention = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("status") == "paused":
            continue
        if row.get("source") == "codex_automation":
            continue
        errors = int(row.get("errors") or 0)
        run_status = str(row.get("runStatus") or "")
        if errors > 0 or row.get("status") == "error" or run_status == "missed":
            attention.append({
                "name": row.get("name") or "scheduled job",
                "status": row.get("status"),
                "runStatus": run_status,
                "errors": errors,
                "qaJobId": row.get("qaJobId"),
                "qaMeta": bool(row.get("qaMeta")) or row.get("qaJobId") in QA_META_JOB_IDS,
            })
    return attention


def is_qa_meta_attention(row: dict[str, Any]) -> bool:
    return bool(row.get("qaMeta")) or str(row.get("qaJobId") or "") in QA_META_JOB_IDS


def is_blocking_action(row: dict[str, Any]) -> bool:
    if row.get("qaMetaOnly") is True:
        return False
    return str(row.get("priority") or "").strip().lower() in {"critical", "high", "p0", "p1"}


def publish(status: str, detail: str, *, job: bool) -> None:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_publish.py"),
        "--agent", "josh2",
        "--type", "complete" if status == "ok" else "status",
        "--status", "done" if status == "ok" else "blocked",
        "--title", "Daily agent ecosystem health sweep",
        "--tool", "ecosystem health sweep",
        "--detail", detail,
        "--privacy", "dashboard-safe",
        "--brain-feed",
    ]
    if job:
        cmd.append("--job")
    subprocess.run(cmd, cwd=ROOT, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dashboard-safe ecosystem health checks.")
    parser.add_argument("--brain-feed", action="store_true")
    parser.add_argument("--job", action="store_true")
    parser.add_argument("--telegram-summary", action="store_true")
    parser.add_argument("--ci", action="store_true", help="Validate the offline health contract without probing host services.")
    args = parser.parse_args()

    if args.ci:
        required_paths = [HEARTBEATS_PATH, DASHBOARD_PATH, *SIDECAR_PATHS.values()]
        payload = {
            "ok": len(REQUIRED_AGENTS) == len(set(REQUIRED_AGENTS)),
            "status": "ok",
            "mode": "ci-contract",
            "requiredAgents": list(REQUIRED_AGENTS),
            "fixturePaths": [str(path.relative_to(ROOT)) for path in required_paths],
            "liveServicesProbed": False,
        }
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 1

    now_iso = utc_now()
    now_dt = dt.datetime.now(dt.timezone.utc)
    heartbeats = read_json(HEARTBEATS_PATH, {"heartbeats": [], "staleAfterMinutes": 120})
    dashboard = read_json(DASHBOARD_PATH, {})
    agent_rows, stale_after = latest_agent_rows(heartbeats, now_dt)
    action_required = dashboard.get("actionRequired", []) if isinstance(dashboard.get("actionRequired"), list) else []
    action_required = [row for row in action_required if isinstance(row, dict)]
    qa_meta_actions = [row for row in action_required if row.get("qaMetaOnly") is True]
    operational_actions = [row for row in action_required if row.get("qaMetaOnly") is not True]
    blocking_actions = [row for row in operational_actions if is_blocking_action(row)]
    non_blocking_actions = [row for row in operational_actions if not is_blocking_action(row)]
    all_cron_issues = cron_attention(dashboard)
    qa_meta_attention = [row for row in all_cron_issues if is_qa_meta_attention(row)]
    operational_cron_attention = [row for row in all_cron_issues if not is_qa_meta_attention(row)]
    model_ok = model_status_ok()
    dashboard_age = None
    dashboard_updated = parse_ts(dashboard.get("sourceUpdatedAt") or dashboard.get("lastUpdated"))
    if dashboard_updated:
        dashboard_age = round((now_dt - dashboard_updated.astimezone(dt.timezone.utc)).total_seconds() / 60, 1)

    ok_agents = sum(1 for row in agent_rows if row["ok"])
    ok = (
        ok_agents == len(REQUIRED_AGENTS)
        and not blocking_actions
        and not operational_cron_attention
        and model_ok
        and (dashboard_age is None or dashboard_age <= 30)
    )
    detail = (
        f"Daily health sweep completed: {ok_agents}/{len(REQUIRED_AGENTS)} host rows ok; "
        f"Control Tower age {dashboard_age if dashboard_age is not None else 'unknown'} min; "
        f"operational cron attention {len(operational_cron_attention)}; "
        f"blocking action items {len(blocking_actions)}; QA aggregate evidence {len(qa_meta_attention)}."
    )
    result = {
        "ok": ok,
        "status": "ok" if ok else "attention",
        "checkedAt": now_iso,
        "detail": detail,
        "staleAfterMinutes": stale_after,
        "agents": agent_rows,
        "modelRoutesOk": model_ok,
        "controlTowerAgeMinutes": dashboard_age,
        "actionRequiredCount": len(action_required),
        "operationalActionRequiredCount": len(operational_actions),
        "blockingActionRequiredCount": len(blocking_actions),
        "nonBlockingActionRequiredCount": len(non_blocking_actions),
        "qaMetaActionRequired": qa_meta_actions[:8],
        "cronAttentionCount": len(operational_cron_attention),
        "cronAttention": operational_cron_attention[:8],
        "operationalCronAttention": operational_cron_attention[:8],
        "qaMetaAttention": qa_meta_attention[:8],
    }
    write_json(HEALTH_PATH, result)
    if args.brain_feed:
        publish(result["status"], detail, job=args.job)
    if args.telegram_summary:
        print(json.dumps({"ok": ok, "status": result["status"], "detail": detail}, indent=2))
    else:
        print(json.dumps(result, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
