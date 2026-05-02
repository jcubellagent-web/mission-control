#!/usr/bin/env python3
"""Send a compact Telegram quick card for one agent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
if str(WORKSPACE / "scripts") not in sys.path:
    sys.path.insert(0, str(WORKSPACE / "scripts"))

from send_josh_reply import send_message  # type: ignore  # noqa: E402

BUTTONS = [
    [{"text": "Check Mission Control", "callback_data": "next:check_mission_control"}],
    [{"text": "Josh 2.0", "callback_data": "agent:josh"}],
    [{"text": "JAIMES", "callback_data": "agent:jaimes"}],
    [{"text": "J.AI.N", "callback_data": "agent:jain"}],
    [{"text": "JOSHeX", "callback_data": "agent:joshex"}],
    [{"text": "Hold", "callback_data": "next:hold"}],
]

ALIASES = {
    "josh": "josh2",
    "josh2": "josh2",
    "jaimes": "jaimes",
    "jain": "jaimes",
    "joshex": "joshex",
}


def load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def agent_card(agent: str) -> str:
    key = ALIASES[agent]
    control = load_json(ROOT / "data" / "agent-control-status.json", {})
    personal = load_json(ROOT / "data" / "personal-codex.json", {})
    agents = control.get("agents") or {}
    if key == "joshex":
        status = personal.get("status") or personal.get("summary", {}).get("status") or "active"
        return "\n".join(
            [
                "Codex 5.5 (openai-codex subscription)",
                "",
                "- Route: JOSHeX quick card.",
                "- Objective: Show coordinator status.",
                f"- Status: {status}.",
                "- Now: Coordinating Mission Control, routing, and repo-safe changes.",
                "- Done: Personal Codex lane and Brain Feed sidecars are the source of truth.",
                "- Blocker: None unless a task needs private connector access or auth.",
                "- Next: Tap a route button or send the task.",
            ]
        )

    data = agents.get(key, {})
    label = data.get("label") or key
    status = data.get("status") or "unknown"
    services = data.get("services") or []
    ready_services = sum(1 for svc in services if svc.get("status") in {"ready", "auth_required"})
    failed_queues = (data.get("queues") or {}).get("failedDeliveryCount", 0)
    dirty = sum(int(repo.get("dirtyCount") or 0) for repo in data.get("repos") or [])
    responsibilities = "; ".join((data.get("responsibilities") or [])[:3])
    return "\n".join(
        [
            "Codex 5.5 (openai-codex subscription)",
            "",
            f"- Route: {label} quick card.",
            f"- Objective: Show {label} status.",
            f"- Status: {status}; services {ready_services}/{len(services)} acceptable.",
            f"- Now: {responsibilities or 'Operational status check.'}",
            f"- Done: Failed queues {failed_queues}; repo drift entries {dirty}.",
            "- Blocker: None visible in this card." if status == "ready" else "- Blocker: Needs follow-up from Agent Control.",
            "- Next: Tap another agent, check Mission Control, or send a task.",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("agent", choices=sorted(ALIASES))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    text = agent_card(args.agent)
    if args.dry_run:
        print(text)
        return 0
    ok = send_message(text, BUTTONS, dedupe_key=f"agent-quick-card:{args.agent}", force=True)
    print("ok" if ok else "fail")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
