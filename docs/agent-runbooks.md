# Agent Runbooks

## JOSHeX / Personal Codex

Owns sensitive account connectors, approval decisions, Mission Control code, dashboard validation, task routing, and cross-agent handoffs.

Must publish meaningful work to:

- Brain Feed as agent `joshex`
- Today Jobs for automation or substantial execution
- `data/personal-codex.json` for local dashboard context

When delegating work to another agent, use `scripts/agent_delegate.py` rather than only sending an out-of-band message. The delegate wrapper publishes the JOSHeX request, writes/syncs the task queue, and asks the receiving host to publish its own Brain Feed receipt. Example:

```bash
python3 scripts/agent_delegate.py --to josh --title "Check kiosk health" --objective "Confirm the Josh 2.0 Mission Control kiosk is reachable and current." --job
```

Must not put secrets, raw connector payloads, private account contents, OAuth payloads, tokens, or raw emails into dashboard-visible stores.

Recovery:

- Run `scripts/agent_heartbeat.py write --agent joshex --node macbook-codex --status ok --summary "..."`
- Run `scripts/update_mission_control.py`
- Validate with `scripts/mission_control_regression_check.py`

## Josh 2.0

Owns Mission Control hosting, Brain Feed server health, local OpenCLAW services, Josh-side crons, and host operations.

Use:

- `scripts/agent_publish.py --agent josh`
- `scripts/agent_job_wrap.sh josh ...`
- `scripts/agent_task.py start --agent josh --id <task-id> --brain-feed --job`
- `scripts/agent_task.py list --owner josh`
- `scripts/agent_heartbeat.py write --agent josh --node josh2-lan`

On receipt of a delegated instruction, Josh 2.0 should publish receipt immediately, then use `agent_task.py start` and `agent_job_wrap.sh` for execution so the Josh tile moves from received to active to done/error.

Do not perform destructive maintenance, auth refresh, account mutation, or sensitive account action without an approved task.

Recovery:

- Check wrapped cron logs under `~/.openclaw/workspace/logs/`
- Publish a heartbeat after recovery
- Keep Mission Control data refreshed

## JAIMES

Owns Hermes jobs, reports, Sorare ML, specialist background analysis, and model-heavy summaries.

Use:

- `scripts/agent_publish.py --agent jaimes`
- `scripts/agent_job_wrap.sh jaimes ...`
- `scripts/agent_task.py start --agent jaimes --id <task-id> --brain-feed --job`
- `scripts/agent_task.py list --owner jaimes`
- `scripts/agent_heartbeat.py write --agent jaimes --node jaimes-via-josh`

On receipt of a delegated instruction, JAIMES should publish receipt immediately, then use `agent_task.py start` and `agent_job_wrap.sh` for execution so the JAIMES tile moves from received to active to done/error.

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
- `scripts/agent_task.py start --agent jain --id <task-id> --brain-feed --job`
- `scripts/agent_task.py list --owner jain`
- `scripts/agent_heartbeat.py write --agent jain --node jaimes-via-josh`

On receipt of a delegated instruction, J.A.I.N should publish receipt immediately, then use `agent_task.py start` and `agent_job_wrap.sh` for execution so the J.A.I.N tile moves from received to active to done/error.

Do not publish public posts, mutate accounts, bulk-wrap high-frequency jobs, or touch sensitive account actions unless explicitly approved.

Recovery:

- Check the specific job log first
- Publish `blocked` only when Josh must approve or fix something
- Use heartbeat status `ok`, `degraded`, or `blocked` to keep Mission Control honest
