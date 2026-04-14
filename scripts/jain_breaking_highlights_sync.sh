#!/usr/bin/env bash
# jain_breaking_highlights_sync.sh — pull J.A.I.N breaking highlights from remote and save locally
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT_DIR/data/jain-breaking-highlights.json"

ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no \
    jc_agent@100.121.89.84 \
    "cat /Users/jc_agent/.openclaw/workspace/mission-control/data/breaking-highlights.json" \
    > "$DEST" 2>/dev/null || true
