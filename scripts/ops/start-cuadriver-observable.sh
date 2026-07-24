#!/bin/zsh
set -eu

APP="/Applications/CuaDriver.app"
BIN="$APP/Contents/MacOS/cua-driver"

if [[ ! -x "$BIN" ]]; then
  echo "cua-driver: application is not installed" >&2
  exit 1
fi

# The long-lived AppKit runloop owns the synthetic agent cursor and the
# on-host post-action preview. Nothing from that preview is copied into shared
# telemetry or Control Tower.
exec /usr/bin/open -W -g -a "$APP" --args \
  serve \
  --experimental-pip \
  --experimental-pip-geometry 480x360+1420+32 \
  --cursor-shape teardrop

#JAIMES: the CuaDriver service now uses one LaunchServices-owned process so
# cursor/PiP observability stays visible without duplicate daemon loops.
