#!/usr/bin/env python3
"""Mission Control UI/data regression checks.

Catches the specific Brain Feed / Memory Roadmap wiring regressions that have
broken the kiosk before, without requiring a browser session.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
DATA_DIR = ROOT / "data"
TMP_JS = Path("/tmp/mc_scripts_regression.js")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def require(condition: bool, msg: str) -> None:
    if not condition:
        fail(msg)


def parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def get_function(src: str, name: str) -> str:
    marker = f"function {name}"
    start = src.find(marker)
    require(start >= 0, f"missing function {name}")
    brace = src.find("{", start)
    require(brace >= 0, f"missing opening brace for {name}")
    depth = 0
    for i in range(brace, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    fail(f"unterminated function {name}")


def check_json() -> None:
    bad: list[tuple[str, str]] = []
    for p in sorted(DATA_DIR.glob("*.json")):
        try:
            txt = p.read_text()
            if not txt.strip():
                raise ValueError("empty")
            json.loads(txt)
        except Exception as exc:  # noqa: BLE001 - diagnostic script
            bad.append((str(p.relative_to(ROOT)), str(exc)))
    print("bad_json_count", len(bad))
    for path, err in bad:
        print(f"  {path}: {err}")
    require(not bad, "invalid JSON in data/*.json")


def check_index_wiring() -> None:
    html = INDEX.read_text()

    apply_bf = get_function(html, "applyBrainFeed")
    require(
        "normalizeRemoteAgentFeed(bf, 2 * 60 * 60 * 1000)" in apply_bf,
        "applyBrainFeed must normalize remote/local payloads before freshness/hash checks",
    )
    require(
        "hasRenderedHero" in apply_bf and ".bf-objective" in apply_bf,
        "applyBrainFeed must rerender when the Brain Feed DOM is empty even if hash is unchanged",
    )

    picker = get_function(html, "pickDualLiveObjectiveFeeds")
    require("isRenderableLiveObjective(joshEntry)" in picker, "JOSH hero must require live/renderable feed")
    require("isRenderableLiveObjective(jaimesEntry)" in picker, "JAIMES hero must require live/renderable feed")
    require(
        "agentLabel: 'J.A.I.N'" not in picker and 'agentLabel: "J.A.I.N"' not in picker,
        "J.A.I.N must not compete for hero objective slots",
    )

    render_dashboard = get_function(html, "renderDashboard")
    require(
        "renderAgentChatFeed(_uaComms || [])" in render_dashboard,
        "Memory Roadmap must refresh from renderDashboard after contextWindow updates",
    )

    step_label = get_function(html, "resolveBrainFeedStepLabel")
    require("safeSteps" in step_label, "resolveBrainFeedStepLabel must guard missing steps")

    render_bf = get_function(html, "renderBrainFeed")
    require(
        "renderCombinedBrainFeed(bf, _jainBrainFeed)" not in render_bf,
        "renderBrainFeed must not recursively call renderCombinedBrainFeed",
    )

    require(
        'onclick="window.openEightSleepDashboard && window.openEightSleepDashboard()"' in html,
        "Eight Sleep stat pill must call openEightSleepDashboard",
    )
    require("window.openEightSleep && window.openEightSleep()" not in html, "stale Eight Sleep handler still present")

    require('id="build-sha-badge"' in html, "Mission Control header must expose a visible build SHA badge")
    require('id="build-sha-text"' in html, "Mission Control build SHA badge must include a target text node")
    require('id="build-age-chip"' in html, "Mission Control header must expose build age/status chip")
    require('id="ci-run-chip"' in html, "Mission Control header must expose latest CI run chip")
    require("function hydrateBuildShaBadge" in html, "Mission Control must hydrate the visible build SHA")
    require(
        "api.github.com/repos/jcubellagent-web/mission-control/commits/main" in html,
        "Mission Control build SHA must resolve against origin/main",
    )
    require(
        "actions/workflows/mission-control-regression.yml/runs?branch=main&per_page=1" in html,
        "Mission Control CI chip must resolve latest regression run",
    )
    require("function brainFeedAgentClass" in html, "Brain Feed cards must expose agent-specific visual classes")
    require("agent-josh" in html and "agent-jaimes" in html, "Brain Feed cards must visually distinguish JOSH 2.0 and JAIMES")
    require(".bf-hero-grid.dual-live .bf-objective" in html and "min-height: clamp(224px, 23vh, 248px)" in html, "Dual live objective boxes must stay tall but kiosk-balanced")
    require(".bf-hero-grid.dual-live .bf-objective-text-wrap" in html and "* 3" in html, "Dual live objectives must show a taller text viewport")

    # 24in visual canaries: prevent kiosk Brain Feed from regressing into tall/noisy poster cards.
    require("24in fullscreen polish" in html, "24in desktop Brain Feed polish block missing")
    require(".memory-roadmap-compact-grid" in html and "repeat(4, minmax(0, 1fr))" in html, "Memory Roadmap must stay compact on desktop")
    require(".memory-roadmap-card-note { display: none" in html, "Desktop roadmap notes must stay hidden to reduce Brain Feed noise")
    require("Model spend desktop: compact ledger view" in html, "Model Usage desktop compact ledger CSS missing")
    require("function toggleLayoutMode" in html and "mc_layout_mode" in html, "24in/phone layout toggle must be wired")
    require('id="layout-mode-toggle"' in html and 'id="layout-mode-text"' in html, "Layout toggle button/text target missing")
    require('id="personal-codex-panel"' in html, "Personal Codex panel shell missing")
    require("function renderPersonalCodex" in html, "Personal Codex renderer missing")
    require("renderPersonalCodex(data)" in html, "renderDashboard must render Personal Codex lane")
    require('id="agent-control-panel"' in html, "Agent Control panel shell missing")
    require("function renderAgentControlPanel" in html, "Agent Control renderer missing")
    require("renderAgentControlPanel(data)" in html, "renderSystemHealth must render Agent Control lane")
    require("agentControl" in html, "Agent Control dashboard payload key must be referenced")
    require("codex-jobs-top" in html and "codexJobs" in html, "Today Jobs must expose Codex Automations")
    require("shared-ledger-top" in html and "sharedEvents" in html, "Today Jobs must expose Shared Ledger events")
    require("shared-os-top" in html and "sharedOperatingLayer" in html, "Today Jobs must expose Shared OS status")
    require("activeTasks" in html and "approvalNeeded" in html and "capabilityAgents" in html, "Shared OS must expose task/capability summary")

    agent_status_path = DATA_DIR / "agent-control-status.json"
    if agent_status_path.exists():
        agent_status = json.loads(agent_status_path.read_text())
        require("summary" in agent_status and "agents" in agent_status, "Agent Control sidecar must include summary and agents")

    shared_events_path = DATA_DIR / "shared-events.json"
    if shared_events_path.exists():
        shared_events = json.loads(shared_events_path.read_text())
        require(isinstance(shared_events.get("events"), list), "Shared Events sidecar must include an events list")
    for filename, key in [
        ("decisions.json", "decisions"),
        ("handoff-queue.json", "handoffs"),
        ("knowledge-index.json", "entries"),
        ("agent-task-queue.json", "tasks"),
        ("agent-capabilities.json", "agents"),
        ("agent-routing-policy.json", "routes"),
        ("agent-heartbeats.json", "heartbeats"),
        ("capability-inventory.json", "nodes"),
        ("automation-rollout.json", "rollouts"),
    ]:
        path = DATA_DIR / filename
        if path.exists():
            payload = json.loads(path.read_text())
            require(isinstance(payload.get(key), list), f"{filename} must include a {key} list")
    gemini_path = DATA_DIR / "gemini-ecosystem.json"
    if gemini_path.exists():
        gemini = json.loads(gemini_path.read_text())
        require((gemini.get("localCli") or {}).get("command") == "gemini", "Gemini sidecar must identify the local CLI command")
        require(isinstance(gemini.get("roles"), list) and gemini.get("roles"), "Gemini sidecar must list dashboard-safe roles")
        require("raw emails" in " ".join(gemini.get("guardrails") or []).lower(), "Gemini sidecar must include privacy guardrails")

    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S | re.I)
    TMP_JS.write_text("\n;\n".join(scripts))
    print("script_chunks", len(scripts))
    require(bool(scripts), "no inline scripts extracted")
    result = subprocess.run(["node", "--check", str(TMP_JS)], cwd=ROOT, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip())
    require(result.returncode == 0, "embedded index.html JavaScript syntax check failed")


def check_roadmap_freshness(max_age_min: int, write_status: Path | None = None) -> None:
    data = json.loads((DATA_DIR / "dashboard-data.json").read_text())
    candidates = [
        data.get("lastUpdated"),
        (data.get("focus") or {}).get("updatedAt") if isinstance(data.get("focus"), dict) else None,
        (data.get("codingVisibility") or {}).get("updatedAt") if isinstance(data.get("codingVisibility"), dict) else None,
    ]
    parsed = [ts for ts in (parse_iso(v) for v in candidates) if ts]
    newest = max(parsed) if parsed else None
    now = datetime.now(timezone.utc)
    age_min = ((now - newest).total_seconds() / 60.0) if newest else None
    fresh = bool(newest and age_min is not None and age_min <= max_age_min)
    status = {
        "ok": fresh,
        "alert_count": 0 if fresh else 1,
        "check": "memory-roadmap-freshness",
        "maxAgeMinutes": max_age_min,
        "ageMinutes": round(age_min, 2) if age_min is not None else None,
        "newestSourceAt": newest.isoformat().replace("+00:00", "Z") if newest else None,
        "checkedAt": now.isoformat().replace("+00:00", "Z"),
    }
    if write_status:
        write_status.parent.mkdir(parents=True, exist_ok=True)
        write_status.write_text(json.dumps(status, indent=2) + "\n")
    print("roadmap_age_min", status["ageMinutes"])
    require(fresh, f"Memory Roadmap stale or missing; age={status['ageMinutes']}m max={max_age_min}m")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-roadmap-freshness", action="store_true")
    parser.add_argument("--max-roadmap-age-min", type=int, default=20)
    parser.add_argument("--write-status", type=Path)
    args = parser.parse_args()

    check_json()
    check_index_wiring()
    if args.check_roadmap_freshness:
        check_roadmap_freshness(args.max_roadmap_age_min, args.write_status)
    print("mission_control_regression_check OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
