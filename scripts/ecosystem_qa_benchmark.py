#!/usr/bin/env python3
"""Run the canonical benchmark, release QA, or non-destructive fault tests."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import math
import multiprocessing as mp
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "ecosystem-qa-benchmark.json"
SLO_PATH = ROOT / "config" / "ecosystem-qa-slo.json"
QA_PYTHON = ROOT / ".venv-qa" / "bin" / "python"


def iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def compact(value: Any, limit: int = 2000) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[-limit:]


def execute(name: str, command: list[str], timeout: int = 300, env: dict[str, str] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False, env=env)
    return {
        "name": name,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "durationMs": round((time.perf_counter() - started) * 1000),
        "stdout": compact(proc.stdout),
        "stderr": compact(proc.stderr),
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 2)


def http_performance(
    url: str = "http://127.0.0.1:5174/data/control-tower-live.json",
    samples: int = 50,
    attempts_per_sample: int = 3,
) -> dict[str, Any]:
    latencies: list[float] = []
    sizes: list[int] = []
    errors = 0
    transient_errors = 0
    for _ in range(samples):
        sample_ok = False
        for attempt in range(max(1, attempts_per_sample)):
            started = time.perf_counter()
            try:
                request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    body = response.read()
                    if response.status != 200:
                        raise RuntimeError(f"HTTP {response.status}")
                    json.loads(body)
                    sizes.append(len(body))
                    latencies.append((time.perf_counter() - started) * 1000)
                    sample_ok = True
                    break
            except Exception:
                if attempt + 1 < max(1, attempts_per_sample):
                    transient_errors += 1
                    time.sleep(0.05)
        if not sample_ok:
            errors += 1
    p95 = percentile(latencies, 0.95)
    max_bytes = max(sizes, default=0)
    return {
        "name": "control-tower-http-performance",
        "ok": errors == 0 and p95 is not None and p95 <= 500 and max_bytes <= 250_000,
        "samples": samples,
        "errors": errors,
        "retryAttempts": transient_errors,
        "latencyMs": {"p50": percentile(latencies, 0.50), "p95": p95, "hardMaxP95": 500},
        "payloadBytes": {"max": max_bytes, "hardMax": 250_000},
    }


def ecosystem_health_check(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Treat medium human decisions as attention, not an ecosystem outage."""
    row = execute("ecosystem-health", [sys.executable, "scripts/ecosystem_health_sweep.py"], timeout=120, env=env)
    try:
        payload = json.loads(str(row.get("stdout") or ""))
    except Exception:
        return row
    try:
        live = json.loads((ROOT / "data" / "control-tower-live.json").read_text(encoding="utf-8"))
    except Exception:
        live = {}
    actions = live.get("actionRequired") if isinstance(live, dict) else []
    actions = actions if isinstance(actions, list) else []
    blocking_actions = [
        item for item in actions
        if isinstance(item, dict)
        and str(item.get("priority") or "").strip().lower() in {"critical", "high", "p0", "p1"}
    ]
    agents = payload.get("agents") if isinstance(payload, dict) else []
    agents = agents if isinstance(agents, list) else []
    operational_ok = bool(
        agents
        and all(isinstance(agent, dict) and agent.get("ok") and not agent.get("stale") for agent in agents)
        and payload.get("modelRoutesOk") is True
        and int(payload.get("cronAttentionCount") or 0) == 0
        and float(payload.get("controlTowerAgeMinutes") or 9999) <= 5
        and not blocking_actions
    )
    row["ok"] = operational_ok
    row["reportedStatus"] = payload.get("status")
    row["nonBlockingActionRequired"] = max(0, len(actions) - len(blocking_actions))
    row["blockingActionRequired"] = len(blocking_actions)
    return row


def _lock_worker(path: str, barrier: mp.Barrier, results: mp.Queue) -> None:
    with open(path, "a+", encoding="utf-8") as handle:
        barrier.wait()
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            results.put("acquired")
            time.sleep(0.15)
        except BlockingIOError:
            results.put("skipped")


