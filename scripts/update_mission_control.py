#!/usr/bin/env python3
"""Update Mission Control dashboard JSON with live data."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[0]
def utc_iso(delta: dt.timedelta | None = None) -> str:
    base = dt.datetime.now(dt.timezone.utc)
    if delta:
        base += delta
    return base.replace(microsecond=0).isoformat().replace('+00:00', 'Z')

DASHBOARD_PATH = ROOT.parent / "data" / "dashboard-data.json"
NEXT_BASE = "http://127.0.0.1:3030"

CRON_TARGETS = [
    {"name": "Chiro invite sync", "pattern": "scripts/chiro_invite_sync.sh", "schedule": "Hourly"},
    {"name": "Mission Control refresh", "pattern": "mission-control/scripts/update_and_push.sh", "schedule": "*/30 * * * *"}
]


def fetch_next(endpoint: str) -> Dict[str, Any] | None:
    url = f"{NEXT_BASE}{endpoint}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # nosec B310
            return json.load(resp)
    except urllib.error.URLError as exc:  # pragma: no cover - diagnostics only
        print(f"[warn] failed to fetch {url}: {exc}", file=sys.stderr)
        return None


def fetch_brain_feed() -> Dict[str, Any] | None:
    data = fetch_next("/api/brain-feed")
    if not data:
        return None
    now_iso = utc_iso()
    return {
        "status": data.get("status") or "",
        "context": data.get("focus") or "",
        "runway": data.get("energy") or 0,
        "updatedAt": now_iso,
    }


def fetch_model_usage() -> Dict[str, Any] | None:
    data = fetch_next("/api/model-usage")
    if not data:
        return None
    def normalize(bucket: Dict[str, Any]) -> float:
        return float(bucket.get("totalCost") or 0)
    rows: List[Dict[str, Any]] = []
    seen = set()
    for bucket_name in ("session", "daily", "weekly"):
        for model in data.get(bucket_name, {}).get("models", []):
            name = model.get("name") or ""
            if not name:
                continue
            key = (bucket_name, name)
            if key in seen:
                continue
            seen.add(key)
            amount = model.get(f"{bucket_name}Cost")
            if amount is None:
                amount = model.get("sessionCost")
            rows.append({
                "name": name,
                "window": bucket_name,
                "cost": float(amount or 0),
            })
    rows.sort(key=lambda row: row["cost"], reverse=True)
    return {
        "session": normalize(data.get("session", {})),
        "daily": normalize(data.get("daily", {})),
        "weekly": normalize(data.get("weekly", {})),
        "topModels": rows[:5],
        "lastUpdated": data.get("lastUpdated"),
    }


def fetch_upcoming_events(limit: int = 3) -> List[Dict[str, Any]]:
    time_min = utc_iso()
    time_max = utc_iso(dt.timedelta(days=2))
    params = json.dumps({
        "calendarId": "primary",
        "singleEvents": True,
        "orderBy": "startTime",
        "timeMin": time_min,
        "timeMax": time_max,
        "maxResults": 10,
    })
    try:
        result = subprocess.run(
            ["gws", "calendar", "events", "list", "--params", params],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"[warn] gws calendar list failed: {exc.stderr}", file=sys.stderr)
        return []
    payload = json.loads(result.stdout or "{}")
    events = []
    for item in payload.get("items", []):
        start = item.get("start", {})
        start_time = start.get("dateTime") or start.get("date")
        if not start_time:
            continue
        events.append(
            {
                "time": start_time,
                "title": item.get("summary") or "(No title)",
                "source": (item.get("organizer") or {}).get("email") or item.get("creator", {}).get("email") or "calendar",
            }
        )
    return events[:limit]


def caffeinate_status() -> Dict[str, str]:
    result = subprocess.run(
        ["pgrep", "-f", "caffeinate -dims"], capture_output=True, text=True
    )
    if result.returncode != 0:
        return {
            "name": "caffeinate (neat-otter)",
            "status": "attention",
            "detail": "Not running",
        }
    pid = result.stdout.strip().splitlines()[0]
    etime = subprocess.run(["ps", "-o", "etime=", "-p", pid], capture_output=True, text=True).stdout.strip()
    return {
        "name": "caffeinate (neat-otter)",
        "status": "ok",
        "detail": f"PID {pid} · up {etime}",
    }


def airpoint_status() -> Dict[str, str]:
    try:
        result = subprocess.run([
            "airpoint",
            "status",
            "--json",
        ], capture_output=True, text=True, check=True)
        data = json.loads(result.stdout or "{}")
        version = data.get("version", "?")
        return {"name": "Airpoint", "status": "ok", "detail": f"v{version}"}
    except subprocess.CalledProcessError as exc:
        return {"name": "Airpoint", "status": "attention", "detail": f"Status check failed ({exc.returncode})"}


def peekaboo_status() -> Dict[str, str]:
    try:
        result = subprocess.run([
            "peekaboo",
            "permissions",
            "--json",
        ], capture_output=True, text=True, check=True)
        data = json.loads(result.stdout or "{}")
        grants = data.get("data", {}).get("permissions", [])
        missing = [p for p in grants if p.get("isRequired") and not p.get("isGranted")]
        if missing:
            names = ", ".join(p.get("name", "") for p in missing)
            return {"name": "Peekaboo", "status": "attention", "detail": f"Missing: {names}"}
        return {"name": "Peekaboo", "status": "ok", "detail": "Permissions granted"}
    except subprocess.CalledProcessError as exc:
        return {"name": "Peekaboo", "status": "attention", "detail": f"permissions check failed ({exc.returncode})"}


def fetch_crons() -> List[Dict[str, Any]]:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True)
        listing = result.stdout
    except subprocess.CalledProcessError:
        listing = ""
    rows = []
    for target in CRON_TARGETS:
        present = target['pattern'] in listing
        rows.append({
            'name': target['name'],
            'schedule': target['schedule'],
            'status': 'ok' if present else 'paused',
            'errors': 0,
            'lastError': None
        })
    return rows

def build_devices() -> List[Dict[str, str]]:
    return [airpoint_status(), caffeinate_status(), peekaboo_status()]


def main() -> None:
    DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DASHBOARD_PATH.exists():
        dashboard = json.loads(DASHBOARD_PATH.read_text())
    else:
        dashboard = {}

    brain = fetch_brain_feed()
    if brain:
        dashboard["focus"] = brain

    model_usage = fetch_model_usage()
    if model_usage:
        dashboard["modelUsage"] = model_usage

    events = fetch_upcoming_events()
    dashboard["upcomingEvents"] = events

    dashboard["devices"] = build_devices()

    dashboard["crons"] = fetch_crons()

    dashboard["lastUpdated"] = utc_iso()

    DASHBOARD_PATH.write_text(json.dumps(dashboard, indent=2))
    print(f"Updated {DASHBOARD_PATH}")


if __name__ == "__main__":
    main()
