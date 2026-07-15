#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

FORCE_RELOAD="${MISSION_CONTROL_FORCE_RELOAD:-0}"
if [[ "${1:-}" == "--force" ]]; then
  FORCE_RELOAD="1"
  shift
fi

KIOSK_ORIGIN="http://127.0.0.1:5174"
URL="${1:-$KIOSK_ORIGIN/?ct_refresh=$(date -u +%Y%m%dT%H%M%SZ)}"
PROFILE="${CONTROL_TOWER_CHROME_PROFILE:-$HOME/.openclaw/browser-profiles/control-tower-kiosk}"
CHROME_APP="Google Chrome"

mkdir -p "$PROFILE"

if ! curl -fsS --max-time 2 "http://127.0.0.1:5174/" >/dev/null 2>&1; then
  echo "control-tower: current React kiosk server not ready at http://127.0.0.1:5174/"
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

if [[ "$FORCE_RELOAD" != "1" && "$current_url" == "$KIOSK_ORIGIN"* ]]; then
  exit 0
fi

if [[ "$FORCE_RELOAD" != "1" ]] && pgrep -f "Google Chrome.*--user-data-dir=$PROFILE" >/dev/null 2>&1; then
  echo "control-tower: kiosk Chrome is already running; leaving it in place"
  exit 0
fi

# Restart only the dedicated kiosk process. Credential/browser automation uses
# a separate persistent profile and must remain available when the kiosk heals.
pkill -f "Google Chrome.*--user-data-dir=$PROFILE" 2>/dev/null || true
sleep 2
rm -rf "$PROFILE/SingletonLock" "$PROFILE/SingletonSocket" "$PROFILE/SingletonCookie" 2>/dev/null || true

open -na "$CHROME_APP" --args \
  --user-data-dir="$PROFILE" \
  --remote-debugging-port=9224 \
  --remote-allow-origins=http://127.0.0.1:9224 \
  --no-first-run \
  --disable-session-crashed-bubble \
  --disable-infobars \
  --force-prefers-reduced-motion \
  --hide-scrollbars \
  --app="$URL" \
  --start-fullscreen \
  --start-maximized

sleep 2
osascript -e 'tell application "Google Chrome" to activate' \
  -e 'tell application "System Events" to key code 53' >/dev/null 2>&1 || true
