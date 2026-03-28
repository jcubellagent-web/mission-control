#!/usr/bin/env bash
# bf_push.sh — update brain-feed.json with zero blocking latency
# Usage: bf_push.sh "objective" "step1:tool|step2:tool" "active|done|idle"
# Writes JSON instantly, pushes to GitHub in background (non-blocking)
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BF_FILE="$ROOT_DIR/data/brain-feed.json"

OBJECTIVE="${1:-Working…}"
STEPS_RAW="${2:-}"
STATE="${3:-active}"
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Build steps JSON
if [[ -n "$STEPS_RAW" ]]; then
  STEPS_JSON=$(python3 -c "
import json, sys
raw = sys.argv[1]
parts = [p.strip() for p in raw.split('|') if p.strip()]
out = []
for i, p in enumerate(parts):
  status = 'active' if i == len(parts)-1 else 'done'
  tool = p.split(':')[1].strip() if ':' in p else 'exec'
  label = p.split(':')[0].strip() if ':' in p else p
  out.append({'label': label, 'status': status, 'tool': tool})
print(json.dumps(out))
" "$STEPS_RAW")
else
  STEPS_JSON="[]"
fi

IS_ACTIVE="true"
[[ "$STATE" == "done" || "$STATE" == "idle" ]] && IS_ACTIVE="false"

# ── Write JSON instantly (synchronous, fast) ──────────────────────────────────
python3 -c "
import json
bf = {}
try:
  bf = json.load(open('$BF_FILE'))
except:
  pass
bf['active']          = True if '$IS_ACTIVE' == 'true' else False
bf['objective']       = '$OBJECTIVE'
bf['status']          = '$STATE'
bf['updatedAt']       = '$NOW'
bf['messageReceived'] = bf.get('messageReceived', '$NOW')
bf['steps']           = $STEPS_JSON
bf['currentTool']     = bf['steps'][-1].get('tool', '') if bf['steps'] else ''
json.dump(bf, open('$BF_FILE', 'w'), indent=2)
"

# ── Push to Supabase Realtime in background (non-blocking, fast) ──────────────
SUPABASE_URL="https://cdzaeptrggczynijegls.supabase.co"
SUPABASE_KEY="sb_publishable_S6K05dWzCylIOjEOM1TcEQ_FUG1DAJ6"

(
  BF_JSON=$(cat "$BF_FILE")
  curl -s -o /dev/null -X POST \
    "$SUPABASE_URL/rest/v1/brain_feed" \
    -H "apikey: $SUPABASE_KEY" \
    -H "Authorization: Bearer $SUPABASE_KEY" \
    -H "Content-Type: application/json" \
    -H "Prefer: resolution=merge-duplicates,return=minimal" \
    -d "{\"id\": \"main\", \"data\": $BF_JSON, \"updated_at\": \"$NOW\"}" 2>/dev/null || true
) &

# ── Push to GitHub in background (non-blocking) ───────────────────────────────
(
  cd "$ROOT_DIR"
  git add data/brain-feed.json
  if git diff --cached --quiet; then
    exit 0
  fi
  git commit -m "brain: $OBJECTIVE [$STATE $(date -u +%H:%M:%SZ)]" -q
  # Prefer saved token file (works in cron without keyring)
  GH_TOKEN=""
  if [[ -f "${HOME}/.secrets/gh_token" ]]; then
    GH_TOKEN=$(cat "${HOME}/.secrets/gh_token")
  else
    GH_TOKEN=$(gh auth token 2>/dev/null || true)
  fi
  if [[ -n "$GH_TOKEN" ]]; then
    AUTH_HEADER=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')
    git -c http.https://github.com/.extraheader="AUTHORIZATION: basic $AUTH_HEADER" push origin main -q 2>/dev/null || true
  else
    git push origin main -q 2>/dev/null || true
  fi
) &

# Return immediately — caller is not blocked
exit 0
