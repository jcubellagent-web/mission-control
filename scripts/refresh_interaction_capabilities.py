#!/usr/bin/env python3
"""Refresh Josh 2.0 and JAIMES interaction capability metadata.

This runs on Josh 2.0, probes local capability state, requests the same
metadata-only probe from JAIMES, merges both records into the existing
capability inventory, and optionally refreshes Control Tower.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import capability_inventory  # noqa: E402


def run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)


def parse_payload(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def make_args(node: str, agent: str, role: str, active: bool) -> argparse.Namespace:
    return argparse.Namespace(
        node=node,
        agent=agent,
        python=sys.executable,
        interaction_role=role,
        active_interaction_canary=active,
        merge=False,
    )


def remote_record(target: str, active: bool) -> dict[str, Any]:
    remote_root = os.environ.get(
        "JAIMES_MISSION_CONTROL_ROOT",
        "/Users/jc_agent/.openclaw/workspace/mission-control",
    )
    command = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        target,
        "/usr/bin/python3",
        f"{remote_root}/scripts/capability_inventory.py",
        "--node", "jaimes",
        "--agent", "jaimes",
        "--interaction-role", "headless",
    ]
    if active:
        command.append("--active-interaction-canary")
    proc = run(command, timeout=80)
    payload = parse_payload(proc.stdout)
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    if proc.returncode != 0 or not nodes or not isinstance(nodes[0], dict):
        return {
            "node": "jaimes",
            "agent": "jaimes",
            "checkedAt": capability_inventory.utc_now(),
            "interaction": {
                "host": "jaimes",
                "role": "headless",
                "status": "down",
                "privacy": {"dashboardSafeOnly": True},
            },
        }
    return nodes[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh metadata-only interaction capabilities.")
    parser.add_argument("--remote-target", default=os.environ.get("JAIMES_SSH_ALIAS", "jc_agent@100.121.89.84"))
    parser.add_argument("--active-canary", action="store_true")
    parser.add_argument("--refresh-dashboard", action="store_true")
    args = parser.parse_args()

    local = capability_inventory.collect(make_args("josh2", "josh2", "visible", args.active_canary))
    capability_inventory.merge(local)
    remote = remote_record(args.remote_target, args.active_canary)
    merged = capability_inventory.merge(remote)

    if args.refresh_dashboard:
        run([sys.executable, str(SCRIPTS / "update_mission_control.py")], timeout=120)

    summary = {
        "ok": all(
            isinstance(row.get("interaction"), dict) and row["interaction"].get("status") == "ok"
            for row in (local, remote)
        ),
        "updatedAt": merged.get("updatedAt"),
        "hosts": [
            {
                "host": row.get("node"),
                "role": (row.get("interaction") or {}).get("role"),
                "status": (row.get("interaction") or {}).get("status"),
            }
            for row in (local, remote)
        ],
        "privacy": "dashboard-safe metadata only",
    }
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
