#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
exec "$SCRIPT_DIR/open_mission_control_kiosk.sh" "$@"
