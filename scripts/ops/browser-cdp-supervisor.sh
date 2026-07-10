#!/bin/zsh
set -u

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PORT="${HERMES_CDP_PORT:-9222}"
PROFILE="$HERMES_HOME/browser-cdp-profile"
LOG_DIR="$HERMES_HOME/logs"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ERR_LOG="$LOG_DIR/browser-cdp.err.log"
MAX_LOG_BYTES=$((5 * 1024 * 1024))

mkdir -p "$PROFILE" "$LOG_DIR"

rotate_log() {
  local path="$1"
  [[ -f "$path" ]] || return 0
  local bytes
  bytes=$(stat -f%z "$path" 2>/dev/null || echo 0)
  if (( bytes > MAX_LOG_BYTES )); then
    mv -f "$path" "$path.1"
    : > "$path"
  fi
}

if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  exit 0
fi

# Only remove Chromium singleton files when no CDP owner is alive.
if ! pgrep -f -- "--remote-debugging-port=$PORT.*--user-data-dir=$PROFILE" >/dev/null 2>&1; then
  rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonCookie" "$PROFILE/SingletonSocket"
fi

rotate_log "$ERR_LOG"

exec "$CHROME" \
  --headless=new \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run \
  --no-default-browser-check \
  --disable-background-networking \
  --disable-component-update \
  --disable-logging \
  --disable-sync \
  about:blank >>"$ERR_LOG" 2>&1
