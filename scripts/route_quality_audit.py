#!/usr/bin/env python3
"""Audit privacy-safe route telemetry against explicit quality SLOs."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("ROUTE_QA_SOURCE", ROOT / "data" / "agent-route-decisions.jsonl"))
OUTPUT = Path(os.environ.get("ROUTE_QA_OUTPUT", ROOT / "data" / "route-quality-audit.json"))
WINDOW = max(1, int(os.environ.get("ROUTE_QA_WINDOW", "100")))
MIN_WINDOW_ROUTES = max(1, int(os.environ.get("ROUTE_QA_MIN_WINDOW", "24")))
REQUIRED_COVERAGE_PCT = float(os.environ.get("ROUTE_QA_REQUIRED_COVERAGE_PCT", "100"))
ROUTING_TIMING_COVERAGE_PCT = float(os.environ.get("ROUTE_QA_ROUTING_TIMING_COVERAGE_PCT", "95"))

TIMING_FIELDS = (
    "queueDurationMs",
    "routingDurationMs",
    "memoryDurationMs",
    "toolDurationMs",
    "modelDurationMs",
)
REQUIRED_FIELDS = (
    "routeDecisionId",
    "requestSignature",
    "owner",
    "provider",
    "model",
    "reason",
    "outcome",
)
FORBIDDEN_RAW_KEYS = {
    "prompt",
    "rawprompt",
    "title",
    "objective",
    "message",
    "rawemail",
    "emailbody",
    "cookie",
    "password",
    "token",
    "oauthpayload",
    "connectorpayload",
    "privateaccountcontent",
}
HEX_SIGNATURE = re.compile(r"^[0-9a-f]{16}$")
HEX_DECISION_ID = re.compile(r"^[0-9a-f]{20}$")


def percentile(values: list[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 1)


def load_rows() -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    invalid_json = 0
    if not SOURCE.exists():
        return rows, invalid_json
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            invalid_json += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            invalid_json += 1
    return rows, invalid_json


def is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() != "unknown"
    return True


def coverage(rows: list[dict[str, Any]], field: str) -> float:
    if not rows:
        return 0.0
    present = sum(is_present(row.get(field)) for row in rows)
    return round(100 * present / len(rows), 1)


def normalized_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def forbidden_keys(row: dict[str, Any]) -> list[str]:
    return sorted(str(key) for key in row if normalized_key(key) in FORBIDDEN_RAW_KEYS)


def atomic_write(payload: dict[str, Any]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=OUTPUT.name + ".", dir=OUTPUT.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, OUTPUT)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def main() -> int:
    rows, invalid_json = load_rows()
    # Coverage SLOs apply to the current privacy-safe telemetry contract. Keep
    # legacy rows in the audit trail, but do not let pre-v2 records make a
    # correctly instrumented deployment look incomplete forever.
    current_rows = [
        row for row in rows
        if str(row.get("schemaVersion") or "").strip() == "2"
    ]
    recent = current_rows[-WINDOW:]
    raw_recent = rows[-WINDOW:]
    timing_coverage = {field: coverage(recent, field) for field in TIMING_FIELDS}
    field_coverage = {field: coverage(recent, field) for field in REQUIRED_FIELDS}
    latency: dict[str, dict[str, Optional[float]]] = {}
    for field in TIMING_FIELDS:
        values = [float(row[field]) for row in recent if isinstance(row.get(field), (int, float))]
        latency[field] = {"p50": percentile(values, 0.50), "p95": percentile(values, 0.95)}

    unsafe_rows = [
        {"offset": index, "keys": keys}
        for index, row in enumerate(raw_recent)
        if (keys := forbidden_keys(row))
    ]
    malformed_ids = sum(
        not HEX_DECISION_ID.fullmatch(str(row.get("routeDecisionId") or ""))
        or not HEX_SIGNATURE.fullmatch(str(row.get("requestSignature") or ""))
        for row in recent
    )
    sparse = len(recent) < MIN_WINDOW_ROUTES
    missing_required = {
        field: value
        for field, value in field_coverage.items()
        if value < REQUIRED_COVERAGE_PCT
    }
    routing_timing_shortfall = timing_coverage["routingDurationMs"] < ROUTING_TIMING_COVERAGE_PCT

    reasons: list[str] = []
    if sparse:
        reasons.append(f"sparse telemetry: {len(recent)}/{MIN_WINDOW_ROUTES} required routes")
    if missing_required:
        reasons.append("required field coverage below SLO: " + ", ".join(sorted(missing_required)))
    if routing_timing_shortfall:
        reasons.append(
            f"routing timing coverage {timing_coverage['routingDurationMs']}% is below {ROUTING_TIMING_COVERAGE_PCT}%"
        )
    if malformed_ids:
        reasons.append(f"{malformed_ids} route rows have missing or malformed privacy-safe IDs")
    if invalid_json:
        reasons.append(f"{invalid_json} telemetry lines are invalid JSON")
    if unsafe_rows:
        reasons.append(f"{len(unsafe_rows)} recent rows contain forbidden raw-content keys")

    if unsafe_rows or invalid_json:
        status = "fail"
        exit_code = 1
    elif reasons:
        status = "attention"
        exit_code = 2
    else:
        status = "ok"
        exit_code = 0

    payload = {
        "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "slo": {
            "minimumWindowRoutes": MIN_WINDOW_ROUTES,
            "requiredFieldCoveragePct": REQUIRED_COVERAGE_PCT,
            "routingTimingCoveragePct": ROUTING_TIMING_COVERAGE_PCT,
            "rawPromptOrPrivateContentKeysAllowed": 0,
        },
        "totalRoutes": len(rows),
        "currentSchemaRoutes": len(current_rows),
        "legacySchemaRoutes": len(rows) - len(current_rows),
        "auditedSchemaVersion": 2,
        "windowRoutes": len(recent),
        "sparseTelemetry": sparse,
        "requiredFieldCoveragePct": field_coverage,
        "timingCoveragePct": timing_coverage,
        "latencyMs": latency,
        "malformedPrivacySafeIds": malformed_ids,
        "invalidJsonLines": invalid_json,
        "unsafeRawContentRows": len(unsafe_rows),
        "unsafeRawContentFindings": unsafe_rows[:10],
        "reasons": reasons or ["Route telemetry meets volume, coverage, timing, and privacy SLOs."],
        "telemetryContract": "Store route metadata and one-way signatures only; never raw prompts, messages, objectives, emails, connector payloads, secrets, or private account contents.",
    }
    atomic_write(payload)
    print(json.dumps(payload, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
