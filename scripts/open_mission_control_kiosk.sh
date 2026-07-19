#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

FORCE_RELOAD="${MISSION_CONTROL_FORCE_RELOAD:-0}"
FORCE_ACTIVATE="0"
if [[ "${1:-}" == "--force" ]]; then
  FORCE_RELOAD="1"
  shift
fi
if [[ "${1:-}" == "--activate" ]]; then
  FORCE_ACTIVATE="1"
  shift
fi

KIOSK_ORIGIN="http://127.0.0.1:5174"
URL="${1:-$KIOSK_ORIGIN/?ct_refresh=$(date -u +%Y%m%dT%H%M%SZ)}"
PROFILE="${CONTROL_TOWER_CHROME_PROFILE:-$HOME/.openclaw/browser-profiles/control-tower-kiosk}"
CHROME_APP="Google Chrome"
SCRIPT_DIR="${0:A:h}"
FOREGROUND_GUARD="$SCRIPT_DIR/control_tower_foreground.py"
LAUNCH_LOCK="${CONTROL_TOWER_KIOSK_LAUNCH_LOCK:-$HOME/.openclaw/state/control-tower-kiosk-launch.lock}"
LAUNCH_OWNER_FILE="${LAUNCH_LOCK}.$$"
LOCK_ACQUIRED="0"
LOCK_WAS_CONTENDED="0"

mkdir -p "$PROFILE"
mkdir -p "${LAUNCH_LOCK:h}"

release_launch_lock() {
  local _owner=""
  if [[ -r "$LAUNCH_LOCK" ]]; then
    read -r _owner < "$LAUNCH_LOCK" || _owner=""
  fi
  if [[ "$LOCK_ACQUIRED" == "1" && "$_owner" == "$$" ]]; then
    rm -f "$LAUNCH_LOCK" 2>/dev/null || true
  fi
  rm -f "$LAUNCH_OWNER_FILE" 2>/dev/null || true
  LOCK_ACQUIRED="0"
}

print -r -- "$$" > "$LAUNCH_OWNER_FILE"
chmod 600 "$LAUNCH_OWNER_FILE"
trap release_launch_lock EXIT
trap 'release_launch_lock; exit 130' INT
trap 'release_launch_lock; exit 143' TERM
for _lock_attempt in {1..240}; do
  if /bin/ln "$LAUNCH_OWNER_FILE" "$LAUNCH_LOCK" 2>/dev/null; then
    LOCK_ACQUIRED="1"
    break
  fi
  LOCK_WAS_CONTENDED="1"
  _lock_pid=""
  if [[ -r "$LAUNCH_LOCK" ]]; then
    read -r _lock_pid < "$LAUNCH_LOCK" || _lock_pid=""
  fi
  if [[ ! "$_lock_pid" =~ '^[0-9]+$' ]] || ! kill -0 "$_lock_pid" 2>/dev/null; then
    rm -f "$LAUNCH_LOCK" 2>/dev/null || true
  fi
  sleep 0.25
done
if [[ "$LOCK_ACQUIRED" != "1" ]]; then
  echo "control-tower: another kiosk launch still owns $LAUNCH_LOCK"
  exit 1
fi

control_tower_url() {
  curl -s --max-time 2 http://127.0.0.1:9224/json 2>/dev/null \
    | python3 -c 'import json,sys
try:
    pages=json.load(sys.stdin)
    page=next((p for p in pages if p.get("type")=="page" and "127.0.0.1" in p.get("url","")), {})
    print(page.get("url",""))
except Exception:
    print("")
' || true
}

wait_for_kiosk_cdp() {
  local _attempt _url
  for _attempt in {1..80}; do
    _url="$(control_tower_url)"
    if [[ "$_url" == "$KIOSK_ORIGIN"* ]]; then
      print -r -- "$_url"
      return 0
    fi
    sleep 0.25
  done
  return 1
}

