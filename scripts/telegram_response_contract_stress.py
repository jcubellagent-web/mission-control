#!/usr/bin/env python3
"""Render and optionally transport-test the primary Telegram response contract."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path


LABELS = (
    "Model:",
    "Complete:",
    "What was done:",
    "Issues:",
    "Appropriate next steps:",
    "Approval needed:",
)


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("telegram_work_card_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validate(text: str) -> list[str]:
    problems: list[str] = []
    positions = [text.find(label) for label in LABELS]
    if any(pos < 0 for pos in positions):
        problems.append("one or more final-summary labels are missing")
    elif positions != sorted(positions):
        problems.append("final-summary labels are out of order")
    for forbidden in ("<b>", "</b>", "**", "Objective Complete:", "TLDR:", "Challenges/Blockers:", "•"):
        if forbidden in text:
            problems.append(f"forbidden formatting remains: {forbidden}")
    if "Approval needed:\n- n/a" not in text:
        problems.append("no-approval completion must end with n/a")
    if max((len(line) for line in text.splitlines()), default=0) > 240:
        problems.append("a rendered line exceeds 240 characters")
    return problems


def live_canary(module, chat_id: str, thread_id: str) -> dict:
    start = time.monotonic()
    sent = module.send_card(
        "<pre>Release canary\n- send\n- edit\n- delete</pre>",
        None,
        15,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    message_id = str((sent.get("result") or {}).get("message_id") or "")
    if not sent.get("ok") or not message_id:
        return {"ok": False, "stage": "send", "error": str(sent.get("error") or sent.get("description") or "send failed")[:240]}
    edited = module.edit_card(
        message_id,
        "<pre>Release canary\n- send ok\n- edit ok\n- deleting</pre>",
        None,
        15,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    deleted = module.api_call("deleteMessage", {"chat_id": int(chat_id), "message_id": int(message_id)}, timeout=15)
    return {
        "ok": bool(edited.get("ok") and deleted.get("ok")),
        "send": bool(sent.get("ok")),
        "edit": bool(edited.get("ok")),
        "delete": bool(deleted.get("ok")),
        "elapsedMs": round((time.monotonic() - start) * 1000, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("josh2", "jaimes"), required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--chat-id", default="-1003589561528")
    parser.add_argument("--thread-id")
    args = parser.parse_args()

    home = Path.home()
    script = (
        home / ".openclaw/workspace/scripts/josh_work_card.py"
        if args.role == "josh2"
        else home / ".openclaw/workspace/mission-control/scripts/jaimes_work_card.py"
    )
    module = load_module(script)
    rendered = module.build_completion_summary(
        title="Primary topic readiness",
        status="done",
        model="openai/gpt-5.6-terra" if args.role == "josh2" else "openai-codex/gpt-5.6-sol",
        now="Transport and formatting verified",
        done=["Ownership verified", "Shared memory available", "Live card completed"],
        next_step="No action needed.",
        blocker="None",
    )
    problems = validate(rendered)
    transport = None
    if args.live:
        if not args.thread_id:
            problems.append("--thread-id is required with --live")
        else:
            transport = live_canary(module, args.chat_id, args.thread_id)
            if not transport.get("ok"):
                problems.append("live send/edit/delete canary failed")
    result = {"role": args.role, "ok": not problems, "problems": problems, "transport": transport}
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
