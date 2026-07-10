#!/usr/bin/env python3
"""Summarize privacy-safe route quality and latency instrumentation."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "agent-route-decisions.jsonl"
OUTPUT = ROOT / "data" / "route-quality-audit.json"
WINDOW = 100
TIMING_FIELDS = (
    "queueDurationMs",
    "routingDurationMs",
    "memoryDurationMs",
    "toolDurationMs",
    "modelDurationMs",
)


def percentile(values: list[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 1)


def load_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not SOURCE.exists():
        return rows
    for line in SOURCE.read_text().splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def coverage(rows: list[dict[str, Any]], field: str) -> float:
    if not rows:
        return 0.0
    present = sum(row.get(field) not in {None, "", "unknown"} for row in rows)
    return round(100 * present / len(rows), 1)


def atomic_write(payload: dict[str, Any]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=OUTPUT.name + ".", dir=OUTPUT.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, OUTPUT)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def main() -> int:
    rows = load_rows()
    recent = rows[-WINDOW:]
    instrumented = [row for row in recent if "routingDurationMs" in row]
    timing_coverage = {field: coverage(recent, field) for field in TIMING_FIELDS}
    latency: dict[str, dict[str, Optional[float]]] = {}
    for field in TIMING_FIELDS:
        values = [float(row[field]) for row in recent if isinstance(row.get(field), (int, float))]
        latency[field] = {"p50": percentile(values, 0.50), "p95": percentile(values, 0.95)}

    payload = {
        "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "ok" if instrumented and coverage(instrumented, "model") == 100.0 else "watch",
        "totalRoutes": len(rows),
        "windowRoutes": len(recent),
        "instrumentedRoutes": len(instrumented),
        "modelCoveragePct": coverage(recent, "model"),
        "instrumentedModelCoveragePct": coverage(instrumented, "model"),
        "timingCoveragePct": timing_coverage,
        "latencyMs": latency,
        "legacyRowsMissingModel": sum(row.get("model") in {None, "", "unknown"} for row in rows),
        "note": "Missing historical model IDs remain labeled legacy; new decisions require provider/model and explicit timing fields.",
    }
    atomic_write(payload)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
