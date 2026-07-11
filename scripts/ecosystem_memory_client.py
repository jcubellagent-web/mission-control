#!/usr/bin/env python3
"""Portable client for the Josh 2.0 shared memory registry."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys


HOST = os.environ.get("ECOSYSTEM_MEMORY_HOST", "josh2.0@josh2")
ROOT = "/Users/josh2.0/.openclaw/workspace/mission-control"


def main() -> int:
    if not sys.argv[1:]:
        print("Usage: ecosystem_memory_client.py retrieve|propose|status ...", file=sys.stderr)
        return 2
    remote = f"cd {shlex.quote(ROOT)} && python3 scripts/memory_registry.py {shlex.join(sys.argv[1:])}"
    return subprocess.run(["ssh", "-o", "StrictHostKeyChecking=accept-new", HOST, remote], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
