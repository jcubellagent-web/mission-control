#!/usr/bin/env python3
"""Publish a counts-only, read-only health snapshot for shared Gmail."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "josh2-ops-gmail-status.json"
DEFAULT_ACCOUNT = "jcubellagent@gmail.com"
KEYRING_ENV = Path.home() / ".openclaw" / "secrets" / "gog-keyring.env"


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_keyring_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base or os.environ)
    try:
        lines = KEYRING_ENV.read_text(encoding="utf-8").splitlines()
    except OSError:
        return env
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        if key and key.replace("_", "").isalnum():
            env[key] = value.strip().strip("'\"")
    return env


def build_command(account: str, limit: int) -> list[str]:
    return [
        "gog",
        "gmail",
        "search",
        "is:unread in:inbox newer_than:7d",
        "--account",
        account,
        "--readonly",
        "--gmail-no-send",
        "--no-input",
        "--json",
        "--results-only",
        "--select=id",
        "--max",
        str(limit),
    ]


def result_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("threads", "messages", "results", "items"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return len(rows)
    return 0


def safe_error_reason(stderr: str) -> str:
    lowered = stderr.lower()
    if "no auth" in lowered or "auth add" in lowered:
        return "authentication_not_configured"
    if "keyring" in lowered or "no tty" in lowered:
        return "keyring_unavailable"
    if "invalid_grant" in lowered or "expired" in lowered or "revoked" in lowered:
        return "authentication_refresh_required"
    if "not found" in lowered:
        return "gog_unavailable"
    return "gmail_query_failed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default=DEFAULT_ACCOUNT)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    limit = min(500, max(1, args.limit))
    checked_at = iso_now()
    try:
        proc = subprocess.run(
            build_command(args.account, limit),
            cwd=ROOT,
            env=load_keyring_environment(),
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        reason = "gog_unavailable" if isinstance(exc, FileNotFoundError) else "gmail_query_timeout"
        payload = {
            "schema": 1,
            "checkedAt": checked_at,
            "status": "blocked",
            "account": args.account,
            "mode": "read-only counts only",
            "reason": reason,
            "markedRead": 0,
            "privacy": "No email bodies, subjects, senders, message IDs, or OAuth data persisted.",
        }
        if not args.no_write:
            atomic_write(OUTPUT, payload)
        print(json.dumps(payload, indent=2))
        return 2

    if proc.returncode != 0:
        payload = {
            "schema": 1,
            "checkedAt": checked_at,
            "status": "blocked",
            "account": args.account,
            "mode": "read-only counts only",
            "reason": safe_error_reason(proc.stderr),
            "markedRead": 0,
            "privacy": "No email bodies, subjects, senders, message IDs, or OAuth data persisted.",
        }
        if not args.no_write:
            atomic_write(OUTPUT, payload)
        print(json.dumps(payload, indent=2))
        return 2

    try:
        count = result_count(json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError:
        count = 0
    payload = {
        "schema": 1,
        "checkedAt": checked_at,
        "status": "done",
        "account": args.account,
        "mode": "read-only counts only",
        "unreadBeforeCapped": count,
        "unreadCountCapped": count,
        "resultLimit": limit,
        "mayBeTruncated": count >= limit,
        "markedRead": 0,
        "privacy": "No email bodies, subjects, senders, message IDs, or OAuth data persisted.",
    }
    if not args.no_write:
        atomic_write(OUTPUT, payload)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
