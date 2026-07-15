#!/usr/bin/env python3
"""Probe canonical Josh 2.0 runtime services and perform bounded recovery."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
OUTPUT = ROOT / "data" / "ecosystem-runtime-probe.json"
JOSH_WORK_CARD_SCRIPT = WORKSPACE / "scripts" / "josh_work_card.py"
JOSH_SEND_REPLY_SCRIPT = JOSH_WORK_CARD_SCRIPT.with_name("send_josh_reply.py")
RECOVERY_COOLDOWN = dt.timedelta(minutes=15)
SERVICE_LABELS = {
    "controlTower": "com.josh20.mission-control-react-v2",
    "brainFeed": "com.josh20.brain-feed-server",
    "gateway": "ai.openclaw.gateway",
    "telegramFastAck": "com.josh20.telegram-fast-ack",
}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


def parse_ts(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write(path: Path, payload: Any) -> None:
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


def tcp_probe(port: int, timeout: float = 2.0) -> tuple[bool, float, str]:
    started = time.perf_counter()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True, round((time.perf_counter() - started) * 1000, 1), "listening"
    except OSError as exc:
        return False, round((time.perf_counter() - started) * 1000, 1), f"{type(exc).__name__}"


def http_json(url: str, timeout: float = 4.0) -> tuple[bool, float, Any, str]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read()
        return True, round((time.perf_counter() - started) * 1000, 1), json.loads(raw), "ok"
    except Exception as exc:
        return False, round((time.perf_counter() - started) * 1000, 1), None, f"{type(exc).__name__}"


def launchd_running(label: str) -> bool:
    proc = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )
    text = proc.stdout.lower()
    return proc.returncode == 0 and ("state = running" in text or "pid =" in text)


def collect(base_url: str) -> dict[str, Any]:
    root_ok, root_ms, _, root_detail = http_json(base_url.rstrip("/") + "/data/control-tower-live.json")
    live = _ if isinstance(_, dict) else {}
    brain_ok, brain_ms, brain_detail = tcp_probe(8765)
    gateway_ok, gateway_ms, gateway_detail = tcp_probe(18790)
    fast_ack_ok = launchd_running(SERVICE_LABELS["telegramFastAck"])
    telegram_helper_missing = [
        path.name
        for path in (JOSH_WORK_CARD_SCRIPT, JOSH_SEND_REPLY_SCRIPT)
        if not path.is_file()
    ]
    telegram_helper_ok = not telegram_helper_missing
    source_stamp = parse_ts(live.get("sourceUpdatedAt"))
    source_age = round((utc_now() - source_stamp.astimezone(dt.timezone.utc)).total_seconds() / 60, 1) if source_stamp else None
    checks = {
        "controlTower": {"ok": root_ok, "latencyMs": root_ms, "detail": root_detail},
        "brainFeed": {"ok": brain_ok, "latencyMs": brain_ms, "detail": brain_detail},
        "gateway": {"ok": gateway_ok, "latencyMs": gateway_ms, "detail": gateway_detail},
        "telegramFastAck": {"ok": fast_ack_ok, "detail": "launchd running" if fast_ack_ok else "launchd not running"},
        "telegramWorkCardHelper": {
            "ok": telegram_helper_ok,
            "detail": "workspace helpers present" if telegram_helper_ok else f"missing: {', '.join(telegram_helper_missing)}",
        },
        "sourceFreshness": {"ok": source_age is not None and source_age <= 5, "ageMinutes": source_age, "detail": "sourceUpdatedAt"},
    }
    return {"checks": checks, "ok": all(row["ok"] for row in checks.values())}


def recovery_due(previous: dict[str, Any], service: str, now: dt.datetime) -> bool:
    recoveries = previous.get("recoveries") if isinstance(previous.get("recoveries"), dict) else {}
    row = recoveries.get(service) if isinstance(recoveries.get(service), dict) else {}
    last = parse_ts(row.get("lastAttemptAt"))
    attempts = int(row.get("attemptsSinceHealthy") or 0)
    return attempts < 3 and (not last or now - last.astimezone(dt.timezone.utc) >= RECOVERY_COOLDOWN)


def restart_service(service: str) -> dict[str, Any]:
    label = SERVICE_LABELS[service]
    proc = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    return {"service": service, "label": label, "returncode": proc.returncode, "ok": proc.returncode == 0, "detail": (proc.stderr or proc.stdout).strip()[:400]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("CONTROL_TOWER_BASE", "http://127.0.0.1:5174"))
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    now = utc_now()
    previous = read_json(OUTPUT, {})
    result = collect(args.base_url)
    streak = 0 if result["ok"] else int(previous.get("failureStreak") or 0) + 1
    recoveries = previous.get("recoveries") if isinstance(previous.get("recoveries"), dict) else {}
    attempted = []
    if args.recover and streak >= 2:
        for service in ("controlTower", "brainFeed", "gateway", "telegramFastAck"):
            if not result["checks"][service]["ok"] and recovery_due(previous, service, now):
                row = restart_service(service)
                attempted.append(row)
                previous_row = recoveries.get(service) if isinstance(recoveries.get(service), dict) else {}
                recoveries[service] = {
                    "lastAttemptAt": iso(now),
                    "attemptsSinceHealthy": int(previous_row.get("attemptsSinceHealthy") or 0) + 1,
                    "lastResult": row,
                }
        if attempted:
            time.sleep(4)
            result = collect(args.base_url)
            if result["ok"]:
                streak, recoveries = 0, {}
    payload = {
        "checkedAt": iso(),
        "ok": result["ok"],
        "status": "ok" if result["ok"] else "attention",
        "failureStreak": streak,
        "checks": result["checks"],
        "recoveryAttempts": attempted,
        "recoveries": recoveries,
        "policy": "One restart per service per 15 minutes; circuit opens after three attempts; source staleness never triggers a service restart.",
    }
    if not args.no_write:
        atomic_write(OUTPUT, payload)
    print(json.dumps(payload, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
