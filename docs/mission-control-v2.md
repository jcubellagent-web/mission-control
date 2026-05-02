# Mission Control v2

Mission Control v2 is an additive migration, not a replacement cutover.

Rollback anchor at v2 start:

- Branch created from: `a56c37caa`
- Working branch: `codex/mission-control-v2`
- v1 entrypoint remains: `index.html`
- v2 entrypoint: `v2/index.html`

## Direction

Keep the custom agent operating layer:

- JOSHeX / Personal Codex coordination
- Josh 2.0 host operations
- JAIMES / Hermes specialist work
- J.A.I.N monitor and scheduled worker lanes
- approval gates, privacy tiers, local execution, and final accountability

Move commodity app infrastructure to platform-backed layers:

- canonical status and event state in Supabase
- realtime reads from clients
- durable job, approval, handoff, and audit tables
- focused web/mobile operator surfaces

## First Slice

The first v2 slice adds:

- `schemas/mission-control-v2.sql`: Supabase table and RLS contract
- `scripts/mc_v2_publish.py`: dashboard-safe v2 publisher
- `scripts/mc_v2_verify.py`: read-only v2 row verifier
- `v2/index.html`: operator surface that reads v2 Supabase tables when configured
- local fallback mode that reads existing v1 sidecars until v2 tables are installed

## Install Order

1. Keep v1 running.
2. Apply `schemas/mission-control-v2.sql` in Supabase.
3. Verify reads from `v2/index.html` in fallback mode.
4. Run a dry-run publisher check:

```bash
python3 scripts/mc_v2_publish.py --agent joshex --type status --status active --title "v2 smoke" --detail "dashboard-safe smoke" --dry-run
```

5. Run a real publisher check after the schema exists and the server-side key is available in the shell environment:

```bash
export SUPABASE_SERVICE_ROLE_KEY=...
python3 scripts/mc_v2_publish.py --agent joshex --type status --status active --title "v2 smoke" --detail "dashboard-safe smoke"
```

6. Enable dual-write from the existing v1 publisher when ready:

```bash
export SUPABASE_SERVICE_ROLE_KEY=...
export MISSION_CONTROL_V2_DUAL_WRITE=1
python3 scripts/agent_publish.py --agent joshex --type status --status active --title "dual-write smoke" --detail "dashboard-safe smoke" --brain-feed
```

You can also use `--v2` on a single `agent_publish.py` call without setting the environment flag.

On Josh 2.0, the preferred secret location is outside the repo:

```bash
~/.openclaw/workspace/secrets/mission-control-v2.env
```

It should contain only:

```bash
SUPABASE_SERVICE_ROLE_KEY=...
```

After that file exists, run:

```bash
scripts/mc_v2_dual_write_smoke.sh
```

Or let the Josh 2.0 watcher activate it automatically:

```bash
scripts/mc_v2_activation_watch.sh
```

The watcher is safe to run repeatedly. It waits for the private env file, runs the smoke once, then writes:

```bash
~/.openclaw/workspace/state/mission-control-v2-dual-write.ok
```

The activation log is:

```bash
~/.openclaw/workspace/logs/mission-control-v2-activation.log
```

On Josh 2.0, a LaunchAgent can run the watcher every five minutes:

```bash
~/Library/LaunchAgents/com.josh20.mission-control-v2-activation.plist
```

When the private key file appears, the next watcher run should activate v2 dual-write and write the sentinel.

The smoke script performs both sides of the proof:

- v1 Brain Feed publish still succeeds.
- v2 `mc_v2_agent_status` and `mc_v2_events` rows are visible through read-only dashboard policies.

Read-only verification can also be run manually:

```bash
python3 scripts/mc_v2_verify.py --agent joshex --expect-title "v2 dual-write smoke"
```

The next workflow proof covers JAIMES jobs and handoffs:

```bash
scripts/mc_v2_job_handoff_smoke.sh
```

That smoke writes a dashboard-safe JAIMES job, a JAIMES handoff event to JOSHeX,
and a pending approval inbox row, then verifies all three through the read-only
v2 dashboard policy:

```bash
python3 scripts/mc_v2_verify.py \
  --agent jaimes \
  --expect-title "JAIMES v2 handoff smoke" \
  --expect-job-title "JAIMES v2 job smoke" \
  --expect-handoff-title "JAIMES v2 handoff smoke" \
  --expect-approval-title "JAIMES v2 handoff smoke"
```

7. Point `v2/config.local.js` at the publishable Supabase URL/key for browser reads, or let v2 continue in local fallback mode.

## Safety Rules

- Do not store secrets or raw private connector payloads in v2 tables.
- Do not add anonymous write policies.
- Publishers write dashboard-safe summaries only and require `SUPABASE_SERVICE_ROLE_KEY` outside browser code.
- Keep `index.html` as the v1 rollback surface until v2 proves the full agent lifecycle.
- Do not cut over Josh 2.0 kiosk until v2 reads status, jobs, approvals, and recent events correctly.
- `agent_publish.py` remains v1-first. v2 mirroring is opt-in through `--v2` or `MISSION_CONTROL_V2_DUAL_WRITE=1`.

## Josh 2.0 Deployment Shape

For now, deploy v2 beside v1 on Josh 2.0:

- existing v1 URL stays unchanged
- v2 is served from `/v2/index.html`
- current cron/update loop remains unchanged
- v2 publisher runs manually or from wrappers after schema validation
- wrappers and heartbeats inherit v2 dual-write when `MISSION_CONTROL_V2_DUAL_WRITE=1`; `agent_heartbeat.py write` also accepts `--v2`

Cutover is only appropriate after v2 dual-write has run cleanly for Brain Feed status, jobs, heartbeats, and task routing with regression coverage.
