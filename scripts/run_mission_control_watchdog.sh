#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
LOG_DIR="${HOME}/scripts/logs"
mkdir -p "$LOG_DIR"
cd "$ROOT_DIR"

status_path="data/mission-control-regression-status.json"
log_path="$LOG_DIR/mission_control_watchdog.log"
publisher="${HOME}/scripts/mission_control_brain_feed_publish.py"
jaimes_push="${HOME}/scripts/jaimes_bf_push.sh"

publish_bf() {
  local status="$1"
  local objective="$2"
  local step="$3"
  local event_type="status"
  if [[ "$status" == "done" ]]; then
    event_type="complete"
  elif [[ "$status" == "blocked" || "$status" == "error" ]]; then
    event_type="blocked"
  fi

  if [[ -x "$publisher" || -f "$publisher" ]]; then
    python3 "$publisher" \
      --agent jaimes \
      --status "$status" \
      --tool cron \
      --cron "Control Tower watchdog" \
      --objective "$objective" \
      --step "$step" >/dev/null 2>&1 && return 0
  fi

  python3 scripts/agent_publish.py \
    --agent jaimes \
    --type "$event_type" \
    --title "$objective" \
    --status "$status" \
    --tool "Control Tower watchdog" \
    --detail "$step" \
    --privacy dashboard-safe \
    --brain-feed >/dev/null 2>&1 || true
}

push_jaimes() {
  if [[ -x "$jaimes_push" || -f "$jaimes_push" ]]; then
    bash "$jaimes_push" "$@" || true
  fi
}

refresh_dashboard_data() {
  python3 scripts/update_mission_control.py
}

run_ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
publish_bf active "Control Tower watchdog running" "Started regression, optional screenshot, and live layout checks"
watchdog_ok=1
{
  echo "=== $run_ts mission-control watchdog ==="
  python3 scripts/mission_control_regression_check.py \
    --check-joshex-freshness \
    --max-joshex-age-min 45 \
    --write-status "$status_path"
  if python3 - <<'PY' >/dev/null 2>&1
import PIL  # noqa: F401
import playwright  # noqa: F401
PY
  then
    python3 scripts/mission_control_screenshot_diff.py --max-diff-ratio 0.08
  else
    echo "screenshot_diff skipped: optional Playwright/Pillow dependency missing; live Chrome layout guard still runs"
  fi
  python3 scripts/mission_control_runtime_layout_check.py
} >> "$log_path" 2>&1 || watchdog_ok=0

if ! refresh_dashboard_data >> "$log_path" 2>&1; then
  watchdog_ok=0
fi

if [[ "$watchdog_ok" != "1" ]]; then
  tail_msg=$(tail -n 12 "$log_path" | tr '\n' ' ' | cut -c1-180)
  push_jaimes "Control Tower watchdog alert: ${tail_msg}" idle exec
  publish_bf blocked "Control Tower watchdog failed" "Regression or live layout check failed"
  exit 1
fi

push_jaimes "Control Tower watchdog clean" idle exec
publish_bf done "Control Tower watchdog clean" "Regression, live layout, and dashboard refresh checks passed"
