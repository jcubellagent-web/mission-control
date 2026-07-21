# Governed Brain topic intake

The Telegram topic named **Brain** is a Josh 2.0-owned media intake lane. It
accepts Telegram-deliverable attachments with or without captions and keeps raw
media, source identifiers, extraction text, and memory candidates private.

## Canonical ownership

- The OpenCLAW ingress hook persists the private receipt before download,
  stores the attachment, and durably enqueues the work. It does not extract,
  invoke a model, or call Telegram delivery APIs.
- `scripts/brain_intake_worker.py` performs bounded local safety checks,
  extraction, indexing, and governed candidate handling. It emits lifecycle
  events and commits the terminal outbox; it never reacts or sends messages.
- `scripts/brain_gateway_dispatcher.py` is the sole visible Brain writer. After
  a durable binding it reserves lifecycle effects and produces one eyes
  reaction, one editable live card, and one separate final receipt. Unknown API
  outcomes are marked indeterminate and are not blindly retried.
- `scripts/brain_gateway_actions.py` is the reply-bound control adapter. It
  validates the exact private source/final mapping and authorized sender before
  it can change privacy, review a candidate, or execute the two-step Forget
  flow; only cancellation may also target the active card. Its private response
  journal and Control Tower outbox are drained independently of later Telegram
  updates.
- `scripts/brain_topic_manager.py` reuses the confirmed private topic receipt.
  `verify-control` binds that receipt to the live control bot and an explicit
  `can_manage_topics` proof in a separate owner-only receipt. If creation is
  ever required, the manager persists an intent before the one create call and
  fences an ambiguous response.
- JAIMES may perform explicitly delegated extraction or review, but it does not
  own the topic, run a Telegram watcher, or write visible Brain surfaces.

The retired JAIMES catalog/watcher and its launchd job are intentionally absent.
They must not be restored or loaded.

## Private identity and configuration

The tracked dynamic Brain entry contains only the label, owner, lane,
`topicIdSource: private-confirmed-receipt`, and routine enable control. It adds
no Brain topic, sender, or bot identifier and no private receipt path. Existing
non-Brain routing identifiers in the canonical lane registry remain unchanged.

The owner-only confirmed topic receipt is resolved at runtime through
`--topic-receipt`, `BRAIN_TOPIC_RECEIPT`, or the canonical private default. The
receipt must be a regular, single-link, current-user-owned `0600` file and must
prove one confirmed topic named exactly `Brain` with one creation history.

Every submission uses one canonical `work-telegram-*` identity across the
private intake store, lifecycle, dispatcher, Control Tower ledger, Brain Feed,
worker result, governed memory provenance, and final receipt.

## Visible contract

Each accepted media submission is Tier 3:

1. One `👀` reaction on the source message.
2. One editable, sanitized Brain intake card.
3. One separate terminal receipt after the terminal outbox commit.

The worker is fenced until the lifecycle confirms both the reaction and card.
The final Telegram call is additionally fenced until the canonical local
Control Tower work ledger accepts the sanitized terminal event. A private
bounded visibility outbox recovers temporary publisher outages; raw captions,
filenames, Telegram identifiers, and extracted content never enter publisher
arguments or shared output.

The live card objective comes only from the private store's bounded,
deterministically derived objective and artifact media class. It never reads a
filename, Telegram file ID, raw caption, or model-provided objective.

Governed reply forms are exact: `/cancel` or `Cancel this Brain intake`,
`Reference only`, `Correct: subject | predicate | value`, candidate
approve/reject/supersede forms, `Privacy: private|internal|dashboard-safe`, and
`Forget`. Privacy broadening and Forget issue sanitized previews and accept
only `CONFIRM PRIVACY` or `CONFIRM FORGET` as replies to their exact preview.

## Runtime jobs

- `launchd/com.josh2.brain-gateway-dispatcher.plist` polls the private lifecycle
  for surface and terminal-outbox work.
- `launchd/com.josh2.brain-gateway-actions.plist` drains reply responses that
  were durably deferred by a hard stop and replays the sanitized control outbox
  on a bounded interval, including after a process restart or temporary Control
  Tower outage.
- `launchd/com.josh2.brain-intake-worker.plist` processes only surface-ready
  jobs.

