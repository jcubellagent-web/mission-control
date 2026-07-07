# J.A.I.N Control Center Topic Protocol

This document records the Telegram topic routing protocol Josh asked JAIMES to install on 2026-07-06. It exists so JOSHeX, JOSH 2.0, and future JAIMES sessions can audit the pinned-topic messages without relying on Telegram history alone.

## Source of truth

- Telegram supergroup: `J.A.I.N Control Center`
- Chat id: `-1003589561528`
- Topic map: `~/.hermes/state/jain_control_center_topics.json` on both JAIMES/J.AI.N and Josh 2.0
- Pin helper: `mission-control/scripts/control_center_topic_pins.py`
- Pin manifest: `~/.hermes/state/jain_control_center_pinned_protocol.json`

## Operating protocol

- JOSH 2.0 owns front-line conversation, triage, and daily flow.
- JOSH 2.0 answers first in Inbox; if the work belongs elsewhere, it routes to JAIMES and keeps the thread moving.
- JAIMES owns backend execution: crons, code, SSH, batch jobs, alert hygiene.
- JOSHeX reviews/checks codebase work and deployment quality when needed.
- Other agents stay quiet unless tagged, delegated, or asked to verify.
- Specialist work should be moved to the matching topic instead of staying in Inbox.
- Routine progress belongs on the Live Work Board; Telegram should be used for direct completions, blockers, approvals, urgent alerts, or material findings.
- Replies should stay in the topic where the request was made unless the agent explicitly summarizes and redirects the work into a better topic.
- If multiple agents see the same group message, the first appropriate owner should acknowledge; other agents should stay quiet unless tagged, delegated, or asked to verify.

## Topic responsibilities

| Topic | Use |
|---|---|
| Inbox | General asks, triage, unclear routing. |
| JAIMES Ops | Backend work: crons, scripts, SSH, system alerts, repairs. |
| JOSH 2.0 | JOSH 2.0 status, protocol, recovery, cross-agent handoffs. |
| Sorare | Sorare MLB lineups, missions, GW locks, player-risk alerts. |
| Crypto Alerts | Wallet, token, watchlist, and trade-signal alerts. |
| Approvals | Josh approve/reject/adjust decisions before execution. |
| Mission Control | Legacy topic label for Control Tower, Live Work Board, and dashboard visibility. Rename to `Control Tower` in Telegram when convenient; until then, treat it as the Control Tower topic. |
| News | J.A.I.N breaking-news alerts and scheduled intelligence digests. |

## Notes for future agents

- Josh specifically asked the visible pinned messages to be short statements about what each chat is used for. Keep detailed protocol here, not in the pinned text.
- Inbox triage routes through JOSH 2.0 first so untagged questions can be answered or handed to JAIMES without waiting for a mention.
- Live OpenClaw config keeps Inbox topic 1 mention-free, while the other topics can stay mention-gated.
- Keep topic labels and routing state mirrored on Josh 2.0 and JAIMES so either host can route without guessing.
- Telegram Bot API can post/pin into known topic IDs, but it cannot reliably list all forum topics. Keep the JSON topic map current.
- If a topic is recreated, update the topic ID before rerunning the pin helper.
- The helper intentionally posts short one-line purpose messages.
- Before posting replacements, the helper unpins message IDs from the prior manifest so old protocol pins do not accumulate.
- Telegram cannot reliably list all per-topic pins through the Bot API, so manifest cleanup is the auditable source of truth.
- Do not print bot tokens in logs or reports. The helper reads local secret files and only prints topic/message IDs.
- If Josh changes the operating protocol, update both this doc and `TOPIC_MESSAGES` in the helper, then rerun the helper and commit/push/deploy through the normal Control Tower path.

## Quick Inbox routing tags

If Josh starts in `Inbox` but wants to hint the destination, prefix the message with the topic name or hashtag, for example `#sorare`, `#crypto`, `#approvals`, `#control`, `#controltower`, `#mission`, `#news`, `#jaimes`, or `#josh2`. Telegram does not natively move an existing human message between topics for us; agents should copy/summarize the work into the correct topic when the route matters. Topic buttons on the pinned messages are navigation shortcuts only.
