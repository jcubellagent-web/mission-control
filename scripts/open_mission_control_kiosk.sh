#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

FORCE_RELOAD="${MISSION_CONTROL_FORCE_RELOAD:-0}"
FORCE_ACTIVATE="0"
if [[ "${1:-}" == "--force" ]]; then
  FORCE_RELOAD="1"
  shift
fi
if [[ "${1:-}" == "--activate" ]]; then
  FORCE_ACTIVATE="1"
  shift
fi

KIOSK_ORIGIN="http://127.0.0.1:5174"
URL="${1:-$KIOSK_ORIGIN/?ct_refresh=$(date -u +%Y%m%dT%H%M%SZ)}"
PROFILE="${CONTROL_TOWER_CHROME_PROFILE:-$HOME/.openclaw/browser-profiles/control-tower-kiosk}"
CHROME_APP="Google Chrome"
SCRIPT_DIR="${0:A:h}"
FOREGROUND_GUARD="$SCRIPT_DIR/control_tower_foreground.py"

mkdir -p "$PROFILE"

server_ready="0"
for _attempt in {1..45}; do
  if curl -fsS --max-time 2 "http://127.0.0.1:5174/" >/dev/null 2>&1; then
    server_ready="1"
    break
  fi
  sleep 1
done
if [[ "$server_ready" != "1" ]]; then
  echo "control-tower: current React kiosk server not ready at http://127.0.0.1:5174/"
  exit 1
fi

ensure_foreground() {
  if [[ "${CONTROL_TOWER_FOREGROUND_CHILD:-0}" == "1" || ! -f "$FOREGROUND_GUARD" ]]; then
    return 0
  fi
  local args=(ensure)
  if [[ "$FORCE_ACTIVATE" == "1" ]]; then
    args+=(--force)
  fi
  /usr/bin/python3 "$FOREGROUND_GUARD" "${args[@]}"
}

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
  ensure_foreground
  exit $?
fi

if [[ "$FORCE_RELOAD" != "1" ]] && pgrep -f "Google Chrome.*--user-data-dir=$PROFILE" >/dev/null 2>&1; then
  echo "control-tower: kiosk Chrome is already running; leaving it in place"
  ensure_foreground
  exit $?
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
#JAIMES: Never activate Chrome by bundle name here. Josh 2.0 keeps an
# independent auth profile open, so the PID-specific guard must own focus.
ensure_foreground
