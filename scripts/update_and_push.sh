#!/bin/zsh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
WORKSPACE_DIR=$(cd -- "$ROOT_DIR/.." && pwd)
cd "$WORKSPACE_DIR/kiosk-dashboard"
npm run model:sync
cd "$ROOT_DIR"
python3 scripts/update_mission_control.py
# Always publish brain-feed.json changes too (fast-poll real-time brain feed)
# This ensures updates to data/brain-feed.json make it to GitHub Pages.
if git diff --quiet data/dashboard-data.json data/brain-feed.json; then
  echo "mission-control: no changes"
  exit 0
fi
ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
git add data/dashboard-data.json data/brain-feed.json
git commit -m "dashboard: auto refresh $ts"
git push origin main
