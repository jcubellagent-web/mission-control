---
name: task-closeout
description: Use before completing, blocking, cancelling, or erroring an agent task so source, leases, pushes, handoffs, and governed artifacts cannot remain stale.
---

# Complete Agentic Task Closeout

Terminal status is an auditable outcome, not a progress label. Close every task
under its exact task/work/run identity.

## Source-changing tasks

1. Create the task with `--work-scope shared-source` before editing.
2. Begin the Control Tower change lease with the exact `--task-id`, `--work-id`,
   and `--run-id`. Never edit shared source under an unlinked lease.
3. On success: test, commit only intentional files, push the validated commit,
   and call `control_tower_change_guard.py finish`.
4. On failure, cancellation, or interruption: preserve evidence and call
   `abort`. Do not mark a task blocked, errored, or cancelled while its source is
   still dirty; those are terminal outcomes and require clean restoration too.
5. Use `recover-expired` only when the owner process is absent and guarded source
   is already clean. Dirty orphaned work must be inspected, adopted explicitly,
   and either finished or restored; never delete it to clear a lease.

The guard writes a private source-closeout receipt bound to the exact task/work/run
identity. `agent_task.py` fails closed on every terminal transition until a
matching finished, aborted, or clean expired-orphan receipt exists. A finished
receipt additionally proves the guarded source is clean and `HEAD == origin/main`.

## Every task

- Resolve handoffs and publish the final dashboard-safe phase under the real owner.
- Declare exactly one artifact outcome: `promoted`, `updated-existing`, or
  `no-artifact-needed`, with a concrete reason.
- Promoted or updated artifacts enter governed memory as review candidates; task
  completion never bypasses memory review. If the registry is unavailable, keep
  the durable proposal-error/retry record.
- Finish only after live task state, lease state, Git state, and artifact state
  agree. If they do not agree, the task remains active while recovery proceeds.

## Final proof

Before reporting completion, verify: no matching active lease; guarded source is
clean; local and origin commits match when source was finished; the terminal task
has its source-closeout and artifact decisions; and any reusable procedure is in
the governed memory review queue.

#JAIMES: terminal task state now consumes exact source-closeout evidence, preventing completed work from leaving invisible locks or uncommitted source behind.
