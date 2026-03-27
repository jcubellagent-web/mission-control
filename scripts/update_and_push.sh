#!/bin/zsh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/opt/homebrew/sbin"
export HOME="${HOME:-/Users/josh2.0}"  # ensure HOME is set in cron environment
ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
WORKSPACE_DIR=$(cd -- "$ROOT_DIR/.." && pwd)
cd "$WORKSPACE_DIR/kiosk-dashboard"
npm run model:sync
cd "$ROOT_DIR"
python3 scripts/update_mission_control.py
# Always commit all data files — brain-feed.json gets a heartbeat idleUpdatedAt
# every run so GH Pages always receives a fresh file (prevents false "Stale" display).
ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
git add data/dashboard-data.json data/brain-feed.json data/modelUsage.json
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
  git -c http.https://github.com/.extraheader="AUTHORIZATION: basic $AUTH_HEADER" push origin main
else
  git push origin main
fi
