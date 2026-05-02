# Historical Handoff: JAIMES Mission Control Data-Layer Readiness

> Superseded operational context: for current work, "Mission Control" means the Josh 2.0 React kiosk at `http://127.0.0.1:5174/` from `v2-react/`. This handoff is historical migration context only; do not use its static route references as the current operator surface.

- Time: 2026-05-01T02:45:00Z
- From: JOSHeX
- To: JAIMES
- Status: ready for dashboard-safe review work
- Privacy: dashboard-safe only

## Objective

JAIMES should support the Mission Control dashboard-safe data layer by reviewing architecture, validating status visibility, and preparing specialist/evaluator feedback. JAIMES should not handle private service keys, raw connector payloads, OAuth data, account mutation, or browser-authenticated workflows.

## Current State

- Mission Control dashboard-safe dual-write is activated.
- Current Mission Control operator surface is the React kiosk at `http://127.0.0.1:5174/`.
- `index.html` and `v2/index.html` are legacy rollback/proof surfaces.
- v2 status/events are readable through dashboard-safe policies.
- React migration has started and is now the active display path.

## What JAIMES Should Do

1. Confirm local helper health:

```bash
cd ~/.openclaw/workspace/mission-control
python3 scripts/agent_heartbeat.py write --agent jaimes --node jaimes-via-josh --status ok --summary "JAIMES ready for Mission Control data-layer review"
```

2. Read the v2 docs:

```bash
sed -n '1,220p' docs/mission-control-v2.md
```

3. Run read-only v2 verification:

```bash
python3 scripts/mc_v2_verify.py --agent joshex
```

4. Prepare a dashboard-safe evaluator note covering:

- whether the v2 data model is enough for JAIMES/Hermes report status;
- which JAIMES outputs should become dashboard-safe jobs/events;
- what should stay out because it is private or account-sensitive;
- what React modules would help JAIMES operators most.

5. Publish only dashboard-safe progress:

```bash
python3 scripts/agent_publish.py --agent jaimes --brain-feed --type status --status active --title "Mission Control JAIMES data review" --tool "JAIMES evaluator" --detail "Reviewing dashboard-safe Mission Control data model and JAIMES/Hermes visibility needs."
```

## Do Not

- Do not touch `SUPABASE_SERVICE_ROLE_KEY`.
- Do not edit v2 RLS policies.
- Do not publish raw reports, raw account data, raw emails, OAuth payloads, secrets, or private connector contents.
- Do not treat J.A.I.N cron/monitor work as JAIMES work unless assigned.
- Do not cut over any kiosk or production route.
