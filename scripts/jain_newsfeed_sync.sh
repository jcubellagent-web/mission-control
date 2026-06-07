#!/usr/bin/env bash
# jain_newsfeed_sync.sh — pull J.A.I.N newsfeed from remote and save locally
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT_DIR/data/jain-newsfeed.json"
TMP="$(mktemp)"

if ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no \
    jc_agent@100.121.89.84 \
    "cat /Users/jc_agent/.openclaw/workspace/mission-control/data/newsfeed.json" \
    > "$TMP" 2>/dev/null && python3 -m json.tool "$TMP" >/dev/null 2>&1; then
  mv "$TMP" "$DEST"
else
  rm -f "$TMP"
  echo "mission-control: kept existing J.A.I.N newsfeed; remote pull unavailable or invalid" >&2
fi
