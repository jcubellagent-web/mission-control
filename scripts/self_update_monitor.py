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


def age_seconds(path: Path) -> int | None:
    try:
        return max(0, int(utc_now().timestamp() - path.stat().st_mtime))
    except OSError:
        return None


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


def publish_transition(payload: dict[str, Any], previous: dict[str, Any]) -> None:
    if payload.get("status") == previous.get("status"):
        return
    script = ROOT / "scripts" / "agent_publish.py"
    if not script.exists():
        return
    status = "failed" if payload.get("status") == "attention" else "done"
    run([
        sys.executable, str(script), "--agent", "joshex", "--type", "status",
        "--status", status, "--title", "Self-update monitor",
        "--detail", payload.get("summary") or "Self-update monitor changed state.",
        "--tool", "self-update-monitor", "--brain-feed",
    ])


def build(config: dict[str, Any]) -> dict[str, Any]:
    max_watch_age = int(config.get("capabilityWatchMaxAgeSeconds") or 93600)
    watch_age = age_seconds(CAPABILITY_WATCH)
    jaimes = str(config.get("jaimesHost") or "jaimes")
    job = str(config.get("jaimesCapabilityJob") or "ai.jaimes.capability-upgrade-sweep")
    checks = {
        "capabilityWatchFresh": {"ok": watch_age is not None and watch_age <= max_watch_age, "ageSeconds": watch_age, "maxAgeSeconds": max_watch_age},
        "openclawVersion": run(["openclaw", "--version"], timeout=15),
        "openclawUpdateStatus": run(["openclaw", "update", "status", "--json"], timeout=45),
        "openclawGateway": run(["openclaw", "gateway", "status", "--json"], timeout=45),
        "jaimesHermesVersion": run(["ssh", jaimes, "$HOME/.hermes/hermes-agent/venv/bin/hermes --version"], timeout=30),
        "jaimesTelegramHealth": run(["ssh", jaimes, "cd $HOME/.openclaw/workspace/mission-control && /opt/homebrew/bin/python3 scripts/jaimes_telegram_health.py"], timeout=60),
        "jaimesCapabilityJob": run(["ssh", jaimes, f"launchctl print gui/$(id -u)/{job}"], timeout=30),
    }
    candidates = {
        "openclaw": latest_manifest(ROOT / "data" / "openclaw-update-evidence"),
        "hermes": latest_manifest(ROOT / "data" / "hermes-update-evidence"),
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
