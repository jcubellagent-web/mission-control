#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${MISSION_CONTROL_V2_ENV_FILE:-$HOME/.openclaw/workspace/secrets/mission-control-v2.env}"
PYTHON_BIN="${PYTHON_BIN:-}"

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x /opt/homebrew/bin/python3 ]]; then
    PYTHON_BIN=/opt/homebrew/bin/python3
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "python3 not found" >&2
  exit 69
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${SUPABASE_SERVICE_ROLE_KEY:-${SUPABASE_SERVICE_KEY:-}}" ]]; then
  echo "Missing SUPABASE_SERVICE_ROLE_KEY. Add it to $ENV_FILE or export it in the shell." >&2
  exit 78
fi

export MISSION_CONTROL_V2_DUAL_WRITE=1
JOB_TITLE="JAIMES v2 job smoke"
HANDOFF_TITLE="JAIMES v2 handoff smoke"

"$PYTHON_BIN" "$ROOT/scripts/agent_publish.py" \
  --agent jaimes \
  --type job \
  --status done \
  --title "$JOB_TITLE" \
  --tool "mc_v2_job_handoff_smoke.sh" \
  --detail "dashboard-safe JAIMES job row smoke for the current Mission Control jobs rail" \
  --brain-feed \
  --job \
  --v2

"$PYTHON_BIN" "$ROOT/scripts/agent_publish.py" \
  --agent jaimes \
  --type handoff \
  --status active \
  --title "$HANDOFF_TITLE" \
  --tool "mc_v2_job_handoff_smoke.sh" \
  --detail "dashboard-safe JAIMES handoff smoke for the current Mission Control approval path" \
  --handoff-to joshex \
  --brain-feed \
  --v2

"$PYTHON_BIN" "$ROOT/scripts/mc_v2_verify.py" \
  --agent jaimes \
  --expect-title "$HANDOFF_TITLE" \
  --expect-job-title "$JOB_TITLE" \
  --expect-handoff-title "$HANDOFF_TITLE" \
  --expect-approval-title "$HANDOFF_TITLE"
