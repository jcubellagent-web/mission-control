# Shared Agent Operating Layer - Phase 3

Phase 3 makes adoption measurable and repeatable.

## Goal

Every node should have:

- `mission-control/scripts/agent_publish.py`
- `mission-control/scripts/agent_job_wrap.sh`
- top-level shims in `~/.openclaw/workspace/scripts/`
- Phase 1/2/3 docs
- schemas
- a compliance report in Mission Control

## Compliance Check

Run from a Mission Control checkout:

```bash
python3 scripts/shared_layer_adoption_check.py --label "JOSH 2.0" --agent josh --workspace ~/.openclaw/workspace
```

The check writes:

```text
data/shared-layer-adoption.json
```

Mission Control reads this through the Shared OS block.

## Wrapping Jobs

Use the wrapper around safe jobs first:

```bash
scripts/agent_job_wrap.sh josh "Mission Control Refresh" update_and_push.sh "Refresh dashboard data" -- /bin/zsh scripts/update_and_push.sh
```

The wrapper publishes:

- active at start
- done on success
- error on non-zero exit

It preserves the original command exit code.

## Adoption Order

1. Mission Control refresh and dashboard health jobs.
2. Non-sensitive health checks.
3. Report generation.
4. Intelligence scans.
5. Specialist/Hermes jobs.
6. Sensitive connector jobs only after confirming dashboard-safe summaries.

## Do Not Auto-Wrap Blindly

Do not rewrite crontabs in bulk. Some jobs rely on exact shell environment, redirects, working directory, launchd behavior, or auth context.

For each job:

1. Confirm owner lane.
2. Confirm command works manually.
3. Wrap one job.
4. Confirm exit code, logs, Brain Feed, and Today Jobs.
5. Move to the next job.
