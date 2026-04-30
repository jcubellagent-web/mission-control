#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
LOG_DIR="${HOME}/scripts/logs"
mkdir -p "$LOG_DIR"
cd "$ROOT_DIR"

status_path="data/mission-control-regression-status.json"
log_path="$LOG_DIR/mission_control_watchdog.log"
publisher="${HOME}/scripts/mission_control_brain_feed_publish.py"

publish_bf() {
  local status="$1"
  local objective="$2"
  local step="$3"
  if [[ -x "$publisher" || -f "$publisher" ]]; then
    python3 "$publisher" \
      --agent jaimes \
      --status "$status" \
      --tool cron \
      --cron "Mission Control watchdog" \
      --objective "$objective" \
      --step "$step" >/dev/null 2>&1 || true
  fi
}

run_ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
publish_bf active "Mission Control watchdog running" "Started regression and screenshot checks"
{
  echo "=== $run_ts mission-control watchdog ==="
  python3 scripts/mission_control_regression_check.py \
    --check-joshex-freshness \
    --max-joshex-age-min 45 \
    --write-status "$status_path"
  python3 scripts/mission_control_screenshot_diff.py --max-diff-ratio 0.08
} >> "$log_path" 2>&1 || {
  tail_msg=$(tail -n 12 "$log_path" | tr '\n' ' ' | cut -c1-180)
  bash ~/scripts/jaimes_bf_push.sh "Mission Control watchdog alert: ${tail_msg}" idle exec || true
  publish_bf blocked "Mission Control watchdog failed" "Regression or screenshot check failed"
  exit 1
}

bash ~/scripts/jaimes_bf_push.sh "Mission Control watchdog clean" idle exec || true
publish_bf done "Mission Control watchdog clean" "Regression and screenshot checks passed"
