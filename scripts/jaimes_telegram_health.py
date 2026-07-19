#!/usr/bin/env python3
"""Josh 2.0 supervisor for JAIMES Telegram/Hermes/CUA health.

This loop is intentionally deterministic and small:
observe -> decide -> recover once if safe -> verify -> publish safe status.
Routine healthy checks update a heartbeat and local state only. Brain Feed is
updated on failures, recoveries, state changes, or explicit verification runs.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


HOME = Path.home()
ROOT = HOME / "agent-loops"
STATE_DIR = ROOT / "state"
LOCK_DIR = STATE_DIR / "locks"
LOG_DIR = ROOT / "logs"
STATE_PATH = STATE_DIR / "jaimes-telegram-health.json"
LOCK_PATH = LOCK_DIR / "jaimes-telegram-health.lock"
LOG_PATH = LOG_DIR / "jaimes-telegram-health.log"
MC_ROOT = HOME / ".openclaw" / "workspace" / "mission-control"
# This runs on Josh 2.0, so do not depend on JOSHeX-only SSH aliases.
JAIMES_ALIAS = os.environ.get("JAIMES_SSH_ALIAS", "jc_agent@100.121.89.84")
SSH_BASE = [
    "ssh",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=10",
    "-o", "ServerAliveCountMax=2",
]
RECOVERY_COOLDOWN_SECONDS = 15 * 60
LOCK_STALE_SECONDS = 10 * 60
FAST_ACK_STALE_SECONDS = 5 * 60


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


def parse_ts(value: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def compact(value: Any, limit: int = 600) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"{iso()} {message}\n")


@contextlib.contextmanager
def lock_or_exit() -> Any:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("loop already running") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield


def run(cmd: list[str], timeout: int = 45) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def remote(script: str, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    return run([*SSH_BASE, JAIMES_ALIAS, "/bin/zsh", "-lc", script], timeout=timeout)


def probe_jaimes() -> dict[str, Any]:
    probe = r'''
python3 - <<'PY'
import datetime as dt
import json
import pathlib
import subprocess

def read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}

home = pathlib.Path.home()
gateway = read_json(home / ".hermes" / "gateway_state.json")
sessions = read_json(home / ".hermes" / "sessions" / "sessions.json")
fast_ack_state = read_json(home / ".openclaw" / "telegram" / "jaimes_fast_ack_state.json")
brain = read_json(home / ".openclaw" / "workspace" / "mission-control" / "data" / "jaimes-brain-feed.json")
heartbeat = read_json(home / ".openclaw" / "workspace" / "mission-control" / "data" / "agent-heartbeats.json")
status = subprocess.run(["/bin/zsh", "-lc", "hermes status"], capture_output=True, text=True, timeout=35)
launch = subprocess.run(["/bin/zsh", "-lc", "launchctl print gui/$(id -u)/ai.hermes.gateway 2>/dev/null | sed -n '1,80p'"], capture_output=True, text=True, timeout=10)
fast_ack_launch = subprocess.run(["/bin/zsh", "-lc", "launchctl print gui/$(id -u)/ai.jaimes.telegram-fast-ack 2>/dev/null | sed -n '1,80p'"], capture_output=True, text=True, timeout=10)
cua_launch = subprocess.run(["/bin/zsh", "-lc", "launchctl print gui/$(id -u)/ai.jaimes.cua-driver 2>/dev/null | sed -n '1,80p'"], capture_output=True, text=True, timeout=10)
ps = subprocess.run(["/bin/zsh", "-lc", "ps ax -o pid=,command= | egrep 'hermes.*gateway|telegram|jaimes_telegram|ai.hermes|cua-driver' | egrep -v egrep || true"], capture_output=True, text=True, timeout=10)
cua_status = subprocess.run(["/bin/zsh", "-lc", "/Users/jc_agent/.local/bin/cua-driver status 2>&1"], capture_output=True, text=True, timeout=12)
cua_perms = subprocess.run(["/bin/zsh", "-lc", "/Users/jc_agent/.local/bin/cua-driver permissions status --json 2>/dev/null"], capture_output=True, text=True, timeout=12)
cua_tools = subprocess.run(["/bin/zsh", "-lc", "/Users/jc_agent/.local/bin/cua-driver list-tools 2>&1 | sed -n '1,220p'"], capture_output=True, text=True, timeout=15)
cua_screen = subprocess.run(["/bin/zsh", "-lc", "python3 - <<'PYCALL'\nimport json, subprocess\ntry:\n    p = subprocess.run(['/Users/jc_agent/.local/bin/cua-driver', 'call', 'get_screen_size', '{}'], capture_output=True, text=True, timeout=8)\n    print(json.dumps({'returncode': p.returncode, 'stdout': p.stdout[-800:], 'stderr': p.stderr[-400:]}))\nexcept subprocess.TimeoutExpired:\n    print(json.dumps({'timeout': True}))\nPYCALL"], capture_output=True, text=True, timeout=12)
cua_version = subprocess.run(["/bin/zsh", "-lc", "/Users/jc_agent/.local/bin/cua-driver --version 2>&1"], capture_output=True, text=True, timeout=8)
cua_update = subprocess.run(["/bin/zsh", "-lc", "/Users/jc_agent/.local/bin/cua-driver check-update --json 2>/dev/null"], capture_output=True, text=True, timeout=15)
try:
    cua_perms_json = json.loads(cua_perms.stdout or "{}")
except Exception:
    cua_perms_json = {"_error": cua_perms.stderr[-400:] or cua_perms.stdout[-400:]}
try:
    cua_update_json = json.loads(cua_update.stdout or "{}")
except Exception:
    cua_update_json = {"_error": cua_update.stderr[-400:] or cua_update.stdout[-400:]}
try:
    cua_screen_json = json.loads(cua_screen.stdout or "{}")
except Exception:
    cua_screen_json = {"_error": cua_screen.stderr[-400:] or cua_screen.stdout[-400:]}
payload = {
    "checkedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "hermesStatusCode": status.returncode,
    "hermesStatus": status.stdout[-4000:],
    "hermesStatusErr": status.stderr[-1200:],
    "launchd": launch.stdout[-2000:],
    "fastAckLaunchd": fast_ack_launch.stdout[-2000:],
    "fastAckState": {
        "status": fast_ack_state.get("status"),
        "lastCheckedAt": fast_ack_state.get("last_checked_at"),
        "identity": fast_ack_state.get("telegram_identity"),
        "lastSurfaceAt": fast_ack_state.get("last_attempt_at") or fast_ack_state.get("last_sent_at"),
        "lastSurfaceOk": (fast_ack_state.get("last_result") or {}).get("ok") if isinstance(fast_ack_state.get("last_result"), dict) else None,
        "surfaceIndeterminate": bool((fast_ack_state.get("last_result") or {}).get("surface_indeterminate")) if isinstance(fast_ack_state.get("last_result"), dict) else False,
        "activeCardCount": sum(
            1 for card in (fast_ack_state.get("active_cards") or {}).values()
            if isinstance(card, dict) and card.get("status") != "done"
        ) if isinstance(fast_ack_state.get("active_cards"), dict) else 0,
        "deliveryError": {
            "at": (fast_ack_state.get("last_telegram_delivery_error") or {}).get("at"),
            "method": (fast_ack_state.get("last_telegram_delivery_error") or {}).get("method"),
        } if isinstance(fast_ack_state.get("last_telegram_delivery_error"), dict) else None,
    },
    "cuaLaunchd": cua_launch.stdout[-2000:],
    "processes": ps.stdout[-2000:],
    "cua": {
        "statusCode": cua_status.returncode,
        "status": cua_status.stdout[-1200:] or cua_status.stderr[-1200:],
        "permissionsCode": cua_perms.returncode,
        "permissions": cua_perms_json,
        "toolsCode": cua_tools.returncode,
        "tools": cua_tools.stdout[-3000:] or cua_tools.stderr[-1200:],
        "screenProbe": cua_screen_json,
        "version": (cua_version.stdout or cua_version.stderr).strip(),
        "update": cua_update_json,
        "launchd": cua_launch.stdout[-1600:],
    },
    "gateway": gateway,
    "sessions": sessions,
    "brainFeed": {
        "objective": brain.get("objective"),
        "status": brain.get("status"),
        "updatedAt": brain.get("updatedAt"),
        "model": brain.get("model"),
        "auth": brain.get("auth"),
    },
    "heartbeats": [
        row for row in heartbeat.get("heartbeats", [])
        if row.get("agent") in {"jaimes", "jain"}
    ][:12],
}
print(json.dumps(payload))
PY
'''
    proc = remote(probe, timeout=55)
    if proc.returncode != 0:
        return {
            "checkedAt": iso(),
            "probeError": compact(proc.stderr or proc.stdout or f"ssh returned {proc.returncode}"),
            "ok": False,
        }
    try:
        return json.loads(proc.stdout)
    except Exception as exc:
        return {"checkedAt": iso(), "probeError": f"Probe JSON parse failed: {exc}", "raw": proc.stdout[-2000:]}


def evaluate(probe: dict[str, Any]) -> tuple[str, list[str], set[str]]:
    issues: list[str] = []
    recovery_targets: set[str] = set()
    gateway = probe.get("gateway") if isinstance(probe.get("gateway"), dict) else {}
    platforms = gateway.get("platforms") if isinstance(gateway.get("platforms"), dict) else {}
    telegram = platforms.get("telegram") if isinstance(platforms.get("telegram"), dict) else {}
    sessions = probe.get("sessions") if isinstance(probe.get("sessions"), dict) else {}
    telegram_sessions = [
        row for key, row in sessions.items()
        if "telegram" in str(key).lower() and isinstance(row, dict)
    ] if isinstance(sessions, dict) else []

    if probe.get("probeError"):
        issues.append(f"Cannot reach JAIMES over SSH: {probe.get('probeError')}")
    if gateway.get("gateway_state") != "running":
        issues.append("Hermes gateway state is not running.")
        recovery_targets.add("gateway")
    if telegram.get("state") != "connected":
        issues.append("Telegram platform is not connected.")
        recovery_targets.add("gateway")
    launchd_live = "state = running" in str(probe.get("launchd") or "")
    if not launchd_live and not str(probe.get("processes") or "").strip():
        issues.append("No Hermes/Telegram watcher process was visible.")
        recovery_targets.add("gateway")
    fast_ack_live = "state = running" in str(probe.get("fastAckLaunchd") or "")
    if not fast_ack_live:
        issues.append("JAIMES Telegram fast-ack watcher is not running.")
        recovery_targets.add("fast_ack")
    fast_ack_state = probe.get("fastAckState") if isinstance(probe.get("fastAckState"), dict) else {}
    fast_ack_identity = fast_ack_state.get("identity") if isinstance(fast_ack_state.get("identity"), dict) else {}
    if fast_ack_identity.get("ok") is not True:
        issues.append("JAIMES Telegram fast-ack bot identity is not verified.")
        recovery_targets.add("fast_ack")
    fast_ack_checked_at = parse_ts(fast_ack_state.get("lastCheckedAt"))
    if (
        fast_ack_checked_at is None
        or (utc_now() - fast_ack_checked_at).total_seconds()
        > FAST_ACK_STALE_SECONDS
    ):
        issues.append("JAIMES Telegram fast-ack has not completed a recent poll.")
        recovery_targets.add("fast_ack")
    delivery_error = fast_ack_state.get("deliveryError") if isinstance(fast_ack_state.get("deliveryError"), dict) else {}
    unresolved_delivery_error = bool(delivery_error)
    last_surface_at = parse_ts(fast_ack_state.get("lastSurfaceAt"))
    recent_failed_surface = bool(
        fast_ack_state.get("lastSurfaceOk") is False
        and (last_surface_at is None or (utc_now() - last_surface_at) <= dt.timedelta(minutes=30))
    )
    if unresolved_delivery_error or recent_failed_surface:
        issues.append("A JAIMES Telegram card send or edit still lacks a confirmed receipt.")
    if not telegram_sessions:
        issues.append("No Telegram session binding is present.")
        recovery_targets.add("gateway")
    elif all(row.get("resume_pending") for row in telegram_sessions):
        issues.append("All Telegram sessions are marked resume_pending.")
        recovery_targets.add("gateway")

    brain_stamp = parse_ts((probe.get("brainFeed") or {}).get("updatedAt") if isinstance(probe.get("brainFeed"), dict) else None)
    if brain_stamp and (utc_now() - brain_stamp) > dt.timedelta(hours=3):
        issues.append("JAIMES Brain Feed sidecar is stale.")

    cua = probe.get("cua") if isinstance(probe.get("cua"), dict) else {}
    cua_status_text = str(cua.get("status") or "")
    cua_tools_text = str(cua.get("tools") or "")
    cua_permissions = cua.get("permissions") if isinstance(cua.get("permissions"), dict) else {}
    if cua.get("statusCode") != 0 or "daemon is running" not in cua_status_text:
        issues.append("Computer Use driver is not running.")
        recovery_targets.add("cua")
    if not cua_permissions.get("accessibility"):
        issues.append("Computer Use Accessibility permission is missing.")
    if not cua_permissions.get("screen_recording"):
        issues.append("Computer Use Screen Recording permission is missing.")
    if cua.get("toolsCode") != 0:
        issues.append("Computer Use tool listing failed.")
        recovery_targets.add("cua")
    else:
        missing_tools = [
            tool for tool in ("list_apps", "get_screen_size", "get_accessibility_tree")
            if tool not in cua_tools_text
        ]
        if missing_tools:
            issues.append("Computer Use is missing expected tools: " + ", ".join(missing_tools) + ".")
    screen_probe = cua.get("screenProbe") if isinstance(cua.get("screenProbe"), dict) else {}
    if screen_probe.get("timeout"):
        issues.append("Computer Use screen probe timed out.")
        recovery_targets.add("cua")
    elif screen_probe.get("returncode") not in {0, None}:
        issues.append("Computer Use screen probe failed.")
        recovery_targets.add("cua")

    return ("ok" if not issues else "unhealthy"), issues, recovery_targets


def brain_feed_needs_reconcile(probe: dict[str, Any]) -> bool:
    brain = probe.get("brainFeed") if isinstance(probe.get("brainFeed"), dict) else {}
    status = str(brain.get("status") or "").lower()
    objective = str(brain.get("objective") or "").lower()
    updated = parse_ts(brain.get("updatedAt"))
    if status in {"error", "blocked", "failed"}:
        return True
    if updated and (utc_now() - updated) > dt.timedelta(hours=1):
        return True
    stale_markers = (
        "sorare pre-lock loop",
        "daily missions",
        "provider response",
        "computer_use",
        "cua-driver",
        "resource temporarily unavailable",
    )
    return any(marker in objective for marker in stale_markers)


def reconcile_visibility() -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(MC_ROOT / "scripts" / "state_visibility_guard.py"),
        "--repair",
        "--remote-jaimes",
        "--publish",
    ]
    proc = run(cmd, timeout=75)
    return {
        "attemptedAt": iso(),
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": compact(proc.stdout, 1800),
        "stderr": compact(proc.stderr, 1200),
    }


def can_recover(previous: dict[str, Any]) -> bool:
    stamp = parse_ts(previous.get("lastRecoveryAt"))
    return not stamp or (utc_now() - stamp).total_seconds() > RECOVERY_COOLDOWN_SECONDS


def recover(targets: set[str]) -> dict[str, Any]:
    commands = ["set -e"]
    if "gateway" in targets:
        commands.append("launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway")
    if "fast_ack" in targets:
        commands.append("launchctl kickstart -k gui/$(id -u)/ai.jaimes.telegram-fast-ack")
    if "cua" in targets:
        commands.append("launchctl kickstart -k gui/$(id -u)/ai.jaimes.cua-driver")
    commands.extend([
        "sleep 4",
        "hermes status | sed -n '1,120p'",
        "/Users/jc_agent/.local/bin/cua-driver status",
        "/Users/jc_agent/.local/bin/cua-driver permissions status --json",
    ])
    cmd = "\n".join(commands)
    proc = remote(cmd, timeout=45)
    return {
        "attemptedAt": iso(),
        "targets": sorted(targets),
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": compact(proc.stdout, 1800),
        "stderr": compact(proc.stderr, 1200),
    }


def heartbeat(status: str, summary: str) -> None:
    cmd = [
        sys.executable,
        str(MC_ROOT / "scripts" / "agent_heartbeat.py"),
        "write",
        "--agent", "jaimes",
        "--node", "jaimes-telegram-health-loop",
        "--status", "ok" if status == "ok" else "error",
        "--summary", compact(summary, 240),
    ]
    run(cmd, timeout=25)


def publish(status: str, title: str, detail: str) -> None:
    cmd = [
        sys.executable,
        str(MC_ROOT / "scripts" / "agent_publish.py"),
        "--agent", "jaimes",
        "--type", "status" if status in {"ok", "info"} else "blocked",
        "--status", "done" if status == "ok" else "error",
        "--title", compact(title, 120),
        "--tool", "jaimes_telegram_health_loop",
        "--detail", compact(detail, 700),
        "--privacy", "dashboard-safe",
        "--brain-feed",
    ]
    run(cmd, timeout=30)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-publish", action="store_true")
    args = parser.parse_args()

    lock_context = contextlib.nullcontext() if args.dry_run else lock_or_exit()
    with lock_context:
        previous = read_json(STATE_PATH, {})
        probe = probe_jaimes()
        visibility_reconcile: dict[str, Any] | None = None
        if not args.dry_run and brain_feed_needs_reconcile(probe):
            visibility_reconcile = reconcile_visibility()
            probe = probe_jaimes()
        status, issues, recovery_targets = evaluate(probe)
        failure_streak = 0 if status == "ok" else int(previous.get("failureStreak") or 0) + 1
        recovery_attempts = 0 if status == "ok" else int(previous.get("recoveryAttemptsSinceHealthy") or 0)
        recovery: dict[str, Any] | None = None

        if (
            status != "ok"
            and failure_streak >= 2
            and recovery_targets
            and recovery_attempts < 3
            and not args.dry_run
            and can_recover(previous)
        ):
            recovery = recover(recovery_targets)
            recovery_attempts += 1
            post_probe = probe_jaimes()
            if brain_feed_needs_reconcile(post_probe):
                visibility_reconcile = reconcile_visibility()
                post_probe = probe_jaimes()
            post_status, post_issues, post_targets = evaluate(post_probe)
            probe = post_probe
            status = post_status
            issues = post_issues
            recovery_targets = post_targets
            if status == "ok":
                failure_streak = 0
                recovery_attempts = 0

        state = {
            "loop": "jaimes-telegram-health",
            "owner": "josh2",
            "target": "jaimes",
            "checkedAt": iso(),
            "status": status,
            "issues": issues,
            "failureStreak": failure_streak,
            "recoveryTargets": sorted(recovery_targets),
            "recoveryAttemptsSinceHealthy": recovery_attempts,
            "recovery": recovery,
            "visibilityReconcile": visibility_reconcile,
            "telegramAlertPolicy": "alert only when recovery fails or approval is required",
            "probe": {
                "gatewayState": (probe.get("gateway") or {}).get("gateway_state") if isinstance(probe.get("gateway"), dict) else None,
                "telegramState": (((probe.get("gateway") or {}).get("platforms") or {}).get("telegram") or {}).get("state") if isinstance(probe.get("gateway"), dict) else None,
                "fastAckState": "running" if "state = running" in str(probe.get("fastAckLaunchd") or "") else "not-running",
                "fastAckIdentity": (probe.get("fastAckState") or {}).get("identity") if isinstance(probe.get("fastAckState"), dict) else None,
                "fastAckDelivery": {
                    "lastSurfaceAt": (probe.get("fastAckState") or {}).get("lastSurfaceAt"),
                    "lastSurfaceOk": (probe.get("fastAckState") or {}).get("lastSurfaceOk"),
                    "surfaceIndeterminate": (probe.get("fastAckState") or {}).get("surfaceIndeterminate"),
                    "activeCardCount": (probe.get("fastAckState") or {}).get("activeCardCount"),
                    "deliveryError": (probe.get("fastAckState") or {}).get("deliveryError"),
                } if isinstance(probe.get("fastAckState"), dict) else None,
                "brainFeed": probe.get("brainFeed"),
                "computerUse": {
                    "status": (probe.get("cua") or {}).get("status") if isinstance(probe.get("cua"), dict) else None,
                    "version": (probe.get("cua") or {}).get("version") if isinstance(probe.get("cua"), dict) else None,
                    "permissions": (probe.get("cua") or {}).get("permissions") if isinstance(probe.get("cua"), dict) else None,
                    "screenProbe": (probe.get("cua") or {}).get("screenProbe") if isinstance(probe.get("cua"), dict) else None,
                    "update": (probe.get("cua") or {}).get("update") if isinstance(probe.get("cua"), dict) else None,
                },
                "telegramSessionPresent": any("telegram" in str(key).lower() for key in (probe.get("sessions") or {})) if isinstance(probe.get("sessions"), dict) else False,
                "activeSessions": len(probe.get("sessions") or {}) if isinstance(probe.get("sessions"), dict) else 0,
            },
        }
        if recovery:
            state["lastRecoveryAt"] = recovery["attemptedAt"]
        else:
            state["lastRecoveryAt"] = previous.get("lastRecoveryAt")
        state["lastHealthyAt"] = iso() if status == "ok" else previous.get("lastHealthyAt")

        summary = "JAIMES Telegram, Hermes, and Computer Use are healthy." if status == "ok" else "JAIMES needs attention: " + "; ".join(issues[:3])
        state_changed = previous.get("status") != status
        last_published = parse_ts(previous.get("lastPublishedAt"))
        unresolved_reminder = bool(
            status != "ok"
            and last_published
            and (utc_now() - last_published) >= dt.timedelta(hours=2)
        )
        if args.dry_run:
            state["lastPublishedAt"] = previous.get("lastPublishedAt")
        else:
            heartbeat(status, summary)
            if args.force_publish or state_changed or recovery or unresolved_reminder:
                publish("ok" if status == "ok" else "error", "JAIMES health loop", summary)
                state["lastPublishedAt"] = iso()
            else:
                state["lastPublishedAt"] = previous.get("lastPublishedAt")
            write_json(STATE_PATH, state)
            log(f"{status}: {summary}")
        print(json.dumps({"ok": status == "ok", **state}, indent=2))
        return 0 if status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
