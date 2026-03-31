#!/usr/bin/env python3
"""Fetch Eight Sleep device state + 7-day trends and save to data/eight-sleep-data.json."""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "data" / "eight-sleep-data.json"

# Eight Sleep credentials
AUTH_URL = "https://auth-api.8slp.net/v1/tokens"
CLIENT_API = "https://client-api.8slp.net/v1"
EMAIL = "jcubell16@gmail.com"
PASSWORD = "Drakemaye123!!!"
CLIENT_ID = "0894c7f33bb94800a03f1f4df13a4f38"
CLIENT_SECRET = "f0954a3ed5763ba3d06834c73731a32f15f168f47d4f164751275def86db0c76"
DEVICE_ID = "46765770c69adc8ab1f0b25401b0684e7b6f41a5"
USER_ID = "c162f25b35354979ba76ed46d28f537b"


def _post(url: str, body: dict, headers: dict | None = None) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _get(url: str, token: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def authenticate() -> str:
    """Get access token from Eight Sleep auth API."""
    resp = _post(AUTH_URL, {
        "email": EMAIL,
        "password": PASSWORD,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    })
    token = resp.get("session", {}).get("token") or resp.get("access_token") or resp.get("token")
    if not token:
        # Try nested structures
        for key in resp:
            if isinstance(resp[key], dict) and "token" in resp[key]:
                token = resp[key]["token"]
                break
    if not token:
        raise ValueError(f"No token in auth response: {list(resp.keys())}")
    return token


def fetch_device(token: str) -> dict:
    url = f"{CLIENT_API}/devices/{DEVICE_ID}"
    resp = _get(url, token)
    return resp.get("result", resp)


def fetch_trends(token: str, days: int = 7) -> list:
    today = dt.date.today()
    from_date = today - dt.timedelta(days=days)
    url = (
        f"{CLIENT_API}/users/{USER_ID}/trends"
        f"?tz=America%2FNew_York"
        f"&from={from_date.isoformat()}"
        f"&to={today.isoformat()}"
    )
    resp = _get(url, token)
    days_data = resp.get("days") or resp.get("result", {}).get("days") or []
    return days_data


def parse_device(device: dict) -> dict:
    """Extract left/right heating levels and schedule from device state."""
    left_heating = device.get("leftHeatingLevel", 0)
    right_heating = device.get("rightHeatingLevel", 0)
    left_now = device.get("leftNowHeating", False)
    right_now = device.get("rightNowHeating", False)

    # Current activity / schedule info
    left_kelvin = device.get("leftKelvin", {})
    right_kelvin = device.get("rightKelvin", {})

    def extract_schedule(kelvin: dict) -> dict | None:
        profiles = kelvin.get("scheduleProfiles", [])
        if not profiles:
            return None
        # Find active profile
        for p in profiles:
            if p.get("enabled"):
                return {
                    "startTime": p.get("startLocalTime", ""),
                    "weekdays": p.get("daysOfWeek", []),
                    "enabled": True,
                }
        return None

    left_schedule = extract_schedule(left_kelvin)
    right_schedule = extract_schedule(right_kelvin)

    current_activity = device.get("leftKelvin", {}).get("currentActivity") or ""

    return {
        "leftHeatingLevel": left_heating,
        "rightHeatingLevel": right_heating,
        "leftNowHeating": left_now,
        "rightNowHeating": right_now,
        "currentActivity": current_activity,
        "leftSchedule": left_schedule,
        "rightSchedule": right_schedule,
    }


def parse_trends(days_data: list) -> dict:
    """Parse trends into summary + 7-day bars."""
    if not days_data:
        return {"lastNight": {}, "week": []}

    # Sort by date descending
    sorted_days = sorted(days_data, key=lambda d: d.get("day", ""), reverse=True)

    def parse_day(d: dict) -> dict:
        dur_s = d.get("sleepDuration", 0) or 0
        rem_s = d.get("remDuration", 0) or 0
        light_s = d.get("lightDuration", 0) or 0
        deep_s = d.get("deepDuration", 0) or 0
        total_h = round(dur_s / 3600, 1) if dur_s else 0
        deep_pct = round(deep_s / dur_s * 100) if dur_s > 0 else 0
        rem_pct = round(rem_s / dur_s * 100) if dur_s > 0 else 0
        return {
            "date": d.get("day", ""),
            "totalHours": total_h,
            "deepPct": deep_pct,
            "remPct": rem_pct,
            "tnt": d.get("tnt", 0) or 0,
            "snorePercent": round(d.get("snorePercent", 0) or 0),
            "presenceStart": d.get("presenceStart", ""),
            "presenceEnd": d.get("presenceEnd", ""),
        }

    last_night = parse_day(sorted_days[0]) if sorted_days else {}
    week = [parse_day(d) for d in reversed(sorted_days[:7])]

    return {"lastNight": last_night, "week": week}


def fetch_eight_sleep() -> dict:
    token = authenticate()
    device_raw = fetch_device(token)
    trends_raw = fetch_trends(token)

    status = parse_device(device_raw)
    trends = parse_trends(trends_raw)

    return {
        "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": status,
        "trends": trends,
    }


def main() -> None:
    try:
        data = fetch_eight_sleep()
        OUTPUT_PATH.write_text(json.dumps(data, indent=2))
        print(f"Updated {OUTPUT_PATH}")
    except Exception as e:
        print(f"[warn] fetch_eight_sleep failed: {e}", file=sys.stderr)
        # Write a safe fallback so the dashboard doesn't break
        if not OUTPUT_PATH.exists():
            OUTPUT_PATH.write_text(json.dumps({
                "updatedAt": None,
                "status": {"leftHeatingLevel": 0, "rightHeatingLevel": 0, "leftNowHeating": False, "rightNowHeating": False},
                "trends": {"lastNight": {}, "week": []},
            }, indent=2))


if __name__ == "__main__":
    main()
