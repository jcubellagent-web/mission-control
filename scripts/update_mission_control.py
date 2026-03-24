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
WORKSPACE_ROOT = ROOT.parent.parent
KIOSK_MODEL_USAGE_PATH = WORKSPACE_ROOT / "kiosk-dashboard" / "data" / "modelUsage.json"

CRON_TARGETS = [
    {"name": "Chiro invite sync", "pattern": "scripts/chiro_invite_sync.sh", "schedule": "Hourly"},
    {"name": "Mission Control refresh", "pattern": "mission-control/scripts/update_and_push.sh", "schedule": "*/5 * * * *"}
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


def normalize_model_usage_payload(data: Dict[str, Any]) -> Dict[str, Any]:
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
        "breakdown": data.get("breakdown", []),
        "lastUpdated": data.get("lastUpdated"),
    }



def fetch_model_usage() -> Dict[str, Any] | None:
    data = fetch_next("/api/model-usage")
    if data:
        return normalize_model_usage_payload(data)

    if not KIOSK_MODEL_USAGE_PATH.exists():
        return None

    try:
        fallback = json.loads(KIOSK_MODEL_USAGE_PATH.read_text())
    except json.JSONDecodeError as exc:
        print(f"[warn] failed to parse {KIOSK_MODEL_USAGE_PATH}: {exc}", file=sys.stderr)
        return None

    print(f"[info] using fallback model usage file: {KIOSK_MODEL_USAGE_PATH}", file=sys.stderr)
    return normalize_model_usage_payload(fallback)


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
    return [airpoint_status()]


def check_http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # nosec B310
            return 200 <= getattr(resp, 'status', 200) < 400
    except Exception:
        return False


def build_products(now_iso: str) -> List[Dict[str, str]]:
    mission_control_url = "https://jcubellagent-web.github.io/mission-control/"
    kiosk_url = "http://192.168.4.40:3030"
    kiosk_live = check_http_ok(kiosk_url)
    return [
        {
            "name": "Mission Control",
            "url": mission_control_url,
            "status": "live",
            "lastChecked": now_iso,
        },
        {
            "name": "Mission Control PWA",
            "url": mission_control_url,
            "status": "live",
            "lastChecked": now_iso,
        },
        {
            "name": "Kiosk dashboard",
            "url": kiosk_url,
            "status": "live" if kiosk_live else "down",
            "lastChecked": now_iso,
        },
    ]


def build_recent_activity(now_iso: str, model_usage: Dict[str, Any] | None) -> List[Dict[str, str]]:
    items = [
        {"time": now_iso, "event": "🚀 Published Mission Control refresh"},
    ]
    if model_usage:
        items.insert(0, {"time": model_usage.get("lastUpdated") or now_iso, "event": "💸 Synced live CodexBar model usage"})
    return items


def main() -> None:
    DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    now_iso = utc_iso()
    dashboard: Dict[str, Any] = {
        "actionRequired": [],
        "activeNow": [],
        "upcomingEvents": [],
    }

    brain = fetch_brain_feed()
    dashboard["focus"] = brain or {
        "status": "System nominal",
        "context": "Mission Control is syncing live CodexBar usage and publishing refreshes automatically.",
        "runway": 0.98,
        "updatedAt": now_iso,
    }

    model_usage = fetch_model_usage()
    if model_usage:
        dashboard["modelUsage"] = model_usage

    dashboard["upcomingEvents"] = fetch_upcoming_events()
    dashboard["devices"] = build_devices()
    dashboard["products"] = build_products(now_iso)
    dashboard["crons"] = fetch_crons()
    dashboard["recentActivity"] = build_recent_activity(now_iso, model_usage)
    dashboard["lastUpdated"] = now_iso

    DASHBOARD_PATH.write_text(json.dumps(dashboard, indent=2))
    print(f"Updated {DASHBOARD_PATH}")


if __name__ == "__main__":
    main()
