#!/bin/zsh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/opt/homebrew/sbin"
export HOME="${HOME:-/Users/josh2.0}"  # ensure HOME is set in cron environment
ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
WORKSPACE_DIR=$(cd -- "$ROOT_DIR/.." && pwd)
KIOSK_DIR="$WORKSPACE_DIR/kiosk-dashboard"
if [[ -d "$KIOSK_DIR" ]]; then
  cd "$KIOSK_DIR"
  npm run model:sync
else
  echo "mission-control: kiosk-dashboard missing; skipping model sync"
fi
cd "$ROOT_DIR"
publisher="${HOME}/scripts/mission_control_brain_feed_publish.py"

publish_bf() {
  local status="$1"
  local objective="$2"
  local step="$3"
  if [[ -x "$publisher" || -f "$publisher" ]]; then
    python3 "$publisher" \
      --agent josh \
      --status "$status" \
      --tool cron \
      --cron "Mission Control publisher" \
      --objective "$objective" \
      --step "$step" >/dev/null 2>&1 || true
  fi
}

publish_bf active "Publishing Mission Control data" "Started dashboard refresh"
trap 'rc=$?; if [[ $rc -ne 0 ]]; then publish_bf blocked "Mission Control publisher failed" "Refresh exited with code $rc"; fi' EXIT

sync_branch() {
  local phase="$1"
  echo "mission-control: syncing origin/main (${phase})"
  git fetch origin main
  if ! git rebase --autostash origin/main; then
    echo "mission-control: rebase failed during ${phase}; aborting publish so cron does not create another divergent commit" >&2
    git rebase --abort >/dev/null 2>&1 || true
    exit 1
  fi
}

sync_branch "preflight"

# Pull J.A.I.N / JAIMES brain feed and newsfeed from remote (non-blocking on failure)
bash scripts/jain_bf_pull.sh || true
bash scripts/jain_newsfeed_sync.sh || true
bash scripts/jain_breaking_highlights_sync.sh || true
pull_json() {
  local remote_path="$1"
  local dest="$2"
  local fallback="$3"
  local tmp
  tmp=$(mktemp)
  if ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no \
      jc_agent@100.121.89.84 \
      "cat ${remote_path}" > "$tmp" 2>/dev/null && python3 -m json.tool "$tmp" >/dev/null 2>&1; then
    mv "$tmp" "$dest"
  else
    rm -f "$tmp"
    if [[ ! -s "$dest" ]] || ! python3 -m json.tool "$dest" >/dev/null 2>&1; then
      printf '%s
' "$fallback" > "$dest"
    fi
    echo "mission-control: kept fallback for $dest"
  fi
}

# Pull remote JSON safely; never overwrite valid local JSON with empty SSH output.
pull_json "/Users/jc_agent/.openclaw/workspace/mission-control/data/jaimes-brain-feed.json" data/jaimes-brain-feed.json '{"agent":"JAIMES","status":"unknown","active":false}'
pull_json "/Users/jc_agent/.openclaw/workspace/mission-control/data/moltworld-state.json" data/moltworld-state.json '{}'
python3 scripts/update_mission_control.py
python3 scripts/mission_control_visual_canaries.py || true
python3 scripts/update_mission_control.py
# Commit dashboard data — brain-feed.json is intentionally excluded.
# Brain feed active state is managed by Supabase Realtime (bf_push.sh).
# Pushing brain-feed.json to GH Pages would overwrite live active state every 5min.
ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
git add data/dashboard-data.json data/modelUsage.json data/jain-brain-feed.json data/jaimes-brain-feed.json data/agent-comms.json data/jain-api-costs.json data/eight-sleep-data.json data/moltworld-data.json data/moltworld-state.json data/jain-breaking-highlights.json data/mission-control-canaries.json data/capability-canary.json
if git diff --cached --quiet; then
  echo "mission-control: no changes"
  publish_bf done "Mission Control data current" "No dashboard changes to publish"
  trap - EXIT
  exit 0
fi
git commit -m "dashboard: auto refresh $ts"

# Prefer saved token file (works in cron without keyring), fall back to gh CLI
GH_TOKEN=""
if [[ -f "${HOME}/.secrets/gh_token" ]]; then
  GH_TOKEN=$(cat "${HOME}/.secrets/gh_token")
else
  GH_TOKEN=$(gh auth token 2>/dev/null || true)
fi
if [[ -n "$GH_TOKEN" ]]; then
  AUTH_HEADER=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')
  sync_branch "pre-push"
  git -c http.https://github.com/.extraheader="AUTHORIZATION: basic $AUTH_HEADER" push origin HEAD:main
else
  sync_branch "pre-push"
  git push origin HEAD:main
fi
publish_bf done "Mission Control data published" "Pushed dashboard refresh to main"
trap - EXIT
