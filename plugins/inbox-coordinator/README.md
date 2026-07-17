# Inbox Coordinator

Trusted Josh 2.0 OpenCLAW hook for the J.A.I.N Control Center Inbox.

It claims only untagged Telegram messages in group `-1003589561528`, topic
`1`, then passes the prompt over a pipe to the host-local fast-ack helper. It
uses both `inbound_claim` for plugin-owned bindings and global
`before_dispatch` for unbound Topic 1 traffic. Because `before_dispatch` omits
the current Telegram message ID, the plugin correlates it with the earlier
`message_received` hook through a 30-second in-memory session/content-hash
cache. The helper can therefore place the eyes reaction before it creates one
live card and submits one asynchronous coordinator worker. Prompt text is never
placed in a process argument, cache, or plugin log.

The default helper is the canonical checked-in
`mission-control/scripts/josh_telegram_fast_ack.py`, not the legacy workspace
copy. A Josh claim is accepted only after its exact receipt proves the eyes
reaction, successful card start, immutable header ID, editable live-card ID,
and durable worker job ID. A timeout after the helper may have produced visible
Telegram effects is retained as indeterminate and suppresses an unsafe native
fallback; a clean failure before any possible effects remains fail-open.

Inbound `message_received` correlations are consumed one-to-one. This keeps a
burst of global `before_dispatch` hooks from binding multiple requests to the
same Telegram message ID.

Direct `@JAIMES` mentions are handed off only when the JAIMES health
snapshot is fresh and JAIMES returns a per-message acceptance receipt for the
exact chat, topic, and inbound message. That receipt proves the eyes reaction,
immutable task header, and editable live card all exist before Josh 2.0 stands
down. A timeout, stale or mismatched receipt, missing surface, or unhealthy
watcher falls back to Josh 2.0 instead of silently dropping the message. The
cross-host handoff carries numeric origin IDs only; prompt text never leaves the
normal Telegram delivery lanes. `#jaimes` and plain-language delegation
requests, including `@JAIN`, remain Josh-owned routing hints for explicit
worker delegation.

Natural Inbox finals use a pre-delivery gate. The fast-ack watcher waits for
the agent to create an interpreted, own-words objective card, adopts that exact
run's card, and leaves model completion to the gate. The gate validates the
single structured final, atomically fences the watcher, makes the existing live
card terminal, and only then permits the native Telegram final. Interim
messages never close the card, stale same-topic cards cannot authorize a new
run, and an existing final receipt suppresses duplicate native delivery.

Because `before_agent_finalize` reads the candidate final in memory, this
trusted non-bundled plugin must explicitly opt into conversation-hook access:

```bash
openclaw config set plugins.entries.inbox-coordinator.hooks.allowConversationAccess true --strict-json
```

Final text crosses the terminal helper only through private standard input and
a mode-0600 ephemeral file that is removed immediately. Before Telegram I/O,
the helper also records a mode-0600, origin-scoped private outbox item. The
fast-ack watcher retries that item with the same card lock and final-message
receipt until delivery succeeds; an expired close claim is reclaimed without
operator action. It is never put in helper arguments, plugin logs, or Control
Tower data, and the outbox item is deleted as soon as delivery is proven.

## Verify

```bash
npm test
openclaw plugins inspect inbox-coordinator --runtime --json
```
