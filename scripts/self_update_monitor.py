#!/usr/bin/env python3
"""Monitor release discovery, candidate evidence, and live agent health.

The monitor is intentionally fail closed: it observes and alerts, but never
installs or promotes a release. Candidate preparation and production promotion
remain governed by the release pipelines and host runbook.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "self-update-monitor.json"
OUTPUT = ROOT / "data" / "self-update-monitor.json"
CAPABILITY_WATCH = ROOT / "data" / "capability-watch.json"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def run(command: list[str], timeout: int = 45) -> dict[str, Any]:
    try:
        proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__}
    return {"ok": proc.returncode == 0, "code": proc.returncode}


def run_capture(command: list[str], timeout: int = 45) -> dict[str, Any]:
    try:
        proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__, "output": ""}
    return {"ok": proc.returncode == 0, "code": proc.returncode, "output": proc.stdout.strip()}


def age_seconds(path: Path) -> int | None:
    try:
        return max(0, int(utc_now().timestamp() - path.stat().st_mtime))
    except OSError:
        return None


def timestamp_age_seconds(value: Any) -> int | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return max(0, int((utc_now() - parsed.astimezone(dt.timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return None


def remote_capability_watch(host: str, path: str) -> dict[str, Any]:
    """Read the owning host's bounded dashboard-safe watch payload."""
    code = """import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file() or path.stat().st_size > 131072:
    raise SystemExit(2)
value = json.loads(path.read_text(encoding='utf-8'))
allowed = {'updatedAt', 'checkedAt', 'status', 'summary', 'sources', 'recommendations', 'previews', 'fastLane', 'privacy'}
print(json.dumps({key: value.get(key) for key in allowed if key in value}))
"""
    remote = " ".join(
        shlex.quote(part)
        for part in ["/opt/homebrew/bin/python3", "-c", code, path]
    )
    result = run_capture(["ssh", host, remote], timeout=30)
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason") or f"exit-{result.get('code')}"}
    try:
        value = json.loads(result.get("output") or "")
    except (TypeError, json.JSONDecodeError):
        return {"ok": False, "reason": "invalid-json"}
    if not isinstance(value, dict) or value.get("privacy") != "dashboard-safe metadata only":
        return {"ok": False, "reason": "invalid-payload"}
    if timestamp_age_seconds(value.get("updatedAt")) is None:
        return {"ok": False, "reason": "invalid-timestamp"}
    return {"ok": True, "payload": value}


def sync_remote_capability_watch(host: str, remote_path: str, local_path: Path = CAPABILITY_WATCH) -> dict[str, Any]:
    result = remote_capability_watch(host, remote_path)
    if not result.get("ok"):
        return result
    remote = result["payload"]
    local = read_json(local_path, {})
    remote_stamp = timestamp_age_seconds(remote.get("updatedAt"))
    local_stamp = timestamp_age_seconds(local.get("updatedAt")) if isinstance(local, dict) else None
    updated = local_stamp is None or (remote_stamp is not None and remote_stamp < local_stamp)
    if updated:
        write_json(local_path, remote)
    return {"ok": True, "updated": updated, "remoteAgeSeconds": remote_stamp}


