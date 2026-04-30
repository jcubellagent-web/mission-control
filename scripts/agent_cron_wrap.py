#!/usr/bin/env python3
"""Safely wrap a single crontab line with agent_job_wrap.sh."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ROLLOUT_PATH = DATA_DIR / "automation-rollout.json"
BACKUP_DIR = DATA_DIR / "crontab-backups"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:60] or "cron"


def run(cmd: list[str], input_text: str | None = None) -> tuple[int, str]:
    proc = subprocess.run(cmd, input=input_text, capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def split_cron_line(line: str) -> tuple[str, str]:
    parts = line.split(None, 5)
    if not parts:
        raise SystemExit("Empty cron line.")
    if parts[0].startswith("@"):
        if len(parts) < 2:
            raise SystemExit("Invalid @ cron line.")
        return parts[0], parts[1]
    if len(parts) < 6:
        raise SystemExit(f"Invalid cron line: {line}")
    return " ".join(parts[:5]), parts[5]


def split_env(command: str) -> tuple[str, str]:
    tokens = shlex.split(command, posix=True)
    env_tokens: list[str] = []
    consumed = 0
    for token in tokens:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
            env_tokens.append(token)
            consumed += 1
            continue
        break
    if not env_tokens:
        return "", command
    remainder = command
    for _ in range(consumed):
        remainder = remainder.split(None, 1)[1] if len(remainder.split(None, 1)) > 1 else ""
    return " ".join(shlex.quote(token) for token in env_tokens), remainder.strip()


def wrapped_line(line: str, args: argparse.Namespace) -> str:
    schedule, command = split_cron_line(line)
    env_prefix, body = split_env(command)
    wrapper = args.wrapper
    call = " ".join([
        shlex.quote(wrapper),
        shlex.quote(args.agent),
        shlex.quote(args.title),
        shlex.quote(args.tool),
        shlex.quote(args.detail),
        "--",
        "/bin/zsh",
        "-lc",
        shlex.quote(body or command),
    ])
    command_out = f"{env_prefix} {call}".strip()
    return f"{schedule} {command_out}"


def record_rollout(args: argparse.Namespace, status: str, backup: str, original: str, wrapped: str) -> None:
    now = utc_now()
    row = {
        "id": f"{args.agent}-{slug(args.title)}",
        "agent": args.agent,
        "node": args.node,
        "title": args.title,
        "match": args.match,
        "status": status,
        "backup": backup,
        "original": original,
        "wrapped": wrapped,
        "updatedAt": now,
    }
    lock_path = ROLLOUT_PATH.with_suffix(".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_json(ROLLOUT_PATH, {"updatedAt": now, "rollouts": []})
        rows = [item for item in data.get("rollouts", []) if item.get("id") != row["id"]]
        rows.insert(0, row)
        data["rollouts"] = rows[:100]
        data["updatedAt"] = now
        write_json(ROLLOUT_PATH, data)
        fcntl.flock(lock, fcntl.LOCK_UN)


def main() -> int:
    parser = argparse.ArgumentParser(description="Wrap exactly one matching crontab line.")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--detail", required=True)
    parser.add_argument("--match", required=True)
    parser.add_argument("--wrapper", default=str(ROOT / "scripts" / "agent_job_wrap.sh"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    code, crontab = run(["crontab", "-l"])
    if code != 0:
        raise SystemExit(crontab.strip() or "crontab -l failed")
    lines = crontab.splitlines()
    matches = [idx for idx, line in enumerate(lines) if args.match in line and line.strip() and not line.lstrip().startswith("#")]
    if not matches:
        raise SystemExit(f"No active crontab line matched: {args.match}")
    unwrapped = [idx for idx in matches if "agent_job_wrap.sh" not in lines[idx] and "agent_publish.py" not in lines[idx]]
    if not unwrapped:
        print(json.dumps({"ok": True, "status": "already_wrapped", "matches": len(matches)}, indent=2))
        return 0
    if len(unwrapped) != 1:
        raise SystemExit(f"Refusing to wrap {len(unwrapped)} matching unwrapped lines for: {args.match}")

    idx = unwrapped[0]
    original = lines[idx]
    replacement = wrapped_line(original, args)
    new_lines = lines[:]
    new_lines[idx] = replacement
    backup_path = ""
    if args.apply:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / f"{utc_now().replace(':', '').replace('-', '')}-{args.agent}-{slug(args.title)}.crontab"
        backup.write_text(crontab if crontab.endswith("\n") else crontab + "\n")
        backup_path = str(backup)
        new_crontab = "\n".join(new_lines).rstrip() + "\n"
        code, out = run(["crontab", "-"], input_text=new_crontab)
        if code != 0:
            pending = BACKUP_DIR / f"{utc_now().replace(':', '').replace('-', '')}-{args.agent}-{slug(args.title)}.pending.crontab"
            pending.write_text(new_crontab)
            record_rollout(args, "install_blocked", backup_path, original, replacement)
            raise SystemExit(
                (out.strip() or "crontab install failed")
                + f"\nPending wrapped crontab written to {pending}"
            )
        record_rollout(args, "wrapped", backup_path, original, replacement)
    print(json.dumps({
        "ok": True,
        "status": "wrapped" if args.apply else "dry_run",
        "agent": args.agent,
        "node": args.node,
        "backup": backup_path,
        "original": original,
        "wrapped": replacement,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
