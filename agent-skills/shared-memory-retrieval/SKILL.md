---
name: shared-memory-retrieval
description: Use before repeating ecosystem work, asking Josh for context another agent may know, accepting a handoff, or proposing durable learning. Queries and updates the governed shared memory registry with provenance, confidence, validity, privacy, and review controls.
---

# Shared Memory Retrieval

The registry supplements checked-in `AGENTS.md`, `MEMORY.md`, and skills; those files remain authoritative.

## Preflight before substantial work

Before repeating work, accepting a handoff, or asking Josh for context another
agent may already know, run the host-appropriate fail-open preflight:

- Josh 2.0: `python3 mission-control/scripts/memory_registry.py preflight --agent josh2 --query "<specific question>" --work-id "<work-id>" --run-id "<run-id>"`
- JAIMES: `~/scripts/ecosystem_memory.py preflight --agent jaimes --query "<specific question>" --work-id "<work-id>" --run-id "<run-id>"`
- J.A.I.N: `~/scripts/ecosystem_memory.py preflight --agent jain --query "<specific question>" --work-id "<work-id>" --run-id "<run-id>"`
- JOSHeX: `ssh josh2 'cd ~/.openclaw/workspace/mission-control && python3 scripts/memory_registry.py preflight --agent joshex --query "<specific question>" --work-id "<work-id>" --run-id "<run-id>"'`

Omit work/run IDs only for genuinely ad hoc work. A registry error returns a
small `unavailable` result and never blocks execution. The raw query and raw
work/run/session IDs are not stored or echoed; context IDs are hashed.

Use the returned source, confidence, validity, and status. If a record conflicts
with current tool evidence, trust current evidence and propose a correction.
Use `retrieve` only when an explicit deeper lookup is needed after preflight.

## Record actual reuse

Keep the returned `retrievalId`. For each memory you intend to apply, record a
`selected` event before using it, with the same work/run context:

`memory_registry.py reuse-outcome --agent <agent> --retrieval-id <retrieval-id> --memory-id <memory-id> --outcome selected --reason-code context-only --work-id <work-id> --run-id <run-id>`

After the result is known, record exactly one follow-up for that selected memory:

- `used` with `--reason-code applied` or `duplicate-work-avoided` when it materially affected the work.
- `ignored` with `--reason-code not-relevant`, `stale`, `conflict`, or `other` when it did not.

`used` is rejected unless the same retrieval/memory/context was selected first.
Do not mark every returned memory as selected and do not infer use from silence.

After the memory materially affects the quality of a decision or result, also
record one explicit quality outcome with the same host wrapper used for retrieval:

- Josh 2.0: `python3 mission-control/scripts/memory_registry.py feedback ...`
- JAIMES/J.A.I.N: `~/scripts/ecosystem_memory.py feedback ...`
- JOSHeX: run `memory_registry.py feedback ...` through the Josh 2.0 SSH path.
- Helpful: use `--outcome helpful --reason "<observed benefit>"`.
- Ignored: use `--outcome ignored` when the result was valid but not used.
- Harmful: use `--outcome harmful` with the affected `--memory-id` when following it caused a bad result.
- Corrected: use `--outcome corrected --correction "<verified replacement>"`; this creates a governed candidate and never overwrites durable memory directly.

Record feedback only after a meaningful outcome is known. Do not grade every
lookup, infer feedback from silence, or include private task content in the reason.

## Propose

Use `propose` for a durable fact, decision, preference, procedure, lesson, entity, relationship, or episode. Include a specific subject, predicate, value, source, evidence, owner, visibility, privacy, and confidence.

Never promote raw model inference directly. Preferences, policy, procedures, sensitive facts, and conflicts stay pending review. The nightly reviewer may auto-promote only verified low-risk facts, lessons, entities, and relationships.

## Close out durable artifacts

For Control Tower tasks using artifact contract version 1, every terminal transition must declare exactly one artifact outcome:

- `promoted`: a new durable artifact was produced and is referenced with `--artifact`.
- `updated-existing`: an authoritative existing artifact was materially updated and is referenced with `--artifact`.
- `no-artifact-needed`: the result was intentionally transient or already represented; explain why.

Pass `--artifact-outcome` and `--artifact-reason` to `agent_task.py complete`, `block`, `error`, or `cancel`. Promoted and updated artifacts automatically create a pending episode candidate with task/work/run provenance. Review that candidate through the normal governed queue; task closeout never directly activates memory. When a promoted artifact is authoritative policy, procedure, preference, or a durable fact, review it into the appropriate typed memory or supersede the older record rather than leaving duplicate active guidance.

If the registry is unavailable, the task ledger durably records `proposal-error` without blocking terminal state. After recovery, run `agent_task.py replay-artifact-proposals --agent <agent>` to idempotently retry failed or pending task-bound proposals.

## Maintain

- Status: `memory_registry.py status`
- Review queue: `memory_registry.py candidates --status candidate` and `--status disputed`
- Approve: `memory_registry.py approve --id <candidate-id> --reviewer <agent>`
- Supersede a verified conflict: add `--supersedes <active-memory-id>` to approval
- Reject: `memory_registry.py reject --id <candidate-id> --reviewer <agent> --reason "<reason>"`
- Rebuild deterministic sources: `memory_registry.py build`
- Governed review: `memory_registry.py review --apply-safe`
- Refresh Control Tower sidecar: `memory_registry.py export`
- Outcome history is aggregated into recall quality; raw reasons and memory contents are not displayed in Control Tower.

Do not publish raw memory content to Control Tower. It receives health, counts, retrieval latency/hit rate, review state, and source coverage only.

## Telegram Integration

Inbox and JAIMES Ops must run the fail-open preflight before asking Josh to repeat
ecosystem context. Retrieval is silent unless provenance or a conflict matters
to the answer. Reuse and quality outcomes are recorded after the task result,
not shown as an approval step unless a policy correction genuinely needs review.
