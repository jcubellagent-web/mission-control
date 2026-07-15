#!/bin/zsh
set -euo pipefail
set +x

vault_name="${AGENT_ECOSYSTEM_OP_VAULT:-Agent Ecosystem}"
service_name="${AGENT_ECOSYSTEM_OP_TOKEN_SERVICE:-com.josh.agent-ecosystem.op-service-account.$(hostname -s)}"
op_bin="${AGENT_ECOSYSTEM_OP_BIN:-/opt/homebrew/bin/op}"
env_file="${1:-}"

if [[ -z "${env_file}" || "${env_file}" == "--help" ]]; then
  printf 'usage: op_agent_env.sh <op-env-file> -- <command> [args...]\n' >&2
  exit 2
fi
shift || true
if [[ "${1:-}" == "--" ]]; then shift; fi
if [[ $# -eq 0 || ! -f "${env_file}" || ! -x "${op_bin}" ]]; then
  printf '1Password launcher prerequisites are missing.\n' >&2
  exit 2
fi

token="$(/usr/bin/security find-generic-password -a "$USER" -s "${service_name}" -w 2>/dev/null || true)"
if [[ -z "${token}" ]]; then
  printf '1Password service-account token is unavailable for the "%s" vault.\n' "${vault_name}" >&2
  exit 78
fi
export OP_SERVICE_ACCOUNT_TOKEN="${token}"
export OP_BIOMETRIC_UNLOCK_ENABLED=false

# JAIMES: op writes only to a private kernel FIFO; a separate reader owns the
# parent capture pipe. This avoids the CLI 2.34.x launchd daemon pipe deadlock.
lock_file="$HOME/.openclaw/private/op-agent-env.lock"
lock_acquired=false
active_fifo=""
for _ in {1..240}; do
  if /usr/bin/shlock -f "${lock_file}" -p "$$"; then
    lock_acquired=true
    break
  fi
  sleep 0.25
done
if [[ "${lock_acquired}" != true ]]; then
  printf 'Timed out waiting for the host-local 1Password startup lock.\n' >&2
  exit 75
fi

stop_op_daemons() {
  /usr/bin/pkill -TERM -f '^op daemon' >/dev/null 2>&1 || true
  for _ in {1..40}; do
    /usr/bin/pgrep -f '^op daemon' >/dev/null 2>&1 || break
    sleep 0.05
  done
  /usr/bin/pkill -KILL -f '^op daemon' >/dev/null 2>&1 || true
  /bin/rm -f "$HOME/.config/op/op-daemon.sock"
}

cleanup() {
  stop_op_daemons
  [[ -n "${active_fifo}" ]] && /bin/rm -f "${active_fifo}"
  /bin/rm -f "${lock_file}"
}
trap cleanup EXIT HUP INT TERM

while IFS='=' read -r variable_name reference; do
  variable_name="${variable_name//[[:space:]]/}"
  reference="${reference#${reference%%[![:space:]]*}}"
  [[ -z "${variable_name}" || "${variable_name}" == \#* ]] && continue
  if [[ ! "${variable_name}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ || "${reference}" != op://* ]]; then
    printf 'Invalid 1Password environment template.\n' >&2
    exit 65
  fi

  unset "${variable_name}"
  stop_op_daemons
  active_fifo="$HOME/.openclaw/private/.op-read.$$.$RANDOM.fifo"
  /usr/bin/mkfifo -m 600 "${active_fifo}"
  "${op_bin}" --cache=false read "${reference}" >"${active_fifo}" 2>/dev/null &
  reader_source=$!
  (
    for _ in {1..200}; do
      if ! kill -0 "${reader_source}" 2>/dev/null; then
        sleep 2
        /usr/bin/pkill -TERM -f '^op daemon' >/dev/null 2>&1 || true
        sleep 1
        /usr/bin/pkill -KILL -f '^op daemon' >/dev/null 2>&1 || true
        exit 0
      fi
      sleep 0.1
    done
    kill -TERM "${reader_source}" 2>/dev/null || true
    sleep 1
    kill -KILL "${reader_source}" 2>/dev/null || true
    /usr/bin/pkill -TERM -f '^op daemon' >/dev/null 2>&1 || true
    sleep 1
    /usr/bin/pkill -KILL -f '^op daemon' >/dev/null 2>&1 || true
  ) >/dev/null 2>&1 &
  watchdog=$!

  resolved_value="$(
    IFS= read -r fifo_value < "${active_fifo}" || true
    printf '%s' "${fifo_value:-}"
  )"

  kill -TERM "${reader_source}" 2>/dev/null || true
  sleep 0.1
  kill -KILL "${reader_source}" 2>/dev/null || true
  wait "${reader_source}" 2>/dev/null || true
  kill "${watchdog}" 2>/dev/null || true
  wait "${watchdog}" 2>/dev/null || true
  stop_op_daemons
  /bin/rm -f "${active_fifo}"
  active_fifo=""

  if [[ -z "${resolved_value}" ]]; then
    printf '1Password could not resolve %s within the bounded read window.\n' "${variable_name}" >&2
    exit 69
  fi
  export "${variable_name}=${resolved_value}"
  unset resolved_value
done < "${env_file}"

cleanup
trap - EXIT HUP INT TERM
unset token OP_SERVICE_ACCOUNT_TOKEN OP_BIOMETRIC_UNLOCK_ENABLED
exec "$@"
