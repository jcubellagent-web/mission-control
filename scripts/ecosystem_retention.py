#!/usr/bin/env python3
"""Bound ecosystem log growth while preserving active tails and audit history."""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "ecosystem-retention.json"
MAX_LOG_BYTES = 10 * 1024 * 1024
TAIL_BYTES = 2 * 1024 * 1024
MAX_GENERATIONS = 7


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
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
    payload = {
        "checkedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "attention" if any("error" in row for row in rows) else "ok",
        "rotated": sum("error" not in row for row in rows),
        "rows": rows,
        "policy": {"maxLogBytes": MAX_LOG_BYTES, "preservedTailBytes": TAIL_BYTES, "compressedGenerations": MAX_GENERATIONS, "hermesDatabaseTouched": False},
    }
    if not args.dry_run:
        atomic_write(OUTPUT, payload)
    print(json.dumps(payload, indent=2))
    return 1 if payload["status"] == "attention" else 0


if __name__ == "__main__":
    raise SystemExit(main())
