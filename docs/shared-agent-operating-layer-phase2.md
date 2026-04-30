# Shared Agent Operating Layer - Phase 2

Phase 2 turns the event ledger into durable shared knowledge and coordination.

## New Stores

- `data/decisions.json`: accepted/superseded/rejected coordination decisions.
- `data/knowledge-index.json`: dashboard-safe index of important docs and runbooks.
- `data/handoff-queue.json`: structured handoffs between JOSHeX, JOSH 2.0, JAIMES, and J.A.I.N.
- `data/daily-rollup.json`: generated summary of today's events, jobs, decisions, handoffs, and blockers.

## Dashboard

Today Jobs now includes a compact Shared OS block:

- Events today
- Decision count
- Knowledge entry count
- Open handoff count

The Shared Ledger remains underneath it for recent cross-agent events.

## Publishing Contract

Use `scripts/agent_publish.py` for all dashboard-safe coordination writes.

Status:

```bash
python3 scripts/agent_publish.py --agent josh --type status --status active --title "..." --tool "..." --detail "..." --brain-feed
```

Decision:

```bash
python3 scripts/agent_publish.py --agent jaimes --type decision --status done --title "..." --tool hermes --detail "..." --tag sorare --tag routing --rollup
```

Handoff:

```bash
python3 scripts/agent_publish.py --agent joshex --type handoff --status done --title "..." --tool codex --detail "..." --handoff-to jaimes --rollup
```

Job:

```bash
python3 scripts/agent_publish.py --agent jain --type job --status done --title "..." --tool cron --detail "..." --job --rollup
```

## Freshness Rules

Mission Control marks the shared layer:

- `ready` when recent dashboard-safe events exist and no open handoffs/blockers are present.
- `attention` when open handoffs or blocked/error events are present.
- `stale` when no recent shared event was published.

## Adoption Path

1. Use direct helper calls manually.
2. Wrap cron/Hermes/OpenCLAW jobs with helper calls at start and finish.
3. Add blocked/error calls on failure paths.
4. Use decision events for durable architecture or routing choices.
5. Use handoff events whenever another agent owns the next step.

## Boundary

Phase 2 still avoids secrets and raw connector contents. This is operational knowledge, not a private-data warehouse.
