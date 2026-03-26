#!/usr/bin/env bash
# bf_push.sh — update brain-feed.json in real-time during active tasks
# Usage: bf_push.sh "objective text" [step1|step2|...] [active|done|idle]
# Designed to be called async (fire-and-forget) during task execution
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BF_FILE="$ROOT_DIR/data/brain-feed.json"

OBJECTIVE="${1:-Working…}"
STEPS_RAW="${2:-}"
STATE="${3:-active}"   # active | done | idle

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
MSG_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Build steps JSON array
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

python3 -c "
import json
bf = {}
try:
  bf = json.load(open('$BF_FILE'))
except:
  pass
bf['active'] = True if '$IS_ACTIVE' == 'true' else False
bf['objective'] = '''$OBJECTIVE'''
bf['status'] = '$STATE'
bf['updatedAt'] = '$NOW'
bf['messageReceived'] = bf.get('messageReceived', '$MSG_TIME')
bf['steps'] = $STEPS_JSON
if bf['steps']:
  bf['currentTool'] = bf['steps'][-1].get('tool', 'exec')
else:
  bf['currentTool'] = ''
json.dump(bf, open('$BF_FILE', 'w'), indent=2)
"

cd "$ROOT_DIR"
git add data/brain-feed.json
if git diff --cached --quiet; then
  exit 0  # no change
fi
git commit -m "brain: $OBJECTIVE [$(date -u +%H:%M:%SZ)]"
GH_TOKEN=$(gh auth token 2>/dev/null || true)
if [[ -n "$GH_TOKEN" ]]; then
  AUTH_HEADER=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')
  git -c http.https://github.com/.extraheader="AUTHORIZATION: basic $AUTH_HEADER" push origin main -q
else
  git push origin main -q
fi
