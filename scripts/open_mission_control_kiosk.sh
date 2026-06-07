#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

FORCE_RELOAD="${MISSION_CONTROL_FORCE_RELOAD:-0}"
if [[ "${1:-}" == "--force" ]]; then
  FORCE_RELOAD="1"
  shift
fi

URL="${1:-http://127.0.0.1:5174/?mc_refresh=$(date -u +%Y%m%dT%H%M%SZ)}"
PROFILE="/tmp/mission-control-kiosk-profile"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

if ! curl -fsS --max-time 2 "http://127.0.0.1:5174/" >/dev/null 2>&1; then
  echo "mission-control: current React kiosk server not ready at http://127.0.0.1:5174/"
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

if [[ "$FORCE_RELOAD" != "1" && "$current_url" == "$URL" ]]; then
  exit 0
fi

pkill -f "mission-control-kiosk-profile" 2>/dev/null || true
sleep 1

exec "$CHROME" \
  --user-data-dir="$PROFILE" \
  --remote-debugging-port=9224 \
  --remote-allow-origins='*' \
  --no-first-run \
  --use-mock-keychain \
  --password-store=basic \
  --disable-session-crashed-bubble \
  --disable-infobars \
  --kiosk "$URL"
