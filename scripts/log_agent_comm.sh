#!/usr/bin/env bash
# log_agent_comm.sh — append an entry to data/agent-comms.json
# Usage: log_agent_comm.sh "direction" "message" "status"
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMMS_FILE="$ROOT_DIR/data/agent-comms.json"

DIRECTION="${1:-outbound}"
MESSAGE="${2:-}"
STATUS="${3:-sent}"

python3 - "$DIRECTION" "$MESSAGE" "$STATUS" "$COMMS_FILE" <<'EOF'
import json, sys, datetime

direction, message, status, path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')

try:
    with open(path) as f:
        entries = json.load(f)
    if not isinstance(entries, list):
        entries = []
except Exception:
    entries = []

new_entry = {"timestamp": now, "direction": direction, "message": message, "status": status}
entries = [new_entry] + entries
entries = entries[:20]

with open(path, 'w') as f:
    json.dump(entries, f, indent=2)
EOF
