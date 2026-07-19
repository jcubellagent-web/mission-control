#!/usr/bin/env python3
"""Independent JAIMES black-box QA for Josh 2.0 Control Tower.

The script is safe for Hermes ``--no-agent`` scheduling. It emits no stdout on
success and writes only dashboard-safe observations to the JAIMES sidecar.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path.home() / ".openclaw" / "workspace" / "mission-control"
OUTPUT = ROOT / "data" / "jaimes-control-tower-blackbox.json"
BASE_URL = os.environ.get(
    "CONTROL_TOWER_BASE",
    "https://josh2.tail2a17bd.ts.net/control-tower",
).rstrip("/")


def parse_ts(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


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


def fetch(path: str) -> tuple[Any, float]:
    started = time.perf_counter()
    with urllib.request.urlopen(BASE_URL + path, timeout=8) as response:
        data = response.read()
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
    return json.loads(data), round((time.perf_counter() - started) * 1000, 1)


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    issues: list[str] = []
    latency = None
    live: dict[str, Any] = {}
    try:
        raw, latency = fetch("/data/control-tower-live.json")
        if not isinstance(raw, dict):
            issues.append("live payload is not an object")
        else:
            live = raw
    except Exception as exc:
        issues.append(f"Control Tower fetch failed: {type(exc).__name__}")
    required = {"lastUpdated", "sourceUpdatedAt", "brainFeed", "todayJobs", "runtimeLayout"}
    missing = sorted(required - set(live))
    if missing:
        issues.append("live payload missing fields: " + ", ".join(missing))
    source_stamp = parse_ts(live.get("sourceUpdatedAt"))
    generated_stamp = parse_ts(live.get("lastUpdated"))
    source_age = round((now - source_stamp.astimezone(dt.timezone.utc)).total_seconds() / 60, 1) if source_stamp else None
    generated_age = round((now - generated_stamp.astimezone(dt.timezone.utc)).total_seconds() / 60, 1) if generated_stamp else None
    if source_age is None or source_age > 5:
        issues.append(f"source freshness exceeds 5 minutes ({source_age if source_age is not None else 'unknown'})")
    if generated_age is None or generated_age > 5:
        issues.append(f"dashboard generation exceeds 5 minutes ({generated_age if generated_age is not None else 'unknown'})")
    if latency is not None and latency > 500:
        issues.append(f"local-network dashboard latency exceeds 500 ms ({latency})")
    payload = {
        "owner": "jaimes",
        "team": "Independent Control Tower black-box QA",
        "privacy": "dashboard-safe",
        "checkedAt": now.isoformat().replace("+00:00", "Z"),
        "status": "ok" if not issues else "attention",
        "ok": not issues,
        "issues": issues,
        "metrics": {"latencyMs": latency, "sourceAgeMinutes": source_age, "generatedAgeMinutes": generated_age},
        "contract": "Read-only HTTP verification; JAIMES never writes Josh 2.0 canonical dashboard data.",
    }
    atomic_write(OUTPUT, payload)
    if issues:
        print(json.dumps({"status": payload["status"], "issues": issues}, ensure_ascii=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
