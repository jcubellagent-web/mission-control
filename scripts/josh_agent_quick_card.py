#!/usr/bin/env python3
"""Send a compact Telegram quick card for one agent."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path = [str(SCRIPT_DIR)] + [path for path in sys.path if path != str(SCRIPT_DIR)]

from josh_work_card import send_rich_message  # type: ignore  # noqa: E402

BUTTONS = [
    [{"text": "Force Control Tower sync", "callback_data": "next:check_mission_control"}],
    [{"text": "Josh 2.0", "callback_data": "agent:josh"}],
    [{"text": "JAIMES", "callback_data": "agent:jaimes"}],
    [{"text": "J.AI.N", "callback_data": "agent:jain"}],
    [{"text": "JOSHeX", "callback_data": "agent:joshex"}],
    [{"text": "Hold / no action", "callback_data": "next:hold"}],
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
                "- Now: Coordinating Control Tower, routing, and repo-safe changes.",
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
            "- Next: Tap another agent, check Control Tower, or send a task.",
        ]
    )


def rich_agent_card(agent: str) -> tuple[str, str]:
    fallback = agent_card(agent)
    key = ALIASES[agent]
    control = load_json(ROOT / "data" / "agent-control-status.json", {})
    personal = load_json(ROOT / "data" / "personal-codex.json", {})
    agents = control.get("agents") or {}

    if key == "joshex":
        label = "JOSHeX"
        status = personal.get("status") or personal.get("summary", {}).get("status") or "active"
        rows = [
            ("Status", status, "Coordinator lane"),
            ("Scope", "Private-account support", "Use when local/private auth matters"),
            ("Blocker", "None visible", "Send exact task when ready"),
        ]
    else:
        data = agents.get(key, {})
        label = data.get("label") or key
        status = data.get("status") or "unknown"
        services = data.get("services") or []
        ready_services = sum(1 for svc in services if svc.get("status") in {"ready", "auth_required"})
        failed_queues = (data.get("queues") or {}).get("failedDeliveryCount", 0)
        dirty = sum(int(repo.get("dirtyCount") or 0) for repo in data.get("repos") or [])
        responsibilities = "; ".join((data.get("responsibilities") or [])[:2])
        rows = [
            ("Status", status, f"Services {ready_services}/{len(services)} acceptable"),
            ("Queues", str(failed_queues), "Failed deliveries"),
            ("Repos", str(dirty), "Dirty file entries"),
            ("Focus", responsibilities or "Operational status check", "Current lane"),
        ]

    table_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(area)}</td>"
        f"<td>{html.escape(value)}</td>"
        f"<td>{html.escape(note)}</td>"
        "</tr>"
        for area, value, note in rows
    )
    rich_html = f"""
<h2>{html.escape(str(label))} quick card</h2>
<table bordered="true" striped="true">
  <caption>Agent status snapshot</caption>
  <tr><th>Area</th><th>Value</th><th>Note</th></tr>
  {table_rows}
</table>
<footer>Tap another agent, sync Control Tower, or send a task.</footer>
""".strip()
    return rich_html, fallback


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("agent", choices=sorted(ALIASES))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    rich_html, text = rich_agent_card(args.agent)
    if args.dry_run:
        print(json.dumps({"ok": True, "rich_html": rich_html, "text": text, "buttons": BUTTONS}, indent=2))
        return 0
    result = send_rich_message(rich_html, text, timeout=15, buttons=BUTTONS, silent=False)
    print(json.dumps({
        "ok": bool(result.get("ok")),
        "native_rich_message": bool(result.get("native_rich_message")),
        "message_id": (result.get("result") or {}).get("message_id"),
    }, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
