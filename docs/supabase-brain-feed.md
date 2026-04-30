# Supabase Brain Feed Bridge

Mission Control uses Supabase `public.brain_feed` as the live agent bus. The existing JSON files remain as fallbacks.

## Rows
- `main`: backwards-compatible JOSH 2.0 feed.
- `josh`: JOSH 2.0 live actions and cron updates.
- `jaimes`: JAIMES live actions and Hermes/specialist updates.
- `jain`: J.A.I.N worker updates.
- `joshex`: JOSHeX/OpenAI Codex updates.

## Publishing
From any agent worktree with Mission Control checked out, or from the installed helper at `~/scripts/mission_control_brain_feed_publish.py`:

```bash
python3 scripts/supabase_brain_feed_publish.py \
  --agent josh \
  --status active \
  --tool cron \
  --cron "Mission Control Refresh" \
  --objective "Refreshing Mission Control data" \
  --step "Fetched dashboard state" \
  --decision "Keep JSON fallback while publishing live row"
```

The helper reads the existing frontend Supabase publishable config from the repo, the current directory, or the usual `.openclaw/workspace` Mission Control paths. It can also use these environment variables:

```bash
MISSION_CONTROL_SUPABASE_URL=...
MISSION_CONTROL_SUPABASE_KEY=...
```

Do not paste private service-role keys into chat. The helper does not print credential values.

## Cron Pattern
For long jobs, publish at start and finish:

```bash
python3 scripts/supabase_brain_feed_publish.py --agent jaimes --status active --tool cron --cron "Sorare ML Training" --objective "Training Sorare mission model" --step "Started training run"

# run the job

python3 scripts/supabase_brain_feed_publish.py --agent jaimes --status done --tool cron --cron "Sorare ML Training" --objective "Sorare mission model training complete" --step "Saved latest model artifacts"
```

## Frontend Behavior
Mission Control subscribes to `brain_feed` realtime changes and also polls these rows as a fallback:

`main,josh,jain,jaimes,joshex`

The freshest Supabase row wins over older JSON data for each agent. If Supabase is stale or unavailable, local/GitHub JSON feeds still render.
