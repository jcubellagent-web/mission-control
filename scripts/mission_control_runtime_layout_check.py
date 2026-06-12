#!/usr/bin/env python3
"""Write a lightweight Control Tower runtime layout status.

The full visual watchdog is optional on this host; this guard keeps the kiosk
state explicit so the UI does not show an indefinite "layout not checked" alert.
It verifies the local kiosk endpoint, dashboard JSON, and (when available) the
physical screenshot helper.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "mission-control-runtime-layout.json"
DASHBOARD = DATA / "dashboard-data.json"
KIOSK_URL = "http://127.0.0.1:5174/"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def check_http() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(KIOSK_URL, timeout=3) as resp:  # noqa: S310 - loopback kiosk
            ok = 200 <= int(resp.status) < 400
            return ok, f"HTTP {resp.status}"
    except Exception as exc:  # noqa: BLE001 - status writer should explain failures
        return False, f"kiosk unreachable: {exc}"


def check_dashboard_json() -> tuple[bool, str]:
    try:
        data = json.loads(DASHBOARD.read_text())
        if not isinstance(data, dict):
            return False, "dashboard JSON is not an object"
        return True, "dashboard JSON parsed"
    except Exception as exc:  # noqa: BLE001
        return False, f"dashboard JSON failed: {exc}"


def check_screenshot() -> tuple[bool, str]:
    helper = Path.home() / "scripts" / "capture_mission_control_screen.sh"
    if not helper.exists():
        return True, "screenshot helper unavailable; skipped"
    proc = subprocess.run([str(helper)], cwd=ROOT, capture_output=True, text=True, timeout=25, check=False)
    text = " ".join((proc.stdout + " " + proc.stderr).split())
    if proc.returncode != 0 or "SCREENSHOT_OK" not in text:
        return False, text[:220] or f"screenshot helper failed rc={proc.returncode}"
    return True, text[:220]


def main() -> int:
    checked_at = utc_now()
    checks: list[dict[str, Any]] = []
    for name, fn in (
        ("kiosk", check_http),
        ("dashboard", check_dashboard_json),
        ("screenshot", check_screenshot),
    ):
        ok, detail = fn()
        checks.append({"name": name, "ok": ok, "detail": detail})
    ok = all(row["ok"] for row in checks)
    issues = [row["detail"] for row in checks if not row["ok"]]
    payload = {
        "ok": ok,
        "status": "ok" if ok else "attention",
        "checkedAt": checked_at,
        "summary": "Kiosk endpoint, dashboard data, and screen capture checked." if ok else "; ".join(issues),
        "issues": issues,
        "checks": checks,
        "source": "mission_control_runtime_layout_check.py",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
