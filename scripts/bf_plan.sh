#!/usr/bin/env bash
# bf_plan.sh — mirror update_plan steps into brain-feed.json
# Usage: bf_plan.sh "objective" '[{"step":"...", "status":"in_progress|pending|completed"}]'
# Maps update_plan status → brain-feed step status: in_progress→active, completed→done, pending→pending
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BF_FILE="$ROOT_DIR/data/brain-feed.json"

OBJECTIVE="${1:-Working}"
PLAN_JSON="${2:-[]}"
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Translate plan steps → brain-feed steps
BF_STEPS=$(python3 -c "
import json, sys
plan = json.loads(sys.argv[1])
out = []
for item in plan:
    s = item.get('status', 'pending')
    bf_status = {'in_progress': 'active', 'completed': 'done', 'pending': 'pending'}.get(s, 'pending')
    label = item.get('step', 'Step')[:60]
    out.append({'label': label, 'status': bf_status, 'tool': 'plan'})
print(json.dumps(out))
" "$PLAN_JSON")

# Write to brain-feed.json
python3 -c "
import json
bf = {}
try:
    bf = json.load(open('$BF_FILE'))
except:
    pass
bf['active'] = True
bf['objective'] = '$OBJECTIVE'
bf['status'] = 'active'
bf['updatedAt'] = '$NOW'
bf['checkedAt'] = '$NOW'
new_steps = $BF_STEPS
if new_steps:
    bf['steps'] = new_steps
active_step = next((s for s in new_steps if s.get('status') == 'active'), None)
bf['currentTool'] = active_step.get('label', '')[:30] if active_step else ''
if not bf.get('messageReceived'):
    bf['messageReceived'] = '$NOW'
json.dump(bf, open('$BF_FILE', 'w'), indent=2)
print('Plan mirrored:', len(new_steps), 'steps')
"

# Push to Supabase in background
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

# Push to GitHub in background
(
  cd "$ROOT_DIR"
  git add data/brain-feed.json
  if ! git diff --cached --quiet; then
    git commit -m "brain: plan update [$NOW]" -q
    GH_TOKEN=""
    [[ -f "${HOME}/.secrets/gh_token" ]] && GH_TOKEN=$(cat "${HOME}/.secrets/gh_token")
    [[ -z "$GH_TOKEN" ]] && GH_TOKEN=$(gh auth token 2>/dev/null || true)
    if [[ -n "$GH_TOKEN" ]]; then
      AUTH_HEADER=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')
      git -c http.https://github.com/.extraheader="AUTHORIZATION: basic $AUTH_HEADER" push origin main -q 2>/dev/null || true
    fi
  fi
) &

exit 0
