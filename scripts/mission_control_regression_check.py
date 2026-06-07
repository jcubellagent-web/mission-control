#!/usr/bin/env python3
"""Control Tower React UI/data regression checks.

This is the fast, browserless guard for the current Vite/React kiosk. The old
static index.html guard became stale after the Control Tower moved to
v2-react/src, so this script now checks the live source tree and dashboard JSON
contracts without requiring Chrome or Playwright.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SRC_DIR = ROOT / "v2-react" / "src"
MAIN_TSX = SRC_DIR / "main.tsx"
STYLES = SRC_DIR / "styles.css"
TMP_JS = Path("/tmp/mc_scripts_regression.js")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def require(condition: bool, msg: str) -> None:
    if not condition:
        fail(msg)


def require_text(src: str, needles: Iterable[str], context: str) -> None:
    for needle in needles:
        require(needle in src, f"missing {context}: {needle}")


def parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


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


def check_react_source() -> None:
    require(MAIN_TSX.exists(), "React kiosk main.tsx missing")
    require(STYLES.exists(), "React kiosk styles.css missing")
    main = MAIN_TSX.read_text()
    css = STYLES.read_text()

    require_text(
        main,
        [
            "Live Work Board",
            "ledger-live-focus",
            "ledger-focus-primary",
            "is-concurrent",
            "Today's Jobs",
            "daily-calendar-view",
            "calendar-job-block",
            "AttentionTarget",
            "today-jobs",
            "brain-feed",
            "signal-feed",
        ],
        "Control Tower React surface",
    )

    require_text(
        css,
        [
            "CONTROL-TOWER-BRAND-BLUEGREEN-20260607",
            "TODAY'S JOBS STABILITY PASS",
            "CENTER COLUMN SIMPLIFICATION",
            "KIOSK READABILITY PASS — JAIMES — 2026-06-07",
            "scrollbar-width: none",
            "overflow: hidden !important",
            "--brand-blue",
            "--brand-green",
            ".ledger-live-focus.is-concurrent",
            ".calendar-job-block",
        ],
        "kiosk CSS guardrails",
    )

    forbidden_visible_tokens = ["#00CFFF", "#54d6c7", "#a597db"]
    tail = css[css.find("CONTROL-TOWER-BRAND-BLUEGREEN-20260607") :]
    for token in forbidden_visible_tokens:
        require(token not in tail, f"stale accent token after brand guard: {token}")

    min_font_matches = [float(v) for v in re.findall(r"font-size:\s*([0-9]+(?:\.[0-9]+)?)px", css)]
    tiny_after_marker = [float(v) for v in re.findall(r"font-size:\s*([0-9]+(?:\.[0-9]+)?)px", css[css.find("KIOSK READABILITY PASS"):]) if float(v) < 9]
    require(min_font_matches, "no CSS font-size declarations found")
    require(not tiny_after_marker, f"readability pass introduced sub-9px text: {tiny_after_marker[:5]}")
    print("css_font_px_min", min(min_font_matches))


def check_typescript() -> None:
    result = subprocess.run(["npx", "tsc", "--noEmit"], cwd=ROOT, text=True, capture_output=True, timeout=120)
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip())
    require(result.returncode == 0, "TypeScript noEmit check failed")


def check_sidecar_contracts() -> None:
    optional_contracts = [
        ("agent-control-status.json", "summary", "agents"),
        ("shared-events.json", "events", None),
        ("decisions.json", "decisions", None),
        ("handoff-queue.json", "handoffs", None),
        ("knowledge-index.json", "entries", None),
        ("agent-task-queue.json", "tasks", None),
        ("agent-capabilities.json", "agents", None),
        ("agent-routing-policy.json", "routes", None),
        ("agent-heartbeats.json", "heartbeats", None),
        ("capability-inventory.json", "nodes", None),
        ("automation-rollout.json", "rollouts", None),
    ]
    for filename, first, second in optional_contracts:
        path = DATA_DIR / filename
        if not path.exists():
            continue
        payload = json.loads(path.read_text())
        require(first in payload, f"{filename} must include {first}")
        if second:
            require(second in payload, f"{filename} must include {second}")
        if isinstance(payload.get(first), list):
            require(isinstance(payload[first], list), f"{filename}.{first} must be a list")

    gemini_path = DATA_DIR / "gemini-ecosystem.json"
    if gemini_path.exists():
        gemini = json.loads(gemini_path.read_text())
        require((gemini.get("localCli") or {}).get("command") == "gemini", "Gemini sidecar must identify local CLI command")
        require(isinstance(gemini.get("roles"), list) and gemini.get("roles"), "Gemini sidecar must list dashboard-safe roles")


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
    parser.add_argument("--with-tsc", action="store_true", help="also run npx tsc --noEmit; optional because this repo does not vendor React type packages")
    args = parser.parse_args()

    check_json()
    check_react_source()
    check_sidecar_contracts()
    if args.with_tsc:
        check_typescript()
    if args.check_roadmap_freshness:
        check_roadmap_freshness(args.max_roadmap_age_min, args.write_status)
    print("mission_control_regression_check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
