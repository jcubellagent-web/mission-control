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


def parse_json_output(out: str) -> Any | None:
    text = (out or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None
    return None


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


def cli_status(name: str, version_args: list[str] | None = None) -> dict[str, Any]:
    path = shutil.which(name)
    status: dict[str, Any] = {
        "available": bool(path),
        "path": path or "",
        "version": "",
    }
    if not path:
        return status
    args = version_args or ["--version"]
    code, out = run([path, *args], timeout=8)
    status["version"] = out.strip().splitlines()[0] if code == 0 and out.strip() else ""
    status["status"] = "ready" if code == 0 else "installed-version-check-failed"
    return status


def peekaboo_status() -> dict[str, Any]:
    status = cli_status("peekaboo")
    if not status.get("available"):
        return status
    code, out = run(["peekaboo", "permissions", "--json"], timeout=8)
    payload = parse_json_output(out)
    permissions: dict[str, bool] = {}
    if isinstance(payload, dict):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        rows = data.get("permissions") if isinstance(data.get("permissions"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if name:
                permissions[name] = bool(row.get("isGranted"))
    if permissions:
        status["permissions"] = permissions
        required_ok = all(permissions.get(name) for name in ("Screen Recording", "Accessibility"))
        status["computerUseReady"] = bool(required_ok)
        if status.get("status") == "ready" and not required_ok:
            status["status"] = "permissions-needed"
    elif code != 0 and status.get("status") == "ready":
        status["status"] = "permissions-check-failed"
    return status


def openclaw_health() -> dict[str, Any]:
    if not shutil.which("openclaw"):
        return {"available": False, "status": "missing"}
    code, out = run(["openclaw", "health", "--json"], timeout=18)
    payload = parse_json_output(out)
    if not isinstance(payload, dict):
        return {"available": True, "status": "unreadable", "code": code}
    plugins = payload.get("plugins") if isinstance(payload.get("plugins"), dict) else {}
    channels = payload.get("channels") if isinstance(payload.get("channels"), dict) else {}
    telegram = channels.get("telegram") if isinstance(channels.get("telegram"), dict) else {}
    return {
        "available": True,
        "status": "ok" if payload.get("ok") else "attention",
        "ok": bool(payload.get("ok")),
        "plugins": sorted([name for name, value in plugins.items() if value])[:24],
        "telegram": {
            "configured": bool(telegram.get("configured")),
            "running": bool(telegram.get("running")),
            "connected": bool(telegram.get("connected")),
            "polling": bool(telegram.get("polling")),
        },
    }


def openclaw_gateway() -> dict[str, Any]:
    if not shutil.which("openclaw"):
        return {"available": False, "status": "missing"}
    code, out = run(["openclaw", "gateway", "status", "--deep", "--json"], timeout=18)
    payload = parse_json_output(out)
    if not isinstance(payload, dict):
        return {"available": True, "status": "unreadable", "code": code}
    rpc = payload.get("rpc") if isinstance(payload.get("rpc"), dict) else {}
    service = payload.get("service") if isinstance(payload.get("service"), dict) else {}
    runtime = service.get("runtime") if isinstance(service.get("runtime"), dict) else {}
    gateway = payload.get("gateway") if isinstance(payload.get("gateway"), dict) else {}
    server = rpc.get("server") if isinstance(rpc.get("server"), dict) else {}
    ok = bool(payload.get("ok")) or bool(rpc.get("ok") and (runtime.get("status") == "running" or service.get("loaded")))
    return {
        "available": True,
        "status": "ok" if ok else "attention",
        "ok": ok,
        "running": bool(runtime.get("status") == "running" or service.get("loaded") or payload.get("running")),
        "port": gateway.get("port") or payload.get("port"),
        "serverVersion": server.get("version") or payload.get("serverVersion") or payload.get("version"),
        "adminCapable": str(rpc.get("capability") or "").lower() == "admin_capable" or bool(payload.get("adminCapable") or payload.get("admin_capable")),
    }


def openclaw_task_ledger() -> dict[str, Any]:
    if not shutil.which("openclaw"):
        return {"available": False, "status": "missing"}
    code, out = run(["openclaw", "tasks", "audit", "--json"], timeout=35)
    payload = parse_json_output(out)
    if not isinstance(payload, dict):
        return {"available": True, "status": "unreadable", "code": code}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
    by_code: dict[str, int] = {}
    for row in warnings:
        if not isinstance(row, dict):
            continue
        code_name = str(row.get("code") or "warning")
        by_code[code_name] = by_code.get(code_name, 0) + 1
    return {
        "available": True,
        "status": "ok" if not errors else "attention",
        "summary": {
            "total": summary.get("total"),
            "warnings": summary.get("warnings", len(warnings)),
            "errors": summary.get("errors", len(errors)),
        },
        "warningCodes": by_code,
    }


def codex_mcp_servers() -> list[dict[str, Any]]:
    if not shutil.which("codex"):
        return []
    code, out = run(["codex", "mcp", "list"], timeout=10)
    if code != 0:
        return []
    servers: list[dict[str, Any]] = []
    for line in out.splitlines():
        row = line.strip()
        if not row or row.startswith("Name ") or row.startswith("WARNING:"):
            continue
        if row.lower().startswith("no mcp servers configured"):
            continue
        parts = row.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        if name in {"-", "enabled"}:
            continue
        servers.append({
            "name": name,
            "kind": "http" if parts[1].startswith("http") else "stdio",
            "status": "enabled" if " enabled " in f" {row} " or row.endswith(" enabled") else "unknown",
        })
    unique: dict[str, dict[str, Any]] = {}
    for server in servers:
        unique[server["name"]] = server
    return list(unique.values())[:30]


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
        "nodeCli": cli_status("node", ["--version"]),
        "gogCli": cli_status("gog"),
        "peekabooCli": peekaboo_status(),
        "onePasswordCli": cli_status("op"),
        "codexCli": cli_status("codex", ["--version"]),
        "openclawCli": cli_status("openclaw", ["--version"]),
        "openclawHealth": openclaw_health(),
        "openclawGateway": openclaw_gateway(),
        "openclawTaskLedger": openclaw_task_ledger(),
        "hermesCli": cli_status("hermes", ["--version"]),
        "codexMcpServers": codex_mcp_servers(),
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