Enable live intake only after the rollout, Brain kill switch, storage checks,
fixture minimum, gateway tests, and rollback checks pass. Disabling the Brain
dynamic intake entry stops new submissions while already-bound work drains.
The separate Brain kill switch is a hard fail-closed emergency stop: it blocks
new intake, new actions, and visible writes while preserving durable state for
recovery after the switch clears. No lifecycle action token is minted or
consumed and no Telegram acknowledgement is attempted while that hard stop is
active. Neither control hands ownership to a legacy watcher.

## Retention and Forget

Source media is content-addressed and privately retained until an authorized
Forget. Forget must be reply-bound to the exact private source/final mapping,
show a sanitized impact preview, require an exact one-time confirmation,
cancel pending work, remove source/extraction/index/candidate/memory remnants,
and preserve only the privacy-safe deletion receipt. Telegram message deletion
is not a Forget signal.

Until the trusted reply-bound gateway action adapter is loaded and verified,
the human-origin canary and live enablement remain blocked; models and workers
must not simulate that control path.

## Human-origin Brain canary

The production canary accepts one harmless, uncaptioned file uploaded by the
authorized human. Bot-origin messages, userbots, and fabricated production
updates are not valid substitutes. Keep routine Brain intake disabled while
the hard Brain kill switch is cleared and the signed fixture, release
preflight, live bot identity, `can_manage_topics`, and `can_delete_messages`
checks run. Enable the lane only for the bounded upload window, then disable
new intake as soon as the single source is bound so that existing work can
drain without admitting another submission.

Activation and Telegram cleanup require both private JSON on standard input
and `--confirm-production-canary`:

```text
brain_gateway_actions.py human-canary-activate --private-stdin --confirm-production-canary
brain_gateway_actions.py human-canary-status --private-stdin --stage pre-forget
brain_gateway_actions.py human-canary-status --private-stdin --stage post-forget
brain_gateway_actions.py human-canary-cleanup-telegram --private-stdin --confirm-production-canary
```

The action adapter creates one work-bound journal below its owner-private state
root. The directory is `0700`; the SQLite journal, WAL/SHM files, and final
receipt are `0600`. It records the exact source, card, and final message IDs
before Forget can remove their normal bindings. When the privacy-broadening
path is exercised, it also records the command, preview, confirmation, and
final for privacy; the equivalent four Forget messages are always required.
This produces eleven exact deletion targets for the full privacy-to-Forget
canary.

The journal advances `active → sealed → ready → complete`. Sealing occurs
before normal reply and surface bindings are scrubbed. A restart-safe
reconciliation step can advance a sealed journal only after independently
proving those bindings are gone and the single per-work deletion receipt is
present. It never reconstructs or guesses Telegram identifiers.

Before Forget, the redacted audit must prove the uncaptioned, content-grounded
objective; Tier 3 lifecycle; exactly-once dispatcher surfaces; accepted
Control Tower visibility; private artifact and extraction integrity; local
model routing; zero prompt-injection signals; governed candidates; and
provenance-bearing retrieval under the intended privacy boundary. After the
reply-bound `CONFIRM FORGET`, it must prove source, extraction, chunk, vector,
candidate, memory, binding, index, path, and four-agent retrieval cleanup.

Only then may Telegram cleanup delete the journaled messages newest-first.
Unknown, unauthorized, concurrent, or partially completed deletion remains
fail-closed with the private journal intact. A counts-only receipt is written
and synced before the active SQLite files are removed. Shared output contains
no work ID, Telegram ID, query text, filename, raw content, or private path.
Successful cleanup requires every target to be definitively deleted or already
absent, zero unresolved targets, and an idempotent replay that performs no
additional Telegram writes.

Content-addressed source blobs follow reference counts: Forget always removes
the canary work's association. A blob must be physically absent when the
canary held its last reference; if another submission legitimately shares the
digest, the verifier instead requires the remaining association, digest, and
private path to stay valid without any canary retrieval path.

## Temporary gateway canaries

Live gateway canaries require `--confirm-production-canary` and a fresh
caller-created `0700` directory supplied through
`TELEGRAM_CANARY_CLEANUP_JOURNAL`. The active `0600` cleanup journal remains
until every possible temporary message is conclusively deleted. Successful
cleanup first writes a separate `0600` final receipt and only then removes the
in-flight journal. An incomplete or indeterminate cleanup blocks promotion.
