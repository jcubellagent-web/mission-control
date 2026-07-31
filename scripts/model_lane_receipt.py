#!/usr/bin/env python3
"""Append privacy-safe model-lane execution receipts to Control Tower."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECEIPTS_PATH = Path(os.environ.get("MODEL_LANE_RECEIPTS_PATH", ROOT / "data" / "model-lane-execution-receipts.jsonl"))
CONTROL_TOWER_HOST = os.environ.get("MODEL_LANE_CONTROL_TOWER_HOST", "josh2.0@josh2")
CONTROL_TOWER_REPO = "/Users/josh2.0/.openclaw/workspace/mission-control"


def append_record(record: dict[str, object]) -> None:
    RECEIPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RECEIPTS_PATH.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def forward(args: list[str]) -> int:
    command = ["python3", "scripts/model_lane_receipt.py", *args]
    proc = subprocess.run(
        ["ssh", CONTROL_TOWER_HOST, f"cd {shlex.quote(CONTROL_TOWER_REPO)} && {shlex.join(command)}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="" if proc.stderr.endswith("\n") else "\n")
    return proc.returncode


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["append", "disposition"])
    parser.add_argument("--receipt-id", required=True)
    parser.add_argument("--payload", default="{}", help="Metadata-only JSON; raw prompts and outputs are forbidden.")
    parser.add_argument("--status", choices=["integrated", "partial", "rejected", "pending"])
    parser.add_argument("--reason-code", default="")
    parser.add_argument("--local", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if Path.home().name != "josh2.0" and not args.local and "MODEL_LANE_RECEIPTS_PATH" not in os.environ:
        forwarded = [args.command, "--receipt-id", args.receipt_id]
        if args.command == "append":
            forwarded += ["--payload", args.payload]
        else:
            forwarded += ["--status", str(args.status), "--reason-code", args.reason_code]
        return forward(forwarded)
    if args.command == "append":
        payload = json.loads(args.payload)
        forbidden = {"prompt", "output", "response", "objective", "title"}
        if not isinstance(payload, dict) or forbidden.intersection(payload):
            raise SystemExit("receipt payload must be metadata-only")
        record = {"schemaVersion": 1, "event": "execution", "recordedAt": utc_iso(), "receiptId": args.receipt_id, **payload}
    else:
        if not args.status:
            parser.error("disposition requires --status")
        record = {
            "schemaVersion": 1,
            "event": "disposition",
            "recordedAt": utc_iso(),
            "receiptId": args.receipt_id,
            "integrationDisposition": args.status,
            "integrationReasonCode": args.reason_code or "controller-recorded",
        }
    append_record(record)
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
