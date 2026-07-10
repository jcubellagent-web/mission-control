#!/usr/bin/env python3
"""Review Control Tower autofresh history and surface repeat incidents."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "control-tower-autofresh-ops.json"

#JAIMES: nightly review stays quiet unless repeat Control Tower drift needs a real follow-up or skill/runbook improvement.


def load_state() -> dict[str, Any]:
    try:
        payload = json.loads(STATE_PATH.read_text())
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def publish(detail: str) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "agent_publish.py"),
            "--agent",
            "josh2",
            "--type",
            "status",
            "--status",
            "done",
            "--title",
            "Control Tower autofresh review",
            "--tool",
            "control_tower_autofresh_review",
            "--detail",
            detail,
            "--privacy",
            "dashboard-safe",
            "--brain-feed",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Publish recommendations to Brain Feed when present.")
    args = parser.parse_args()

    state = load_state()
    if state.get("status") == "ok" and not state.get("afterAlerts"):
        return 0
    recurring = state.get("recurringAlerts") if isinstance(state.get("recurringAlerts"), list) else []
    recommendations = state.get("recommendations") if isinstance(state.get("recommendations"), list) else []
    actionable = [row for row in recurring if isinstance(row, dict) and int(row.get("unresolvedCount") or 0) >= 2]

    if not actionable and not recommendations:
        return 0

    lines = []
    for row in actionable[:3]:
        title = str(row.get("title") or "Control Tower issue")
        recommendation = str(row.get("recommendation") or "Escalate this repeat incident.")
        lines.append(f"{title}: {recommendation}")
    for row in recommendations[:3]:
        if isinstance(row, str) and row not in lines:
            lines.append(row)
    detail = " | ".join(lines[:3])[:700]

    if args.publish and detail:
        publish(detail)

    print(json.dumps({
        "ok": True,
        "checkedAt": state.get("checkedAt"),
        "actionableCount": len(actionable),
        "recommendations": lines[:6],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
