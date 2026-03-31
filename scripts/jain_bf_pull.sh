#!/usr/bin/env bash
# jain_bf_pull.sh — pull J.A.I.N brain feed from remote and save locally
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT_DIR/data/jain-brain-feed.json"

ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no \
    jc_agent@100.121.89.84 \
    "cat /Users/jc_agent/.openclaw/workspace/mission-control/data/brain-feed.json" \
    > "$DEST" 2>/dev/null || true

# Also pull JAIN direct API cost tracker
ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no \
    jc_agent@100.121.89.84 \
    "cat /Users/jc_agent/.openclaw/workspace/mission-control/data/jain-api-costs.json" \
    > "$(dirname "$0")/../data/jain-api-costs.json" 2>/dev/null || true
