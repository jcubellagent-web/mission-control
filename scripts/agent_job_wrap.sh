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

"$PYTHON_BIN" "$PUBLISH" --agent "$AGENT" --type job --status active --title "$TITLE" --tool "$TOOL" --detail "$DETAIL" --brain-feed >/dev/null || true

set +e
"$@"
STATUS=$?
set -e

if [[ $STATUS -eq 0 ]]; then
  "$PYTHON_BIN" "$PUBLISH" --agent "$AGENT" --type job --status done --title "$TITLE" --tool "$TOOL" --detail "$DETAIL completed" --job --brain-feed --rollup >/dev/null || true
else
  "$PYTHON_BIN" "$PUBLISH" --agent "$AGENT" --type blocked --status error --title "$TITLE failed" --tool "$TOOL" --detail "$DETAIL exited with status $STATUS" --job --brain-feed --rollup >/dev/null || true
fi

exit "$STATUS"
