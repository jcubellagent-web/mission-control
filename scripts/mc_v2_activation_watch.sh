#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${MISSION_CONTROL_V2_ENV_FILE:-$HOME/.openclaw/workspace/secrets/mission-control-v2.env}"
STATE_DIR="${MISSION_CONTROL_V2_STATE_DIR:-$HOME/.openclaw/workspace/state}"
LOG_DIR="${MISSION_CONTROL_V2_LOG_DIR:-$HOME/.openclaw/workspace/logs}"
SENTINEL="$STATE_DIR/mission-control-v2-dual-write.ok"
LOG_FILE="$LOG_DIR/mission-control-v2-activation.log"
PYTHON_BIN="${PYTHON_BIN:-}"

mkdir -p "$STATE_DIR" "$LOG_DIR"

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG_FILE"
}

if [[ -f "$SENTINEL" ]]; then
  log "already activated"
  exit 0
fi

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x /opt/homebrew/bin/python3 ]]; then
    PYTHON_BIN=/opt/homebrew/bin/python3
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  log "python3 not found"
  exit 69
fi

if [[ ! -f "$ENV_FILE" ]]; then
  log "waiting for $ENV_FILE"
  exit 0
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

if [[ -z "${SUPABASE_SERVICE_ROLE_KEY:-${SUPABASE_SERVICE_KEY:-}}" ]]; then
  log "env file present but service key variable missing"
  exit 0
fi

log "service key detected; running v2 dual-write smoke"
if "$ROOT/scripts/mc_v2_dual_write_smoke.sh" >> "$LOG_FILE" 2>&1; then
  printf 'activated_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$SENTINEL"
  "$PYTHON_BIN" "$ROOT/scripts/agent_publish.py" \
    --agent joshex \
    --type complete \
    --status done \
    --title "Mission Control v2 dual-write activated" \
    --tool "mc_v2_activation_watch.sh" \
    --detail "First real v2 dual-write smoke passed on Josh 2.0; sentinel written and watcher will stay idle." \
    --brain-feed >/dev/null || true
  log "activation complete"
else
  "$PYTHON_BIN" "$ROOT/scripts/agent_publish.py" \
    --agent joshex \
    --type blocked \
    --status blocked \
    --title "Mission Control v2 dual-write activation failed" \
    --tool "mc_v2_activation_watch.sh" \
    --detail "Service key was present, but the v2 smoke failed. Check the activation log on Josh 2.0." \
    --brain-feed >/dev/null || true
  log "activation failed"
  exit 1
fi
