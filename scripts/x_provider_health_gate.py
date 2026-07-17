#!/usr/bin/env python3
"""Apply a sparse, externally-confirmed Grok credit health result.

No network calls are made here. A separate manual/provider health check must write
usableCreditsConfirmed=true; this gate enforces cooldown and safe restoration.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "config" / "x-intelligence-provider-state.json"
HEALTH = ROOT / "data" / "x-provider-health-check.json"
COOLDOWN = ROOT / "data" / "x-provider-health-cooldown.json"


def load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def evaluate(state_path=STATE, health_path=HEALTH, cooldown_path=COOLDOWN, now=None):
    now = now or datetime.now(timezone.utc)
    state = load(state_path, {})
    health = load(health_path, {})
    cooldown = load(cooldown_path, {})
    next_check = cooldown.get("nextEligibleAt")
    if next_check:
        try:
            if now < datetime.fromisoformat(next_check.replace("Z", "+00:00")):
                return "cooldown"
        except ValueError:
            pass
    confirmed = health.get("usableCreditsConfirmed") is True and float(health.get("remainingCredits") or 0) > 0
    fresh = False
    try:
        checked = datetime.fromisoformat(str(health.get("checkedAt") or "").replace("Z", "+00:00"))
        fresh = now - checked <= timedelta(hours=24)
    except ValueError:
        pass
    if confirmed and fresh:
        state.setdefault("xaiApi", {}).update({"status": "available", "enabled": True, "autoRecharge": False})
        state.setdefault("grokSubscription", {}).update({"status": "available", "enabled": True, "healthCheck": "confirmed"})
        write(state_path, state)
        write(cooldown_path, {"lastResult": "restored", "nextEligibleAt": (now + timedelta(days=1)).isoformat()})
        return "restored"
    write(cooldown_path, {"lastResult": "unavailable", "nextEligibleAt": (now + timedelta(days=7)).isoformat()})
    return "unavailable"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    result = evaluate()
    if args.verbose or result == "restored":
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
