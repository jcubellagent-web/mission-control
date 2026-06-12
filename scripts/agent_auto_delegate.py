#!/usr/bin/env python3
"""Choose the right agent owner for dashboard-safe auto-offload requests.

This is intentionally side-effect free by default. The auto-offload watchdog
uses --dry-run to verify routing policy without creating tasks.
"""
from __future__ import annotations

import argparse
import json
import re
from typing import Any


AGENT_LABELS = {
    "josh2": "Josh 2.0",
    "jaimes": "JAIMES",
    "jain": "J.A.I.N",
    "joshex": "JOSHeX",
}


def compact(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "..."


def decide_owner(title: str, objective: str, requester: str = "joshex") -> tuple[str, str, str]:
    text = f"{title}\n{objective}".lower()

    # Private laptop / browser / keychain / personal-account work belongs to JOSHeX.
    if re.search(r"\b(gmail|personal|browser session|keychain|oauth|private mac|macbook|local files|reply draft|draft a reply)\b", text):
        return "joshex", "private-account-or-local-browser", "Private account/browser/keychain/local-file work stays with JOSHeX."

    # Control Tower / OpenClaw kiosk / Josh 2.0 device/service maintenance belongs to Josh 2.0.
    # Keep this before Sorare matching so "Mission Control" is not mistaken for Sorare missions.
    if re.search(r"\b(control tower|mission control|dashboard|kiosk|openclaw|gateway|josh 2\.0|josh2|screen|display|refresh|brain feed)\b", text):
        return "josh2", "josh2-kiosk-or-gateway", "Control Tower, kiosk, gateway, and Josh 2.0 device work routes to Josh 2.0."

    # Sorare/fantasy/model/background compute belongs to JAIMES/J.A.I.N; use JAIMES
    # as the receiving lane for user-visible Sorare ops.
    if re.search(r"\b(sorare|fantasy|lineup|lineups|pre-lock|starter|starters|dnp|daily mission|daily missions|mlb|model|train|training)\b", text):
        return "jaimes", "sorare-or-heavy-background", "Sorare/fantasy/heavy background work routes to JAIMES."

    # Default: keep coordination tasks with the requester when safe.
    normalized_requester = requester.strip().lower().replace(" ", "")
    if normalized_requester in {"josh", "josh2", "josh2.0"}:
        owner = "josh2"
    elif normalized_requester in AGENT_LABELS:
        owner = normalized_requester
    else:
        owner = "joshex"
    return owner, "requester-default", "No specialist trigger matched; keep work with the requesting coordination lane."


def main() -> int:
    parser = argparse.ArgumentParser(description="Route an agent task to the most appropriate owner.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--requester", default="joshex")
    parser.add_argument("--privacy", default="dashboard-safe")
    parser.add_argument("--dry-run", action="store_true", help="Return route decision only; create no tasks.")
    args = parser.parse_args()

    owner, decision, reason = decide_owner(args.title, args.objective, args.requester)
    result = {
        "ok": True,
        "dryRun": bool(args.dry_run),
        "title": compact(args.title, 160),
        "objective": compact(args.objective, 400),
        "requester": args.requester,
        "privacy": args.privacy,
        "owner": owner,
        "ownerLabel": AGENT_LABELS[owner],
        "decision": decision,
        "ownerReason": reason,
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
