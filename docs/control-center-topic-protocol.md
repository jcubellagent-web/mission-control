# J.A.I.N Control Center Topic Protocol

This document records the Telegram topic routing protocol Josh asked JAIMES to install on 2026-07-06. It exists so JOSHeX, JOSH 2.0, and future JAIMES sessions can audit the pinned-topic messages without relying on Telegram history alone.

## Source of truth

- Telegram supergroup: `J.A.I.N Control Center`
- Chat id: `-1003589561528`
- Topic map: `~/.hermes/state/jain_control_center_topics.json`
- Pin helper: `mission-control/scripts/control_center_topic_pins.py`
- Pin manifest: `~/.hermes/state/jain_control_center_pinned_protocol.json`

## Operating protocol

- One agent answers unless explicitly tagged.
- JOSH 2.0 owns front-line conversation, triage, and daily flow.
- JAIMES owns backend execution: crons, code, SSH, batch jobs, alert hygiene.
- JOSHeX reviews/checks codebase work and deployment quality when needed.
- Specialist work should be moved to the matching topic instead of staying in Inbox.
- Routine progress belongs on the Live Work Board / Brain Feed; Telegram should be used for direct completions, blockers, approvals, urgent alerts, or material findings.

## Topic responsibilities

| Topic | Use |
|---|---|
| Inbox | General asks, triage, unclear routing. |
| JAIMES Ops | Backend work: crons, scripts, SSH, system alerts, repairs. |
| JOSH 2.0 | JOSH 2.0 status, protocol, recovery, cross-agent handoffs. |
| Sorare | Sorare MLB lineups, missions, GW locks, player-risk alerts. |
| Crypto Alerts | Wallet, token, watchlist, and trade-signal alerts. |
| Approvals | Josh approve/reject/adjust decisions before execution. |
| Mission Control | Control Tower, Live Work Board, Brain Feed, dashboard visibility. |
| News | J.A.I.N breaking-news alerts and intelligence digests. |

## Notes for future agents

- Josh specifically asked the visible pinned messages to be short statements about what each chat is used for. Keep detailed protocol here, not in the pinned text.
- Telegram Bot API can post/pin into known topic IDs, but it cannot reliably list all forum topics. Keep the JSON topic map current.
- If a topic is recreated, update the topic ID before rerunning the pin helper.
- The helper intentionally posts short one-line purpose messages; Telegram clients display the latest pinned message per topic.
- Do not print bot tokens in logs or reports. The helper reads local secret files and only prints topic/message IDs.
- If Josh changes the operating protocol, update both this doc and `TOPIC_MESSAGES` in the helper, then rerun the helper and commit/push/deploy through the normal Mission Control path.
