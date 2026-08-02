---
name: shared-edit-coordination
description: Use before changing shared agent-ecosystem source, prompts, configs, skills, routing, Telegram behavior, Control Tower, or host services so agents do not overwrite one another.
---

# Shared Edit Coordination

Use this before any shared ecosystem change. Runtime data refreshes and ordinary task execution do not need a source lease.

## Preflight

1. Retrieve relevant shared memory and inspect current handoffs.
2. On Josh 2.0 canonical source, run:
   `python3 scripts/ecosystem_edit_preflight.py --agent <agent> --objective "<specific change>" --fetch`
3. Stop on remote divergence or authored source changes. Generated
   `docs/handoffs/` records are operational audit churn and do not own unrelated
   source; preserve them for the audit reconciler.
4. Create or confirm the task with `--work-scope shared-source`.
5. For disjoint work, create a linked Git worktree on a non-`main` task branch
   from current `origin/main`, then acquire path claims with the exact identity:
   `python3 scripts/scoped_change_guard.py begin --agent <agent> --objective "<specific change>" --task-id <task-id> --work-id <work-id> --run-id <run-id> --repo <worktree> --scope <path> [--scope <path>]`
6. Use `control_tower_change_guard.py begin` only for direct canonical edits or
   the short canonical integration/push critical section. An active global
   integration lease blocks new scoped leases; scoped parent/child overlaps
   block one another, while disjoint claims may proceed concurrently.

## Apply

- Canonical shared source is Josh 2.0 `mission-control` `main`; concurrent
  preparation happens only in linked task worktrees based on current canonical
  source.
- A scoped task may change only its declared paths. Symlink-crossing, repository
  root claims, canonical-main scoped edits, and dirt present before acquisition
  fail closed.
- Never treat `data/`, `dist/`, logs, caches, or telemetry churn as authored source.
- Back up host-local configuration before editing it. One agent owns a host-config change until verification and restart are complete.
- Update an existing `#JAIMES:` handoff note instead of stacking competing instructions.
- Publish the owning agent's dashboard-safe objective and phase changes.

## Verify And Handoff

1. In a scoped worktree, run focused tests, commit only claimed paths, and call
   `scoped_change_guard.py prepare`. It refuses dirty worktrees and commits that
   touch paths outside the claim. A clean no-change cancellation may call
   `abort`; changed or committed abandoned work must be preserved and adopted,
   never silently reset.
2. Serialize only integration: acquire the exclusive canonical guard, fetch,
   integrate the prepared commit, rerun applicable tests, and push. Never
   force-push over another agent.
3. If the remote advanced, reconcile both histories and rerun tests.
4. Before a source push, record Josh's explicit approval with `approve-push --token <own-token> --approval-ref "<approval reference>"`; then push or create a tracked handoff before declaring completion.
5. Call the exclusive guard's `finish --token <own-token>` only after
   verification passes and local `main` equals `origin/main`.
6. On every failure, cancellation, or interruption, preserve evidence and
   release only the token you own through its matching guard. Never delete a
   dirty scoped worktree to clear a claim.
7. Recover an expired scoped lease only when its owner is absent and the task
   worktree is clean at its base commit. Recover an exclusive lease only under
   its existing clean canonical rules.
8. Publish a terminal task state only after both guards report no matching
   lease, `control_tower_change_guard.py status` reports `lease: null`, the
   exact task-bound source-closeout receipt exists, and
   a fresh `ecosystem_edit_preflight.py --fetch` reports `ok: true`. `agent_task.py`
   rejects done, blocked, error, and cancelled transitions for shared-source tasks
   until this evidence is satisfied. Record shared-memory outcome feedback when
   retrieved memory materially influenced the result.

Do not begin from a stale chat summary when current Git, lease, task, or handoff state is available.

## Immutable Deployments

- Never deploy or load a service from a mutable working directory.
- Create a bounded manifest from an exact commit with
  `immutable_deploy_bundle.py create --repo <repo> --repo-key <key> --revision <commit> --include <path> --output <manifest>`.
- Verify it immediately before transport or activation. Materialize into a new
  versioned directory; never overwrite a live release in place.
- Unrelated working-tree dirt may coexist because manifest creation reads Git
  objects only. Missing files, selector expansion drift, symlinks, submodules,
  mode drift, content-hash drift, and bundle-hash drift fail closed.
- Keep same-service activation, migrations, shared lockfiles, and rollback-link
  changes single-owner even when source preparation was concurrent.

#JAIMES: shared edits bind task, work, and run identity to an explicit success, abort, or clean expired-orphan receipt so terminal tasks cannot silently wedge the canonical checkout.
