#!/usr/bin/env python3
"""Post and pin J.A.I.N Control Center topic summaries.

Why this exists
---------------
Josh asked for each Telegram forum topic in `J.A.I.N Control Center` to
carry a pinned, human-readable routing note, similar to the existing Inbox
pin. This script makes that operation auditable/replayable for JOSHeX,
JOSH 2.0, and JAIMES instead of leaving one-off Bot API calls buried in
shell history.

Operational contract
--------------------
- Uses Telegram Bot API `sendMessage` + `pinChatMessage`.
- Reads the bot token from local secret files; never prints it.
- Posts into explicit `message_thread_id`s from the topic map.
- Writes a JSON manifest with returned message IDs so later agents can
  verify or supersede pins without guessing.
- The messages are intentionally short, dashboard-safe, and boring.

Safety notes
------------
Telegram bots cannot reliably list all forum topics, so this script treats
`~/.hermes/state/jain_control_center_topics.json` as source of truth. If a
topic is renamed/recreated, update that map first.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOPIC_MAP = Path.home() / ".hermes/state/jain_control_center_topics.json"
OUT = Path.home() / ".hermes/state/jain_control_center_pinned_protocol.json"
SECRET_CANDIDATES = [
    # JAIMES is the admin bot in J.A.I.N Control Center. Keep this first;
    # other local Telegram bot tokens may exist but are not members/admins of
    # this supergroup and will return `Bad Request: chat not found`.
    Path.home() / ".secrets/hermes_telegram_token.txt",
    Path.home() / ".secrets/openclaw_telegram_token.txt",
    Path.home() / ".secrets/telegram_bot_token.txt",
]

# Keep these summaries stable and concise. They are meant to teach agents
# and humans where work belongs before anyone replies in the wrong topic.
TOPIC_MESSAGES: dict[str, str] = {
    "Inbox": "Use this chat for general asks, triage, and anything that does not clearly belong in another topic.",
    "JAIMES Ops": "Use this chat for JAIMES backend work: crons, scripts, SSH, system alerts, and repairs.",
    "JOSH 2.0": "Use this chat for JOSH 2.0 status, protocol updates, recovery, and cross-agent handoffs.",
    "Sorare": "Use this chat for Sorare MLB lineups, missions, gameweek locks, and player-risk alerts.",
    "Crypto Alerts": "Use this chat for wallet, token, watchlist, and trade-signal alerts that still need review.",
    "Approvals": "Use this chat for actions that need Josh to approve, reject, or adjust before execution.",
    "Mission Control": "Use this chat for Control Tower, Live Work Board, Brain Feed, and dashboard visibility issues.",
    "News": "Use this chat for J.A.I.N breaking-news alerts and scheduled intelligence digests.",
}


def load_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("HERMES_TELEGRAM_BOT_TOKEN")
    if token:
        return token.strip()
    for path in SECRET_CANDIDATES:
        if path.exists():
            val = path.read_text().strip()
            if val:
                return val
    raise SystemExit("No Telegram bot token found in env or known secret files")


def api(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data["result"]


def main() -> int:
    topics_doc = json.loads(TOPIC_MAP.read_text())
    chat_id = topics_doc["chat_id"]
    topics = topics_doc["topics"]
    token = load_token()
    manifest: dict[str, Any] = {
        "chat_id": chat_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_topic_map": str(TOPIC_MAP),
        "pins": {},
    }
    missing = sorted(set(TOPIC_MESSAGES) - set(topics))
    if missing:
        raise SystemExit(f"Missing topics in map: {missing}")

    for name, text in TOPIC_MESSAGES.items():
        thread_id = topics[name]
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        # Telegram's default/general forum topic is addressed by omitting
        # message_thread_id. Passing the stored General topic id (`1`) returns
        # `Bad Request: message thread not found` for this supergroup.
        if name != "Inbox":
            payload["message_thread_id"] = thread_id
        sent = api(token, "sendMessage", payload)
        msg_id = sent["message_id"]
        # Pin silently so Josh gets one useful pinned banner per topic without
        # extra push-notification noise.
        api(token, "pinChatMessage", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "disable_notification": "true",
        })
        manifest["pins"][name] = {"thread_id": thread_id, "message_id": msg_id}
        print(f"pinned {name}: thread={thread_id} message={msg_id}")
        time.sleep(0.4)

    OUT.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
