# Agent Runbooks

## Linear Durable Work Tracking

Use Linear for durable agent-ecosystem work, not as a copy of Control Tower. The shared project is `Agent Ecosystem` in team `Jcubellagent` (`JCU`).

Create or reuse an issue when work is an approved enhancement, a confirmed bug or regression, an approved proposal, a multi-session initiative, or an unresolved follow-up with an owner. Skip instant answers, routine health checks, successful scheduled jobs, heartbeats, Telegram delivery events, live-card edits, transient telemetry, and self-healed noise.

Before creating:

1. Read `config/linear-integration.json`.
2. Search Linear for the Control Tower `workId` or proposal ID.
3. Reuse the existing issue when found.
4. Select exactly one `Agent` label and one `Area` label; Linear label groups are mutually exclusive.
5. Include dashboard-safe acceptance criteria and the stable Control Tower reference.

For new qualifying work, use the explicit task bridge:

```bash
python3 scripts/agent_task.py create --owner <agent> \
  --title "<safe title>" --objective "<safe objective>" \
  --durable --area "<Area>" \
  --acceptance-criterion "<criterion>"
```

The same flags work through `agent_delegate.py`. A durable boundary creates a sanitized pending intent; heartbeats and non-durable tasks do not. On a noncanonical host, run `python3 scripts/linear_work_intent.py flush-local` first to replay any fail-open transport spool. Then list the owning lane with `python3 scripts/linear_work_intent.py pending --route-to <agent>`, claim the intent for the connected agent, search/upsert the issue using the connected Linear tool, and persist the stable reference with `linear_work_intent.py ack --intent-id <intent> --claim-token <claim> --issue-id <JCU-n> --verified-work-id <workId>`. Every J.A.I.N durable lifecycle boundary refreshes one stable, non-durable JAIMES connector task keyed to the source `workId`; it always resolves the latest intent instead of retaining a superseded ID. Connector failure is recorded with the same claim plus a machine-safe error code and retried without blocking the underlying task.

For a confirmed existing task created before this bridge, enable tracking once with `agent_task.py track --id <task> --agent <agent> --area <Area> --acceptance-criterion <criterion>`. Reconcile latest state first; never bulk-project raw queue history.

Lifecycle mapping:

- Accepted, planned, or routed: `Todo`
- Active: `In Progress`
- Verifying: `In Review`
- Done: `Done`
- Cancelled: `Canceled`
- Blocked: remain `In Progress` and record the durable blocker; waiting for a source lease is coordination state, not a Josh-facing blocker

JOSHeX, Josh 2.0, and JAIMES have verified connector access. J.A.I.N asks the JAIMES Codex lane to perform a Linear write when needed. If a connector is temporarily unavailable, continue safe execution and preserve the stable work reference in Control Tower rather than creating a duplicate issue later.

## JOSHeX / Personal Codex

Owns sensitive account connectors, approval decisions, Control Tower code, dashboard validation, task routing, and cross-agent handoffs.

Must publish meaningful work to:

- Live Work Board as agent `joshex`
- Today Jobs for automation or substantial execution
- `data/personal-codex.json` for local dashboard context

When delegating work to another agent, use `scripts/agent_delegate.py` rather than only sending an out-of-band message. The delegate wrapper publishes the JOSHeX request, writes/syncs the task queue, and asks the receiving host to publish its own Live Work Board receipt. Example:

```bash
python3 scripts/agent_delegate.py --to josh2 --title "Check kiosk health" --objective "Confirm the Josh 2.0 Control Tower kiosk is reachable and current." --job
```

Must not put secrets, raw connector payloads, private account contents, OAuth payloads, tokens, or raw emails into dashboard-visible stores.

Recovery:

- Run `scripts/agent_heartbeat.py write --agent joshex --node macbook-codex --status ok --summary "..."`
- Run `scripts/update_mission_control.py`
- Validate with `scripts/mission_control_regression_check.py`

## Josh 2.0

Owns Control Tower hosting, Live Work Board server health, local OpenCLAW services, Josh-side crons, and host operations.

Use:

- `scripts/agent_publish.py --agent josh2`
- `scripts/agent_job_wrap.sh josh2 ...`
- `scripts/agent_task.py start --agent josh2 --id <task-id> --job`
- `scripts/agent_task.py list --owner josh2`
- `scripts/agent_heartbeat.py write --agent josh2 --node josh2-lan`

On receipt of a delegated instruction, Josh 2.0 should publish receipt immediately, then use `agent_task.py start` and `agent_job_wrap.sh` for execution so the Josh tile moves from received to active to done/error.

`agent_task.py` publishes to Live Work Board by default. Use `--no-brain-feed` only for dry-runs, local render tests, or explicit maintenance overrides.

Do not perform destructive maintenance, auth refresh, account mutation, or sensitive account action without an approved task.

Recovery:

- Check wrapped cron logs under `~/.openclaw/workspace/logs/`
- Publish a heartbeat after recovery
- Keep Control Tower data refreshed

## JAIMES

Owns Hermes jobs, reports, Sorare ML, specialist background analysis, and model-heavy summaries.

Use:

- `scripts/agent_publish.py --agent jaimes`
- `scripts/agent_job_wrap.sh jaimes ...`
- `scripts/agent_task.py start --agent jaimes --id <task-id> --job`
- `scripts/agent_task.py list --owner jaimes`
- `scripts/agent_heartbeat.py write --agent jaimes --node jaimes-via-josh`

On receipt of a delegated instruction, JAIMES should publish receipt immediately, then use `agent_task.py start` and `agent_job_wrap.sh` for execution so the JAIMES tile moves from received to active to done/error.

`agent_task.py` publishes to Live Work Board by default. Use `--no-brain-feed` only for dry-runs, local render tests, or explicit maintenance overrides.

Do not treat J.A.I.N monitor/cron work as JAIMES work unless the task is assigned to `jaimes`.

Recovery:

- Publish a heartbeat with current report/analysis status
- Use the task queue to accept/start/complete assigned work
- Escalate browser-auth or account mutation tasks to JOSHeX

## J.A.I.N

Owns scheduled workers, intelligence scans, X/watchlist monitors, recurring checks, and background worker reports.

Use:

- `scripts/agent_publish.py --agent jain`
- `scripts/agent_job_wrap.sh jain ...`
- `scripts/agent_task.py start --agent jain --id <task-id> --job`
- `scripts/agent_task.py list --owner jain`
- `scripts/agent_heartbeat.py write --agent jain --node jaimes-via-josh`

On receipt of a delegated instruction, J.A.I.N should publish receipt immediately, then use `agent_task.py start` and `agent_job_wrap.sh` for execution so the J.A.I.N tile moves from received to active to done/error.

Do not publish public posts, mutate accounts, bulk-wrap high-frequency jobs, or touch sensitive account actions unless explicitly approved.

Recovery:

- Check the specific job log first
- Publish `blocked` only when Josh must approve or fix something
- Use heartbeat status `ok`, `degraded`, or `blocked` to keep Control Tower honest
