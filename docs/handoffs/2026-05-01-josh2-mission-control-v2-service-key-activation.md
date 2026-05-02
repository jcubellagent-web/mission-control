# Historical Handoff: Mission Control Data-Layer Service Key Activation

> Superseded operational context: for current work, "Mission Control" means the Josh 2.0 React kiosk at `http://127.0.0.1:5174/` from `v2-react/`. This handoff is historical migration context only; do not use its static route references as the current operator surface.

- Time: 2026-05-01T01:44:17Z
- From: JOSHeX
- To: Josh 2.0
- Status: blocked until private service key is present
- Privacy: dashboard-safe instructions only

## Objective

Activate the first real Mission Control dashboard-safe dual-write publisher on Josh 2.0 without weakening Supabase RLS or changing the active kiosk path.

## Current State

- Current Mission Control operator surface is the React kiosk at `http://127.0.0.1:5174/`.
- `index.html` and `v2/index.html` remain available only as legacy rollback/proof surfaces.
- v2 schema is installed in Supabase.
- `agent_publish.py` supports optional v2 mirroring through `--v2` or `MISSION_CONTROL_V2_DUAL_WRITE=1`.
- `scripts/mc_v2_dual_write_smoke.sh` is installed on Josh 2.0.
- `scripts/mc_v2_verify.py` verifies readable v2 rows after a smoke publish.

## Required Private Step

Create this file on Josh 2.0:

```bash
/Users/josh2.0/.openclaw/workspace/secrets/mission-control-v2.env
```

It must contain:

```bash
SUPABASE_SERVICE_ROLE_KEY=...
```

Do not put the key in the repo, Brain Feed, Personal Codex, dashboard data, shared events, handoff docs, or chat.

## Activation Command

After the private env file exists:

```bash
cd /Users/josh2.0/.openclaw/workspace/mission-control
scripts/mc_v2_dual_write_smoke.sh
```

The watcher can also be run repeatedly and will activate once the key is present:

```bash
cd /Users/josh2.0/.openclaw/workspace/mission-control
scripts/mc_v2_activation_watch.sh
```

A Josh 2.0 LaunchAgent is installed to run that watcher every five minutes:

```bash
~/Library/LaunchAgents/com.josh20.mission-control-v2-activation.plist
```

Watcher log:

```bash
~/.openclaw/workspace/logs/mission-control-v2-activation.log
```

Expected result:

- `agent_publish.py` publishes the normal v1 Brain Feed update.
- `mc_v2_publish.py` writes the dashboard-safe status and event rows.
- `mc_v2_verify.py` confirms the v2 rows are readable.
- `~/.openclaw/workspace/state/mission-control-v2-dual-write.ok` exists after watcher success.

## Do Not

- Do not add anonymous write policies to Mission Control tables.
- Do not use the browser publishable key for server writes.
- Do not cut over the kiosk to legacy static routes.
- Do not store private raw connector/account data in Mission Control tables.
