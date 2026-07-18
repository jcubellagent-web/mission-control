#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 6 ]]; then
  echo "usage: agent_job_wrap.sh <agent> <title> <tool> <detail> -- <command...>" >&2
  exit 64
fi

AGENT="$1"
TITLE="$2"
TOOL="$3"
DETAIL="$4"
shift 4

if [[ "${1:-}" != "--" ]]; then
  echo "missing -- before command" >&2
  exit 64
fi
shift

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBLISH="${SCRIPT_DIR}/agent_publish.py"
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x /opt/homebrew/bin/python3 ]]; then
    PYTHON_BIN=/opt/homebrew/bin/python3
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
fi

WORK_ID="${CONTROL_TOWER_WORK_ID:-}"
RUN_ID="${CONTROL_TOWER_RUN_ID:-}"
if [[ -z "$WORK_ID" ]]; then
  WORK_ID="work-job-$($PYTHON_BIN -c 'import uuid; print(uuid.uuid4().hex)')"
fi
if [[ -z "$RUN_ID" ]]; then
  RUN_ID="run-$($PYTHON_BIN -c 'import uuid; print(uuid.uuid4().hex)')"
fi
GENERATION="${CONTROL_TOWER_GENERATION:-1}"
ORIGIN="${CONTROL_TOWER_ORIGIN:-scheduled-job}"
HEARTBEAT_SECONDS="${CONTROL_TOWER_HEARTBEAT_SECONDS:-60}"

WORK_ARGS=(
  --work-id "$WORK_ID"
  --run-id "$RUN_ID"
  --generation "$GENERATION"
  --origin "$ORIGIN"
)
if [[ -n "${CONTROL_TOWER_MODEL_FAMILY:-}" ]]; then
  WORK_ARGS+=(--model-family "$CONTROL_TOWER_MODEL_FAMILY")
fi
if [[ -n "${CONTROL_TOWER_MODEL_ID:-}" ]]; then
  WORK_ARGS+=(--model-id "$CONTROL_TOWER_MODEL_ID")
fi
if [[ "${CONTROL_TOWER_ROUTE_VERIFIED:-0}" == "1" ]]; then
  WORK_ARGS+=(--route-verified)
fi

"$PYTHON_BIN" "$PUBLISH" --agent "$AGENT" --type job --status active --title "$TITLE" --tool "$TOOL" --detail "$DETAIL" --job --brain-feed --work-event start --phase executing "${WORK_ARGS[@]}" >/dev/null

set +e
"$@" &
COMMAND_PID=$!
(
  while kill -0 "$COMMAND_PID" 2>/dev/null; do
    sleep "$HEARTBEAT_SECONDS"
    if kill -0 "$COMMAND_PID" 2>/dev/null; then
      "$PYTHON_BIN" "$PUBLISH" --agent "$AGENT" --type job --status active --title "$TITLE" --tool "$TOOL" --detail "$DETAIL" --job --brain-feed --work-event heartbeat --phase executing "${WORK_ARGS[@]}" >/dev/null || true
    fi
  done
) &
HEARTBEAT_PID=$!
wait "$COMMAND_PID"
STATUS=$?
kill "$HEARTBEAT_PID" 2>/dev/null || true
wait "$HEARTBEAT_PID" 2>/dev/null || true
set -e

if [[ $STATUS -eq 0 ]]; then
  "$PYTHON_BIN" "$PUBLISH" --agent "$AGENT" --type job --status done --title "$TITLE" --tool "$TOOL" --detail "$DETAIL completed" --job --brain-feed --rollup --work-event terminal --phase complete "${WORK_ARGS[@]}" >/dev/null
else
  "$PYTHON_BIN" "$PUBLISH" --agent "$AGENT" --type blocked --status error --title "$TITLE failed" --tool "$TOOL" --detail "$DETAIL exited with status $STATUS" --job --brain-feed --rollup --work-event terminal --phase failed "${WORK_ARGS[@]}" >/dev/null
fi

exit "$STATUS"
