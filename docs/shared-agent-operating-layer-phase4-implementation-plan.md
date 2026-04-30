# Shared Agent Operating Layer - Phase 4 Implementation Plan

Phase 4 turns the shared operating layer into an execution system: task queue, capability registry, permission tiers, orchestration helpers, and agent-specific wiring.

## Objective

Give JOSHeX, JOSH 2.0, JAIMES, and J.A.I.N a common way to:

- declare what they can do
- accept or decline tasks
- publish progress
- hand off work
- enforce privacy/approval tiers
- leave artifacts in predictable locations
- show current work and stale tasks in Mission Control

## Non-Negotiables

- No secrets, raw connector data, OAuth payloads, raw emails, or private account contents in dashboard-visible stores.
- No bulk crontab rewrites.
- No destructive commands without explicit Josh approval.
- Every meaningful task gets start/done/error/blocked visibility.
- JOSHeX/Codex work publishes to `--agent joshex`, not Josh's lane.

## Deliverables

### 1. Capability Registry

Files:

- `data/agent-capabilities.json`
- `schemas/agent-capability.schema.json`

Purpose:

- Define each agent's hardware, tools, model access, repos, services, safe commands, restricted commands, privacy tier, and preferred work types.

Initial agents:

- `joshex`: sensitive coordination, repo edits, Mission Control patches, connected account triage, planning.
- `josh`: dashboard host, fast local ops, OpenCLAW services, Brain Feed server, health checks.
- `jaimes`: Hermes/specialist workflows, Sorare ML, fantasy workflows, reports, specialist background jobs.
- `jain`: scheduled workers, intelligence scans, X/watchlist monitors, cron-heavy automation.

Acceptance criteria:

- Mission Control can show capability counts/status.
- Each agent has a machine-readable lane, owner, and allowed task classes.

### 2. Task Queue

Files:

- `data/agent-task-queue.json`
- `schemas/agent-task.schema.json`
- `scripts/agent_task.py`

Task fields:

- id
- title
- objective
- owner agent
- requester
- status
- priority
- privacy tier
- approval tier
- required capabilities
- dependencies
- due time
- artifact paths
- created/updated timestamps
- completion summary

Statuses:

- `queued`
- `accepted`
- `active`
- `blocked`
- `done`
- `cancelled`
- `error`

Acceptance criteria:

- Any agent can create, accept, update, block, complete, and hand off a task through `agent_task.py`.
- Task updates also write shared events and optionally Brain Feed.
- Mission Control can show active/blocked/queued tasks.

### 3. Permission Tiers

Files:

- `data/permission-tiers.json`
- included in task schema

Tiers:

- `dashboard-safe`: safe operational summary.
- `agent-private`: operational detail not intended for dashboard.
- `josh-approval`: requires Josh approval before execution.
- `sensitive-account`: only minimum necessary summary can be forwarded to worker agents.
- `destructive`: never execute without explicit approval.

Acceptance criteria:

- `agent_task.py` refuses unsafe task creation when required approval metadata is missing.
- Mission Control shows approval-needed tasks without leaking content.

### 4. Orchestration Helper

Files:

- `scripts/agent_task.py`
- optional later: `scripts/agent_run.py`

Commands:

```bash
python3 scripts/agent_task.py create --owner jaimes --title "..." --objective "..." --privacy dashboard-safe
python3 scripts/agent_task.py accept --id TASK_ID --agent jaimes
python3 scripts/agent_task.py update --id TASK_ID --status active --note "..."
python3 scripts/agent_task.py block --id TASK_ID --reason "..."
python3 scripts/agent_task.py complete --id TASK_ID --summary "..." --artifact path/to/report.md
python3 scripts/agent_task.py handoff --id TASK_ID --to jain --reason "..."
```

Acceptance criteria:

- Every command appends to `shared-events.json`.
- Status changes can publish Brain Feed when requested.
- Jobs/tasks can be tied to Today Jobs.

### 5. Mission Control UI

Add to Today Jobs / Shared OS:

- active task count
- blocked task count
- queued task count
- tasks by owner
- approval-needed count
- stale task count

Add detail modal later:

- task queue table
- owner
- status
- latest event
- artifact links

Acceptance criteria:

- The main dashboard can answer: "what is each agent doing, what is waiting, what is blocked, what needs Josh?"

### 6. Freshness and SLA Monitors

Files:

- extend `scripts/shared_layer_adoption_check.py`
- add `scripts/shared_layer_freshness_check.py`

Rules:

