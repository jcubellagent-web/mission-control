#!/usr/bin/env python3
"""History-preserving ledger for dashboard-safe ecosystem change proposals."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "ecosystem-proposals.json"
VALID = {
    "proposed", "approved", "leased", "implementing", "verifying",
    "implemented", "deferred", "rejected", "superseded",
}
RISK_LEVELS = {"low", "medium", "high", "unclassified"}


def iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


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


def proposal_id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:52]
    return f"proposal-{dt.datetime.now(dt.timezone.utc):%Y%m%d}-{slug}-{uuid.uuid4().hex[:6]}"


def current_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """Return one latest event per proposal without rewriting ledger history."""
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, row in enumerate(rows):
        if isinstance(row, dict) and row.get("id"):
            grouped.setdefault(str(row["id"]), []).append((index, row))
    latest: list[dict[str, Any]] = []
    for events in grouped.values():
        _index, row = max(events, key=lambda item: (str(item[1].get("updatedAt") or ""), item[0]))
        latest.append(row)
    return latest


def publish_summary(document: dict[str, Any]) -> None:
    open_rows = [
        row for row in current_rows(document.get("proposals", []))
        if row.get("status") in {"proposed", "approved", "leased", "implementing", "verifying"}
    ]
    command = [
        sys.executable, str(ROOT / "scripts" / "agent_publish.py"),
        "--agent", "josh2", "--type", "status", "--status", "info",
        "--title", "Ecosystem proposal ledger updated",
        "--tool", "proposal ledger",
        "--detail", f"{len(open_rows)} current proposal(s) remain open; publication never implies approval.",
        "--privacy", "dashboard-safe", "--brain-feed",
    ]
    subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title")
    parser.add_argument("--summary")
    parser.add_argument("--owner", default="josh2")
    parser.add_argument("--status", choices=sorted(VALID), default="proposed")
    parser.add_argument("--id")
    parser.add_argument("--risk", choices=sorted(RISK_LEVELS))
    parser.add_argument("--area")
    parser.add_argument("--source-candidate-id")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    document = read_json(LEDGER, {"version": 1, "proposals": []})
    proposals = [row for row in document.get("proposals", []) if isinstance(row, dict)]
    if args.title:
        if not args.summary:
            parser.error("--summary is required with --title")
        timestamp = iso()
        row = {
            "id": args.id or proposal_id(args.title),
            "title": args.title,
            "summary": args.summary,
            "owner": args.owner,
            "status": args.status,
            "risk": args.risk or "unclassified",
            "area": args.area or "Reliability",
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "privacy": "dashboard-safe",
            "approvalImpliedByPublication": False,
        }
        if args.source_candidate_id:
            row["sourceCandidateId"] = args.source_candidate_id
        proposals.append(row)
    elif args.id:
        matches = [row for row in proposals if row.get("id") == args.id]
        if not matches:
            parser.error(f"proposal not found: {args.id}")
        current = max(matches, key=lambda row: str(row.get("updatedAt") or ""))
        transition = dict(current)
        transition["previousStatus"] = current.get("status")
        transition["status"] = args.status
        transition["updatedAt"] = iso()
        if args.summary:
            transition["summary"] = args.summary
        if args.risk:
            transition["risk"] = args.risk
        if args.area:
            transition["area"] = args.area
        if args.source_candidate_id:
            transition["sourceCandidateId"] = args.source_candidate_id
        proposals.append(transition)
    document.update({"version": 1, "updatedAt": iso(), "proposals": proposals[-1000:]})
    atomic_write(LEDGER, document)
    if args.publish:
        publish_summary(document)
    print(json.dumps(document, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
