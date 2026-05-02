#!/usr/bin/env python3
"""Send compact Josh 2.0 Telegram overview/daily digest cards."""

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
    [{"text": "Route to JOSHeX", "callback_data": "route:joshex"}],
    [{"text": "Route to JAIMES", "callback_data": "route:jaimes"}],
    [{"text": "Show Models", "callback_data": "next:show_models"}],
    [{"text": "Hold", "callback_data": "next:hold"}],
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
        f"- Now: Tracking Mission Control, Brain Feed, and jobs.",
        f"- Done: Active jobs {len(active_jobs)}; recent updates {len(recent)}.",
        "- Blocker: OpenAI-Codex re-auth if doctor still reports expired token.",
    ]
    if recent:
        lines.append(f"- Recent: {'; '.join(recent[:2])}.")
    lines.append("- Next: Tap a button or send a task.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["overview", "daily"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    text = build_digest(args.kind)
    if args.dry_run:
        print(json.dumps({"ok": True, "text": text, "buttons": BUTTONS}, indent=2))
        return 0
    ok = send_message(text, BUTTONS, silent=False, dedupe_key=f"josh-telegram-{args.kind}", force=True)
    print("ok" if ok else "fail")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
