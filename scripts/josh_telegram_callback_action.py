#!/usr/bin/env python3
"""Handle common Josh 2.0 Telegram inline-button actions.

This script is intended to be called by OpenCLAW/agent callback handling when a
button tap arrives. It does not poll Telegram itself, avoiding getUpdates
conflicts with the active OpenCLAW Telegram channel.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
if str(WORKSPACE / "scripts") not in sys.path:
    sys.path.insert(0, str(WORKSPACE / "scripts"))

from send_josh_reply import send_message  # type: ignore  # noqa: E402

BUTTONS = [
    [{"text": "Check Mission Control", "callback_data": "next:check_mission_control"}],
    [{"text": "Daily digest", "callback_data": "next:daily_digest"}],
    [{"text": "Route to JOSHeX", "callback_data": "route:joshex"}],
    [{"text": "Route to JAIMES", "callback_data": "route:jaimes"}],
    [{"text": "Show Models", "callback_data": "next:show_models"}],
    [{"text": "Hold", "callback_data": "next:hold"}],
]


def run_text(cmd: list[str], timeout: int = 45) -> str:
    try:
        proc = subprocess.run(cmd, cwd=WORKSPACE, text=True, capture_output=True, timeout=timeout)
        text = (proc.stdout or proc.stderr or "").strip()
        return "\n".join(text.splitlines()[:16])
    except Exception as exc:  # noqa: BLE001 - user-facing callback should not crash noisily
        return f"error: {exc}"


def bullet_card(*, status: str, objective: str, now: str, done: str, blocker: str = "None", next_step: str = "Tap a button or send the next task.") -> str:
    return "\n".join(
        [
            "Codex 5.5 (openai-codex subscription)",
            "",
            "- Route: Josh 2.0 Telegram callback handler.",
            f"- Objective: {objective}",
            f"- Status: {status}",
            f"- Now: {now}",
            f"- Done: {done}",
            f"- Blocker: {blocker}",
            f"- Next: {next_step}",
        ]
    )


def handle(action: str) -> tuple[str, list | None]:
    if action in {"next:daily_digest", "next:overview"}:
        kind = "daily" if action == "next:daily_digest" else "overview"
        text = run_text(["python3", "mission-control/scripts/josh_telegram_digest.py", kind, "--dry-run"])
        if '"text":' in text:
            return bullet_card(
                status="ready",
                objective=f"Prepare {kind} digest.",
                now="Digest helper is available.",
                done=f"Run `python3 mission-control/scripts/josh_telegram_digest.py {kind}` to send it.",
            ), BUTTONS
        return text, BUTTONS

    if action == "next:show_models":
        models = run_text(["openclaw", "models", "list"], timeout=30)
        compact = "; ".join(line.strip() for line in models.splitlines()[1:6] if line.strip())
        return bullet_card(
            status="ready",
            objective="Show model routing.",
            now="Read OpenCLAW model list.",
            done=compact or "Model list checked.",
            next_step="Use /models for the full view or tap another action.",
        ), BUTTONS

    if action.startswith("agent:"):
        agent = action.split(":", 1)[1]
        if agent in {"josh", "josh2", "jaimes", "jain", "joshex"}:
            text = run_text(["python3", "mission-control/scripts/josh_agent_quick_card.py", agent, "--dry-run"])
            return text, BUTTONS

    if action == "next:check_mission_control":
        result = run_text(["python3", "mission-control/scripts/update_mission_control.py"], timeout=90)
        return bullet_card(
            status="done",
            objective="Refresh Mission Control.",
            now="Dashboard data regenerated.",
            done=result.splitlines()[-1] if result else "Mission Control update command ran.",
            next_step="Check the kiosk/dashboard or tap another action.",
        ), BUTTONS

    if action == "route:joshex":
        return bullet_card(
            status="ready",
            objective="Route work to JOSHeX.",
            now="JOSHeX should own architecture, repo edits, private connectors, and cross-agent coordination.",
            done="Route recommendation prepared.",
            next_step="Send the task details or tap Run safe steps if already clear.",
        ), BUTTONS

    if action == "route:jaimes":
        return bullet_card(
            status="ready",
            objective="Route work to JAIMES.",
            now="JAIMES should own Hermes, headless services, heavy workflows, and background checks.",
            done="Route recommendation prepared.",
            next_step="Send the task details or tap Hold.",
        ), BUTTONS

    if action == "next:hold":
        return bullet_card(
            status="paused",
            objective="Hold current action.",
            now="No further action will run from this button.",
            done="Hold acknowledged.",
            next_step="Send a new command when ready.",
        ), None

    return bullet_card(
        status="needs approval",
        objective="Handle Telegram button tap.",
        now=f"Unknown action `{action}`.",
        done="No action executed.",
        blocker="Callback action is not mapped yet.",
        next_step="Send a normal message with what you want Josh to do.",
    ), BUTTONS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    text, buttons = handle(args.action)
    if args.dry_run:
        print(text)
        return 0
    ok = send_message(text, buttons, dedupe_key=f"callback-action:{args.action}", force=True)
    print("ok" if ok else "fail")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
