#!/usr/bin/env bash
# jain_bf_pull.sh — pull J.A.I.N brain feed from remote and save locally
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT_DIR/data/jain-brain-feed.json"

pull_json() {
  local remote_path="$1"
  local dest="$2"
  local fallback="$3"
  local tmp
  tmp=$(mktemp)
  if ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no \
      jc_agent@100.121.89.84 \
      "cat ${remote_path}" > "$tmp" 2>/dev/null && python3 -m json.tool "$tmp" >/dev/null 2>&1; then
    mv "$tmp" "$dest"
  else
    rm -f "$tmp"
    if [[ ! -s "$dest" ]] || ! python3 -m json.tool "$dest" >/dev/null 2>&1; then
      printf '%s
' "$fallback" > "$dest"
    fi
    echo "mission-control: kept fallback for $dest"
  fi
}

pull_json "/Users/jc_agent/.openclaw/workspace/mission-control/data/brain-feed.json" "$DEST" '{"agent":"J.A.I.N","status":"unknown","active":false}'

# Also pull JAIN direct API cost tracker
pull_json "/Users/jc_agent/.openclaw/workspace/mission-control/data/jain-api-costs.json" "$(dirname "$0")/../data/jain-api-costs.json" '{"daily":0,"weekly":0,"monthly":0,"models":{},"available":false,"stale":true,"lastError":"fallback: source unavailable"}'
