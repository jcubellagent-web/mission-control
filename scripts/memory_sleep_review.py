#!/usr/bin/env python3
"""Nightly governed memory review: ingest, dedupe, review, and publish health."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "scripts" / "memory_registry.py"
REPORT = ROOT / "logs" / "memory-review-latest.json"


def run(*args: str) -> dict:
    proc = subprocess.run([sys.executable, str(REGISTRY), *args], cwd=ROOT, text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)


def main() -> int:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    built = run("build")
    reviewed = run("review", "--apply-safe")
    status = run("export")
    payload = {"ok": True, "build": built, "review": reviewed, "status": status}
    REPORT.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
