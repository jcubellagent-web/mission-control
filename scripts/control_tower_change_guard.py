#!/usr/bin/env python3
"""Coordinate safe, auditable edits to the canonical Control Tower source."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path.home() / ".openclaw" / "state"
LOCK_PATH = STATE_DIR / "control-tower-change-lock.json"
BACKUP_ROOT = Path.home() / ".openclaw" / "backups" / "control-tower-changes"
SOURCE_PATHS = (
    "AGENTS.md",
    "v2-react/src",
    "v2-react/index.html",
    "vite.config.ts",
    "package.json",
    "scripts/update_mission_control.py",
    "scripts/control_tower_path_guard.py",
    "scripts/control_tower_change_guard.py",
    "scripts/mission_control_regression_check.py",
    "scripts/mission_control_runtime_layout_check.py",
)
LEASE_MINUTES = 45


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(args: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, env=env, check=False)
    if check and proc.returncode:
        if proc.stdout.strip():
            print(proc.stdout.strip(), file=sys.stderr)
        if proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc


def read_lock() -> dict:
    try:
        payload = json.loads(LOCK_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    expires = payload.get("expiresAt")
    try:
        expired = dt.datetime.fromisoformat(str(expires).replace("Z", "+00:00")) <= now()
    except (TypeError, ValueError):
        expired = True
    if expired:
        LOCK_PATH.unlink(missing_ok=True)
        return {}
    return payload


def source_changes() -> list[str]:
    proc = run(["git", "status", "--porcelain", "--", *SOURCE_PATHS], check=False)
    return [line[3:].strip() for line in proc.stdout.splitlines() if len(line) > 3]


def require_token(token: str) -> dict:
    payload = read_lock()
    if not payload:
        raise SystemExit("No active Control Tower change lease.")
    if payload.get("token") != token:
        raise SystemExit(f"Control Tower is leased by {payload.get('agent', 'another agent')}.")
    return payload


def begin(agent: str, objective: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    existing = read_lock()
    if existing:
        raise SystemExit(json.dumps({"ok": False, "reason": "leased", "lease": existing}, indent=2))
    dirty = source_changes()
    if dirty:
        raise SystemExit(json.dumps({"ok": False, "reason": "canonical source already dirty", "paths": dirty}, indent=2))
    token = uuid.uuid4().hex
    stamp = now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_ROOT / f"{stamp}-{agent}"
    backup.mkdir(parents=True)
    for relative in SOURCE_PATHS:
        source = ROOT / relative
        if not source.exists():
            continue
        target = backup / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
    payload = {
        "agent": agent,
        "objective": objective,
        "token": token,
        "startedAt": iso(),
        "expiresAt": iso(now() + dt.timedelta(minutes=LEASE_MINUTES)),
        "baseCommit": run(["git", "rev-parse", "HEAD"]).stdout.strip(),
        "backup": str(backup),
        "source": "v2-react",
        "port": 5174,
    }
    LOCK_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"ok": True, "lease": payload}, indent=2))


def status() -> None:
    print(json.dumps({"ok": True, "lease": read_lock() or None, "sourceChanges": source_changes()}, indent=2))


def verify(token: str) -> None:
    payload = require_token(token)
    env = dict(os.environ)
    env["CONTROL_TOWER_ALLOW_GENERATED"] = "1"
    checks = [
        ([sys.executable, "scripts/control_tower_path_guard.py"], env),
        (["npm", "run", "build"], None),
        ([sys.executable, "scripts/update_mission_control.py"], None),
        ([sys.executable, "scripts/mission_control_regression_check.py"], None),
        ([sys.executable, "scripts/mission_control_runtime_layout_check.py"], None),
    ]
    results = []
    for command, command_env in checks:
        proc = run(command, check=False, env=command_env)
        results.append({"command": " ".join(command), "ok": proc.returncode == 0})
        if proc.returncode:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
            raise SystemExit(proc.returncode)
    print(json.dumps({"ok": True, "agent": payload["agent"], "checks": results, "sourceChanges": source_changes()}, indent=2))


def finish(token: str) -> None:
    payload = require_token(token)
    verify(token)
    LOCK_PATH.unlink(missing_ok=True)
    print(json.dumps({"ok": True, "released": payload["agent"], "backup": payload["backup"]}, indent=2))


def abort(token: str) -> None:
    payload = require_token(token)
    backup = Path(payload["backup"])
    for relative in SOURCE_PATHS:
        source = backup / relative
        target = ROOT / relative
        if not source.exists():
            continue
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    LOCK_PATH.unlink(missing_ok=True)
    print(json.dumps({"ok": True, "restored": str(backup), "released": payload["agent"]}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("begin")
    start.add_argument("--agent", required=True, choices=["joshex", "josh2", "jaimes", "jain"])
    start.add_argument("--objective", required=True)
    sub.add_parser("status")
    for name in ("verify", "finish", "abort"):
        command = sub.add_parser(name)
        command.add_argument("--token", required=True)
    args = parser.parse_args()
    if args.command == "begin": begin(args.agent, args.objective)
    elif args.command == "status": status()
    elif args.command == "verify": verify(args.token)
    elif args.command == "finish": finish(args.token)
    elif args.command == "abort": abort(args.token)


if __name__ == "__main__":
    main()
