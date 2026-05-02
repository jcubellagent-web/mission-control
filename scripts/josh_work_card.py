#!/usr/bin/env python3
"""Create and update one editable Josh-facing Telegram work card.

This file is intended to be synced to Josh 2.0's workspace `scripts/` folder.
It uses the direct Bot API lane through `send_josh_reply.py`, which lives next
to this script on Josh 2.0.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import textwrap
import urllib.error
import urllib.request

from send_josh_reply import API_BASE, TARGET, build_payload

STATE_PATH = Path(os.environ.get("JOSH_WORK_CARD_STATE", "memory/josh_work_cards.json"))
DEFAULT_BUTTONS = [
    [{"text": "Check Mission Control", "callback_data": "next:check_mission_control"}],
    [{"text": "Route to JOSHeX", "callback_data": "route:joshex"}],
    [{"text": "Route to JAIMES", "callback_data": "route:jaimes"}],
    [{"text": "Show Models", "callback_data": "next:show_models"}],
    [{"text": "Hold", "callback_data": "next:hold"}],
]


def now_label() -> str:
    return dt.datetime.now().astimezone().strftime("%H:%M %Z")


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"cards": {}}
    except Exception:
        return {"cards": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def parse_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def status_label(status: str) -> str:
    labels = {
        "running": "active",
        "done": "done",
        "failed": "blocked",
        "paused": "paused",
    }
    return labels.get(status, status)


def compact(value: str, fallback: str = "") -> str:
    text = " ".join((value or fallback).split())
    return text[:220] + ("..." if len(text) > 220 else "")


def build_card(
    *,
    title: str,
    status: str,
    model: str = "",
    route: str = "",
    now: str = "",
    done: list[str] | None = None,
    next_step: str = "",
    blocker: str = "none",
    eta: str = "",
    updated: str | None = None,
) -> str:
    done = done or []
    model_line = model or os.environ.get("JOSH_WORK_CARD_MODEL") or "Josh 2.0 Telegram task card"
    lines = [
        compact(model_line),
        "",
        f"- Route: {compact(route, 'Josh 2.0 direct chat; foreground lane.')}",
        f"- Objective: {compact(title)}",
        f"- Status: {status_label(status)}",
    ]
    if now:
        lines.append(f"- Now: {compact(now)}")
    if done:
        done_text = "; ".join(compact(item) for item in done[:4])
        lines.append(f"- Done: {done_text}")
    if blocker:
        lines.append(f"- Blocker: {compact(blocker, 'None')}")
    if next_step:
        lines.append(f"- Next: {compact(next_step)}")
    if eta:
        lines.append(f"- ETA: {compact(eta)}")
    lines.append(f"- Updated: {updated or now_label()}")
    return "\n".join(lines)


def api_call(method: str, payload: dict, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        f"{API_BASE}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        return {"ok": False, "error": f"{exc}: {body}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def send_card(text: str, buttons: list | None, timeout: int) -> dict:
    payload = build_payload(text, buttons, silent=True)
    return api_call("sendMessage", payload, timeout=timeout)


def edit_card(message_id: int | str, text: str, buttons: list | None, timeout: int) -> dict:
    payload = {
        "chat_id": TARGET,
        "message_id": message_id,
        "text": text,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return api_call("editMessageText", payload, timeout=timeout)


def load_buttons(args: argparse.Namespace) -> list | None:
    if args.no_buttons:
        return None
    if args.buttons_file:
        return json.loads(Path(args.buttons_file).read_text(encoding="utf-8"))
    if args.buttons:
        return json.loads(args.buttons)
    return DEFAULT_BUTTONS


def upsert_card(args: argparse.Namespace, status: str) -> int:
    state = load_state()
    cards = state.setdefault("cards", {})
    existing = cards.get(args.key, {})
    title = args.title or existing.get("title") or args.key
    done = parse_list(args.done) or existing.get("done", [])
    route = args.route or existing.get("route") or ""
    model = args.model or existing.get("model") or ""
    text = build_card(
        title=title,
        status=status,
        model=model,
        route=route,
        now=args.now or "",
        done=done,
        next_step=args.next or "",
        blocker=args.blocker or "None",
        eta=args.eta or "",
    )
    buttons = load_buttons(args)

    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "text": text, "buttons": buttons, "existing": existing}, indent=2))
        return 0

    if existing.get("message_id"):
        result = edit_card(existing["message_id"], text, buttons, args.timeout)
        action = "edited"
    else:
        result = send_card(text, buttons, args.timeout)
        action = "sent"

    if not result.get("ok"):
        print(json.dumps({"ok": False, "action": action, "error": result.get("error") or result}, indent=2), file=sys.stderr)
        return 1

    message_id = existing.get("message_id")
    if action == "sent":
        message_id = result.get("result", {}).get("message_id")
    cards[args.key] = {
        "title": title,
        "message_id": message_id,
        "status": status,
        "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "done": done,
        "route": route,
        "model": model,
    }
    save_state(state)
    print(json.dumps({"ok": True, "action": action, "key": args.key, "message_id": message_id}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """
            Send or edit a Josh-facing Telegram work card.
            Example:
              scripts/josh_work_card.py start --key mc-fix --title "Mission Control fix" --now "reading files"
              scripts/josh_work_card.py update --key mc-fix --now "running tests" --done "patched CSS|py_compile passed"
              scripts/josh_work_card.py done --key mc-fix --done "tests passed|pushed main"
            """
        ),
    )
    parser.add_argument("action", choices=["start", "update", "done", "fail", "pause"])
    parser.add_argument("--key", required=True, help="Stable task key, e.g. mission-control-polish")
    parser.add_argument("--title", help="Human-readable task title")
    parser.add_argument("--model", help="Visible model/auth line")
    parser.add_argument("--route", help="Visible route line")
    parser.add_argument("--now", help="Current step")
    parser.add_argument("--done", help="Pipe-separated completed steps")
    parser.add_argument("--next", help="Next step")
    parser.add_argument("--blocker", default="None")
    parser.add_argument("--eta")
    parser.add_argument("--buttons")
    parser.add_argument("--buttons-file")
    parser.add_argument("--no-buttons", action="store_true")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status-button", action="store_true", help="Deprecated; buttons are attached by default")
    args = parser.parse_args()

    if args.buttons and args.buttons_file:
        parser.error("Use either --buttons or --buttons-file, not both")

    status = {
        "start": "running",
        "update": "running",
        "done": "done",
        "fail": "failed",
        "pause": "paused",
    }[args.action]
    return upsert_card(args, status)


if __name__ == "__main__":
    raise SystemExit(main())