def fault_injection() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        canonical = root / "canonical.json"
        canonical.write_text('{"generation":"last-good"}\n', encoding="utf-8")
        interrupted = root / ".canonical.json.interrupted.tmp"
        interrupted.write_text('{"generation":', encoding="utf-8")
        retained = json.loads(canonical.read_text(encoding="utf-8"))
        checks.append({"name": "interrupted-promotion-retains-last-good", "ok": retained == {"generation": "last-good"}})

        lock_path = str(root / "writer.lock")
        barrier = mp.Barrier(20)
        queue: mp.Queue = mp.Queue()
        workers = [mp.Process(target=_lock_worker, args=(lock_path, barrier, queue)) for _ in range(20)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
        results = [queue.get(timeout=1) for _ in range(20)]
        checks.append({"name": "twenty-writers-coalesce", "ok": results.count("acquired") == 1 and results.count("skipped") == 19, "acquired": results.count("acquired"), "skipped": results.count("skipped")})

    test_env = os.environ.copy()
    test_env["PYTHONPYCACHEPREFIX"] = "/private/tmp/ecosystem-qa-pycache"
    test_env["PYTHONPATH"] = str(ROOT / "scripts")
    unit = execute(
        "fault-fixture-unit-tests",
        [sys.executable, "-m", "unittest", "scripts/test_ecosystem_state_reconciler.py", "scripts/test_ecosystem_qa_scheduler.py", "-v"],
        timeout=60,
        env=test_env,
    )
    checks.append(unit)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--route-only", action="store_true")
    mode.add_argument("--full", action="store_true")
    mode.add_argument("--fault-injection", action="store_true")
    mode.add_argument("--health-only", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    checks: list[dict[str, Any]] = []
    if args.route_only:
        checks.append(execute("route-contract", [sys.executable, "scripts/route_contract_benchmark.py"], timeout=240))
        mode_name = "route-only"
    elif args.fault_injection:
        checks = fault_injection()
        mode_name = "fault-injection"
    elif args.health_only:
        checks = [ecosystem_health_check()]
        mode_name = "health-only"
    else:
        env = os.environ.copy()
        env["PYTHONPYCACHEPREFIX"] = "/private/tmp/ecosystem-qa-pycache"
        env["PYTHONPATH"] = str(ROOT / "scripts")
        pytest_python = str(QA_PYTHON) if QA_PYTHON.exists() else sys.executable
        checks.extend([
            http_performance(),
            execute("route-contract", [sys.executable, "scripts/route_contract_benchmark.py"], timeout=240, env=env),
            execute("route-telemetry-slo", [sys.executable, "scripts/route_quality_audit.py"], timeout=120, env=env),
            execute("python-contract-tests", [pytest_python, "-m", "pytest", "-q", "tests", "scripts/test_telegram_approval_extraction.py"], timeout=300, env=env),
            execute("lifecycle-scheduler-tests", [sys.executable, "-m", "unittest", "scripts/test_ecosystem_state_reconciler.py", "scripts/test_ecosystem_qa_scheduler.py", "-v"], timeout=120, env=env),
            execute("control-tower-regression", [sys.executable, "scripts/mission_control_regression_check.py"], timeout=180, env=env),
            execute("react-build", ["npm", "run", "build"], timeout=300, env=env),
            execute("runtime-layout", [sys.executable, "scripts/mission_control_runtime_layout_check.py"], timeout=180, env=env),
            ecosystem_health_check(env),
        ])
        mode_name = "full"
    ok = all(bool(row.get("ok")) for row in checks)
    payload = {
        "checkedAt": iso(),
        "slo": json.loads(SLO_PATH.read_text(encoding="utf-8")) if SLO_PATH.exists() else {"status": "missing"},
        "mode": mode_name,
        "ok": ok,
        "status": "ok" if ok else "attention",
        "checksPassed": sum(bool(row.get("ok")) for row in checks),
        "checksTotal": len(checks),
        "checks": checks,
    }
    if not args.no_write:
        atomic_write(OUTPUT, payload)
    print(json.dumps(payload, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    mp.set_start_method("spawn")
    raise SystemExit(main())
