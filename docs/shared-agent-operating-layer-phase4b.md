# Shared Agent Operating Layer - Phase 4B

Phase 4B makes the shared layer operational for reliability work:

- `scripts/agent_route.py` chooses an agent owner from task type, capabilities, privacy tier, and approval state.
- `scripts/agent_heartbeat.py` writes heartbeat rows and can flag stale agents.
- `scripts/capability_inventory.py` collects dashboard-safe node inventory, including local model names, cron counts, wrapped cron counts, and visible service hints.
- `scripts/gemini_agent.py` checks the local Gemini CLI and can run dashboard-safe smoke prompts without writing raw prompts or model output into Mission Control sidecars.
- `scripts/agent_cron_wrap.py` wraps exactly one matching crontab line with `agent_job_wrap.sh`, writes a backup, and records the rollout in `data/automation-rollout.json`.

## Approval Gates

The routing policy sends risky work to JOSHeX unless the task is explicitly approved. Risky work includes destructive maintenance, auth-cookie refresh, account mutation, posting side effects, raw private data forwarding, and secret handling. Agents should not self-approve these tasks.

## Automation Wrapping Policy

Wrap automations one at a time. For each job:

1. choose a low-risk monitoring or status job first;
2. run `agent_cron_wrap.py` in dry-run mode;
3. apply only if exactly one active line matches;
4. verify `crontab -l` contains `agent_job_wrap.sh`;
5. let the next scheduled run prove the Brain Feed and Today Jobs loop.

The wrapper preserves command exit code and publishes start/done/error status. Do not bulk-wrap high-frequency or account-mutating jobs until the first wrapped job on that node has run cleanly.

## Agent Responsibilities

JOSHeX owns sensitive connectors, approvals, routing policy, repo changes, and dashboard validation.

Josh 2.0 owns host operations, Mission Control service health, OpenCLAW support, and local worker health checks.

JAIMES owns Hermes, reporting, ML workflows, specialist summaries, and non-sensitive analysis.

J.A.I.N owns monitors, intelligence scans, watchlists, background workers, and recurring cron-heavy automation.

Gemini is a shared specialist model layer. JOSHeX owns Gemini brokering and approvals, Josh 2.0 can report Gemini CLI health, JAIMES can use Gemini only as an evaluator for non-sensitive specialist summaries, and J.A.I.N can use it for scheduled dashboard-safe digests.

## Current Rollout

Phase 4B begins with one wrapped job on Josh 2.0 and one wrapped job on JAIMES/J.A.I.N. Remaining jobs stay visible as adoption backlog until they are wrapped deliberately.
