# Supabase Brain Feed Bridge

Mission Control uses Supabase `public.brain_feed` as the live agent bus. The existing JSON files remain as fallbacks. Publish to the intentional lane for the work owner; do not only update local JSON.

## Lane routing

| Lane | Supabase row | JSON fallback | Use for |
| --- | --- | --- | --- |
| JOSH 2.0 | `josh` | `data/brain-feed.json` | JOSH 2.0 system work, Mission Control hosting, OpenCLAW gateway/node work, local dashboard refreshes, Josh-side keepalive/service actions. |
| JAIMES | `jaimes` | `data/jaimes-brain-feed.json` | JAIMES/Hermes work, specialist tasks, Sorare ML, fantasy workflows, intelligence workflows owned by JAIMES, Hermes-managed jobs. |
| J.A.I.N | `jain` | `data/jain-brain-feed.json` | J.A.I.N worker activity, scheduled intelligence scans, X/watchlist automation, background monitors, J.A.I.N-owned cron jobs. |
| JOSHeX | `joshex` | `data/personal-codex.json` | Codex/JOSHeX work: UI patches, Mission Control edits, sidecar updates, repo changes, dashboard validation, handoff docs, Personal Codex coordination. |
| Legacy | `main` | `data/brain-feed.json` | Backward-compatible main feed only. Prefer named rows above for new work. |

Important: updating JOSH 2.0's `brain-feed.json` does not update the JOSHeX card. JOSHeX must publish to `--agent joshex`.

## Publishing

From any agent worktree with Mission Control checked out, or from the installed helper at `~/scripts/mission_control_brain_feed_publish.py`:

```bash
python3 scripts/supabase_brain_feed_publish.py \
  --agent jaimes \
  --status active \
  --tool cron \
  --cron "Sorare ML Training" \
  --objective "Training Sorare mission model" \
  --step "Started training run"
```

Agent examples:

```bash
python3 scripts/supabase_brain_feed_publish.py --agent josh --status active --tool openclaw --objective "Refreshing Mission Control display" --step "Reloaded dashboard host"
python3 scripts/supabase_brain_feed_publish.py --agent jaimes --status active --tool hermes --objective "Running Sorare ML audit" --step "Loaded projections"
python3 scripts/supabase_brain_feed_publish.py --agent jain --status active --tool monitor --objective "Running intelligence scan" --step "Scanning watchlists"
python3 scripts/supabase_brain_feed_publish.py --agent joshex --status active --tool codex --objective "Patching Mission Control UI" --step "Validating dashboard diff"
```

The helper reads the existing frontend Supabase publishable config from the repo, the current directory, or the usual `.openclaw/workspace` Mission Control paths. It can also use these environment variables:

```bash
MISSION_CONTROL_SUPABASE_URL=...
MISSION_CONTROL_SUPABASE_KEY=...
```

Do not paste private service-role keys into chat. The helper does not print credential values.

## Long-work pattern

For long work:

1. Publish at start with `--status active`.
2. Publish at major phase changes.
3. Publish at finish with `--status done`, `blocked`, or `error`.

Prefer the wrapper when wiring shell cron entries because it preserves the wrapped job's exit code and treats Brain Feed publish failures as non-fatal:

```bash
python3 scripts/cron_brain_feed_wrap.py \
  --agent jaimes \
  --cron "Sorare ML Training" \
  --objective "Training Sorare mission model" \
  --done-objective "Sorare mission model training complete" \
  --start-step "Started training run" \
  --done-step "Saved latest model artifacts" \
  -- /opt/homebrew/bin/python3 /path/to/train.py
```

Use the direct helper for in-script progress points:

```bash
python3 scripts/supabase_brain_feed_publish.py --agent jaimes --status active --tool cron --cron "Sorare ML Training" --objective "Training Sorare mission model" --step "Started training run"
python3 scripts/supabase_brain_feed_publish.py --agent jaimes --status done --tool cron --cron "Sorare ML Training" --objective "Sorare mission model training complete" --step "Saved latest model artifacts"
```

## Today's Jobs / automation visibility

Codex/JOSHeX-run automations should also write operational job rows to `data/codex-jobs.json` for the separate Today’s Jobs top section.

Use it for:

- Supabase Brain Feed publishes
- dashboard regeneration
- hard refreshes
- sidecar syncs
- validation runs
- live patch/deploy actions

Example entry:

```json
{
  "id": "unique-job-id",
  "time": "2026-04-30T07:49:29Z",
  "title": "Published JOSHeX status to Brain Feed",
  "status": "done",
  "tool": "supabase_brain_feed_publish.py",
  "owner": "Personal Codex",
  "detail": "Updated the joshex Supabase row after Mission Control patch work"
}
```

Then regenerate dashboard data:

```bash
python3 scripts/update_mission_control.py
```

Do not spam Action Required. Use Action Required only when Josh needs to approve or fix something.

## Frontend behavior

Mission Control subscribes to `brain_feed` realtime changes and also polls these rows as a fallback:

`main,josh,jain,jaimes,joshex`

The freshest Supabase row wins over older JSON data for each agent. If Supabase is stale or unavailable, local/GitHub JSON feeds still render.

## Privacy rule

Never write secrets, raw private connector contents, API keys, passwords, tokens, credentials, account data, emails, sensitive OAuth details, cookies, or private customer/account content into:

- Supabase Brain Feed rows
- `brain-feed.json`
- `personal-codex.json`
- `jain-brain-feed.json`
- `jaimes-brain-feed.json`
- `dashboard-data.json`
- `codex-jobs.json`
- handoff docs

Keep entries operational: objective, status, current tool, step summary, validation state, timestamp.
