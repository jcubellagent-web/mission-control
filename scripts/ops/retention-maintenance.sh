#!/bin/zsh
set -u

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
LOG_DIR="$HERMES_HOME/logs"
ARCHIVE_DIR="$HERMES_HOME/archive/logs"
MAX_ACTIVE_BYTES=$((20 * 1024 * 1024))
KEEP_ACTIVE_BYTES=$((5 * 1024 * 1024))

"$HERMES_HOME/hermes-agent/venv/bin/python" -m hermes_cli.main checkpoints prune \
  --retention-days 7 --max-size-mb 500 >/dev/null 2>&1 || true

mkdir -p "$ARCHIVE_DIR"

find "$LOG_DIR" -maxdepth 1 -type f -print0 | while IFS= read -r -d '' path; do
  bytes=$(stat -f%z "$path" 2>/dev/null || echo 0)
  if (( bytes > MAX_ACTIVE_BYTES )); then
    stamp=$(date +%Y%m%d-%H%M%S)
    gzip -c "$path" > "$ARCHIVE_DIR/$(basename "$path").$stamp.gz"
    tmp=$(mktemp)
    tail -c "$KEEP_ACTIVE_BYTES" "$path" > "$tmp"
    cat "$tmp" > "$path"
    rm -f "$tmp"
  fi
done
