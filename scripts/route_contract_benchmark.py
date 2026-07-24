#!/usr/bin/env python3
"""Execute deterministic agent/model routing fixtures without emitting telemetry."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "data" / "agent-route-benchmark-suite.json"
DEFAULT_OUTPUT = ROOT / "data" / "agent-route-benchmark-results.json"
ROUTER = ROOT / "scripts" / "agent_route.py"


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object in {path}")
    return payload


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def command_for(case: dict[str, Any], *, emit_telemetry: bool = False) -> list[str]:
    cmd = [
        sys.executable,
        str(ROUTER),
        "--task-type",
        str(case["taskType"]),
        "--title",
        str(case.get("title") or case["id"]),
        "--objective",
        str(case.get("objective") or "Exercise the deterministic route contract"),
        "--privacy",
        str(case.get("privacy") or "dashboard-safe"),
        "--priority",
        str(case.get("priority") or "normal"),
        "--requester",
        str(case.get("requester") or "joshex"),
        "--complexity",
        str(case.get("complexity") or "auto"),
        "--blast-radius",
        str(case.get("blastRadius") or "auto"),
        "--codex-allowance",
        str(case.get("codexAllowance") or "normal"),
    ]
    if not emit_telemetry:
        cmd.append("--no-telemetry")
    for capability in case.get("capabilities") or []:
        cmd.extend(["--capability", str(capability)])
    if case.get("approval"):
        cmd.extend(["--approval", str(case["approval"])])
    if case.get("prefer"):
        cmd.extend(["--prefer", str(case["prefer"])])
    if case.get("requestedProvider"):
        cmd.extend(["--requested-provider", str(case["requestedProvider"])])
    if case.get("requestedModel"):
        cmd.extend(["--requested-model", str(case["requestedModel"])])
    return cmd


def run_case(case: dict[str, Any], *, emit_telemetry: bool = False) -> dict[str, Any]:
    env = os.environ.copy()
    env.pop("XAI_ENABLED", None)
    env.pop("XAI_VERIFIED", None)
    for key, value in (case.get("environment") or {}).items():
        env[str(key)] = str(value)
    started = time.perf_counter()
    proc = subprocess.run(
        command_for(case, emit_telemetry=emit_telemetry),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    if proc.returncode != 0:
        return {
            "id": case.get("id"),
            "passed": False,
            "durationMs": duration_ms,
            "errors": [f"router exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}"],
        }
    try:
        result = json.loads(proc.stdout)
    except Exception as exc:
        return {"id": case.get("id"), "passed": False, "durationMs": duration_ms, "errors": [f"invalid router JSON: {exc}"]}

    model_route = result.get("modelRoute") if isinstance(result.get("modelRoute"), dict) else {}
    actual = {
        "owner": result.get("agent"),
        "provider": model_route.get("provider"),
        "model": model_route.get("model"),
        "role": model_route.get("role"),
        "reason": model_route.get("reason"),
        "needsApproval": result.get("needsApproval"),
    }
    errors: list[str] = []
    expectations = {
        "owner": case.get("expectedOwner"),
        "provider": case.get("expectedProvider"),
        "model": case.get("expectedModel"),
        "role": case.get("expectedRole"),
        "needsApproval": case.get("expectedNeedsApproval"),
    }
    for field, expected in expectations.items():
        if expected is not None and actual.get(field) != expected:
            errors.append(f"{field}: expected {expected!r}, got {actual.get(field)!r}")
    for field, fixture_key in (
        ("owner", "expectedOwnerAny"),
        ("provider", "expectedProviderAny"),
        ("model", "expectedModelAny"),
        ("role", "expectedRoleAny"),
    ):
        allowed = case.get(fixture_key)
        if allowed and actual.get(field) not in allowed:
            errors.append(f"{field}: expected one of {allowed!r}, got {actual.get(field)!r}")
    if not all(actual.get(field) not in {None, "", "unknown"} for field in ("provider", "model", "reason")):
        errors.append("provider/model/reason coverage is incomplete")
    return {"id": case.get("id"), "passed": not errors, "durationMs": duration_ms, "actual": actual, "errors": errors}


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--emit-telemetry", action="store_true", help="Emit the router's privacy-safe route metadata for live instrumentation QA.")
    args = parser.parse_args()

    suite = read_json(args.suite)
    cases = suite.get("cases") or []
    if not isinstance(cases, list) or len(cases) < int(suite.get("minimumFixtures") or 24):
        print(json.dumps({"status": "fail", "reason": "benchmark suite is below its minimum fixture count"}, indent=2))
        return 1
    results = [run_case(case, emit_telemetry=args.emit_telemetry) for case in cases if isinstance(case, dict)]
    passed = sum(bool(row.get("passed")) for row in results)
    coverage_rows = [row.get("actual") or {} for row in results]
    coverage = {
        field: round(100 * sum(bool(row.get(field)) for row in coverage_rows) / len(results), 1) if results else 0.0
        for field in ("provider", "model", "reason")
    }
    pass_rate = round(100 * passed / len(results), 1) if results else 0.0
    required_rate = float(suite.get("minimumPassRatePct") or 100)
    durations = [float(row["durationMs"]) for row in results if isinstance(row.get("durationMs"), (int, float))]
    latency = {"p50": percentile(durations, 0.50), "p95": percentile(durations, 0.95), "hardMaxP95": 250.0}
    ok = (
        pass_rate >= required_rate
        and all(value == 100.0 for value in coverage.values())
        and latency["p95"] is not None
        and latency["p95"] <= latency["hardMaxP95"]
    )
    payload = {
        "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "ok" if ok else "fail",
        "fixtureCount": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "passRatePct": pass_rate,
        "minimumPassRatePct": required_rate,
        "routeMetadataCoveragePct": coverage,
        "decisionLatencyMs": latency,
        "privacy": (
            "The benchmark emitted privacy-safe hashed route metadata; no title, objective, or prompt is retained."
            if args.emit_telemetry
            else "The benchmark invokes --no-telemetry and its result artifact contains case IDs and route metadata only."
        ),
        "results": results,
    }
    if not args.no_write:
        atomic_write(args.output, payload)
    print(json.dumps(payload, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
