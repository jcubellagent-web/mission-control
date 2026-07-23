# J.A.I.N Control Center Topic Protocol

This document records the Telegram topic protocol and the historical pin
mechanics first installed on 2026-07-06. It is an operator reference, not an
independent routing authority.

## Canonical authority

- `config/telegram-intake-lanes.json` is the only checked-in topic ownership and
  mention-override registry.
- `scripts/telegram_channel_registry.py` is the fail-closed runtime resolver.
- `agent-skills/telegram-task-flow/SKILL.md` is the detailed visible reaction,
  card, final, model-disclosure, and Brain Feed contract.
- `scripts/telegram_primary_topics_check.py` verifies that both host configs
  match the registry. Host-local maps and prompts are deployment mirrors, never
  fallback ownership sources.

If this document conflicts with any authority above, the checked-in registry,
resolver, and skill win.

## Historical pin mechanics

- Telegram supergroup: `J.A.I.N Control Center`
- Chat id: `-1003589561528`
- Historical discovery map: `~/.hermes/state/jain_control_center_topics.json`
- Pin helper: `mission-control/scripts/control_center_topic_pins.py`
- Pin manifest: `~/.hermes/state/jain_control_center_pinned_protocol.json`

The discovery map and pin manifest are retained for pin cleanup and migration
context only. They do not authorize a responder or override the canonical
registry.

## Operating protocol

- The registry assigns exactly one owner to every authorized static topic.
- The registered owner alone acknowledges an untagged request. One unambiguous
  configured `@mention` may override topic ownership; multiple agent mentions
  are ambiguous and every responder remains silent.
- JOSH 2.0 owns Inbox conversation and may delegate backend execution while
  retaining delivery ownership. JAIMES owns untagged work in its registered
  operational topics.
- Delegation and specialist execution do not move, copy, or duplicate the human
  request. The acknowledgement, live card, and final stay in the origin topic.
- Non-owners observe silently unless the registry selects them, they are
  explicitly delegated internal work, or the user addresses them with one
  configured mention.
- Routine progress belongs on the Live Work Board. Telegram background
  notifications are limited to failures, blockers, approvals, time-sensitive
  alerts, and material findings; direct task replies still follow the shared
  Telegram task-flow contract.

## Topic responsibilities

| Topic | Owner | Use |
|---|---|---|
| Inbox | JOSH 2.0 | General asks, triage, and unclear routing. |
| JAIMES Ops | JAIMES | Backend work: crons, scripts, SSH, system alerts, and repairs. |
| JOSH 2.0 | JOSH 2.0 | JOSH 2.0 status, protocol, recovery, and cross-agent handoffs. |
| Sorare | JAIMES | Sorare MLB lineups, missions, GW locks, and player-risk alerts. |
| Crypto Alerts | JAIMES | Wallet, token, watchlist, and trade-signal alerts. |
| Approvals | JOSH 2.0 | Josh approve/reject/adjust decisions before execution. |
| Mission Control | JOSH 2.0 | Legacy Telegram label for Control Tower, Live Work Board, and dashboard visibility. |
| News | JAIMES | Breaking-news alerts and scheduled intelligence digests. |

## Notes for future agents

- Josh specifically asked the visible pinned messages to be short statements about what each chat is used for. Keep detailed protocol here, not in the pinned text.
- Inbox triage routes through JOSH 2.0 so untagged questions can be answered or
  delegated without creating a second responder.
- Mention gates on each host are deployment details derived from the registry;
  do not describe all non-Inbox topics as mention-gated.
- Keep host configs synchronized with `config/telegram-intake-lanes.json` and
  verify them with `scripts/telegram_primary_topics_check.py`. Never route by a
  guessed or stale host-local map.
- Telegram Bot API can post/pin into known topic IDs, but it cannot reliably list all forum topics. Keep the JSON topic map current.
- If a topic is recreated, update the topic ID before rerunning the pin helper.
- The helper intentionally posts short one-line purpose messages.
- Before posting replacements, the helper unpins message IDs from the prior manifest so old protocol pins do not accumulate.
- Telegram cannot reliably list all per-topic pins through the Bot API, so manifest cleanup is the auditable source of truth.
- Do not print bot tokens in logs or reports. The helper reads local secret files and only prints topic/message IDs.
- If Josh changes the operating protocol, update both this doc and `TOPIC_MESSAGES` in the helper, then rerun the helper and commit/push/deploy through the normal Control Tower path.

## Inbox routing hints

Only configured `@mentions` change response ownership. A `#jaimes` hashtag or
plain-language delegation request may help JOSH 2.0 select an internal worker,
but JOSH 2.0 still owns the Inbox card and final. Other historical hashtag
shortcuts are not advertised because the runtime registry does not implement
them. Topic buttons on pinned messages are navigation shortcuts only.
