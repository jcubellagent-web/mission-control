#!/bin/zsh
# sync_cache_version.sh — keep service-worker.js CACHE_NAME in sync with index.html BUILD timestamp
set -euo pipefail
ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"

NEW_TS=$(date +%s)

# Update index.html BUILD comment
sed -i '' "s/<!-- BUILD:[0-9]* -->/<!-- BUILD:${NEW_TS} -->/" index.html

# Update service-worker.js CACHE_NAME to match
sed -i '' "s/mission-control-pwa-v[0-9]*/mission-control-pwa-v${NEW_TS}/" service-worker.js

echo "Synced BUILD + CACHE_NAME to ${NEW_TS}"
