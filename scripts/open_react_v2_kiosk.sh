#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

URL="${1:-http://127.0.0.1:5174/?mc_refresh=$(date -u +%Y%m%dT%H%M%SZ)}"
PROFILE="/tmp/mission-control-kiosk-profile"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

if ! curl -fsS --max-time 2 "http://127.0.0.1:5174/" >/dev/null 2>&1; then
  echo "mission-control-react-v2: server not ready"
  exit 1
fi

current_url="$(curl -s --max-time 2 http://127.0.0.1:9224/json 2>/dev/null \
  | python3 -c 'import json,sys
try:
    pages=json.load(sys.stdin)
    page=next((p for p in pages if p.get("type")=="page" and "127.0.0.1" in p.get("url","")), {})
    print(page.get("url",""))
except Exception:
    print("")
' || true)"

if [[ "$current_url" == http://127.0.0.1:5174/* || "$current_url" == "http://127.0.0.1:5174/"* ]]; then
  exit 0
fi

pkill -f "mission-control-kiosk-profile" 2>/dev/null || true
sleep 1

exec "$CHROME" \
  --user-data-dir="$PROFILE" \
  --remote-debugging-port=9224 \
  --remote-allow-origins='*' \
  --no-first-run \
  --disable-session-crashed-bubble \
  --disable-infobars \
  --kiosk "$URL"
