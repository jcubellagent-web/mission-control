#!/bin/zsh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
WORKSPACE_DIR=$(cd -- "$ROOT_DIR/.." && pwd)
cd "$WORKSPACE_DIR/kiosk-dashboard"
npm run model:sync
cd "$ROOT_DIR"
python3 scripts/update_mission_control.py
# Always publish brain-feed.json and modelUsage.json changes too.
# This ensures fast-poll brain feed + standalone model usage make it to GitHub Pages.
if git diff --quiet data/dashboard-data.json data/brain-feed.json data/modelUsage.json; then
  echo "mission-control: no changes"
  exit 0
fi
ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
git add data/dashboard-data.json data/brain-feed.json data/modelUsage.json
git commit -m "dashboard: auto refresh $ts"

GH_TOKEN=$(gh auth token 2>/dev/null || true)
if [[ -n "$GH_TOKEN" ]]; then
  AUTH_HEADER=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')
  git -c http.https://github.com/.extraheader="AUTHORIZATION: basic $AUTH_HEADER" push origin main
else
  git push origin main
fi
