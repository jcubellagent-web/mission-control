#!/usr/bin/env python3
"""Update the local JOSHeX Mission Control sidecar without exposing secrets."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Push a safe live JOSHeX status into Mission Control.")
    parser.add_argument("--file", default="data/personal-codex.json", help="Sidecar JSON file to update.")
    parser.add_argument("--status", default="active", help="Visible JOSHeX status.")
    parser.add_argument("--objective", required=True, help="Current work visible in the JOSHeX panel and Brain Feed.")
    parser.add_argument("--validation", default="", help="Current validation state.")
    parser.add_argument("--activity", action="append", default=[], help="Recent activity row. May be repeated.")
    parser.add_argument("--capability", action="append", default=[], help="Capability row. May be repeated.")
    parser.add_argument("--summary", default="", help="Patch summary.")
    parser.add_argument("--detail", default="", help="Patch detail.")
    parser.add_argument("--dirty-count", type=int, default=None, help="Number of files currently changed.")
    parser.add_argument("--changed-file", action="append", default=[], help="Changed file path. May be repeated.")
    args = parser.parse_args()

    path = Path(args.file)
    data = load_json(path)
    now = utc_now()

    data["status"] = args.status
    data["objective"] = args.objective
    data["validation"] = args.validation or data.get("validation") or "Mission Control live status updated"
    data["updatedAt"] = now
    data["agentSlot"] = False
    data["promoteToBrainFeed"] = False

    if args.activity:
        data["recentActivity"] = args.activity[:4]
    elif not data.get("recentActivity"):
        data["recentActivity"] = [args.objective]

    if args.capability:
        data["capabilities"] = args.capability[:5]
    elif not data.get("capabilities"):
        data["capabilities"] = ["Inspect Mission Control", "Edit UI", "Run validation", "Prepare Git patches"]

    patch = data.get("patchStatus") if isinstance(data.get("patchStatus"), dict) else {}
    patch["status"] = args.status
    patch["summary"] = args.summary or args.objective
    patch["detail"] = args.detail or data["validation"]
    patch["updatedAt"] = now
    if args.dirty_count is not None:
        patch["dirtyCount"] = max(0, args.dirty_count)
    if args.changed_file:
        patch["files"] = args.changed_file[:8]
    data["patchStatus"] = patch

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"JOSHeX status updated: {path} @ {now}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
