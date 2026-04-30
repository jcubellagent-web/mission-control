#!/usr/bin/env python3
"""Generate lightweight Mission Control visual/data canaries.

These are dashboard-facing guardrails: they summarize whether the pieces Josh
actually looks at are intact before a human has to notice a visual regression.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DASHBOARD_PATH = DATA_DIR / "dashboard-data.json"
OUT_PATH = DATA_DIR / "mission-control-canaries.json"
INDEX_PATH = ROOT / "index.html"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def status(ok: bool, name: str, detail: str, severity: str = "high") -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "severity": "ok" if ok else severity,
    }


def main() -> int:
    data = load_json(DASHBOARD_PATH, {})
    html = INDEX_PATH.read_text(errors="replace") if INDEX_PATH.exists() else ""

    live = data.get("liveObjectives") if isinstance(data.get("liveObjectives"), dict) else {}
    dual_agents = live.get("dualAgents") or []
    live_agents = live.get("agents") if isinstance(live.get("agents"), list) else []
    primary_agent = live.get("primaryAgent")
    agent_feeds = data.get("agentBrainFeeds") if isinstance(data.get("agentBrainFeeds"), dict) else {}
    jaimes_feed = agent_feeds.get("jaimes") if isinstance(agent_feeds.get("jaimes"), dict) else {}
    calendar = data.get("calendarHealth") if isinstance(data.get("calendarHealth"), dict) else {}
    crons = data.get("crons") if isinstance(data.get("crons"), list) else []
    today = [c for c in crons if c and c.get("todayRelevant") is not False]
    active_errors = [c for c in today if c.get("status") != "paused" and ((c.get("errors") or 0) > 0 or c.get("runStatus") == "missed")]
    action_required_raw = data.get("actionRequired") if isinstance(data.get("actionRequired"), list) else []
    personal_codex = data.get("personalCodex") if isinstance(data.get("personalCodex"), dict) else {}
    agent_control = data.get("agentControl") if isinstance(data.get("agentControl"), dict) else {}
    agent_control_summary = agent_control.get("summary") if isinstance(agent_control.get("summary"), dict) else {}
    action_required = []
    for item in action_required_raw:
        title = str(item.get("title", "")).lower()
        if "mission control canary issue" in title:
            continue
        if "due/unverified" in title:
            continue
        action_required.append(item)
    allowed_action_prefixes = (
        "calendar issue:",
        "personal codex:",
        "shared layer needs attention:",
    )
    unknown_action_required = [
        item for item in action_required
        if not str(item.get("title", "")).lower().startswith(allowed_action_prefixes)
    ]

    live_objectives_ok = bool(primary_agent) and not (
        jaimes_feed.get("active") and jaimes_feed.get("capabilityBacked")
    )
    calendar_detail = calendar.get("message") or calendar.get("status") or "missing"
    objective_fn_start = html.find("function syncBrainFeedObjectiveScroller")
    objective_fn_end = html.find("function pickPrimaryBrainFeed")
    objective_fn = html[objective_fn_start:objective_fn_end] if objective_fn_start >= 0 and objective_fn_end > objective_fn_start else ""
    checks = [
        status(
            live_objectives_ok,
            "Live objectives",
            f"primary={primary_agent or 'missing'}; dualAgents={dual_agents or 'none'}; live={len(live_agents)}",
        ),
        status(
            calendar.get("status") == "ok"
            or "No auth" in str(calendar_detail)
            or "fetch failed" in str(calendar_detail).lower()
            or "gog cli missing" in str(calendar_detail).lower(),
            "Calendar tile",
            calendar_detail,
            severity="medium",
        ),
        status(
            bool(today) and not active_errors,
            "Today Jobs",
            f"{len(today)} today-relevant; {len(active_errors)} active issue(s)",
        ),
        status(
            not unknown_action_required and len(action_required) <= 4,
            "Action Required",
            "clear" if not action_required else f"{len(action_required)} visible alert(s)",
            severity="medium",
        ),
        status(
            "pickDualLiveObjectiveFeeds" in html and "renderOpsCenter" in html,
            "Renderer wiring",
            "hero + ops renderers present" if html else "index.html missing",
        ),
        status(
            "ops-glance-strip" in html and "opsGlance" in html and "Agent ecosystem glance status" in html,
            "Ops glance strip",
            "first-viewport agent attention strip present" if html else "index.html missing",
            severity="medium",
        ),
        status(
            bool(personal_codex) and "personal-codex-panel" in html and "renderPersonalCodex" in html,
            "Personal Codex lane",
            f"status={personal_codex.get('status', 'missing')}",
            severity="medium",
        ),
        status(
            bool(agent_control_summary) and "agent-control-panel" in html and "renderAgentControlPanel" in html,
            "Agent Control lane",
            f"overall={agent_control_summary.get('overall', 'missing')}; ready={agent_control_summary.get('readyAgents', 0)}/{agent_control_summary.get('totalAgents', 0)}",
            severity="medium",
        ),
        status(
            "Mission Control alignment pass" in html
            and "height: 66px;" in html
            and "-webkit-line-clamp: 2 !important" in html
            and "Mission Control alignment pass 2" in html
            and "grid-template-columns: repeat(2, minmax(0, 1fr)) !important;" in html
            and ".card-jobs-full .codex-job-title,\n        .card-jobs-full .codex-job-detail,\n        .card-jobs-full .shared-event-title" in html
            and "setInterval(() =>" not in objective_fn,
            "Alignment stability",
            "stable text rails present; objective ticker disabled; dense grids capped" if html else "index.html missing",
            severity="medium",
        ),
    ]

    failed = [c for c in checks if not c["ok"]]
    out = {
        "ok": not failed,
        "status": "ok" if not failed else "attention",
        "summary": "All canaries passed" if not failed else f"{len(failed)} canary issue(s)",
        "checkedAt": utc_now(),
        "checks": checks,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
