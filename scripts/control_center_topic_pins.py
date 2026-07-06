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
    "Inbox": """Pinned message\n\nJ.A.I.N Control Center routing\n\nUse Inbox for general asks, triage, and anything that does not clearly belong elsewhere.\n\nDefault protocol:\n- One agent answers unless tagged.\n- JOSH 2.0 owns conversation/front-line triage.\n- JAIMES owns backend, crons, code, SSH, batch jobs.\n- Move specialist work into its topic when the route is clear.\n\nTopic map:\n- JAIMES Ops: systems, scripts, crons, SSH, alerts.\n- JOSH 2.0: primary assistant status/protocol/handoffs.\n- Sorare: Sorare MLB lineups, missions, alerts.\n- Crypto Alerts: wallet/watchlist/trade signals only.\n- Approvals: actions needing Josh approval.\n- Mission Control: dashboard/feed/kiosk health.\n- News: J.A.I.N intelligence and breaking-news digests.""",
    "JAIMES Ops": """Pinned message\n\nJAIMES Ops\n\nUse this topic for backend/system work:\n- Cron failures and alert hygiene.\n- Code, SSH, scripts, data pipelines.\n- Auth/watchdog/canary health.\n- Batch jobs and long-running repairs.\n\nProtocol:\n- JAIMES leads execution here.\n- JOSH 2.0 can triage and tag JAIMES.\n- Routine progress goes to Live Work Board.\n- Telegram posts should be completion, blocker, or approval only.""",
    "JOSH 2.0": """Pinned message\n\nJOSH 2.0\n\nUse this topic for the primary assistant lane:\n- Front-line conversation protocol.\n- Recovery/status for JOSH 2.0.\n- Cross-agent handoffs with JAIMES/JOSHeX.\n- Updates that JOSH 2.0 must know before replying elsewhere.\n\nNew operating protocol:\n- One agent answers unless explicitly tagged.\n- JOSH 2.0 handles Josh-facing triage and daily flow.\n- JAIMES handles backend execution and quiet fixes.\n- JOSHeX reviews/checks codebase work when needed.\n- Use specialist topics instead of mixing everything in Inbox.""",
    "Sorare": """Pinned message\n\nSorare\n\nUse this topic for all Sorare work and alerts:\n- MLB lineup optimization and submissions.\n- Daily missions and claims.\n- GW lock timing, DNP/IL/DTD risk.\n- RP-start scouts and model/eval reports.\n\nProtocol:\n- Optimize for first-place upside where appropriate.\n- Hard-block OUT/IL/DNP/zero-game issues.\n- Keep lineup and mission results detailed, not summarized away.""",
    "Crypto Alerts": """Pinned message\n\nCrypto Alerts\n\nUse this topic for portfolio, wallet, and token signals:\n- Watchlist and green-card alerts.\n- Memecoin/token research.\n- Wallet mutation monitoring.\n- Trade cards and route drafts.\n\nProtocol:\n- Research, score, simulate, and draft freely.\n- No signing, swaps, transfers, revokes, staking, minting, bridging, or purchases without Josh approval.\n- Only surface trades JAIMES would actually consider.""",
    "Approvals": """Pinned message\n\nApprovals\n\nUse this topic for actions that need Josh to say yes/no:\n- External sends/posts/messages.\n- Financial or wallet actions.\n- Irreversible account/config changes.\n- Paid spend, subscriptions, purchases.\n\nProtocol:\n- Include exact action, target, risk, and rollback if any.\n- Prefer short approve/reject/adjust choices.\n- After approval, execute and report what changed.""",
    "Mission Control": """Pinned message\n\nMission Control\n\nUse this topic for dashboard and visibility systems:\n- Control Tower / kiosk health.\n- Live Work Board / Brain Feed freshness.\n- Visibility guard issues.\n- Dashboard deploys, regressions, and UI checks.\n\nProtocol:\n- Live Work Board is source of truth.\n- Fix stale/duplicate/noisy alerts at the source.\n- Keep routine state visible in dashboard, not chat spam.""",
    "News": """Pinned message\n\nNews\n\nUse this topic for J.A.I.N intelligence outputs:\n- Breaking-news scanner alerts.\n- Market/policy/security digests.\n- Premarket, midday, close, evening, weekend summaries.\n\nProtocol:\n- BREAKING is only urgent, high-confidence, same-day material updates.\n- Intelligence digests should be compact and readable.\n- Routine QA/health noise belongs in JAIMES Ops, not News.""",
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
