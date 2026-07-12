#!/usr/bin/env python3
"""Behavior smoke test for promotion, retrieval, and conflict handling."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "memory_registry.py"


def run(env: dict[str, str], *args: str) -> dict:
    proc = subprocess.run([sys.executable, str(CLI), *args], cwd=ROOT, env=env, text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="memory-registry-") as folder:
        env = dict(os.environ)
        env["MEMORY_REGISTRY_DB"] = str(Path(folder) / "registry.sqlite")
        env["MEMORY_OPERATIONS_PATH"] = str(Path(folder) / "status.json")
        run(env, "init")
        common = ["propose", "--agent", "jaimes", "--type", "fact", "--subject", "Acceptance memory", "--predicate", "has state", "--owner", "ecosystem", "--visibility", "shared", "--privacy", "dashboard-safe", "--source", "smoke:tool-verified", "--confidence", "0.98"]
        run(env, *common, "--value", "ready", "--evidence", "smoke test")
        first = run(env, "review", "--apply-safe")
        found = run(env, "retrieve", "--agent", "jain", "--query", "Acceptance memory ready", "--limit", "2")
        run(env, *common, "--value", "not-ready", "--evidence", "conflict test")
        second = run(env, "review", "--apply-safe")
        policy = run(env, "propose", "--agent", "josh2", "--type", "procedure", "--subject", "Manual policy", "--predicate", "requires", "--value", "human review", "--owner", "ecosystem", "--visibility", "shared", "--privacy", "dashboard-safe", "--source", "smoke:user-stated", "--confidence", "0.99")
        third = run(env, "review", "--apply-safe")
        pending = run(env, "candidates", "--status", "candidate")
        rejected = run(env, "reject", "--id", policy["id"], "--reviewer", "joshex", "--reason", "smoke cleanup")
        assert first["promoted"] == 1, first
        assert found["results"] and found["results"][0]["value"] == "ready", found
        assert second["disputed"] == 1, second
        assert third["pending"] == 1 and pending["candidates"], (third, pending)
        assert rejected["status"] == "rejected", rejected
    print(json.dumps({"ok": True, "promotion": True, "retrieval": True, "conflictDetection": True, "manualReview": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