def latest_manifest(directory: Path) -> dict[str, Any]:
    paths = sorted(directory.glob("candidate-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not paths:
        return {"present": False, "healthy": True}
    path = paths[0]
    manifest = read_json(path, {})
    sandbox = Path(str(manifest.get("sandbox") or ""))
    return {
        "present": True,
        "healthy": bool(manifest) and sandbox.is_dir(),
        "target": manifest.get("target"),
        "ageSeconds": age_seconds(path),
        "promotion": (manifest.get("promotion") or {}).get("status"),
        "observationRecorded": isinstance(manifest.get("observationEvidence"), dict),
    }


def latest_remote_manifest(host: str, directory: str) -> dict[str, Any]:
    """Read candidate health on its owning host without copying private logs."""
    code = """import datetime as dt
import json
import sys
from pathlib import Path

directory = Path(sys.argv[1])
paths = sorted(directory.glob('candidate-*.json'), key=lambda path: path.stat().st_mtime, reverse=True)
if not paths:
    print(json.dumps({'present': False, 'healthy': True}))
    raise SystemExit(0)
path = paths[0]
try:
    manifest = json.loads(path.read_text(encoding='utf-8'))
except Exception:
    manifest = {}
sandbox = Path(str(manifest.get('sandbox') or ''))
age = max(0, int(dt.datetime.now(dt.timezone.utc).timestamp() - path.stat().st_mtime))
print(json.dumps({
    'present': True,
    'healthy': bool(manifest) and sandbox.is_dir(),
    'target': manifest.get('target'),
    'ageSeconds': age,
    'promotion': (manifest.get('promotion') or {}).get('status'),
    'observationRecorded': isinstance(manifest.get('observationEvidence'), dict),
}))
"""
    remote = " ".join(
        shlex.quote(part)
        for part in ["/opt/homebrew/bin/python3", "-c", code, directory]
    )
    result = run_capture(["ssh", host, remote], timeout=30)
    if not result.get("ok"):
        return {"present": False, "healthy": False, "reason": result.get("reason") or f"exit-{result.get('code')}"}
    try:
        value = json.loads(result.get("output") or "")
    except (TypeError, json.JSONDecodeError):
        return {"present": False, "healthy": False, "reason": "invalid-json"}
    return value if isinstance(value, dict) else {"present": False, "healthy": False, "reason": "invalid-payload"}


def publish_transition(payload: dict[str, Any], previous: dict[str, Any]) -> None:
    if payload.get("status") == previous.get("status"):
        return
    script = ROOT / "scripts" / "agent_publish.py"
    if not script.exists():
        return
    status = "error" if payload.get("status") == "attention" else "done"
    run([
        sys.executable, str(script), "--agent", "joshex", "--type", "status",
        "--status", status, "--title", "Self-update monitor",
        "--detail", payload.get("summary") or "Self-update monitor changed state.",
        "--tool", "self-update-monitor", "--brain-feed",
    ])


def build(config: dict[str, Any]) -> dict[str, Any]:
    max_watch_age = int(config.get("capabilityWatchMaxAgeSeconds") or 93600)
    jaimes = str(config.get("jaimesHost") or "jaimes")
    jaimes_root = str(config.get("jaimesMissionControl") or "/Users/jc_agent/.openclaw/workspace/mission-control")
    job = str(config.get("jaimesCapabilityJob") or "ai.jaimes.capability-upgrade-sweep")
    watch_sync = sync_remote_capability_watch(jaimes, f"{jaimes_root}/data/capability-watch.json")
    watch_payload = read_json(CAPABILITY_WATCH, {})
    watch_age = timestamp_age_seconds(watch_payload.get("updatedAt")) if isinstance(watch_payload, dict) else None
    checks = {
        "jaimesCapabilityWatchSync": watch_sync,
        "capabilityWatchFresh": {"ok": watch_age is not None and watch_age <= max_watch_age, "ageSeconds": watch_age, "maxAgeSeconds": max_watch_age},
        "openclawVersion": run(["openclaw", "--version"], timeout=15),
        "openclawUpdateStatus": run(["openclaw", "update", "status", "--json"], timeout=45),
        "openclawGateway": run(["openclaw", "gateway", "status", "--json"], timeout=45),
        "jaimesHermesVersion": run(["ssh", jaimes, "$HOME/.hermes/hermes-agent/venv/bin/hermes --version"], timeout=30),
        "jaimesTelegramHealth": run(["ssh", jaimes, "cd $HOME/.openclaw/workspace/mission-control && /opt/homebrew/bin/python3 scripts/jaimes_telegram_health.py"], timeout=60),
        "jaimesCapabilityJob": run(["ssh", jaimes, f"launchctl print gui/$(id -u)/{job}"], timeout=30),
    }
    candidates = {
        "openclaw": latest_remote_manifest(jaimes, f"{jaimes_root}/data/openclaw-update-evidence"),
        "hermes": latest_remote_manifest(jaimes, f"{jaimes_root}/data/hermes-update-evidence"),
    }
    failures = [name for name, result in checks.items() if not result.get("ok")]
    failures.extend(f"{name}Candidate" for name, result in candidates.items() if not result.get("healthy"))
    return {
        "updatedAt": iso(),
        "status": "attention" if failures else "ok",
        "summary": "Self-update monitoring is healthy." if not failures else f"Self-update monitoring needs attention: {', '.join(failures)}.",
        "checks": checks,
        "candidates": candidates,
        "automaticPromotion": False,
        "failures": failures,
        "privacy": "dashboard-safe metadata only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()
    previous = read_json(OUTPUT, {})
    payload = build(read_json(args.config, {}))
    write_json(OUTPUT, payload)
    if not args.no_publish:
        publish_transition(payload, previous)
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
