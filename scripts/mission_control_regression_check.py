#!/usr/bin/env python3
"""Control Tower UI/data regression checks.

This is intentionally a current-surface contract. The retired static
`index.html` dashboard is no longer the operator surface, so this checker
validates the React Control Tower, dashboard sidecars, and routing invariants
that matter for the Josh 2.0 kiosk.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REACT_DIR = ROOT / "v2-react"
REACT_SRC = REACT_DIR / "src"


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


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def ensure_ci_runtime_sidecars() -> None:
    """Seed ignored runtime sidecars so CI checks source contracts, not local state."""
    if not os.environ.get("CI"):
        return

    defaults: dict[str, Any] = {
        "shared-events.json": {"events": []},
        "agent-task-queue.json": {"tasks": []},
        "handoff-queue.json": {"handoffs": []},
        "agent-heartbeats.json": {"heartbeats": []},
    }
    for filename, payload in defaults.items():
        path = DATA_DIR / filename
        if not path.exists():
            path.write_text(json.dumps(payload, indent=2) + "\n")


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


def check_react_surface() -> None:
    main = REACT_SRC / "main.tsx"
    data = REACT_SRC / "data.ts"
    adapters = REACT_SRC / "dataAdapters.ts"
    types = REACT_SRC / "types.ts"
    styles = REACT_SRC / "styles.css"
    for path in (main, data, adapters, types, styles):
        require(path.exists(), f"missing React Control Tower source: {path.relative_to(ROOT)}")

    main_src = main.read_text(errors="ignore")
    data_src = data.read_text(errors="ignore")
    adapters_src = adapters.read_text(errors="ignore")
    types_src = types.read_text(errors="ignore")

    required_main = [
        "Live Work Board",
        "Live Work Board command view",
        "BrainAttentionStrip",
        "AgenticCryptoPanel",
        "JobsRail",
        "SignalFeed",
        "agentNeedsFocus",
        "RuntimeCapabilityPanel",
    ]
    missing = [needle for needle in required_main if needle not in main_src]
    require(not missing, "React Control Tower missing current surface markers: " + ", ".join(missing))

    for agent in ("joshex", "josh2", "jaimes", "jain"):
        require(agent in adapters_src or agent in data_src or agent in main_src, f"missing first-class agent marker: {agent}")

    require("subscribeMissionControlRealtime" in data_src and '"polling"' in data_src, "React data layer must expose the current polling-based realtime bridge")
    require("brain-feed.json" in data_src and "dashboard-data.json" in data_src, "React data layer must read Brain Feed and dashboard sidecars")
    require("recordValue" in adapters_src and "arrayValue" in adapters_src, "React data adapters must expose sidecar normalizers")
    require("MissionControlState" in types_src, "React types must expose the Control Tower state contract")


def check_dashboard_shape() -> None:
    dashboard_path = DATA_DIR / "dashboard-data.json"
    require(dashboard_path.exists(), "missing dashboard-data.json")
    dashboard = load_json(dashboard_path)
    require(isinstance(dashboard, dict), "dashboard-data.json must be an object")

    for key in (
        "lastUpdated",
        "actionRequired",
        "agentBrainFeeds",
        "crons",
        "sharedEvents",
        "sharedOperatingLayer",
        "modelRouter",
    ):
        require(key in dashboard, f"dashboard missing required key: {key}")

    crons = dashboard.get("crons")
    require(isinstance(crons, list), "dashboard crons payload must be a list")
    require(len(crons) >= 8, f"too few tracked jobs visible: {len(crons)}")

    cron_text = json.dumps(crons).lower()
    for lane in ("sorare", "gmail", "brain", "breaking"):
        require(lane in cron_text, f"tracked jobs missing ecosystem lane: {lane}")

    feeds = dashboard.get("agentBrainFeeds")
    require(isinstance(feeds, dict), "agentBrainFeeds must be an object")
    feed_text = json.dumps(feeds).lower()
    for agent in ("josh", "jaimes", "jain"):
        require(agent in feed_text, f"agentBrainFeeds missing agent: {agent}")

    required_sidecars = [
        ("shared-events.json", "events"),
        ("agent-task-queue.json", "tasks"),
        ("handoff-queue.json", "handoffs"),
        ("agent-heartbeats.json", "heartbeats"),
        ("agent-route-decisions.jsonl", None),
    ]
    for filename, key in required_sidecars:
        path = DATA_DIR / filename
        require(path.exists(), f"missing sidecar: {filename}")
        if key:
            payload = load_json(path)
            require(isinstance(payload.get(key), list), f"{filename} must include a {key} list")


def check_model_routes() -> None:
    route_script = ROOT / "scripts" / "agent_route_regression_check.py"
    if route_script.exists():
        proc = subprocess.run(
            [sys.executable, str(route_script), "--json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=120,
        )
        if proc.stdout.strip():
            print(proc.stdout.strip())
        if proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)
        require(proc.returncode == 0, "agent_route_regression_check failed")
        payload = json.loads(proc.stdout)
        require(payload.get("ok") is True, "model ladder route regression reported not-ok")
        return

    agent_route = ROOT / "scripts" / "agent_route.py"
    require(agent_route.exists(), "missing agent_route.py")
    safe_route = subprocess.run(
        [
            sys.executable,
            str(agent_route),
            "--task-type",
            "summary",
            "--title",
            "Regression safe summary",
            "--objective",
            "Summarize dashboard-safe agent activity",
            "--privacy",
            "dashboard-safe",
            "--capability",
            "gemini-review",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
    )
    require(safe_route.returncode == 0, f"agent_route safe summary failed: {safe_route.stderr.strip()}")
    safe_payload = json.loads(safe_route.stdout)
    require((safe_payload.get("modelRoute") or {}).get("firstStop") == "gemini", "safe summaries must route Gemini-first")


def check_roadmap_freshness(max_age_min: int, write_status: Path | None = None) -> None:
    data = load_json(DATA_DIR / "dashboard-data.json")
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
        "check": "control-tower-freshness",
        "maxAgeMinutes": max_age_min,
        "ageMinutes": round(age_min, 2) if age_min is not None else None,
        "newestSourceAt": newest.isoformat().replace("+00:00", "Z") if newest else None,
        "checkedAt": now.isoformat().replace("+00:00", "Z"),
    }
    if write_status:
        write_status.parent.mkdir(parents=True, exist_ok=True)
        write_status.write_text(json.dumps(status, indent=2) + "\n")
    print("control_tower_age_min", status["ageMinutes"])
    require(fresh, f"Control Tower stale or missing; age={status['ageMinutes']}m max={max_age_min}m")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-roadmap-freshness", action="store_true")
    parser.add_argument("--max-roadmap-age-min", type=int, default=20)
    parser.add_argument("--write-status", type=Path)
    args = parser.parse_args()

    ensure_ci_runtime_sidecars()
    check_json()
    check_react_surface()
    check_dashboard_shape()
    check_model_routes()
    if args.check_roadmap_freshness:
        check_roadmap_freshness(args.max_roadmap_age_min, args.write_status)
    print("control_tower_regression_check OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
