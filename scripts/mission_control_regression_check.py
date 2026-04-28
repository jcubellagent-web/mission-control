#!/usr/bin/env python3
"""Mission Control UI/data regression checks.

Catches the specific Brain Feed / Memory Roadmap wiring regressions that have
broken the kiosk before, without requiring a browser session.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
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
    require("agentLabel: 'J.A.I.N'" not in picker and 'agentLabel: "J.A.I.N"' not in picker, "J.A.I.N must not compete for hero objective slots")

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


def main() -> int:
    check_json()
    check_index_wiring()
    print("mission_control_regression_check OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
