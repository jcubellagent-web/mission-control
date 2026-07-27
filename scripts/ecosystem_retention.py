#!/usr/bin/env python3
"""Bound ecosystem log growth while preserving active tails and audit history."""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "ecosystem-retention.json"
MAX_LOG_BYTES = 10 * 1024 * 1024
TAIL_BYTES = 2 * 1024 * 1024
MAX_GENERATIONS = 7
CONVERSATION_RETENTION_DAYS = 5
CONVERSATION_KEEP_NEWEST = 20


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


def log_roots() -> list[Path]:
    home = Path.home()
    return [ROOT / "logs", home / ".openclaw" / "workspace" / "logs", home / "agent-loops" / "logs"]


def rotate(path: Path, dry_run: bool) -> dict[str, Any] | None:
    size = path.stat().st_size
    if size <= MAX_LOG_BYTES:
        return None
    record = {"path": str(path), "beforeBytes": size, "preservedTailBytes": min(size, TAIL_BYTES), "dryRun": dry_run}
    if dry_run:
        return record
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = path.with_name(f"{path.name}.{timestamp}.gz")
    with path.open("rb") as source, gzip.open(archive, "wb", compresslevel=6) as target:
        shutil.copyfileobj(source, target)
    with path.open("rb") as source:
        source.seek(max(0, size - TAIL_BYTES))
        tail = source.read()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tail")
    with temporary.open("wb") as handle:
        handle.write(tail)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    archives = sorted(path.parent.glob(path.name + ".*.gz"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in archives[MAX_GENERATIONS:]:
        old.unlink(missing_ok=True)
    record["archive"] = str(archive)
    record["afterBytes"] = path.stat().st_size
    return record


def open_conversation_paths(root: Path) -> set[Path]:
    """Return only currently open Antigravity conversation files.

    The result is used as a retention exclusion and is intentionally never
    written to the dashboard-safe retention sidecar.
    """
    try:
        completed = subprocess.run(
            ["/usr/sbin/lsof", "-Fn", "+D", str(root)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {Path(line[1:]).resolve() for line in completed.stdout.splitlines() if line.startswith("n")}


def retain_antigravity_conversations(dry_run: bool, retention_days: int, keep_newest: int) -> dict[str, Any]:
    """Bound completed Antigravity conversation storage without touching live work.

    Keep the newest recovery set, every recently modified file, and any database
    that is open at the moment of the sweep.  The sidecar exposes counts and
    bytes only, never filenames or conversation content.
    """
    root = Path.home() / ".gemini" / "antigravity-cli" / "conversations"
    policy = {
        "retentionDays": retention_days,
        "keepNewest": keep_newest,
        "preserveOpenFiles": True,
        "contentsPublished": False,
    }
    if not root.exists():
        return {"status": "absent", "policy": policy, "examined": 0, "removed": 0, "reclaimedBytes": 0}

    files = sorted((item for item in root.glob("*.db") if item.is_file()), key=lambda item: item.stat().st_mtime, reverse=True)
    protected = {item.resolve() for item in files[:keep_newest]}
    protected.update(open_conversation_paths(root))
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - (retention_days * 24 * 60 * 60)
    candidates = [item for item in files if item.resolve() not in protected and item.stat().st_mtime < cutoff]
    reclaimed = 0
    removed = 0
    errors = 0
    for path in candidates:
        try:
            reclaimed += path.stat().st_size
            if not dry_run:
                path.unlink()
                for suffix in ("-wal", "-shm"):
                    path.with_name(path.name + suffix).unlink(missing_ok=True)
            removed += 1
        except OSError:
            errors += 1
    return {
        "status": "attention" if errors else "ok",
        "policy": policy,
        "examined": len(files),
        "protected": len(files) - len(candidates),
        "eligible": len(candidates),
        "removed": removed,
        "reclaimedBytes": reclaimed,
        "dryRun": dry_run,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--conversation-retention-days", type=int, default=CONVERSATION_RETENTION_DAYS)
    parser.add_argument("--conversation-keep-newest", type=int, default=CONVERSATION_KEEP_NEWEST)
    parser.add_argument("--skip-conversation-retention", action="store_true")
    args = parser.parse_args()
    if args.conversation_retention_days < 1 or args.conversation_keep_newest < 1:
        parser.error("conversation retention days and newest count must be positive")
    rows = []
    for root in log_roots():
        if not root.exists():
            continue
        for path in root.rglob("*.log"):
            try:
                result = rotate(path, args.dry_run)
            except OSError as exc:
                rows.append({"path": str(path), "error": type(exc).__name__})
                continue
            if result:
                rows.append(result)
    conversations = {"status": "skipped"}
    if not args.skip_conversation_retention:
        conversations = retain_antigravity_conversations(
            args.dry_run,
            args.conversation_retention_days,
            args.conversation_keep_newest,
        )
    payload = {
        "checkedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "attention" if any("error" in row for row in rows) or conversations.get("status") == "attention" else "ok",
        "rotated": sum("error" not in row for row in rows),
        "rows": rows,
        "conversations": conversations,
        "policy": {
            "maxLogBytes": MAX_LOG_BYTES,
            "preservedTailBytes": TAIL_BYTES,
            "compressedGenerations": MAX_GENERATIONS,
            "antigravityConversationRetentionDays": args.conversation_retention_days,
            "antigravityConversationKeepNewest": args.conversation_keep_newest,
            "hermesDatabaseTouched": False,
        },
    }
    if not args.dry_run:
        atomic_write(OUTPUT, payload)
    print(json.dumps(payload, indent=2))
    return 1 if payload["status"] == "attention" else 0


if __name__ == "__main__":
    raise SystemExit(main())
