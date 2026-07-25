#!/bin/zsh
set -eu

ROOT="${0:A:h:h}"
SOURCE="$ROOT/launchd/ai.control-tower.interaction-capabilities.plist"
TARGET="$HOME/Library/LaunchAgents/ai.control-tower.interaction-capabilities.plist"
LABEL="ai.control-tower.interaction-capabilities"
CANARY_SOURCE="$ROOT/launchd/ai.control-tower.interaction-active-canary.plist"
CANARY_TARGET="$HOME/Library/LaunchAgents/ai.control-tower.interaction-active-canary.plist"
CANARY_LABEL="ai.control-tower.interaction-active-canary"
DOMAIN="gui/$(id -u)"
BACKUP_ROOT="$HOME/.openclaw/backups/interaction-capabilities"

if [[ "$(id -un)" != "josh2.0" ]]; then
  echo "interaction-capabilities: install only on Josh 2.0" >&2
  exit 2
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.openclaw/logs" "$BACKUP_ROOT"
if [[ -f "$TARGET" ]]; then
  cp -p "$TARGET" "$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-interaction-capabilities.plist"
fi
if [[ -f "$CANARY_TARGET" ]]; then
  cp -p "$CANARY_TARGET" "$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-interaction-active-canary.plist"
fi
cp -p "$SOURCE" "$TARGET"
cp -p "$CANARY_SOURCE" "$CANARY_TARGET"
plutil -lint "$TARGET" >/dev/null
plutil -lint "$CANARY_TARGET" >/dev/null
launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
launchctl bootout "$DOMAIN/$CANARY_LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "$DOMAIN" "$TARGET"
launchctl bootstrap "$DOMAIN" "$CANARY_TARGET"
launchctl kickstart -k "$DOMAIN/$LABEL"
launchctl print "$DOMAIN/$LABEL" | sed -n '1,45p'
launchctl print "$DOMAIN/$CANARY_LABEL" | sed -n '1,45p'

#JAIMES: the interaction watch publishes only version, readiness, route, and
# latency metadata; screenshots and page/account content remain on-host.
