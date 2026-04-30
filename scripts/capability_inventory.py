#!/usr/bin/env python3
"""Collect dashboard-safe capability inventory for an agent node."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT = DATA_DIR / "capability-inventory.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(cmd: list[str], timeout: int = 8) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return 126, str(exc)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def ollama_models() -> list[str]:
    if not shutil.which("ollama"):
        return []
    code, out = run(["ollama", "list"], timeout=6)
    if code != 0:
        return []
    rows = []
    for line in out.splitlines()[1:]:
        name = line.split()[0] if line.split() else ""
        if name:
            rows.append(name)
    return rows[:30]


def gemini_cli_status() -> dict[str, Any]:
    path = shutil.which("gemini")
    status: dict[str, Any] = {
        "available": bool(path),
        "path": path or "",
        "version": "",
        "authMode": "oauth-or-api-key",
        "dashboardSafe": True,
    }
    if not path:
        return status
    code, out = run([path, "--version"], timeout=6)
    status["version"] = out.strip().splitlines()[0] if code == 0 and out.strip() else ""
    status["status"] = "ready" if code == 0 else "installed-version-check-failed"
    return status


def crontab_summary() -> tuple[bool, int, int]:
    code, out = run(["crontab", "-l"], timeout=6)
    if code != 0:
        return False, 0, 0
    lines = [line for line in out.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    wrapped = [line for line in lines if "agent_job_wrap.sh" in line or "agent_publish.py" in line]
    return True, len(lines), len(wrapped)


def service_hits() -> list[str]:
    patterns = ["brain_feed_server.py", "ollama", "weaviate", "temporal", "xmcp", "Hermes", "OpenCLAW"]
    hits: list[str] = []
    code, out = run(["ps", "axo", "command"], timeout=8)
    if code != 0:
        return hits
    for pattern in patterns:
        if pattern.lower() in out.lower():
            hits.append(pattern)
    return hits


def collect(args: argparse.Namespace) -> dict[str, Any]:
    cron_ok, cron_count, wrapped = crontab_summary()
    code, py = run([args.python, "--version"], timeout=4)
    return {
        "node": args.node,
        "agent": args.agent,
        "checkedAt": utc_now(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": py.strip() if code == 0 else "",
        "workspace": str(ROOT.parent),
        "crontabAvailable": cron_ok,
        "crontabCount": cron_count,
        "wrappedCronLines": wrapped,
        "ollamaModels": ollama_models(),
        "geminiCli": gemini_cli_status(),
        "services": service_hits(),
    }


def merge(record: dict[str, Any]) -> dict[str, Any]:
    lock_path = OUT.with_suffix(".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_json(OUT, {"updatedAt": record["checkedAt"], "nodes": []})
        nodes = [node for node in data.get("nodes", []) if node.get("node") != record["node"]]
        nodes.insert(0, record)
        data["nodes"] = nodes[:30]
        data["updatedAt"] = record["checkedAt"]
        write_json(OUT, data)
        fcntl.flock(lock, fcntl.LOCK_UN)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect dashboard-safe agent capability inventory.")
    parser.add_argument("--node", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--python", default="python3")
    parser.add_argument("--merge", action="store_true")
    args = parser.parse_args()
    record = collect(args)
    payload = merge(record) if args.merge else {"updatedAt": record["checkedAt"], "nodes": [record]}
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
