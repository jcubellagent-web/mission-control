#!/usr/bin/env python3
"""Send compact Josh 2.0 Telegram overview/daily digest cards."""

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
    [{"text": "Use Gemini for review", "callback_data": "model:gemini_flash"}],
    [{"text": "Send to JAIMES / Hermes", "callback_data": "route:jaimes"}],
    [{"text": "Use Josh 2.0 Mac tools", "callback_data": "model:codex"}],
    [{"text": "Send to JOSHeX Cloud / repo-safe", "callback_data": "route:joshex_cloud"}],
    [{"text": "Send to JOSHeX / private accounts", "callback_data": "route:joshex"}],
    [{"text": "Force Control Tower sync", "callback_data": "next:check_mission_control"}],
    [{"text": "Show model choices", "callback_data": "next:show_models"}],
    [{"text": "Hold / no action", "callback_data": "next:hold"}],
]


def load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def count_status(agent_control: dict) -> tuple[int, int, int]:
    summary = agent_control.get("summary") or {}
    return (
        int(summary.get("readyAgents") or 0),
        int(summary.get("totalAgents") or 0),
        int(summary.get("failedQueues") or 0),
    )


def recent_brain_items(feed: dict, limit: int = 3) -> list[str]:
    items = feed.get("items") or feed.get("events") or []
    out = []
    for item in items[:limit]:
        title = item.get("title") or item.get("summary") or item.get("detail") or "Recent update"
        out.append(" ".join(str(title).split())[:120])
    return out


def build_digest(kind: str) -> str:
    agent_control = load_json(ROOT / "data" / "agent-control-status.json", {})
    brain = load_json(ROOT / "data" / "brain-feed.json", {})
    jobs = load_json(ROOT / "data" / "codex-jobs.json", {})
    ready, total, failed_queues = count_status(agent_control)
    recent = recent_brain_items(brain)
    job_items = jobs.get("jobs") or jobs.get("items") or []
    active_jobs = [j for j in job_items if str(j.get("status", "")).lower() in {"active", "running", "queued"}]
    label = "Daily digest" if kind == "daily" else "Agent overview"
    lines = [
        "Codex 5.5 (openai-codex subscription)",
        "",
        f"- Route: Josh 2.0 Telegram; {label.lower()}.",
        f"- Objective: Give Josh a quick ecosystem view.",
        f"- Status: {ready}/{total} agents ready; failed queues {failed_queues}.",
        f"- Now: Tracking Control Tower, Brain Feed, and jobs.",
        f"- Done: Active jobs {len(active_jobs)}; recent updates {len(recent)}.",
        "- Blocker: OpenAI-Codex re-auth if doctor still reports expired token.",
    ]
    if recent:
        lines.append(f"- Recent: {'; '.join(recent[:2])}.")
    lines.append("- Next: Tap a button or send a task.")
    return "\n".join(lines)


def build_rich_digest(kind: str) -> tuple[str, str]:
    agent_control = load_json(ROOT / "data" / "agent-control-status.json", {})
    brain = load_json(ROOT / "data" / "brain-feed.json", {})
    jobs = load_json(ROOT / "data" / "codex-jobs.json", {})
    ready, total, failed_queues = count_status(agent_control)
    recent = recent_brain_items(brain)
    job_items = jobs.get("jobs") or jobs.get("items") or []
    active_jobs = [j for j in job_items if str(j.get("status", "")).lower() in {"active", "running", "queued"}]
    label = "Daily digest" if kind == "daily" else "Agent overview"
    rows = [
        ("Agents", f"{ready}/{total} ready", f"Failed queues: {failed_queues}"),
        ("Jobs", f"{len(active_jobs)} active", "Control Tower tracked"),
        ("Brain Feed", f"{len(recent)} recent", recent[0] if recent else "No fresh item"),
        ("Route", "Josh 2.0 Telegram", "Tap a button or send a task"),
    ]
    table_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f"<td>{html.escape(status)}</td>"
        f"<td>{html.escape(next_step)}</td>"
        "</tr>"
        for name, status, next_step in rows
    )
    rich_html = f"""
<h2>{html.escape(label)}</h2>
<table bordered="true" striped="true">
  <caption>Control Tower snapshot</caption>
  <tr><th>Area</th><th>Status</th><th>Next</th></tr>
  {table_rows}
</table>
<details>
  <summary>Recent context</summary>
  <p>{html.escape('; '.join(recent[:3]) if recent else 'No recent Brain Feed items found.')}</p>
</details>
<footer>JOSH 2.0 Telegram digest</footer>
""".strip()
    return rich_html, build_digest(kind)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["overview", "daily"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    rich_html, text = build_rich_digest(args.kind)
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
