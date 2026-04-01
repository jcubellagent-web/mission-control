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

# Resolve current active model from OpenClaw sessions
CURRENT_MODEL=$(python3 - << 'PYEOF' 2>/dev/null
import json, pathlib, sys
sessions_file = pathlib.Path.home() / '.openclaw/agents/main/sessions/sessions.json'
result = 'Unknown'
try:
    d = json.loads(sessions_file.read_text())
    for key in ['agent:main:telegram:direct:6218150306', 'agent:main:main']:
        s = d.get(key, {})
        m = s.get('model')
        if m:
            import re
            m = m.replace('claude-sonnet-4-6','Sonnet 4.6').replace('claude-sonnet-4','Sonnet 4') \
                 .replace('claude-opus-4','Opus 4').replace('claude-haiku-4','Haiku 4') \
                 .replace('gemini-2.5-flash','Gemini 2.5 Flash').replace('gemini-2.5-pro','Gemini 2.5 Pro') \
                 .replace('gpt-4o','GPT-4o').replace('gpt-4.1','GPT-4.1').replace('gpt-5','GPT-5') \
                 .replace('anthropic/','').replace('google/','').replace('openai/','').replace('openrouter/','')
            m = re.sub(r'-codex\b', '', m, flags=re.IGNORECASE)
            result = m
            break
except:
    pass
print(result)
PYEOF
)

# Build steps JSON
if [[ -n "$STEPS_RAW" ]]; then
  STEPS_JSON=$(python3 -c "
import json, sys
raw = sys.argv[1]
parts = [p.strip() for p in raw.split('|') if p.strip()]
out = []
for i, p in enumerate(parts):
  final_state = sys.argv[2] if len(sys.argv) > 2 else 'active'
  status = ('done' if final_state in ('done', 'idle') else 'active') if i == len(parts)-1 else 'done'
  tool = p.split(':')[1].strip() if ':' in p else 'exec'
  label = p.split(':')[0].strip() if ':' in p else p
  out.append({'label': label, 'status': status, 'tool': tool})
print(json.dumps(out))
" "$STEPS_RAW" "$STATE")
else
  STEPS_JSON="[]"
fi

IS_ACTIVE="true"
[[ "$STATE" == "idle" ]] && IS_ACTIVE="false"

# On idle: auto-build objective from previous objective if caller passes empty/generic string
if [[ "$STATE" == "idle" ]]; then
  OBJECTIVE=$(python3 -c "
import json, sys
bf_file = '$BF_FILE'
new_obj = sys.argv[1]
generic = ['Response sent · Awaiting instruction', 'Working…', '', 'Awaiting instruction']
try:
    bf = json.load(open(bf_file))
    prev = bf.get('objective', '')
    # If caller passed a generic/empty string, build from previous objective
    if new_obj.strip() in generic or not new_obj.strip():
        if prev and prev not in generic:
            print(prev + ' — complete · awaiting next instruction')
        else:
            print('Awaiting next instruction')
    else:
        print(new_obj)
except:
    print(new_obj if new_obj.strip() else 'Awaiting next instruction')
" "$OBJECTIVE")
fi
# "done" keeps active=true for 90s so the dashboard shows the completion flash
# The idle cron will not overwrite while active=true

# ── Write JSON instantly (synchronous, fast) ──────────────────────────────────
CURRENT_MODEL="$CURRENT_MODEL" python3 -c "
import json, os
bf = {}
try:
  bf = json.load(open('$BF_FILE'))
except:
  pass
bf['active']          = True if '$IS_ACTIVE' == 'true' else False
bf['objective']       = '$OBJECTIVE'
bf['status']          = '$STATE'
bf['updatedAt']       = '$NOW'
bf['model']           = os.environ.get('CURRENT_MODEL', '')
# checkedAt only set on active pushes — never on idle — so hash stays stable when nothing changes
if '$IS_ACTIVE' == 'true':
    bf['checkedAt'] = '$NOW'
if '$IS_ACTIVE' == 'true':
    bf['messageReceived'] = '$NOW'
else:
    bf['messageReceived'] = bf.get('messageReceived', '$NOW')
# Only overwrite steps if new steps were provided — preserve on done/idle
new_steps = $STEPS_JSON
if new_steps:
    bf['steps'] = new_steps
elif '$STATE' in ('done', 'idle'):
    # Mark all existing steps as done on completion so UI shows them correctly
    existing = bf.get('steps', [])
    for s in existing:
        s['status'] = 'done'
    bf['steps'] = existing
else:
    bf['steps'] = new_steps
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
