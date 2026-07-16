#!/usr/bin/env zsh
set -euo pipefail

ROOT="${0:A:h:h}"
LAUNCHD_SOURCE="$ROOT/launchd"
LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/.openclaw/workspace/logs"
BACKUP_DIR="$HOME/.openclaw/backups/control-tower-foreground/$(date -u +%Y%m%dT%H%M%SZ)"
DOMAIN="gui/$(id -u)"
LABELS=(
  com.josh20.control-tower-foreground-guard
  com.josh20.mission-control-kiosk-watchdog
)

mkdir -p "$LAUNCH_AGENT_DIR" "$LOG_DIR" "$BACKUP_DIR"

for label in "${LABELS[@]}"; do
  source_plist="$LAUNCHD_SOURCE/$label.plist"
  destination="$LAUNCH_AGENT_DIR/$label.plist"
  /usr/bin/plutil -lint "$source_plist" >/dev/null
  if [[ -f "$destination" ]]; then
    /bin/cp -p "$destination" "$BACKUP_DIR/$label.plist"
  fi
  /bin/launchctl bootout "$DOMAIN/$label" >/dev/null 2>&1 || true
  /usr/bin/install -m 0644 "$source_plist" "$destination"
  /bin/launchctl bootstrap "$DOMAIN" "$destination"
  /bin/launchctl enable "$DOMAIN/$label"
done

for label in "${LABELS[@]}"; do
  /bin/launchctl print "$DOMAIN/$label" | /usr/bin/sed -n '1,45p'
done

echo "control-tower: installed foreground guard and deep kiosk watchdog"
echo "control-tower: previous host-local plists backed up under $BACKUP_DIR"
