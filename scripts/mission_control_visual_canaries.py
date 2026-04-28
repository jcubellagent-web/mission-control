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
    action_required = []
    for item in action_required_raw:
        title = str(item.get("title", "")).lower()
        if "mission control canary issue" in title:
            continue
        if "due/unverified" in title:
            continue
        action_required.append(item)

    live_objectives_ok = bool(primary_agent) and not (
        jaimes_feed.get("active") and jaimes_feed.get("capabilityBacked")
    )
    calendar_detail = calendar.get("message") or calendar.get("status") or "missing"
    checks = [
        status(
            live_objectives_ok,
            "Live objectives",
            f"primary={primary_agent or 'missing'}; dualAgents={dual_agents or 'none'}; live={len(live_agents)}",
        ),
        status(
            calendar.get("status") == "ok" or "No auth" in str(calendar_detail) or "fetch failed" in str(calendar_detail).lower(),
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
            not action_required or (
                len(action_required) == 1
                and "calendar" in str(action_required[0].get("title", "")).lower()
            ),
            "Action Required",
            "clear" if not action_required else f"{len(action_required)} visible alert(s)",
            severity="medium",
        ),
        status(
            "pickDualLiveObjectiveFeeds" in html and "renderOpsCenter" in html,
            "Renderer wiring",
            "hero + ops renderers present" if html else "index.html missing",
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
