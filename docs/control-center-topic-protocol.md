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
| JAIMES Ops | Crons, scripts, SSH, system alerts, backend fixes. |
| JOSH 2.0 | Primary assistant protocol, status, recovery, cross-agent handoffs. |
| Sorare | Sorare MLB lineups, missions, GW lock, DNP/IL risk, model reports. |
| Crypto Alerts | Wallet/watchlist/token signals and trade-card drafts. |
| Approvals | External sends, financial actions, irreversible changes, paid spend. |
| Mission Control | Control Tower, Live Work Board, Brain Feed, dashboard freshness/visibility. |
| News | J.A.I.N breaking/intelligence digests; not routine health noise. |

## Notes for future agents

- Telegram Bot API can post/pin into known topic IDs, but it cannot reliably list all forum topics. Keep the JSON topic map current.
- If a topic is recreated, update the topic ID before rerunning the pin helper.
- The helper intentionally posts new summary messages rather than editing unknown old pins; Telegram clients display the latest pinned message per topic.
- Do not print bot tokens in logs or reports. The helper reads local secret files and only prints topic/message IDs.
- If Josh changes the operating protocol, update both this doc and `TOPIC_MESSAGES` in the helper, then rerun the helper and commit/push/deploy through the normal Mission Control path.
