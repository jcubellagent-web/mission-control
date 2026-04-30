# Shared Agent Operating Layer - Phase 1

Phase 1 gives JOSHeX, JOSH 2.0, JAIMES, and J.A.I.N one dashboard-safe way to publish operational state.

## What Exists

- Shared event ledger: `data/shared-events.json`
- Event schema: `schemas/shared-agent-event.schema.json`
- Publishing helper: `scripts/agent_publish.py`
- Today Jobs Codex Automations: `data/codex-jobs.json`
- Mission Control dashboard fields:
  - `sharedEvents`
  - `codexJobs`
- Live Brain Feed rows in Supabase:
  - `josh`
  - `jaimes`
  - `jain`
  - `joshex`

## Core Rule

Use `scripts/agent_publish.py` for all important agent activity. Do not hand-edit random dashboard JSON unless debugging.

## Examples

Publish a live status to the correct Brain Feed lane:

```bash
python3 scripts/agent_publish.py \
  --agent joshex \
  --type status \
  --status active \
  --title "Updating Mission Control shared ledger" \
  --tool codex \
  --detail "Adding Phase 1 shared event visibility" \
  --brain-feed
```

Log a completed automation into Today Jobs and the shared ledger:

```bash
python3 scripts/agent_publish.py \
  --agent josh \
  --type job \
  --status done \
  --title "Regenerated Mission Control dashboard data" \
  --tool update_mission_control.py \
  --detail "Dashboard data refreshed and served from Josh 2.0" \
  --job
```

Record a decision:

```bash
python3 scripts/agent_publish.py \
  --agent jaimes \
  --type decision \
  --status done \
  --title "Keep Hermes-managed Sorare jobs in JAIMES lane" \
  --tool hermes \
  --detail "Sorare ML and specialist jobs should publish to agent=jaimes"
```

Create a handoff note:

```bash
python3 scripts/agent_publish.py \
  --agent joshex \
  --type handoff \
  --status done \
  --title "Mission Control shared ledger handoff" \
  --tool codex \
  --detail "Adopt agent_publish.py for Brain Feed and Today Jobs publishing" \
  --handoff-to jaimes
```

Then regenerate the dashboard:

```bash
python3 scripts/update_mission_control.py
```

## Lane Routing

- `--agent josh`: Josh 2.0 hosting, OpenCLAW, local services, Mission Control keepalive and publishing.
- `--agent jaimes`: JAIMES/Hermes specialist jobs, Sorare ML, fantasy workflows, Hermes-managed automations.
- `--agent jain`: J.A.I.N scheduled workers, intelligence scans, X/watchlist monitors, background crons.
- `--agent joshex`: Codex/JOSHeX patches, dashboard edits, validation, docs, sidecar syncs, coordination.

## Privacy

Only use `--privacy dashboard-safe` for Mission Control-visible activity. The helper refuses obvious secret-looking strings in dashboard-safe events.

Never publish:

- passwords
- tokens
- OAuth payloads
- raw emails
- raw private account records
- private connector contents
- sensitive customer or account data

Publish operational summaries only: objective, status, step, tool, validation, timestamp.

## Phase 1 Boundaries

This is not full autonomous orchestration yet. It is the shared contract and ledger that later orchestration will use.

Phase 1 does not:

- move secrets
- change connector permissions
- assign tasks autonomously
- replace Hermes/OpenCLAW
- create long-term semantic memory

It does make daily coordination visible, consistent, and safer.
