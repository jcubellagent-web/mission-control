#!/bin/zsh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"
/usr/bin/python3 scripts/memory_sleep_review.py
/usr/bin/python3 scripts/control_tower_autofresh_review.py --publish
