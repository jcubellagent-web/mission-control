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
3. Stop if the result reports remote divergence, source changes, or another lease owner. Coordinate or hand off instead of overwriting.
4. Acquire the exclusive lease:
   `python3 scripts/control_tower_change_guard.py begin --agent <agent> --objective "<specific change>"`

## Apply

- Canonical shared source is the Josh 2.0 `mission-control` checkout on `main`.
- Edit source, prompts, configs, and skills only from the current canonical version.
- Never treat `data/`, `dist/`, logs, caches, or telemetry churn as authored source.
- Back up host-local configuration before editing it. One agent owns a host-config change until verification and restart are complete.
- Update an existing `#JAIMES:` handoff note instead of stacking competing instructions.
- Publish the owning agent's dashboard-safe objective and phase changes.

## Verify And Handoff

1. Run focused tests plus the canonical change-guard verification.
2. Commit only intentional source files. Fetch/rebase before push; never force-push over another agent.
3. If the remote advanced, reconcile both histories and rerun tests.
4. Push the source commit or create a tracked handoff before declaring completion.
5. Finish the lease and publish completion. Record shared-memory outcome feedback when retrieved memory materially influenced the result.

Do not begin from a stale chat summary when current Git, lease, task, or handoff state is available.