- active task without update for N minutes => stale
- accepted task with no start => stale
- handoff open too long => attention
- active Brain Feed lane stale => attention
- job start without completion => attention

Acceptance criteria:

- Mission Control Action Required only shows actionable issues.
- No noisy alerts for normal progress.

### 7. Agent Wiring

#### JOSHeX / Codex

Install/use:

- `agent_publish.py`
- `agent_task.py`
- `agent_job_wrap.sh`

Responsibilities:

- create tasks
- route sensitive work
- update `joshex` Brain Feed
- write decisions
- validate dashboard changes
- keep Mission Control accurate

Required behavior:

- Use `--agent joshex --brain-feed` for all meaningful ecosystem work.
- Use task queue for work that another agent should own.

#### JOSH 2.0

Install/use:

- Mission Control helper scripts
- top-level shims in `~/.openclaw/workspace/scripts/`
- task queue reader/writer

Responsibilities:

- dashboard hosting
- local services
- OpenCLAW health
- Brain Feed server
- safe local ops
- first low-risk cron wrapping

Initial wrapping candidates:

- Mission Control refresh
- Brain Feed server keepalive
- context watchdog/health checks

Do not initially wrap:

- auth-sensitive Sorare cookie refresh
- destructive maintenance
- anything with fragile shell environment

#### JAIMES

Install/use:

- Mission Control helper scripts
- top-level shims
- task queue reader/writer
- Hermes-aware wrappers

Responsibilities:

- Hermes jobs
- Sorare ML
- fantasy/specialist workflows
- reports
- specialist analysis tasks

Initial wrapping candidates:

- JAIMES health check
- weekly report
- non-sensitive report generation
- Hermes status jobs

Do not initially wrap:

- browser-auth workflows
- account mutation jobs
- connector-sensitive jobs

#### J.A.I.N

Install/use:

- JAIMES workspace helper/shims
- wrapper from `~/.openclaw/workspace/scripts/agent_job_wrap.sh`

Responsibilities:

- scheduled workers
- intelligence scans
- X/watchlist monitors
- background cron execution

Initial wrapping candidates:

- error rate monitor
- log rotation
- health checks
- watchlist monitor after one manual test

Do not initially wrap:

- high-frequency jobs in bulk
- jobs with posting/account side effects until wrapper behavior is verified

## Rollout Order

### Phase 4A - Foundation

1. Add schemas and seed data.
2. Build `agent_task.py`.
3. Add Mission Control task summary.
4. Validate locally.
5. Sync to Josh 2.0 and JAIMES/J.A.I.N.

### Phase 4B - Wiring

1. Install task helper shims on both minis.
2. Smoke-test task create/update/complete from each node.
3. Publish completion to each agent's Brain Feed lane.
4. Add adoption report fields for task helper readiness.

### Phase 4C - Safe Automation Wrapping

1. Wrap one Josh 2.0 low-risk job.
2. Verify exit code, logs, Today Jobs, Brain Feed, and dashboard.
3. Wrap one JAIMES low-risk job.
4. Wrap one J.A.I.N low-risk job.
5. Stop and review before broad rollout.

### Phase 4D - Freshness Monitors

1. Add stale task detection.
2. Add open handoff aging.
3. Add start-without-finish detection.
4. Wire actionable alerts.

## Validation

Required before completion:

```bash
PYTHONPYCACHEPREFIX=/tmp/mission-control-pycache python3 -m py_compile scripts/agent_publish.py scripts/agent_task.py scripts/update_mission_control.py
python3 scripts/mission_control_regression_check.py
python3 scripts/mission_control_visual_canaries.py
python3 scripts/shared_layer_adoption_check.py --json
```

Remote checks:

```bash
ssh josh2-lan 'cd ~/.openclaw/workspace/mission-control && /opt/homebrew/bin/python3 scripts/agent_task.py list'
ssh jaimes-via-josh 'cd ~/.openclaw/workspace/mission-control && /opt/homebrew/bin/python3 scripts/agent_task.py list'
```

## Completion Criteria

Phase 4 is complete when:

- all agents have capability records
- all agents can create/update/complete tasks
- Mission Control shows task queue summary
- Josh 2.0 and JAIMES/J.A.I.N have task helper/shims installed
- at least one safe task lifecycle has been tested from each node
- no secrets appear in dashboard-visible data
- Brain Feed lanes update correctly for task lifecycle events

## Known Dependencies

- SSH reachability to both minis.
- Supabase publishable key remains valid.
- Mission Control repo paths remain stable.
- Josh approval before wrapping sensitive or fragile automations.
- Local `gog` calendar auth still needs separate refresh.
