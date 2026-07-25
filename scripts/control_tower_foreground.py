#!/usr/bin/env python3
"""Keep the dedicated Control Tower Chrome window on the physical display.

Visible browser or Computer Use work can temporarily suppress restoration with a
short, host-local lease. The lease intentionally contains no task text, URLs, or
account data.
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional


ROOT = Path(__file__).resolve().parents[1]
KIOSK_ORIGIN = "http://127.0.0.1:5174"
KIOSK_CDP_URL = "http://127.0.0.1:9224/json"
KIOSK_PROFILE = Path(
    os.environ.get(
        "CONTROL_TOWER_CHROME_PROFILE",
        str(Path.home() / ".openclaw" / "browser-profiles" / "control-tower-kiosk"),
    )
)
LEASE_PATH = Path(
    os.environ.get(
        "CONTROL_TOWER_FOREGROUND_LEASE_PATH",
        str(Path.home() / ".openclaw" / "state" / "control-tower-foreground-work.json"),
    )
)
DISPLAY_STATE_PATH = Path(
    os.environ.get(
        "CONTROL_TOWER_DISPLAY_STATE_PATH",
        str(ROOT / "data" / "control-tower-display.json"),
    )
)
DEFAULT_LEASE_SECONDS = 180
MAX_LEASE_SECONDS = 600
RECENT_INPUT_SECONDS = 90.0
KIOSK_STARTUP_GRACE_SECONDS = 12.0
PURPOSES = {"browser", "computer-use", "local-ui"}
PROTECTED_FRONTMOST_APPS = {"loginwindow", "SecurityAgent", "ScreenSaverEngine"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def run(cmd: list[str], *, timeout: int = 15, env: Optional[dict[str, str]] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def publish_display_lease(state: Optional[dict[str, Any]]) -> None:
    """Project only public lease fields into the five-second kiosk sidecar."""
    try:
        payload = json.loads(DISPLAY_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    active = bool(state and state.get("active"))
    payload["displayLease"] = {
        "active": active,
        "owner": state.get("owner") if active else None,
        "purpose": state.get("purpose") if active else None,
        "startedAt": state.get("startedAt") if active else None,
        "expiresAt": state.get("expiresAt") if active else None,
    }
    payload["leaseUpdatedAt"] = iso_z(utc_now())
    atomic_write_json(DISPLAY_STATE_PATH, payload)


def process_start_fingerprint(pid: int) -> Optional[str]:
    proc = run(["/bin/ps", "-p", str(pid), "-o", "lstart="])
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def lease_state(
    *,
    path: Optional[Path] = None,
    now: Optional[datetime] = None,
    cleanup: bool = True,
) -> dict[str, Any]:
    lease_path = path or LEASE_PATH
    moment = now or utc_now()
    try:
        payload = json.loads(lease_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"active": False, "reason": "no-lease"}
    except (OSError, json.JSONDecodeError):
        if cleanup:
            _safe_unlink(lease_path)
        return {"active": False, "reason": "invalid-lease"}

    expiry = parse_ts(payload.get("expiresAt"))
    heartbeat = parse_ts(payload.get("heartbeatAt"))
    owner = str(payload.get("owner") or "")
    purpose = str(payload.get("purpose") or "")
    lease_id = str(payload.get("leaseId") or "")
    valid_shape = (
        payload.get("schema") == 1
        and bool(re.fullmatch(r"[A-Za-z0-9._-]{1,48}", owner))
        and purpose in PURPOSES
        and bool(lease_id)
        and expiry is not None
        and heartbeat is not None
    )
    if not valid_shape:
        if cleanup:
            _safe_unlink(lease_path)
        return {"active": False, "reason": "invalid-lease"}

    assert expiry is not None and heartbeat is not None
    if expiry <= moment or expiry - heartbeat > timedelta(seconds=MAX_LEASE_SECONDS + 1):
        if cleanup:
            _safe_unlink(lease_path)
        return {"active": False, "reason": "expired-lease"}

    pid_value = payload.get("pid")
    if pid_value is not None:
        try:
            pid = int(pid_value)
        except (TypeError, ValueError):
            pid = 0
        expected_start = str(payload.get("processStart") or "")
        if pid <= 1 or not expected_start or process_start_fingerprint(pid) != expected_start:
            if cleanup:
                _safe_unlink(lease_path)
            return {"active": False, "reason": "dead-owner"}

    return {
        "active": True,
        "reason": "active-work",
        "owner": owner,
        "purpose": purpose,
        "startedAt": payload.get("startedAt"),
        "heartbeatAt": payload.get("heartbeatAt"),
        "expiresAt": payload.get("expiresAt"),
        "leaseId": lease_id,
    }


def begin_lease(
    *,
    owner: str,
    purpose: str,
    ttl_seconds: int = DEFAULT_LEASE_SECONDS,
    pid: Optional[int] = None,
    path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,48}", owner):
        raise ValueError("owner must use 1-48 letters, numbers, dots, dashes, or underscores")
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of: {', '.join(sorted(PURPOSES))}")
    if not 30 <= ttl_seconds <= MAX_LEASE_SECONDS:
        raise ValueError(f"ttl-seconds must be between 30 and {MAX_LEASE_SECONDS}")

    lease_path = path or LEASE_PATH
    existing = lease_state(path=lease_path, now=now)
    if existing.get("active"):
        raise RuntimeError(
            f"visible work is already leased by {existing.get('owner')} until {existing.get('expiresAt')}"
        )

    process_start = None
    if pid is not None:
        process_start = process_start_fingerprint(pid)
        if not process_start:
            raise ValueError(f"pid {pid} is not running")

    moment = now or utc_now()
    payload: dict[str, Any] = {
        "schema": 1,
        "leaseId": secrets.token_urlsafe(24),
        "owner": owner,
        "purpose": purpose,
        "startedAt": iso_z(moment),
        "heartbeatAt": iso_z(moment),
        "expiresAt": iso_z(moment + timedelta(seconds=ttl_seconds)),
    }
    if pid is not None:
        payload["pid"] = pid
        payload["processStart"] = process_start
    atomic_write_json(lease_path, payload)
    return payload


def renew_lease(
    *,
    lease_id: str,
    ttl_seconds: int = DEFAULT_LEASE_SECONDS,
    path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    if not 30 <= ttl_seconds <= MAX_LEASE_SECONDS:
        raise ValueError(f"ttl-seconds must be between 30 and {MAX_LEASE_SECONDS}")
    lease_path = path or LEASE_PATH
    state = lease_state(path=lease_path, now=now, cleanup=False)
    if not state.get("active"):
        raise RuntimeError("no active visible-work lease to renew")
    payload = json.loads(lease_path.read_text(encoding="utf-8"))
    if not secrets.compare_digest(str(payload.get("leaseId") or ""), lease_id):
        raise PermissionError("lease id does not match")
    moment = now or utc_now()
    payload["heartbeatAt"] = iso_z(moment)
    payload["expiresAt"] = iso_z(moment + timedelta(seconds=ttl_seconds))
    atomic_write_json(lease_path, payload)
    return payload


def end_lease(*, lease_id: str, path: Optional[Path] = None) -> dict[str, Any]:
    lease_path = path or LEASE_PATH
    try:
        payload = json.loads(lease_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"ended": False, "reason": "no-lease"}
    if not secrets.compare_digest(str(payload.get("leaseId") or ""), lease_id):
        raise PermissionError("lease id does not match")
    _safe_unlink(lease_path)
    return {"ended": True, "reason": "released"}


def session_is_locked() -> bool:
    proc = subprocess.run(
        ["/usr/sbin/ioreg", "-n", "Root", "-d1", "-a"],
        capture_output=True,
        timeout=5,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return False
    try:
        root: Any = plistlib.loads(proc.stdout)
        if isinstance(root, list):
            root = root[0] if root else {}
        users = root.get("IOConsoleUsers") or []
        for user in users:
            if not isinstance(user, dict) or not user.get("kCGSSessionOnConsoleKey"):
                continue
            return bool(user.get("CGSSessionScreenIsLocked")) or not bool(
                user.get("kCGSessionLoginDoneKey", True)
            )
    except (plistlib.InvalidFileException, AttributeError, TypeError):
        return False
    return False


def hid_idle_seconds() -> Optional[float]:
    proc = run(["/usr/sbin/ioreg", "-r", "-c", "IOHIDSystem", "-d", "1"], timeout=5)
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', proc.stdout)
    if proc.returncode != 0 or not match:
        return None
    return int(match.group(1)) / 1_000_000_000


def kiosk_process_pids() -> list[int]:
    proc = run(["/bin/ps", "-axo", "pid=,command="], timeout=10)
    needle = f"--user-data-dir={KIOSK_PROFILE}"
    candidates: list[int] = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped or "Google Chrome" not in stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        if "--type=" in command or not re.search(rf"(?:^|\s){re.escape(needle)}(?:\s|$)", command):
            continue
        try:
            candidates.append(int(pid_text))
        except ValueError:
            continue
    return sorted(set(candidates))


def singleton_lock_pid() -> Optional[int]:
    try:
        target = os.readlink(KIOSK_PROFILE / "SingletonLock")
    except OSError:
        return None
    match = re.search(r"-(\d+)$", target)
    return int(match.group(1)) if match else None


def cdp_listener_pid() -> Optional[int]:
    proc = run(["/usr/sbin/lsof", "-nP", "-iTCP:9224", "-sTCP:LISTEN", "-t"], timeout=5)
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        try:
            return int(line.strip())
        except ValueError:
            continue
    return None


def find_kiosk_pid() -> Optional[int]:
    candidates = kiosk_process_pids()
    if not candidates:
        return None
    # A duplicate profile launch can leave an orphan root process. Require all
    # available ownership signals to agree, then select that exact root. When a
    # single process is still starting and neither signal exists yet, return it
    # only so the startup-grace path can wait for CDP instead of relaunching.
    owners = [pid for pid in (cdp_listener_pid(), singleton_lock_pid()) if pid is not None]
    if owners:
        if len(set(owners)) != 1:
            return None
        owner_pid = owners[0]
        return owner_pid if owner_pid in candidates else None
    return candidates[0] if len(candidates) == 1 else None


def wait_for_cdp(
    cdp_ready_fn: Callable[[], bool],
    *,
    timeout_seconds: float = KIOSK_STARTUP_GRACE_SECONDS,
    interval_seconds: float = 0.5,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        if cdp_ready_fn():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval_seconds, remaining))


def cdp_has_control_tower() -> bool:
    return control_tower_target_id() is not None


def control_tower_target_id() -> Optional[str]:
    try:
        with urllib.request.urlopen(KIOSK_CDP_URL, timeout=2) as response:
            pages = json.load(response)
    except Exception:
        return None
    for page in pages:
        if not isinstance(page, dict):
            continue
        if page.get("type") == "page" and str(page.get("url") or "").startswith(KIOSK_ORIGIN):
            target_id = str(page.get("id") or "")
            return target_id or None
    return None


def frontmost_application() -> Optional[dict[str, Any]]:
    script = (
        'ObjC.import("AppKit"); '
        'const a=$.NSWorkspace.sharedWorkspace.frontmostApplication; '
        'a ? JSON.stringify({pid:Number(a.processIdentifier), '
        'name:ObjC.unwrap(a.localizedName)}) : "null"'
    )
    proc = run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script], timeout=8)
    if proc.returncode != 0:
        return None
    try:
        value = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def activate_kiosk_process(kiosk_pid: int, _previous_pid: Optional[int]) -> tuple[bool, str]:
    target_id = control_tower_target_id()
    if not target_id:
        return False, "Control Tower CDP target is unavailable for exact activation."
    activate_url = f"http://127.0.0.1:9224/json/activate/{urllib.parse.quote(target_id, safe='')}"
    last_error = ""
    for _attempt in range(2):
        try:
            request = urllib.request.Request(activate_url, method="PUT")
            with urllib.request.urlopen(request, timeout=3) as response:
                response.read(256)
        except Exception as exc:
            last_error = type(exc).__name__
            time.sleep(0.4)
            continue
        time.sleep(0.8)
        frontmost = frontmost_application()
        if frontmost and int(frontmost.get("pid") or 0) == kiosk_pid:
            return True, "Control Tower CDP target owns the physical foreground."
        last_error = "frontmost PID did not match the kiosk"
    return False, f"Exact Control Tower target activation failed verification ({last_error or 'unknown'})."


def ensure_foreground(
    *,
    force: bool = False,
    repair: bool = False,
    lease_path: Optional[Path] = None,
    now: Optional[datetime] = None,
    locked_fn: Optional[Callable[[], bool]] = None,
    idle_fn: Optional[Callable[[], Optional[float]]] = None,
    kiosk_pid_fn: Optional[Callable[[], Optional[int]]] = None,
    cdp_ready_fn: Optional[Callable[[], bool]] = None,
    frontmost_fn: Optional[Callable[[], Optional[dict[str, Any]]]] = None,
    activate_fn: Optional[Callable[[int, Optional[int]], tuple[bool, str]]] = None,
    startup_wait_fn: Optional[Callable[[Callable[[], bool]], bool]] = None,
) -> dict[str, Any]:
    locked = (locked_fn or session_is_locked)()
    if locked:
        return {"ok": True, "status": "deferred", "reason": "session-locked"}

    active_lease = lease_state(path=lease_path, now=now)
    if lease_path is None or lease_path == LEASE_PATH:
        publish_display_lease(active_lease)
    if active_lease.get("active") and not force:
        return {
            "ok": True,
            "status": "deferred",
            "reason": "active-visible-work",
            "work": {key: active_lease.get(key) for key in ("owner", "purpose", "expiresAt")},
        }

    pid_lookup = kiosk_pid_fn or find_kiosk_pid
    cdp_lookup = cdp_ready_fn or cdp_has_control_tower
    frontmost_lookup = frontmost_fn or frontmost_application
    kiosk_pid = pid_lookup()
    cdp_ready = cdp_lookup()
    frontmost = frontmost_lookup() if kiosk_pid and cdp_ready else None
    if frontmost and int(frontmost.get("pid") or 0) == kiosk_pid:
        return {
            "ok": True,
            "status": "foreground",
            "reason": "already-foreground",
            "kioskPid": kiosk_pid,
            "frontmostPid": kiosk_pid,
        }
    if frontmost and str(frontmost.get("name") or "") in PROTECTED_FRONTMOST_APPS:
        return {
            "ok": True,
            "status": "deferred",
            "reason": "protected-system-session",
            "frontmostPid": int(frontmost.get("pid") or 0),
        }

    idle_seconds = (idle_fn or hid_idle_seconds)()
    if not force and idle_seconds is not None and idle_seconds < RECENT_INPUT_SECONDS:
        return {
            "ok": True,
            "status": "deferred",
            "reason": "recent-physical-input",
            "idleSeconds": round(idle_seconds, 1),
        }

    if kiosk_pid and not cdp_ready and repair:
        cdp_ready = (startup_wait_fn or wait_for_cdp)(cdp_lookup)
        frontmost = frontmost_lookup() if cdp_ready else None

    if (not kiosk_pid or not cdp_ready) and repair:
        child_env = os.environ.copy()
        child_env["CONTROL_TOWER_FOREGROUND_CHILD"] = "1"
        opener = ROOT / "scripts" / "open_mission_control_kiosk.sh"
        opened = run([str(opener), "--force"], timeout=75, env=child_env)
        if opened.returncode == 0:
            kiosk_pid = pid_lookup()
            cdp_ready = cdp_lookup()
            frontmost = frontmost_lookup() if kiosk_pid and cdp_ready else None
    if not kiosk_pid or not cdp_ready:
        return {
            "ok": False,
            "status": "missing",
            "reason": "kiosk-process-missing" if not kiosk_pid else "kiosk-page-unhealthy",
            "kioskPid": kiosk_pid,
            "cdpReady": cdp_ready,
        }

    frontmost = frontmost or frontmost_lookup()
    if not frontmost:
        return {
            "ok": False,
            "status": "error",
            "reason": "frontmost-app-unavailable",
            "kioskPid": kiosk_pid,
        }
    frontmost_pid = int(frontmost.get("pid") or 0)
    frontmost_name = str(frontmost.get("name") or "")
    if frontmost_pid == kiosk_pid:
        return {
            "ok": True,
            "status": "foreground",
            "reason": "already-foreground",
            "kioskPid": kiosk_pid,
            "frontmostPid": frontmost_pid,
        }
    if frontmost_name in PROTECTED_FRONTMOST_APPS:
        return {
            "ok": True,
            "status": "deferred",
            "reason": "protected-system-session",
            "frontmostPid": frontmost_pid,
        }

    activated, detail = (activate_fn or activate_kiosk_process)(kiosk_pid, frontmost_pid or None)
    return {
        "ok": activated,
        "status": "focused" if activated else "error",
        "reason": "restored-foreground" if activated else "activation-failed",
        "detail": detail,
        "kioskPid": kiosk_pid,
        "previousFrontmostPid": frontmost_pid,
    }


def public_lease_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in ("owner", "purpose", "startedAt", "heartbeatAt", "expiresAt")}


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Josh 2.0 visible-work leases and Control Tower focus.")
    commands = parser.add_subparsers(dest="command", required=True)

    begin = commands.add_parser("begin", help="Temporarily allow visible work to own the display.")
    begin.add_argument("--owner", required=True)
    begin.add_argument("--purpose", required=True, choices=sorted(PURPOSES))
    begin.add_argument("--ttl-seconds", type=int, default=DEFAULT_LEASE_SECONDS)
    begin.add_argument("--pid", type=int)

    renew = commands.add_parser("renew", help="Renew an active visible-work lease.")
    renew.add_argument("--lease-id", required=True)
    renew.add_argument("--ttl-seconds", type=int, default=DEFAULT_LEASE_SECONDS)

    end = commands.add_parser("end", help="Release visible work and restore Control Tower immediately.")
    end.add_argument("--lease-id", required=True)
    end.add_argument("--no-restore", action="store_true")

    commands.add_parser("status", help="Show dashboard-safe lease state.")

    ensure = commands.add_parser("ensure", help="Enforce the foreground contract.")
    ensure.add_argument("--force", action="store_true", help="Ignore input/work lease gates, but not screen lock.")
    ensure.add_argument("--repair", action="store_true", help="Relaunch the dedicated kiosk if needed.")

    args = parser.parse_args()
    try:
        if args.command == "begin":
            payload = begin_lease(
                owner=args.owner,
                purpose=args.purpose,
                ttl_seconds=args.ttl_seconds,
                pid=args.pid,
            )
            publish_display_lease({"active": True, **public_lease_payload(payload)})
            print_json({"ok": True, "leaseId": payload["leaseId"], **public_lease_payload(payload)})
            return 0
        if args.command == "renew":
            payload = renew_lease(lease_id=args.lease_id, ttl_seconds=args.ttl_seconds)
            publish_display_lease({"active": True, **public_lease_payload(payload)})
            print_json({"ok": True, **public_lease_payload(payload)})
            return 0
        if args.command == "end":
            result = end_lease(lease_id=args.lease_id)
            publish_display_lease(None)
            if not args.no_restore:
                result["foreground"] = ensure_foreground(force=True, repair=True)
            print_json({"ok": True, **result})
            return 0
        if args.command == "status":
            state = lease_state()
            state.pop("leaseId", None)
            publish_display_lease(state)
            print_json({"ok": True, **state})
            return 0
        result = ensure_foreground(force=args.force, repair=args.repair)
        print_json(result)
        return 0 if result.get("ok") else 1
    except (ValueError, RuntimeError, PermissionError) as exc:
        print_json({"ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