dedicated_kiosk_pids() {
  /bin/ps -axo pid=,command= \
    | python3 -c 'import shlex,sys
profile=sys.argv[1]
for raw in sys.stdin:
    fields=raw.strip().split(None, 1)
    if len(fields) != 2 or "Google Chrome" not in fields[1]:
        continue
    try:
        args=shlex.split(fields[1])
    except ValueError:
        continue
    if any(arg.startswith("--type=") for arg in args):
        continue
    exact=f"--user-data-dir={profile}" in args
    split_form=any(arg == "--user-data-dir" and index + 1 < len(args) and args[index + 1] == profile for index, arg in enumerate(args))
    if exact or split_form:
        print(fields[0])
' "$PROFILE"
}

server_ready="0"
for _attempt in {1..45}; do
  if curl -fsS --max-time 2 "http://127.0.0.1:5174/" >/dev/null 2>&1; then
    server_ready="1"
    break
  fi
  sleep 1
done
if [[ "$server_ready" != "1" ]]; then
  echo "control-tower: current React kiosk server not ready at http://127.0.0.1:5174/"
  exit 1
fi

ensure_foreground() {
  if [[ "${CONTROL_TOWER_FOREGROUND_CHILD:-0}" == "1" || ! -f "$FOREGROUND_GUARD" ]]; then
    return 0
  fi
  local args=(ensure)
  if [[ "$FORCE_ACTIVATE" == "1" ]]; then
    args+=(--force)
  fi
  /usr/bin/python3 "$FOREGROUND_GUARD" "${args[@]}"
}

current_url="$(control_tower_url)"

# A second forced caller that waited for an in-flight launch must not restart
# the healthy kiosk that the first caller just finished creating.
if [[ "$LOCK_WAS_CONTENDED" == "1" && "$current_url" == "$KIOSK_ORIGIN"* ]]; then
  ensure_foreground
  exit $?
fi

if [[ "$FORCE_RELOAD" != "1" && "$current_url" == "$KIOSK_ORIGIN"* ]]; then
  ensure_foreground
  exit $?
fi

if [[ "$FORCE_RELOAD" != "1" && -n "$(dedicated_kiosk_pids)" ]]; then
  echo "control-tower: kiosk Chrome is already running; leaving it in place"
  ensure_foreground
  exit $?
fi

# Restart only the dedicated kiosk process. Credential/browser automation uses
# a separate persistent profile and must remain available when the kiosk heals.
typeset -a kiosk_pids
kiosk_pids=(${(f)"$(dedicated_kiosk_pids)"})
if (( ${#kiosk_pids[@]} > 0 )); then
  kill -TERM "${kiosk_pids[@]}" 2>/dev/null || true
  for _stop_attempt in {1..40}; do
    if [[ -z "$(dedicated_kiosk_pids)" ]]; then
      break
    fi
    sleep 0.25
  done
fi
if [[ -n "$(dedicated_kiosk_pids)" ]]; then
  echo "control-tower: dedicated kiosk process did not stop; refusing a duplicate launch"
  exit 1
fi

# Singleton links are removed only after the exact profile process is gone and
# only when their recorded owner is not alive. Never touch another Chrome profile.
singleton_pid=""
if [[ -L "$PROFILE/SingletonLock" ]]; then
  singleton_target="$(readlink "$PROFILE/SingletonLock" 2>/dev/null || true)"
  if [[ "$singleton_target" =~ '-([0-9]+)$' ]]; then
    singleton_pid="${match[1]}"
  fi
fi
if [[ "$singleton_pid" =~ '^[0-9]+$' ]] && kill -0 "$singleton_pid" 2>/dev/null; then
  echo "control-tower: kiosk profile lock still belongs to live pid $singleton_pid; refusing launch"
  exit 1
fi
rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonSocket" "$PROFILE/SingletonCookie" 2>/dev/null || true

open -na "$CHROME_APP" --args \
  --user-data-dir="$PROFILE" \
  --remote-debugging-port=9224 \
  --remote-allow-origins=http://127.0.0.1:9224 \
  --no-first-run \
  --disable-session-crashed-bubble \
  --disable-infobars \
  --hide-scrollbars \
  --app="$URL" \
  --kiosk \
  --start-fullscreen \
  --start-maximized

if ! wait_for_kiosk_cdp >/dev/null; then
  echo "control-tower: kiosk Chrome started but CDP did not become ready"
  exit 1
fi
#JAIMES: Never activate Chrome by bundle name here. Josh 2.0 keeps an
# independent auth profile open, so the PID-specific guard must own focus.
ensure_foreground
