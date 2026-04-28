#!/bin/zsh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/opt/homebrew/sbin"
export HOME="${HOME:-/Users/josh2.0}"  # ensure HOME is set in cron environment
ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
WORKSPACE_DIR=$(cd -- "$ROOT_DIR/.." && pwd)
cd "$WORKSPACE_DIR/kiosk-dashboard"
npm run model:sync
cd "$ROOT_DIR"
python3 scripts/sync_jaimes_brain_feed.py || true
# Pull J.A.I.N brain feed and newsfeed from remote (non-blocking on failure)
bash scripts/jain_bf_pull.sh || true
bash scripts/jain_newsfeed_sync.sh || true
bash scripts/jain_breaking_highlights_sync.sh || true
# Pull MoltWorld state from J.A.I.N
ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no \
    jc_agent@100.121.89.84 \
    "cat /Users/jc_agent/.openclaw/workspace/mission-control/data/moltworld-state.json" \
    > data/moltworld-state.json 2>/dev/null || true
# Pull J.A.I.N x-progress metrics (updated after every post)
ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no \
    jc_agent@100.121.89.84 \
    "cat /Users/jc_agent/.openclaw/workspace/mission-control/data/x-progress.json" \
    > data/x-progress.json 2>/dev/null || true
python3 scripts/update_mission_control.py
# Commit dashboard data — brain-feed.json is intentionally excluded.
# Brain feed active state is managed by Supabase Realtime (bf_push.sh).
# Pushing brain-feed.json to GH Pages would overwrite live active state every 5min.
ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
git add data/dashboard-data.json data/modelUsage.json data/jain-brain-feed.json data/jaimes-brain-feed.json data/agent-comms.json data/x-progress.json data/jain-api-costs.json data/eight-sleep-data.json data/moltworld-data.json data/moltworld-state.json data/jain-breaking-highlights.json
if git diff --cached --quiet; then
  echo "mission-control: no changes"
  exit 0
fi
git commit -m "dashboard: auto refresh $ts"

# Prefer saved token file (works in cron without keyring), fall back to gh CLI
GH_TOKEN=""
if [[ -f "${HOME}/.secrets/gh_token" ]]; then
  GH_TOKEN=$(cat "${HOME}/.secrets/gh_token")
else
  GH_TOKEN=$(gh auth token 2>/dev/null || true)
fi
if [[ -n "$GH_TOKEN" ]]; then
  AUTH_HEADER=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')
  git -c http.https://github.com/.extraheader="AUTHORIZATION: basic $AUTH_HEADER" push origin HEAD:main
else
  git push origin HEAD:main
fi
