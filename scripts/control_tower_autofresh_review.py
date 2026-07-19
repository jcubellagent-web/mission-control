#!/usr/bin/env python3
"""Review Control Tower autofresh history and surface repeat incidents."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from control_tower_priority_autofix import alert_key, load_dashboard, parse_ts, priority_alerts


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "control-tower-autofresh-ops.json"

#JAIMES: nightly review stays quiet unless repeat Control Tower drift needs a real follow-up or skill/runbook improvement.


def load_state() -> dict[str, Any]:
    try:
        payload = json.loads(STATE_PATH.read_text())
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def save_state(payload: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def publish(detail: str, event_id: str) -> bool:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "agent_publish.py"),
            "--agent",
            "josh2",
            "--type",
            "note",
            "--status",
            "info",
            "--title",
            "Control Tower autofresh review",
            "--tool",
            "control_tower_autofresh_review",
            "--detail",
            detail,
            "--event-id",
            event_id,
            "--privacy",
            "dashboard-safe",
            "--brain-feed",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Deprecated alias for --publish-current-only.")
    parser.add_argument("--publish-current-only", action="store_true", help="Publish one deduplicated recommendation only for a currently active repeat incident.")
    args = parser.parse_args()

    state = load_state()
    checked_at = parse_ts(state.get("checkedAt"))
    if state.get("schema") != 2 or not checked_at or dt.datetime.now(dt.timezone.utc) - checked_at > dt.timedelta(hours=48):
        return 0
    current_alerts = priority_alerts(load_dashboard())
    current_keys = {alert_key(alert) for alert in current_alerts}
    active_keys = {
        key for key in state.get("activeAlertKeys") or []
        if isinstance(key, str) and key
    }
    if not current_keys or not active_keys:
        return 0
    recurring = state.get("recurringAlerts") if isinstance(state.get("recurringAlerts"), list) else []
    actionable = [
        row for row in recurring
        if isinstance(row, dict)
        and row.get("active") is True
        and row.get("key") in current_keys
        and row.get("key") in active_keys
        and int(row.get("unresolvedCount") or 0) >= 2
    ]

    if not actionable:
        return 0

    lines = []
    for row in actionable[:3]:
        title = str(row.get("title") or "Control Tower issue")
        recommendation = str(row.get("recommendation") or "Escalate this repeat incident.")
        lines.append(f"{title}: {recommendation}")
    detail = " | ".join(lines[:3])[:700]
    fingerprint = hashlib.sha256("|".join(sorted(str(row.get("key") or "") for row in actionable)).encode()).hexdigest()[:16]

    publish_requested = args.publish or args.publish_current_only
    published = False
    if publish_requested and detail and state.get("lastReviewPublishedFingerprint") != fingerprint:
        published = publish(detail, f"josh2-note-control-tower-autofresh-{fingerprint}")
        if published:
            state["lastReviewPublishedFingerprint"] = fingerprint
            save_state(state)

    print(json.dumps({
        "ok": True,
        "checkedAt": state.get("checkedAt"),
        "actionableCount": len(actionable),
        "recommendations": lines[:6],
        "published": published,
        "fingerprint": fingerprint,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
