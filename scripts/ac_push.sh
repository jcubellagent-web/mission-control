#!/usr/bin/env bash
# ac_push.sh — log agent activity to Supabase agent_comms table
# Usage: ac_push.sh "agent" "message" "tool" "status"
# agent: josh | jain
# status: done | active
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

AGENT="${1:-josh}"
MESSAGE="${2:-}"
TOOL="${3:-}"
STATUS="${4:-done}"
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

SUPABASE_URL="https://cdzaeptrggczynijegls.supabase.co"
SUPABASE_KEY="sb_publishable_S6K05dWzCylIOjEOM1TcEQ_FUG1DAJ6"

[[ -z "$MESSAGE" ]] && exit 0

# Build JSON payload
PAYLOAD=$(python3 -c "
import json
print(json.dumps({
    'agent': '$AGENT',
    'message': '''$MESSAGE''',
    'tool': '$TOOL' if '$TOOL' else None,
    'status': '$STATUS',
    'timestamp': '$NOW'
}))
")

curl -s -o /dev/null -X POST \
  "$SUPABASE_URL/rest/v1/agent_comms" \
  -H "apikey: $SUPABASE_KEY" \
  -H "Authorization: Bearer $SUPABASE_KEY" \
  -H "Content-Type: application/json" \
  -H "Prefer: return=minimal" \
  -d "$PAYLOAD" &

exit 0
