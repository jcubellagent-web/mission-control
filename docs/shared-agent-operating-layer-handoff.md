# Shared Agent Operating Layer Handoff

Audience: JOSH 2.0, JAIMES, J.A.I.N, and JOSHeX.

Mission Control now has a shared dashboard-safe operating ledger. Use it for important work so every agent can see what happened, what is active, what completed, and which lane owns it.

## Required Habit

Before meaningful work:

```bash
python3 scripts/agent_publish.py --agent <josh|jaimes|jain|joshex> --type status --status active --title "..." --tool "..." --detail "..." --brain-feed
```

After meaningful work:

```bash
python3 scripts/agent_publish.py --agent <josh|jaimes|jain|joshex> --type complete --status done --title "..." --tool "..." --detail "..." --brain-feed
```

For automations or manual jobs that should appear in Today Jobs:

```bash
python3 scripts/agent_publish.py --agent <lane> --type job --status done --title "..." --tool "..." --detail "..." --job
```

Regenerate Mission Control after local ledger/job changes:

```bash
python3 scripts/update_mission_control.py
```

## Correct Lane

- JOSH 2.0: host services, dashboard serving, OpenCLAW services, Josh-side crons, keepalives.
- JAIMES: Hermes and specialist work, Sorare ML, fantasy/specialist workflows, JAIMES-owned reports.
- J.A.I.N: scheduled worker jobs, intelligence scans, X/watchlist monitoring, background automation.
- JOSHeX: Codex/Personal Codex work, Mission Control patches, validation, docs, sidecar syncs, coordination.

Do not publish JOSHeX work to the Josh lane just because Josh hosts the dashboard.

## Files

- `data/shared-events.json`: shared event ledger.
- `data/codex-jobs.json`: top Codex Automations area in Today Jobs.
- `schemas/shared-agent-event.schema.json`: event contract.
- `scripts/agent_publish.py`: single helper for status, jobs, decisions, handoffs, and Brain Feed publishing.
- `docs/shared-agent-operating-layer-phase1.md`: full Phase 1 guide.

## Safety

Only publish dashboard-safe summaries. Never include secrets, tokens, raw emails, OAuth payloads, private connector output, or raw sensitive account content.
