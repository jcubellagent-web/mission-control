#!/usr/bin/env python3
"""Validate and atomically promote JAIMES black-box QA into Control Tower."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT = DATA_DIR / "jaimes-control-tower-blackbox.json"
STATUS = DATA_DIR / "remote-qa-ingest-status.json"
REMOTE = "/Users/jc_agent/.openclaw/workspace/mission-control/data/jaimes-control-tower-blackbox.json"
FORBIDDEN = {"token", "password", "cookie", "oauth", "secret", "emailbody", "rawprompt", "connectorpayload"}


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


def parse_ts(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def forbidden_keys(value: Any, found: set[str] | None = None) -> set[str]:
    found = found or set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
            if normalized in FORBIDDEN:
                found.add(str(key))
            forbidden_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            forbidden_keys(item, found)
    return found


def validate(payload: Any) -> list[str]:
    issues = []
    if not isinstance(payload, dict):
        return ["payload is not an object"]
    if payload.get("owner") != "jaimes":
        issues.append("owner must be jaimes")
    if payload.get("privacy") != "dashboard-safe":
        issues.append("privacy must be dashboard-safe")
    stamp = parse_ts(payload.get("checkedAt"))
    if not stamp or dt.datetime.now(dt.timezone.utc) - stamp.astimezone(dt.timezone.utc) > dt.timedelta(minutes=30):
        issues.append("sidecar is missing or older than 30 minutes")
    unsafe = forbidden_keys(payload)
    if unsafe:
        issues.append("forbidden raw-content keys: " + ", ".join(sorted(unsafe)))
    if payload.get("status") not in {"ok", "attention", "error"}:
        issues.append("invalid status")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("JAIMES_SSH_ALIAS", "jc_agent@100.121.89.84"))
    parser.add_argument("--remote", default=REMOTE)
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DATA_DIR) as temporary:
        candidate = Path(temporary) / "candidate.json"
        proc = subprocess.run(["scp", "-q", f"{args.host}:{args.remote}", str(candidate)], text=True, capture_output=True, timeout=30, check=False)
        issues = []
        payload: Any = None
        if proc.returncode:
            issues.append(f"remote fetch failed with exit {proc.returncode}")
        else:
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception as exc:
                issues.append(f"invalid JSON: {type(exc).__name__}")
        if not issues:
            issues.extend(validate(payload))
        checked = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        status = {"checkedAt": checked, "ok": not issues, "status": "ok" if not issues else "attention", "issues": issues, "lastGoodPreserved": bool(issues and OUTPUT.exists())}
        if issues:
            atomic_write(STATUS, status)
            print(json.dumps(status, indent=2))
            return 1
        atomic_write(OUTPUT, payload)
        atomic_write(STATUS, status)
        print(json.dumps(status, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
