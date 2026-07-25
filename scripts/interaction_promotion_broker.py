#!/usr/bin/env python3
"""Josh 2.0 pull broker for JAIMES headless-to-visible interaction promotion.

The broker transfers only bounded request metadata over the private host link.
Lease IDs are returned to JAIMES over SSH stdin and never printed or published.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

try:
    import control_tower_foreground
except ModuleNotFoundError:  # package import during repository tests
    from scripts import control_tower_foreground


ROOT = Path(__file__).resolve().parents[1]
REMOTE_ROOT = os.environ.get("INTERACTION_JAIMES_ROOT", "/Users/jc_agent/.openclaw/workspace/mission-control")
REMOTE_COMMAND = f"cd {shlex.quote(REMOTE_ROOT)} && python3 scripts/interaction_session_engine.py"


def remote_json(arguments: str, payload: dict[str, Any] | None = None, timeout: int = 20) -> dict[str, Any]:
    proc = subprocess.run(
        ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "jaimes", f"{REMOTE_COMMAND} {arguments}"],
        input=json.dumps(payload, separators=(",", ":")) if payload is not None else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    try:
        result = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        result = {}
    if proc.returncode != 0 or not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("JAIMES promotion queue is unavailable")
    return result


def private_complete(request_id: str, response: dict[str, Any]) -> None:
    if not request_id.startswith("ipr-") or len(request_id) > 80:
        raise RuntimeError("invalid promotion request id")
    remote_json(f"broker-complete --request-id {request_id}", response)


def release_request(row: dict[str, Any]) -> str:
    lease_id = str(row.get("leaseId") or "")
    status = "already-released"
    if lease_id:
        try:
            result = control_tower_foreground.end_lease(lease_id=lease_id)
            status = "released" if result.get("ended") else "already-released"
            control_tower_foreground.publish_display_lease(None)
            control_tower_foreground.restore_after_release()
        except PermissionError:
            # Never release another owner's newer lease.
            status = "already-released"
    private_complete(str(row.get("requestId")), {"status": status})
    return status


def promote_request(row: dict[str, Any]) -> str:
    request_id = str(row.get("requestId"))
    if row.get("expired") is True or int(row.get("expiresEpoch") or 0) < int(time.time()):
        private_complete(request_id, {"status": "expired"})
        return "expired"
    owner = str(row.get("owner") or "")
    purpose = str(row.get("purpose") or "")
    try:
        payload = control_tower_foreground.begin_lease(owner=owner, purpose=purpose, ttl_seconds=180)
        control_tower_foreground.publish_display_lease({"active": True, **payload})
    except (ValueError, RuntimeError):
        # A current visible-work lease is a normal temporary deferral.
        return "deferred"
    try:
        private_complete(
            request_id,
            {"status": "leased", "leaseId": payload["leaseId"], "expiresAt": payload.get("expiresAt")},
        )
    except Exception:
        control_tower_foreground.end_lease(lease_id=payload["leaseId"])
        control_tower_foreground.publish_display_lease(None)
        control_tower_foreground.restore_after_release()
        raise
    return "leased"


def process_once(fetch: Callable[[str], dict[str, Any]] = remote_json) -> dict[str, Any]:
    payload = fetch("broker-export")
    rows = payload.get("requests") if isinstance(payload.get("requests"), list) else []
    summary = {"checked": len(rows), "leased": 0, "released": 0, "expired": 0, "deferred": 0, "errors": 0}
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        try:
            if row.get("kind") == "release":
                status = release_request(row)
                summary["released"] += 1 if status in {"released", "already-released"} else 0
            elif row.get("kind") == "promote":
                status = promote_request(row)
                summary[status] = int(summary.get(status) or 0) + 1
        except Exception:
            summary["errors"] += 1
    return {"ok": summary["errors"] == 0, **summary}


def main() -> int:
    try:
        result = process_once()
    except Exception:
        result = {"ok": False, "checked": 0, "leased": 0, "released": 0, "expired": 0, "deferred": 0, "errors": 1}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
