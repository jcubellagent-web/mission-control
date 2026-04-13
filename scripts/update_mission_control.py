#!/usr/bin/env python3
"""Update Mission Control dashboard JSON with live data."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
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
MODEL_USAGE_PATH = ROOT.parent / "data" / "modelUsage.json"
EIGHT_SLEEP_PATH = ROOT.parent / "data" / "eight-sleep-data.json"
AGENT_COMMS_PATH = ROOT.parent / "data" / "agent-comms.json"
NEXT_BASE = "http://127.0.0.1:3030"
WORKSPACE_ROOT = ROOT.parent.parent
KIOSK_MODEL_USAGE_PATH = WORKSPACE_ROOT / "kiosk-dashboard" / "data" / "modelUsage.json"
AGENT_BUS_URL = "https://cdzaeptrggczynijegls.supabase.co"
AGENT_BUS_KEY = "sb_publishable_S6K05dWzCylIOjEOM1TcEQ_FUG1DAJ6"
CONTEXT_WATCHDOG_STATE_PATH = WORKSPACE_ROOT / "memory" / "context-watchdog-state.json"
CONTEXT_HANDOFF_PATH = WORKSPACE_ROOT / "memory" / "context-handoff-latest.md"
CONTEXT_WATCHDOG_LABEL = "com.josh20.context-watchdog"

CRON_TARGETS = [
    # ── JOSH 2.0 (local) ────────────────────────────────────────────────────
    {"name": "Mission Control Refresh", "pattern": "mission-control/scripts/update_and_push.sh", "schedule": "Every 5 min", "description": "Refreshes Mission Control data and pushes local dashboard updates", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "Brain Feed Server", "pattern": "brain_feed_server.py", "schedule": "Every 2 min (keepalive)", "description": "Keeps the live Brain Feed endpoint available for Mission Control", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "Chiro Invite Sync", "pattern": "scripts/chiro_invite_sync.sh", "schedule": "Hourly", "description": "Syncs chiropractic client invites into calendar", "category": "Appointments", "agent": "JOSH 2.0"},
    {"name": "J.A.I.N Silence Detector", "pattern": "jain_silence_detector.py", "schedule": "Hourly", "description": "Alerts if J.A.I.N stops reporting or goes quiet unexpectedly", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "Sorare Cookie Freshness", "pattern": "sorare_cookie_freshness.py", "schedule": "Daily 1:00 PM ET", "description": "Checks Sorare cookie age before it turns into a submission blocker", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "J.A.I.N Medic", "pattern": "jain_medic.sh", "schedule": "Hourly", "description": "Runs local watchdog and recovery checks for J.A.I.N", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "Sorare Cookie Auto-Refresh", "pattern": "sorare_cookie_autorefresh.py", "schedule": "Sun 2:00 PM ET", "description": "Weekly forced refresh for Sorare auth cookies", "category": "Maintenance", "agent": "JOSH 2.0"},

    # ── J.A.I.N intelligence + maintenance ──────────────────────────────────
    {"name": "Breaking News Scanner", "pattern": "breaking_news_scanner.py", "schedule": "Every 5 min (6:00 AM–11:15 PM ET)", "description": "Scores breaking items and pushes high-signal alerts to @JAIN_BREAKING_BOT", "category": "Intelligence Feed", "agent": "J.A.I.N", "jain": True},
    {"name": "X Watchlist Monitor", "pattern": "x_watchlist_monitor.py", "schedule": "Every 5 min (6:00 AM–11:15 PM ET)", "description": "Watches priority X accounts for high-signal posts and routes urgent hits", "category": "Intelligence Feed", "agent": "J.A.I.N", "jain": True},
    {"name": "Intelligence Feed", "pattern": "intelligence_feed.py", "schedule": "Weekdays 7:15a/10a/12p/2p/4:15p/6p/9p/11p · Weekends 10a/4:15p/9p/11p ET", "description": "AI, macro, crypto, and market briefings pushed to Jain Intelligence", "category": "Intelligence Feed", "agent": "J.A.I.N", "jain": True,
     "multiRun": {
         "weekdayRuns": [
             {"time": "7:15 AM",  "mode": "Weekday", "label": "Market open brief"},
             {"time": "10:00 AM", "mode": "Weekday", "label": "Mid-morning brief"},
             {"time": "12:00 PM", "mode": "Weekday", "label": "Midday brief"},
             {"time": "2:00 PM",  "mode": "Weekday", "label": "Pulse brief"},
             {"time": "4:15 PM",  "mode": "Daily",   "label": "Close brief"},
             {"time": "6:00 PM",  "mode": "Weekday", "label": "Evening brief"},
             {"time": "9:00 PM",  "mode": "Daily",   "label": "Late brief"},
             {"time": "11:00 PM", "mode": "Daily",   "label": "Wrap brief"},
         ],
         "weekendRuns": [
             {"time": "10:00 AM", "mode": "Weekend", "label": "Weekend opener"},
             {"time": "4:15 PM",  "mode": "Weekend", "label": "Weekend close"},
             {"time": "9:00 PM",  "mode": "Weekend", "label": "Weekend late brief"},
             {"time": "11:00 PM", "mode": "Weekend", "label": "Weekend wrap"},
         ]
     }},
    {"name": "Intel Feedback Loop", "pattern": "intel_feedback_loop.py", "schedule": "Every 5 min (keepalive)", "description": "Restarts the persistent intelligence feedback loop if it drops", "category": "Intelligence Feed", "agent": "J.A.I.N", "jain": True},
    {"name": "JOSH Health Check", "pattern": "check_josh_health.sh", "schedule": "Every 30 min", "description": "Remote health check from J.A.I.N back to Josh 2.0", "category": "Maintenance", "agent": "J.A.I.N", "jain": True},
    {"name": "Error Rate Monitor", "pattern": "error_rate_monitor.py", "schedule": "Daily 3:00 AM ET", "description": "Nightly scan for elevated error rates across automations", "category": "Maintenance", "agent": "J.A.I.N", "jain": True},
    {"name": "Log Rotation", "pattern": "rotate_logs.sh", "schedule": "Sun 3:00 AM ET", "description": "Weekly log rotation on J.A.I.N", "category": "Maintenance", "agent": "J.A.I.N", "jain": True},
    {"name": "XMCP Boot", "pattern": "start_xmcp.sh", "schedule": "On boot", "description": "Starts XMCP services whenever J.A.I.N reboots", "category": "Maintenance", "agent": "J.A.I.N", "jain": True},

    # ── X account engine ─────────────────────────────────────────────────────
    {"name": "X Feedback ML", "pattern": "x_feedback_ml.py", "schedule": "Daily 6:00 AM ET", "description": "Scores yesterday’s X performance and refreshes strategy state", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Pre-Market", "pattern": "x_post_agent.py", "schedule": "Daily 7:00 AM ET", "description": "[Original] Futures and overnight setup", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Market Open", "pattern": "x_post_agent.py", "schedule": "Daily 8:00 AM ET", "description": "[Original] Market-open macro take", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Mover", "pattern": "x_post_agent.py", "schedule": "Daily 11:00 AM ET", "description": "[Original] Mid-morning mover or stat", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Hot Take", "pattern": "x_post_agent.py", "schedule": "Daily 12:00 PM ET", "description": "[Original] Contrarian take built to spark replies", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Quote Tweets", "pattern": "x_post_agent.py qt", "schedule": "Daily 1p/3p/6p/8p ET", "description": "[QT] Quotes breaking or viral posts with our angle", "category": "X Account", "agent": "J.A.I.N", "jain": True,
     "multiRun": {
         "runs": [
             {"time": "1:00 PM", "label": "Quote Tweet"},
             {"time": "3:00 PM", "label": "Quote Tweet"},
             {"time": "6:00 PM", "label": "Quote Tweet"},
             {"time": "8:00 PM", "label": "Quote Tweet"},
         ]
     }},
    {"name": "X Market Close", "pattern": "x_post_agent.py", "schedule": "Daily 5:00 PM ET", "description": "[Original] Close wrap and next-day setup", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Prime Take", "pattern": "x_post_agent.py", "schedule": "Daily 9:00 PM ET", "description": "[Original] Prime-time flagship take", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Nightcap", "pattern": "x_post_agent.py", "schedule": "Daily 10:00 PM ET", "description": "[Original] Last sharp insight of the day", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Strategic Replies", "pattern": "x_strategic_reply.py", "schedule": "12x daily (9a/10a/11a/1p/2p/3p/4p/5p/6p/7p/9p/11p ET)", "description": "[Reply] Finds fresh target tweets and posts browser-based strategic replies", "category": "X Account", "agent": "J.A.I.N", "jain": True,
     "multiRun": {
         "runs": [
             {"time": "9:00 AM",  "label": "Strategic Reply"},
             {"time": "10:00 AM", "label": "Strategic Reply"},
             {"time": "11:00 AM", "label": "Strategic Reply"},
             {"time": "1:00 PM",  "label": "Strategic Reply"},
             {"time": "2:00 PM",  "label": "Strategic Reply"},
             {"time": "3:00 PM",  "label": "Strategic Reply"},
             {"time": "4:00 PM",  "label": "Strategic Reply"},
             {"time": "5:00 PM",  "label": "Strategic Reply"},
             {"time": "6:00 PM",  "label": "Strategic Reply"},
             {"time": "7:00 PM",  "label": "Strategic Reply"},
             {"time": "9:00 PM",  "label": "Strategic Reply"},
             {"time": "11:00 PM", "label": "Strategic Reply"},
         ]
     }},
    {"name": "X Growth Tracker", "pattern": "x_growth_tracker.py", "schedule": "Daily 12:00 PM ET", "description": "Snapshots follower and impression growth into dashboard state", "category": "X Account", "agent": "J.A.I.N", "jain": True},

    # ── Sorare MLB ──────────────────────────────────────────────────────────
    {"name": "Sorare ML Training", "pattern": "sorare_ml/train.py", "schedule": "Daily 2:00 AM ET", "description": "Hermes retrains the Sorare MLB model on the latest results", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-train-model"},
    {"name": "Sorare Nightly Claim", "pattern": "sorare_claim_bot.py", "schedule": "Daily 3:30 AM ET", "description": "Hermes claim job for overnight Sorare rewards", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-nightly-claim"},
    {"name": "Sorare Sheet Updater", "pattern": "sorare_sheet_updater_v2.py", "schedule": "Daily 3:30 AM ET", "description": "Writes fresh Sorare data into the tracker sheet", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},
    {"name": "Sorare Daily Prep", "pattern": "sorare_daily_prep.sh", "schedule": "Daily 9:00 AM ET", "description": "Raw prep pipeline before model-driven Sorare submissions", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},
    {"name": "Sorare ML Missions", "pattern": "ml_bot.py --missions-only", "schedule": "Daily 10:00 AM ET", "description": "Hermes ML mission picker for Sorare", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-ml-missions"},
    {"name": "Sorare ML Lineups", "pattern": "ml_bot.py --lineups-only", "schedule": "Daily 11:00 AM ET", "description": "Hermes ML lineup builder for Sorare competitions", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-ml-lineups"},
    {"name": "Sorare Champion Submit", "pattern": "sorare_missions.py --sp-classic", "schedule": "Daily 3:00 PM ET", "description": "Champion lineup submitter running from raw crontab", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},
    {"name": "Sorare Canonical Reflector", "pattern": "sorare_canonical_reflector.py", "schedule": "Every 15 min (8:00 AM–10:45 PM ET)", "description": "Keeps canonical Sorare state mirrored into Mission Control data", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},
    {"name": "Sorare Deadline Guard", "pattern": "sorare_deadline_guard.py", "schedule": "Mon 9:45 PM ET", "description": "Late lineup-deadline safety check for Sorare", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},

    # ── Fantasy baseball ────────────────────────────────────────────────────
    {"name": "Fantasy Waiver Scan (post-process)", "pattern": "fantasy_waiver_scan.py", "schedule": "Mon 12:00 AM ET", "description": "Post-waiver scan right after the Sunday-night processing window", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Weekly Recap", "pattern": "fantasy_weekly_recap.py", "schedule": "Sun 12:00 PM ET", "description": "Raw weekly recap sent to Josh", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Injury Monitor", "pattern": "fantasy_injury_monitor.py", "schedule": "Mon 12:45 PM ET", "description": "Monday injury check before setting the weekly roster", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Lineup Check", "pattern": "fantasy_lineup_check.py", "schedule": "Mon 1:00 PM ET", "description": "Monday lineup review on the live cron path", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Waiver Injury Alert", "pattern": "waiver_injury_alert.py", "schedule": "Daily 1:00 PM ET", "description": "Surfaces injured-player replacement opportunities", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Waiver Review (Hermes)", "pattern": "fantasy_waiver_scan.py", "schedule": "Wed/Fri 1:00 PM ET", "description": "Hermes waiver review lane that runs mid-week", "category": "Fantasy Baseball", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "fantasy-waiver-scan"},
    {"name": "Fantasy Waiver Scan (pre-game)", "pattern": "fantasy_waiver_scan.py", "schedule": "Mon 11:00 AM ET", "description": "Final waiver review before first-pitch lineup lock", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},

    # ── JAIMES / Hermes maintenance ─────────────────────────────────────────
    {"name": "Daily Health Check", "pattern": "daily_health_check.py", "schedule": "Daily 5:50 AM ET", "description": "Hermes daily system-health pass", "category": "Maintenance", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "daily-health-check"},
    {"name": "JAIMES Weekly Report", "pattern": "jaimes_weekly_report.py", "schedule": "Sat 1:00 PM ET", "description": "Weekly JAIMES summary sent back to Josh", "category": "Maintenance", "agent": "JAIMES", "jain": True},
]


def fetch_next(endpoint: str) -> Dict[str, Any] | None:
    url = f"{NEXT_BASE}{endpoint}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # nosec B310
            return json.load(resp)
    except urllib.error.URLError as exc:  # pragma: no cover - diagnostics only
        if '127.0.0.1:3030' in url and 'Connection refused' in str(exc):
            return None
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


def is_valid_iso8601(ts: Any) -> bool:
    """Return True if ts is a non-empty string that can be parsed as ISO 8601."""
    if not isinstance(ts, str) or not ts:
        return False
    try:
        dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError):
        return False


def should_exclude_model(name: str, source: str = "") -> bool:
    slug = f"{source} {name}".lower()
    return "opus" in slug


def load_json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def normalize_node_slug(raw: Any) -> str:
    text = str(raw or "").strip().lower().replace("_", "")
    if text in {"josh20", "josh2.0", "josh"}:
        return "josh2.0"
    if text in {"jain", "j.a.i.n"}:
        return "jain"
    if text == "jaimes":
        return "jaimes"
    return text or "system"


def squash_text(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def iso_to_dt(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_recent_ts(value: Any, *, hours: int = 18) -> bool:
    stamp = iso_to_dt(value)
    if not stamp:
        return False
    return (dt.datetime.now(dt.timezone.utc) - stamp) <= dt.timedelta(hours=hours)


def canonicalize_timestamp(value: Any) -> str:
    stamp = iso_to_dt(value)
    if not stamp:
        return str(value or "")
    return stamp.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def agent_bus_status_to_comm_status(status: Any) -> str:
    slug = str(status or "").strip().lower()
    if slug in {"running", "in_progress", "retry"}:
        return "active"
    if slug in {"done", "completed", "success", "succeeded"}:
        return "done"
    if slug in {"error", "failed", "failure"}:
        return "done"
    return "sent"


def extract_agent_bus_message(task: Dict[str, Any]) -> str:
    payload = task.get("payload") or {}
    for key in ("task", "message", "title", "summary"):
        text = squash_text(payload.get(key))
        if text:
            return text
    details = squash_text(payload.get("details"), limit=140)
    if details:
        return details
    task_type = str(task.get("task_type") or "Agent task").replace("_", " ").title()
    target = normalize_node_slug(task.get("target_node") or "agent").upper()
    return f"{task_type} for {target}"


def build_agent_comms(
    existing_entries: List[Dict[str, Any]],
    agent_bus_tasks: List[Dict[str, Any]],
    jain_brain_feed: Dict[str, Any] | None,
    jaimes_brain_feed: Dict[str, Any] | None,
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def push(entry: Dict[str, Any]) -> None:
        message = squash_text(entry.get("message"))
        timestamp = canonicalize_timestamp(entry.get("timestamp"))
        direction = str(entry.get("direction") or "")
        if not (message and timestamp and direction):
            return
        key = (timestamp, direction, message)
        if key in seen:
            return
        seen.add(key)
        clean = {
            "timestamp": timestamp,
            "direction": direction,
            "message": message,
            "status": str(entry.get("status") or "sent"),
        }
        merged.append(clean)

    for entry in existing_entries or []:
        if isinstance(entry, dict):
            push(entry)

    for task in agent_bus_tasks or []:
        origin = normalize_node_slug(task.get("origin_node"))
        target = normalize_node_slug(task.get("target_node"))
        push({
            "timestamp": task.get("created_at") or utc_iso(),
            "direction": f"{origin}→{target}",
            "message": extract_agent_bus_message(task),
            "status": agent_bus_status_to_comm_status(task.get("status")),
        })

    if jain_brain_feed and is_recent_ts(jain_brain_feed.get("updatedAt"), hours=6):
        push({
            "timestamp": jain_brain_feed.get("updatedAt") or utc_iso(),
            "direction": "jain→josh",
            "message": jain_brain_feed.get("objective") or "J.A.I.N standing by",
            "status": "active" if jain_brain_feed.get("active") else "done",
        })

    if jaimes_brain_feed and is_recent_ts(jaimes_brain_feed.get("updatedAt"), hours=12):
        push({
            "timestamp": jaimes_brain_feed.get("updatedAt") or utc_iso(),
            "direction": "jaimes→josh",
            "message": jaimes_brain_feed.get("objective") or "JAIMES standing by",
            "status": "active" if jaimes_brain_feed.get("active") else "done",
        })

    merged.sort(key=lambda item: iso_to_dt(item.get("timestamp")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return merged[:24]


# Known model aliases to canonical "provider/model" names (for models that appear without provider prefix)
MODEL_ALIASES: Dict[str, str] = {
    "sonnet":                  "anthropic/claude-sonnet-4-6",
    "claude-sonnet":           "anthropic/claude-sonnet-4-6",
    "claude-sonnet-4-5":       "anthropic/claude-sonnet-4-5",
    "claude-sonnet-4-6":       "anthropic/claude-sonnet-4-6",
    "claude-haiku":            "anthropic/claude-haiku-3-5",
    "claude-haiku-3-5":        "anthropic/claude-haiku-3-5",
    "gemini-2.5-flash":        "google/gemini-2.5-flash",
    "gemini-2.5-pro":          "google/gemini-2.5-pro",
    "gemini-2.0-flash":        "google/gemini-2.0-flash",
    "gpt-4o":                  "openai/gpt-4o",
    "gpt-4o-mini":             "openai/gpt-4o-mini",
}

def normalize_model_name(raw: str, provider: str = "") -> str:
    """Normalize a raw model name to canonical 'provider/model' form."""
    if "/" in raw:
        return raw.lstrip("/")
    # Check alias table first
    lower = raw.lower()
    if lower in MODEL_ALIASES:
        return MODEL_ALIASES[lower]
    # Prefix with provider if available
    if provider:
        return f"{provider}/{raw}"
    return raw


def normalize_model_usage_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    def normalize(bucket: Dict[str, Any]) -> float:
        return float(bucket.get("totalCost") or 0)

    # Collect breakdown per model, deduplicating by (model_name, window)
    rows: List[Dict[str, Any]] = []
    seen_name_window: set = set()
    for bucket_name in ("session", "daily", "weekly"):
        for model in data.get(bucket_name, {}).get("models", []):
            name = model.get("name") or ""
            if not name or should_exclude_model(name):
                continue
            key = (bucket_name, name)
            if key in seen_name_window:
                continue
            seen_name_window.add(key)
            amount = model.get(f"{bucket_name}Cost")
            if amount is None:
                amount = model.get("sessionCost")
            rows.append({
                "name": name,
                "window": bucket_name,
                "cost": float(amount or 0),
            })
    rows.sort(key=lambda row: row["cost"], reverse=True)

    # Deduplicate breakdown entries by model name (keep latest / highest cost entry)
    raw_breakdown: List[Dict[str, Any]] = data.get("breakdown", [])
    seen_model_names: dict = {}
    for entry in raw_breakdown:
        mname = entry.get("name") or ""
        source = entry.get("source") or ""
        if not mname or should_exclude_model(mname, source):
            continue
        existing = seen_model_names.get(mname)
        if existing is None or (entry.get("weeklyCost") or 0) >= (existing.get("weeklyCost") or 0):
            seen_model_names[mname] = entry
    deduped_breakdown = list(seen_model_names.values())

    # Validate / normalise timestamps
    last_updated = data.get("lastUpdated")
    if not is_valid_iso8601(last_updated):
        last_updated = utc_iso()

    return {
        "session": normalize(data.get("session", {})),
        "daily": normalize(data.get("daily", {})),
        "weekly": normalize(data.get("weekly", {})),
        "topModels": rows[:5],
        "breakdown": deduped_breakdown,
        "lastUpdated": last_updated,
    }



def build_focus_fallback(brain_feed: Dict[str, Any] | None, now_iso: str) -> Dict[str, Any]:
    updated_at = now_iso  # Always use current time for focus.updatedAt — it represents when we last computed it
    if brain_feed:
        objective = str(brain_feed.get("objective") or "").strip()
        status = str(brain_feed.get("status") or "").strip().lower()
        if brain_feed.get("active"):
            return {
                "status": "Brain Feed live",
                "context": objective or "Agent task is currently in motion.",
                "updatedAt": updated_at,
            }
        if objective:
            context = f"Last objective: {objective}"
            if status == "done":
                context = f"Last completed objective: {objective}"
            elif status in {"error", "failed"}:
                context = f"Last task needs review: {objective}"
            return {
                "status": "Brain Feed idle",
                "context": context,
                "updatedAt": updated_at,
            }
    return {
        "status": "System nominal",
        "context": "Mission Control is syncing live CodexBar usage and publishing refreshes automatically.",
        "updatedAt": updated_at,
    }


OPENCLAW_SESSIONS_PATH = Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"

# Gemini pricing (per 1M tokens) — update as pricing changes
GEMINI_PRICING: Dict[str, Dict[str, float]] = {
    "gemini-2.5-flash":       {"input": 0.15,  "output": 0.60},
    "gemini-2.5-pro":         {"input": 1.25,  "output": 10.00},
    "gemini-2.0-flash":       {"input": 0.10,  "output": 0.40},
    "gemini-1.5-flash":       {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro":         {"input": 1.25,  "output": 5.00},
}

def estimate_gemini_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate Gemini cost since codexbar skips it."""
    slug = model.lower().replace("google/", "")
    pricing = None
    for key, p in GEMINI_PRICING.items():
        if key in slug:
            pricing = p
            break
    if not pricing:
        pricing = {"input": 0.15, "output": 0.60}  # default to flash pricing
    return (input_tokens / 1_000_000) * pricing["input"] + (output_tokens / 1_000_000) * pricing["output"]


def fetch_model_usage_from_sessions() -> List[Dict[str, Any]]:
    """Pull per-model usage directly from OpenClaw session data. Captures ALL models including Gemini.

    For each session, cost priority:
      1. Use estimatedCostUsd if non-zero (OpenClaw already computed it).
      2. If zero and model is Gemini with token data, estimate from pricing table.
      3. Otherwise keep 0.

    totalTokens in OpenClaw sessions represents context-window tokens (not input+output sum),
    so we use inputTokens + outputTokens as the canonical token count for display.
    """
    if not OPENCLAW_SESSIONS_PATH.exists():
        return []
    try:
        sessions = json.loads(OPENCLAW_SESSIONS_PATH.read_text())
        by_model: Dict[str, Dict[str, Any]] = {}

        # Determine today's date in ET so we can compute a dailyCost field per model
        now_et = dt.datetime.now(dt.timezone(dt.timedelta(hours=-4)))
        today_et_str = now_et.strftime("%Y-%m-%d")

        for sess in sessions.values():
            raw_model = sess.get("modelOverride") or sess.get("model") or ""
            provider = sess.get("providerOverride") or sess.get("modelProvider") or ""
            if not raw_model:
                continue
            # Normalize model name to "provider/model" form
            full_name = normalize_model_name(raw_model, provider)
            if should_exclude_model(full_name):
                continue

            input_t  = int(sess.get("inputTokens")  or 0)
            output_t = int(sess.get("outputTokens") or 0)
            # Use inputTokens + outputTokens as canonical token count (totalTokens = context-window size, not sum)
            token_count = input_t + output_t

            cost = float(sess.get("estimatedCostUsd") or 0)
            estimated = False
            # For Gemini: use provided cost; only estimate if zero but we have token data
            if "gemini" in full_name.lower():
                if cost == 0 and token_count > 0:
                    cost = estimate_gemini_cost(full_name, input_t, output_t)
                    estimated = True
                # If OpenClaw already gave us a real cost, mark as not estimated
                # (estimatedCostUsd > 0 means the provider billing reported it)

            # Is this session from today (ET)?
            sess_updated_ms = sess.get("updatedAt")
            is_today = False
            if sess_updated_ms:
                try:
                    sess_dt = dt.datetime.fromtimestamp(int(sess_updated_ms) / 1000, tz=dt.timezone.utc)
                    sess_et_str = (sess_dt - dt.timedelta(hours=4)).strftime("%Y-%m-%d")
                    is_today = (sess_et_str == today_et_str)
                except (ValueError, TypeError, OSError):
                    pass

            if full_name not in by_model:
                by_model[full_name] = {
                    "name": full_name,
                    "source": "openclaw",
                    "inputTokens": 0, "outputTokens": 0, "totalTokens": 0,
                    "sessionCost": 0.0, "dailyCost": 0.0, "weeklyCost": 0.0,
                    "sessions": 0,
                    "isGemini": "gemini" in full_name.lower(),
                    "costEstimated": estimated,
                }
            by_model[full_name]["inputTokens"] += input_t
            by_model[full_name]["outputTokens"] += output_t
            by_model[full_name]["totalTokens"] += token_count  # canonical: in + out
            by_model[full_name]["sessionCost"] += cost
            by_model[full_name]["weeklyCost"] += cost
            if is_today:
                by_model[full_name]["dailyCost"] += cost
            by_model[full_name]["sessions"] += 1
            if estimated and cost > 0:
                by_model[full_name]["costEstimated"] = True

        return sorted(by_model.values(), key=lambda x: x["weeklyCost"], reverse=True)
    except Exception as exc:
        print(f"[warn] session usage fetch failed: {exc}", file=sys.stderr)
        return []


def fetch_model_usage_from_codexbar() -> List[Dict[str, Any]]:
    """Fetch model usage from codexbar CLI (covers Codex/OpenAI with precise cost).

    The codexbar daily breakdown tells us per-day cost per model.
    We use that to compute both sessionCost (all days summed) and dailyCost (today only).
    """
    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/codexbar", "cost", "--json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        providers = json.loads(result.stdout)
        now_et = dt.datetime.now(dt.timezone(dt.timedelta(hours=-4)))
        today_str = now_et.strftime("%Y-%m-%d")

        by_model: Dict[str, Dict[str, Any]] = {}
        for provider in providers:
            source = provider.get("provider", "codexbar")
            for day_entry in provider.get("daily", []):
                day_date = day_entry.get("date", "")  # e.g. "2026-03-27"
                is_today = day_date == today_str
                for mb in day_entry.get("modelBreakdowns", []):
                    name = mb.get("modelName", "")
                    cost = float(mb.get("cost") or 0)
                    if not name or should_exclude_model(name, source):
                        continue
                    if name not in by_model:
                        by_model[name] = {
                            "name": name,
                            "source": source,
                            "weeklyCost": 0.0,
                            "dailyCost": 0.0,
                            "sessionCost": 0.0,
                            "totalTokens": 0,
                            "costEstimated": False,
                        }
                    by_model[name]["weeklyCost"] += cost
                    by_model[name]["sessionCost"] += cost
                    if is_today:
                        by_model[name]["dailyCost"] += cost

        return list(by_model.values())
    except Exception as exc:
        print(f"[warn] codexbar fetch failed: {exc}", file=sys.stderr)
        return []


def merge_model_rows(session_rows: List[Dict], codexbar_rows: List[Dict]) -> List[Dict]:
    """Merge session + codexbar rows, preferring session data for Gemini, codexbar for precise OpenAI cost.

    dailyCost is always populated from whichever source has it, or combined if both do.
    """
    merged: Dict[str, Dict] = {}
    for row in session_rows:
        merged[row["name"]] = dict(row)  # shallow copy to avoid mutating source
    for row in codexbar_rows:
        name = row["name"]
        if name not in merged:
            merged[name] = dict(row)
        else:
            # For non-Gemini models, prefer codexbar cost (more precise)
            if not merged[name].get("isGemini"):
                merged[name]["weeklyCost"] = max(merged[name].get("weeklyCost", 0), row.get("weeklyCost", 0))
                merged[name]["sessionCost"] = max(merged[name].get("sessionCost", 0), row.get("sessionCost", 0))
                # Take max of dailyCost across sources (avoid double-counting)
                merged[name]["dailyCost"] = max(
                    merged[name].get("dailyCost", 0),
                    row.get("dailyCost", 0)
                )
    # Ensure every row has a dailyCost field (default 0 if missing)
    for row in merged.values():
        row.setdefault("dailyCost", 0.0)
    return sorted(merged.values(), key=lambda x: x.get("weeklyCost", 0), reverse=True)


ACCUM_PATH = ROOT.parent / "data" / "model-usage-accum.json"

def load_accum() -> Dict[str, Any]:
    """Load persistent daily/weekly accumulator. Resets daily at midnight ET, weekly on Monday."""
    now_et = dt.datetime.now(dt.timezone(dt.timedelta(hours=-4)))  # ET (close enough)
    today  = now_et.strftime("%Y-%m-%d")
    # ISO week starts Monday
    week   = now_et.strftime("%Y-W%W-%w")[:-2]  # e.g. "2026-W12"
    month  = now_et.strftime("%Y-%m")            # e.g. "2026-03"
    empty  = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0,
              "dailyDate": today, "weekKey": week, "monthKey": month, "peak": 0.0}
    if not ACCUM_PATH.exists():
        return empty
    try:
        a = json.loads(ACCUM_PATH.read_text())
        # Reset daily if new day
        if a.get("dailyDate") != today:
            a["daily"] = 0.0
            a["dailyDate"] = today
        # Reset weekly if new week (also reset peak so new week accumulates fresh)
        if a.get("weekKey") != week:
            a["weekly"] = 0.0
            a["weekKey"] = week
            a["peak"] = 0.0
        # Reset monthly if new month
        if a.get("monthKey") != month:
            a["monthly"] = 0.0
            a["monthKey"] = month
        if "monthly" not in a:
            a["monthly"] = 0.0
            a["monthKey"] = month
        return a
    except Exception:
        return empty

def save_accum(a: Dict[str, Any]) -> None:
    ACCUM_PATH.write_text(json.dumps(a, indent=2))

def fetch_openrouter_usage() -> Dict[str, Any]:
    """Fetch OpenRouter key usage stats (covers BYOK Grok/Claude calls routed via openrouter.ai).
    Tries multiple keys — primary key (used for inference) + secondary key from secrets file.
    Aggregates usage across all keys for the same account.
    """
    empty = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0, "byok_daily": 0.0, "byok_weekly": 0.0,
             "byok_monthly": 0.0, "available": False}
    # Keys to try: load from ~/.secrets/openrouter_api_key.txt or auth-profiles.json
    keys_to_try = []
    # Priority 1: ~/.secrets/openrouter_api_key.txt (most up-to-date)
    key_file = Path.home() / ".secrets" / "openrouter_api_key.txt"
    if key_file.exists():
        k = key_file.read_text().strip()
        if k: keys_to_try.append(k)
    # Priority 2: secrets env file
    sec_key_path = Path(os.path.expanduser("~/.openclaw/workspace/secrets/openrouter.env"))
    if sec_key_path.exists():
        for line in sec_key_path.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                sec_key = line.split("=", 1)[1].strip()
                if sec_key and sec_key not in keys_to_try:
                    keys_to_try.append(sec_key)
    # Priority 3: openclaw auth-profiles (may be stale)
    auth_path = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if auth_path.exists():
        try:
            import json as _json
            d = _json.loads(auth_path.read_text())
            k = d.get("profiles", {}).get("openrouter:default", {}).get("key", "")
            if k and k not in keys_to_try: keys_to_try.append(k)
        except: pass

    agg: Dict[str, float] = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0,
                              "byok_daily": 0.0, "byok_weekly": 0.0, "byok_monthly": 0.0}
    any_ok = False
    seen_user_ids: set = set()

    warnings: list[str] = []
    for key in keys_to_try:
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {key}", "User-Agent": "mission-control/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            if "error" in data:
                continue
            d = data.get("data", {})
            # Skip if same account (same creator_user_id) — avoid double-counting
            uid = d.get("creator_user_id", key)
            if uid in seen_user_ids:
                # Same account — take the max of each field (not sum) to avoid double-count
                agg["daily"]        = max(agg["daily"],        float(d.get("usage_daily", 0) or 0))
                agg["weekly"]       = max(agg["weekly"],       float(d.get("usage_weekly", 0) or 0))
                agg["monthly"]      = max(agg["monthly"],      float(d.get("usage_monthly", 0) or 0))
                agg["byok_daily"]   = max(agg["byok_daily"],   float(d.get("byok_usage_daily", 0) or 0))
                agg["byok_weekly"]  = max(agg["byok_weekly"],  float(d.get("byok_usage_weekly", 0) or 0))
                agg["byok_monthly"] = max(agg["byok_monthly"], float(d.get("byok_usage_monthly", 0) or 0))
            else:
                agg["daily"]        += float(d.get("usage_daily", 0) or 0)
                agg["weekly"]       += float(d.get("usage_weekly", 0) or 0)
                agg["monthly"]      += float(d.get("usage_monthly", 0) or 0)
                agg["byok_daily"]   += float(d.get("byok_usage_daily", 0) or 0)
                agg["byok_weekly"]  += float(d.get("byok_usage_weekly", 0) or 0)
                agg["byok_monthly"] += float(d.get("byok_usage_monthly", 0) or 0)
            seen_user_ids.add(uid)
            any_ok = True
        except Exception as exc:
            warnings.append(f"fetch_openrouter_usage key={key[:12]}... failed: {exc}")

    if not any_ok:
        for msg in warnings[:2]:
            print(f"[warn] {msg}", file=sys.stderr)
        return empty
    return {**agg, "available": True}


def fetch_elevenlabs_usage() -> Dict[str, Any]:
    """Fetch ElevenLabs character usage (both machines' keys)."""
    empty = {"chars_used": 0, "chars_limit": 0, "available": False}
    keys = [
        "sk_083befcb684c905b14e3bdb63a44ab993f14d89f6c396ee1",  # JOSH 2.0
    ]
    # Try JAIN key from file
    jain_key_path = Path(os.path.expanduser("~/.secrets/elevenlabs_api_key_jain.txt"))
    if jain_key_path.exists():
        keys.append(jain_key_path.read_text().strip())

    total_chars = 0
    total_limit = 0
    for key in keys:
        try:
            req = urllib.request.Request(
                "https://api.elevenlabs.io/v1/user/subscription",
                headers={"xi-api-key": key, "User-Agent": "mission-control/1.0"},
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                d = json.loads(resp.read().decode())
            total_chars += int(d.get("character_count", 0) or 0)
            total_limit += int(d.get("character_limit", 0) or 0)
        except Exception:
            pass
    return {"chars_used": total_chars, "chars_limit": total_limit, "available": total_chars > 0 or total_limit > 0}


def fetch_machine_health() -> Dict[str, Any]:
    """Collect key health metrics for JOSH 2.0 (local) and J.A.I.N (SSH)."""
    import re as _re

    def parse_top(stdout: str) -> Dict[str, Any]:
        cpu_idle, cpu_user, cpu_sys = None, None, None
        ram_used, ram_total = None, None
        load1, load5, load15 = None, None, None
        uptime_str = ""
        for line in stdout.splitlines():
            m = _re.search(r'CPU usage:\s*([\d.]+)%\s*user,\s*([\d.]+)%\s*sys,\s*([\d.]+)%\s*idle', line)
            if m:
                cpu_user, cpu_sys, cpu_idle = float(m.group(1)), float(m.group(2)), float(m.group(3))
            m = _re.search(r'Load Avg:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)', line)
            if m:
                load1, load5, load15 = float(m.group(1)), float(m.group(2)), float(m.group(3))
            m = _re.search(r'PhysMem:\s*([\d.]+)([GMK])\s*used.*?,\s*([\d.]+)([GMK])\s*unused', line)
            if m:
                def to_gb(val, unit):
                    v = float(val)
                    return v / 1024 if unit == 'M' else v / 1024 / 1024 if unit == 'K' else v
                ram_used = round(to_gb(m.group(1), m.group(2)), 1)
                unused = to_gb(m.group(3), m.group(4))
                ram_total = round(ram_used + unused, 1)
            m = _re.search(r'up\s+(.+?),\s*\d+\s+user', line)
            if m:
                uptime_str = m.group(1).strip()
        cpu_pct = None if cpu_idle is None else round(100 - cpu_idle, 1)
        return {
            "cpu_pct": cpu_pct,
            "cpu_user": cpu_user,
            "cpu_sys": cpu_sys,
            "load1": load1, "load5": load5, "load15": load15,
            "ram_used_gb": ram_used, "ram_total_gb": ram_total,
            "uptime": uptime_str,
        }

    def parse_df(stdout: str) -> Dict[str, Any]:
        for line in stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 5 and parts[-1] == '/':
                try:
                    return {"disk_used": parts[2], "disk_avail": parts[3], "disk_pct": int(parts[4].rstrip('%'))}
                except Exception:
                    pass
        return {}

    result: Dict[str, Any] = {}

    # ── JOSH 2.0 (local) ──────────────────────────────────────────────────
    try:
        top_r = subprocess.run(['top', '-l', '1', '-n', '0', '-s', '0'], capture_output=True, text=True, timeout=8)
        df_r  = subprocess.run(['df', '-h', '/'], capture_output=True, text=True, timeout=5)
        josh_metrics = parse_top(top_r.stdout)
        josh_metrics.update(parse_df(df_r.stdout))
        josh_metrics["available"] = True
        result["josh"] = josh_metrics
    except Exception as e:
        result["josh"] = {"available": False, "error": str(e)}

    # ── J.A.I.N (SSH) ─────────────────────────────────────────────────────
    try:
        jain_cmd = "top -l 1 -n 0 -s 0 2>/dev/null; echo '---DF---'; df -h /"
        r = subprocess.run(
            ['ssh', '-o', 'ConnectTimeout=6', '-o', 'StrictHostKeyChecking=no',
             'jc_agent@100.121.89.84', jain_cmd],
            capture_output=True, text=True, timeout=12
        )
        parts = r.stdout.split('---DF---')
        jain_metrics = parse_top(parts[0])
        if len(parts) > 1:
            jain_metrics.update(parse_df(parts[1]))
        jain_metrics["available"] = r.returncode == 0
        result["jain"] = jain_metrics
    except Exception as e:
        result["jain"] = {"available": False, "error": str(e)}

    return result


def fetch_jain_model_usage() -> Dict[str, Any]:
    """SSH to J.A.I.N and retrieve codexbar cost JSON."""
    empty = {"daily": 0, "session": 0, "total": 0, "available": False}
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "ConnectTimeout=5",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                "jc_agent@100.121.89.84",
                "/opt/homebrew/bin/codexbar cost --json 2>/dev/null || echo '{}'",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return empty
        data = json.loads(result.stdout)
        if not isinstance(data, (list, dict)):
            return empty
        # Sum daily cost across all providers if list
        daily = 0.0
        session = 0.0
        if isinstance(data, list):
            now_et = dt.datetime.now(dt.timezone(dt.timedelta(hours=-4)))
            today_str = now_et.strftime("%Y-%m-%d")
            for provider in data:
                for day_entry in provider.get("daily", []):
                    day_total = sum(
                        float(mb.get("cost") or 0)
                        for mb in day_entry.get("modelBreakdowns", [])
                    )
                    session += day_total
                    if day_entry.get("date") == today_str:
                        daily += day_total
        return {"daily": round(daily, 6), "session": round(session, 6), "total": round(session, 6), "available": True}
    except Exception as exc:
        print(f"[warn] fetch_jain_model_usage failed: {exc}", file=sys.stderr)
        return empty


def fetch_jain_api_costs() -> Dict[str, Any]:
    """Pull jain-api-costs.json from J.A.I.N (direct Gemini/xAI API calls from scripts)."""
    empty = {"daily": 0, "weekly": 0, "monthly": 0, "models": {}, "available": False}
    jain_cost_file = ROOT.parent / "data" / "jain-api-costs.json"
    if jain_cost_file.exists():
        try:
            raw = jain_cost_file.read_text().strip()
            if not raw:
                return empty
            data = json.loads(raw)
            data["available"] = True
            return data
        except Exception as exc:
            print(f"[warn] jain-api-costs.json parse failed: {exc}", file=sys.stderr)
    return empty


def fetch_ollama_usage() -> List[Dict[str, Any]]:
    """Fetch Ollama local model list and inject as $0 breakdown rows."""
    rows = []
    try:
        import urllib.request
        req = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        data = json.loads(req.read())
        models = data.get("models", [])

        # Also check /api/ps for currently loaded models (shows what's hot)
        loaded_names = set()
        try:
            ps_req = urllib.request.urlopen("http://localhost:11434/api/ps", timeout=2)
            ps_data = json.loads(ps_req.read())
            for m in ps_data.get("models", []):
                loaded_names.add(m.get("name", ""))
        except Exception:
            pass

        for m in models:
            name = m.get("name", "")
            size_bytes = m.get("size", 0)
            size_gb = size_bytes / 1e9
            loaded = name in loaded_names
            rows.append({
                "name": f"local/{name}",
                "source": "ollama",
                "weeklyCost": 0.0,
                "dailyCost": 0.0,
                "sessionCost": 0.0,
                "totalTokens": 0,
                "costEstimated": False,
                "isLocal": True,
                "sizeGb": round(size_gb, 2),
                "loaded": loaded,
                "_note": f"Local Ollama model ({size_gb:.1f}GB). Free — runs on-device.",
            })
    except Exception as exc:
        print(f"[warn] fetch_ollama_usage failed: {exc}", file=sys.stderr)
    return rows


JAIN_NEWSFEED_PATH = ROOT.parent / "data" / "jain-newsfeed.json"
NEWSFEED_PATH = ROOT.parent / "data" / "newsfeed.json"


def merge_jain_newsfeed() -> None:
    """Merge J.A.I.N newsfeed into local newsfeed.json (deduped by URL, top 50)."""
    if not JAIN_NEWSFEED_PATH.exists():
        return
    try:
        jain_feed = json.loads(JAIN_NEWSFEED_PATH.read_text())
    except Exception:
        return
    try:
        local_feed = json.loads(NEWSFEED_PATH.read_text()) if NEWSFEED_PATH.exists() else {}
    except Exception:
        local_feed = {}

    def get_items(feed: Any) -> List[Dict]:
        if isinstance(feed, list):
            return feed
        if isinstance(feed, dict):
            return feed.get("items") or []
        return []

    def get_url(item: Dict) -> str:
        return item.get("url") or item.get("link") or ""

    def get_ts(item: Dict) -> str:
        return item.get("timestamp") or item.get("publishedAt") or item.get("date") or ""

    jain_items = get_items(jain_feed)
    local_items = get_items(local_feed)

    # Merge, dedup by URL (prefer newest timestamp)
    by_url: Dict[str, Dict] = {}
    for item in local_items + jain_items:
        url = get_url(item)
        if not url:
            continue
        existing = by_url.get(url)
        if existing is None or get_ts(item) > get_ts(existing):
            by_url[url] = item

    merged = sorted(by_url.values(), key=get_ts, reverse=True)[:50]

    if isinstance(local_feed, dict):
        local_feed["items"] = merged
        NEWSFEED_PATH.write_text(json.dumps(local_feed, indent=2))
    else:
        NEWSFEED_PATH.write_text(json.dumps(merged, indent=2))


def fetch_current_session_cost() -> float:
    """Return cost of the active main Telegram session only (resets when /new or /reset is called)."""
    if not OPENCLAW_SESSIONS_PATH.exists():
        return 0.0
    try:
        sessions = json.loads(OPENCLAW_SESSIONS_PATH.read_text())
        # The primary session key for Josh's Telegram direct chat
        main_key = "agent:main:telegram:direct:6218150306"
        s = sessions.get(main_key, {})
        return float(s.get("estimatedCostUsd") or 0)
    except Exception:
        return 0.0


def fetch_model_usage() -> Dict[str, Any] | None:
    # Primary: merge OpenClaw sessions (all models incl. Gemini) + codexbar (precise Codex costs)
    session_rows = fetch_model_usage_from_sessions()
    codexbar_rows = fetch_model_usage_from_codexbar()
    breakdown = merge_model_rows(session_rows, codexbar_rows)

    if breakdown:
        # session_cost = sum of all weekly (all-time) — used for accumulator tracking only
        session_cost = sum(r.get("weeklyCost", 0) for r in breakdown)
        # current_session_cost = only the live active session (resets on /new or /reset)
        current_session_cost = fetch_current_session_cost()

        # ── Persistent daily/weekly accumulator ──────────────────────────────
        accum = load_accum()
        # Only update if session cost grew (new activity), never shrink
        prev_peak = accum.get("peak", 0.0)
        if session_cost > prev_peak:
            delta = session_cost - prev_peak
            accum["daily"]   = round(accum.get("daily",   0.0) + delta, 6)
            accum["weekly"]  = round(accum.get("weekly",  0.0) + delta, 6)
            accum["monthly"] = round(accum.get("monthly", 0.0) + delta, 6)
            accum["peak"]    = round(session_cost, 6)
            save_accum(accum)

        # Compute daily directly from per-model dailyCost (today's sessions only)
        # Avoids accumulator bug where peak from yesterday prevents daily from updating
        daily_cost = round(sum(r.get("dailyCost", 0) for r in breakdown), 6)
        weekly_cost = accum.get("weekly", session_cost)

        # Write a structured tracker file for future API/newsfeed hooks
        tracker_path = ROOT.parent / "data" / "model-usage-tracker.json"
        tracker_payload = {
            "lastUpdated": utc_iso(),
            "totalCostUsd": round(session_cost, 6),
            "models": [
                {
                    "name": r["name"],
                    "source": r.get("source", "unknown"),
                    "totalTokens": r.get("totalTokens", 0),
                    "inputTokens": r.get("inputTokens", 0),
                    "outputTokens": r.get("outputTokens", 0),
                    "costUsd": round(r.get("weeklyCost", 0), 6),
                    "costEstimated": r.get("costEstimated", False),
                    "sessions": r.get("sessions", 0),
                }
                for r in breakdown
            ],
            "_note": "costEstimated=true means Gemini cost was calculated from token counts × pricing table, not billed directly."
        }
        tracker_path.write_text(json.dumps(tracker_payload, indent=2))
        # Run all external/SSH fetches in parallel to cut pipeline time
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=5) as _pool:
            _f_jain      = _pool.submit(fetch_jain_model_usage)
            _f_jain_api  = _pool.submit(fetch_jain_api_costs)
            _f_openrouter = _pool.submit(fetch_openrouter_usage)
            _f_elevenlabs = _pool.submit(fetch_elevenlabs_usage)
            _f_ollama     = _pool.submit(fetch_ollama_usage)
        jain        = _f_jain.result()
        jain_api    = _f_jain_api.result()
        openrouter  = _f_openrouter.result()
        elevenlabs  = _f_elevenlabs.result()
        ollama_rows = _f_ollama.result()

        # Inject Ollama local models into breakdown
        existing_names_lower = {r["name"].lower() for r in breakdown}
        for ol_row in ollama_rows:
            if ol_row["name"].lower() not in existing_names_lower:
                breakdown.append(ol_row)

        # Inject OpenRouter as a synthetic breakdown row if it has daily/weekly spend
        or_weekly = openrouter.get("byok_weekly", 0) or openrouter.get("weekly", 0)
        or_daily  = openrouter.get("byok_daily",  0) or openrouter.get("daily",  0)
        if openrouter.get("available") and or_weekly > 0:
            or_row = {
                "name": "openrouter/auto (BYOK)",
                "source": "OpenRouter API",
                "weeklyCost": round(or_weekly, 6),
                "dailyCost":  round(or_daily, 6),
                "sessionCost": round(or_weekly, 6),
                "totalTokens": 0,
                "costEstimated": False,
                "_note": "BYOK charges: Grok/Claude calls routed via OpenRouter. Weekly = rolling 7d.",
            }
            # Avoid double-counting if OpenRouter models already in breakdown via session tracking
            breakdown_names_lower = [r["name"].lower() for r in breakdown]
            if "openrouter/auto (byok)" not in breakdown_names_lower:
                breakdown.append(or_row)

        monthly_cost = accum.get("monthly", 0.0)
        or_monthly   = openrouter.get("byok_monthly", 0) or openrouter.get("monthly", 0)
        jain_monthly = jain.get("monthly", jain.get("total", 0))  # approximate from total if no monthly
        jain_api_daily   = jain_api.get("daily", 0.0) if jain_api.get("available") else 0.0
        jain_api_monthly = jain_api.get("monthly", 0.0) if jain_api.get("available") else 0.0

        # Inject JAIN direct API models into breakdown (Gemini calls from scripts)
        jain_api_models = jain_api.get("models", {})
        existing_names_lower2 = {r["name"].lower() for r in breakdown}
        for model_name, mdata in jain_api_models.items():
            row_name = f"jain/{model_name}"
            if row_name.lower() not in existing_names_lower2:
                breakdown.append({
                    "name": row_name,
                    "source": "jain-scripts",
                    "weeklyCost": round(mdata.get("cost", 0), 6),
                    "dailyCost": 0.0,
                    "sessionCost": round(mdata.get("cost", 0), 6),
                    "totalTokens": mdata.get("input_tokens", 0) + mdata.get("output_tokens", 0),
                    "inputTokens": mdata.get("input_tokens", 0),
                    "outputTokens": mdata.get("output_tokens", 0),
                    "costEstimated": False,
                    "_note": f"Direct API call from JAIN script ({mdata.get('last_script','?')}). Calls: {mdata.get('calls',0)}",
                })

        total_monthly = round(monthly_cost + or_monthly + jain_api_monthly, 6)

        # ── Weekly Run Rate: automation baseline vs interactive ───────────────
        # automation = J.A.I.N scripts + OpenRouter background + JAIN API models
        # interactive = JOSH 2.0 chat sessions (Sonnet-driven, Josh-initiated)
        automation_weekly = round(
            jain.get("total", 0) + or_weekly + jain_api.get("weekly", 0), 6
        )
        interactive_weekly = round(max(0.0, weekly_cost - automation_weekly), 6)
        total_weekly_all   = round(weekly_cost + or_weekly + jain.get("total", 0) + jain_api.get("weekly", 0), 6)
        weekly_run_rate = {
            "total":       total_weekly_all,
            "automation":  automation_weekly,
            "interactive": interactive_weekly,
            # projected monthly = total * (30/7)
            "projectedMonthly": round(total_weekly_all * (30 / 7), 2),
        }

        payload = {
            "session": round(current_session_cost, 6),
            "daily":   round(daily_cost,   6),
            "weekly":  round(weekly_cost,  6),
            "monthly": round(monthly_cost, 6),
            "topModels": [{"name": r["name"], "window": "session", "cost": r.get("weeklyCost", 0)} for r in breakdown[:5]],
            "breakdown": breakdown,
            "lastUpdated": utc_iso(),
            "jain": jain,
            "jainApi": jain_api,
            "openrouter": openrouter,
            "elevenlabs": elevenlabs,
            "aggregate": {
                "daily":   round(daily_cost + jain.get("daily", 0) + or_daily + jain_api_daily, 6),
                "total":   round(session_cost + jain.get("total", 0) + or_weekly + jain_api.get("weekly", 0), 6),
                "monthly": total_monthly,
            },
            "weeklyRunRate": weekly_run_rate,
        }
        return payload

    # Legacy fallback: old Next.js API (likely dead)
    raw = fetch_next("/api/model-usage")
    if raw:
        return normalize_model_usage_payload(raw)

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
    fetch_upcoming_events._status = {"status": "unknown", "message": "Unknown"}  # type: ignore[attr-defined]
    try:
        result = subprocess.run(
            [
                "gog", "calendar", "events", "primary",
                "--account", "jcubellagent@gmail.com",
                "--from", "today",
                "--days", "3",
                "--max", "10",
                "-j", "--results-only",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        fetch_upcoming_events._status = {"status": "ok", "message": "Connected"}  # type: ignore[attr-defined]
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or '').strip()
        if 'invalid_grant' in err or 'expired or revoked' in err:
            fetch_upcoming_events._status = {"status": "auth_expired", "message": "Re-auth required"}  # type: ignore[attr-defined]
            print("[info] gog calendar auth needs re-login; skipping calendar fetch", file=sys.stderr)
            return []
        fetch_upcoming_events._status = {"status": "error", "message": "Calendar fetch failed"}  # type: ignore[attr-defined]
        print(f"[warn] gog calendar list failed: {err}", file=sys.stderr)
        return []
    raw = json.loads(result.stdout or "[]")
    # gog --results-only returns array directly; fallback to items envelope
    items_list = raw if isinstance(raw, list) else raw.get("items", [])
    events = []
    for item in items_list:
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


def fetch_active_subagents() -> List[Dict[str, Any]]:
    """Read active/recent sub-agent sessions from OpenClaw sessions.json."""
    sessions_path = Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
    if not sessions_path.exists():
        return []
    try:
        data = json.loads(sessions_path.read_text())
        agents = []
        now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
        for key, val in data.items():
            if "subagent" not in key:
                continue
            status = val.get("status", "unknown")
            label = val.get("label") or key.split(":")[-1][:30]
            updated_at_ms = val.get("updatedAt", 0)
            started_at_ms = val.get("startedAt", 0)
            age_secs = (now_ms - updated_at_ms) / 1000 if updated_at_ms else 9999
            # Only include: active ones OR completed within last 10 minutes
            if status == "running" or (status == "done" and age_secs < 600):
                started_iso = dt.datetime.fromtimestamp(
                    started_at_ms / 1000, tz=dt.timezone.utc
                ).isoformat() if started_at_ms else None
                elapsed_secs = int((now_ms - started_at_ms) / 1000) if started_at_ms else 0
                agents.append({
                    "id": key.split(":")[-1],
                    "label": label,
                    "status": status,
                    "startedAt": started_iso,
                    "elapsedSecs": elapsed_secs,
                    "updatedAt": dt.datetime.fromtimestamp(
                        updated_at_ms / 1000, tz=dt.timezone.utc
                    ).isoformat() if updated_at_ms else None,
                })
        # Active first, then by most recent
        agents.sort(key=lambda x: (0 if x["status"] == "running" else 1, -x.get("elapsedSecs", 0)))
        return agents[:5]
    except Exception as exc:
        print(f"[warn] fetch_active_subagents failed: {exc}", file=sys.stderr)
        return []


def fetch_crons() -> List[Dict[str, Any]]:
    # JOSH 2.0 crontab
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True)
        josh_listing = result.stdout
    except subprocess.CalledProcessError:
        josh_listing = ""
    # J.A.I.N — single batched SSH call for crontab + x_post_agent log + reply state
    import datetime as _dt
    import re as _re
    from zoneinfo import ZoneInfo

    now_et = _dt.datetime.now(ZoneInfo("America/New_York"))
    today_str = now_et.strftime('%Y-%m-%d')

    jain_listing = ""
    x_log_lines_raw = ""
    reply_state_raw = "{}"
    x_log_runs: dict[str, str] = {}
    _jain_replies_today_from_log: list[int] = []
    hermes_jobs: dict[str, dict[str, Any]] = {}
    try:
        jain_batch_cmd = (
            "echo '===CRON==='; crontab -l 2>/dev/null || true; "
            "echo '===XLOG==='; grep -E '[0-9]{2}:[0-9]{2}:[0-9]{2}.*X Post Agent|Posted' "
            "  /Users/jc_agent/.openclaw/workspace/logs/x_post_agent.log 2>/dev/null | tail -50 || true; "
            "echo '===REPLY==='; cat /Users/jc_agent/.openclaw/workspace/mission-control/data/x_reply_state.json 2>/dev/null || echo '{}'; "
            f"echo '===SORAREMISSIONS==='; tail -8 /Users/jc_agent/scripts/logs/sorare_missions.log 2>/dev/null || echo ''; "
            f"echo '===SORARELINEUPS==='; tail -8 /Users/jc_agent/scripts/logs/sorare_lineups.log 2>/dev/null || echo ''; "
            f"echo '===STRATEGICREPLIES==='; grep -E '^\\[([0-9]{{2}}):' /Users/jc_agent/.openclaw/workspace/logs/x_strategic_reply.log 2>/dev/null | tail -20 || echo ''; "
            f"echo '===HERMESJOBS==='; cat /Users/jc_agent/.hermes/cron/jobs.json 2>/dev/null || echo '{{}}'"
        )
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "jc_agent@100.121.89.84", jain_batch_cmd],
            capture_output=True, text=True, timeout=12
        )
        if r.returncode == 0:
            raw = r.stdout
            parts = raw.split("===XLOG===")
            jain_listing = parts[0].replace("===CRON===", "").strip() if parts else ""
            if len(parts) > 1:
                reply_split = parts[1].split("===REPLY===")
                x_log_lines_raw = reply_split[0].strip()
                rest = reply_split[1] if len(reply_split) > 1 else ""
                missions_split = rest.split("===SORAREMISSIONS===")
                reply_state_raw = missions_split[0].strip()
                if len(missions_split) > 1:
                    lineups_split = missions_split[1].split("===SORARELINEUPS===")
                    sorare_missions_tail = lineups_split[0].strip()
                    rest2 = lineups_split[1] if len(lineups_split) > 1 else ""
                    strategic_split = rest2.split("===STRATEGICREPLIES===")
                    sorare_lineups_tail = strategic_split[0].strip()
                    strategic_reply_log = strategic_split[1].strip() if len(strategic_split) > 1 else ""
                    # Split out Hermes jobs JSON
                    hermes_split = strategic_reply_log.split("===HERMESJOBS===")
                    strategic_reply_log_clean = hermes_split[0].strip()
                    hermes_jobs_raw = hermes_split[1].strip() if len(hermes_split) > 1 else "{}"
                    # Parse strategic reply hours from log
                    _strategic_reply_hours: list[int] = []
                    for _srl in strategic_reply_log_clean.splitlines():
                        _srm = _re.match(r'^\[(\d{2}):', _srl.strip())
                        if _srm:
                            _h = int(_srm.group(1))
                            if _h <= now_et.hour:
                                _strategic_reply_hours.append(_h)
                    if _strategic_reply_hours:
                        _jain_replies_today_from_log = _strategic_reply_hours
                    else:
                        _jain_replies_today_from_log = []
                    # Parse Hermes jobs for JAIMES-agent last_run data
                    try:
                        _hdata = json.loads(hermes_jobs_raw)
                        for _hj in _hdata.get('jobs', []):
                            _hname = _hj.get('name', '')
                            if _hname:
                                hermes_jobs[_hname] = _hj
                    except Exception:
                        pass
    except Exception:
        pass

    # Parse X post log for lastRun data (ET hours only, today only)
    try:
        log_lines = x_log_lines_raw.splitlines()
        # Keys are ET hours (cron schedule hours) — log timestamps are also ET
        hour_to_job = {
            7:  "X Pre-Market",
            8:  "X Market Open",
            11: "X Mover",
            12: "X Hot Take",
            17: "X Market Close",
            21: "X Prime Take",
            22: "X Nightcap",
            13: "X Quote Tweets",
            15: "X Quote Tweets",
            18: "X Quote Tweets",
            20: "X Quote Tweets",
        }
        # We need date context — log lines don't include date so we only count if
        # timestamp hour is plausible for today (hour <= now_et.hour + 1 guard)
        for line in log_lines:
            m = _re.match(r'\[(\d{2}):(\d{2}):\d{2}\] X Post Agent', line)
            if m:
                h = int(m.group(1))
                # Only count hours that have already passed today (avoid yesterday bleed)
                if h > now_et.hour:
                    continue
                job_name = hour_to_job.get(h)
                if job_name:
                    iso = f"{today_str}T{m.group(1)}:{m.group(2)}:00"
                    x_log_runs[job_name] = iso
    except Exception:
        pass

    # Parse strategic reply state
    _jain_replies_today: list[int] = []
    try:
        reply_state_r_stdout = reply_state_raw
        if True:  # keep indentation compatible with original code below
            jain_rs = json.loads(reply_state_r_stdout.strip() or '{}')
            for r_item in jain_rs.get('replies', []):
                posted = r_item.get('posted_at', '')
                try:
                    dt_utc = _dt.datetime.fromisoformat(posted.replace('Z', '+00:00'))
                    dt_et = dt_utc.astimezone(ZoneInfo("America/New_York"))
                    if dt_et.strftime('%Y-%m-%d') == today_str:
                        _jain_replies_today.append(dt_et.hour)
                except Exception:
                    pass
            # Also merge log-based hours (more reliable than stale reply_state.json)
            for _lh in _jain_replies_today_from_log:
                if _lh not in _jain_replies_today:
                    _jain_replies_today.append(_lh)
            if _jain_replies_today:
                x_log_runs["X Strategic Replies"] = f"{today_str}T{max(_jain_replies_today):02d}:00:00"
    except Exception:
        pass
    fetch_crons._jain_replies_today = _jain_replies_today  # type: ignore[attr-defined]



    def parse_daily_hour(schedule_str: str):
        """Extract scheduled ET hour from a 'Daily H:MM AM/PM ET' string. Returns int or None."""
        m = _re.search(r'(\d{1,2}):(\d{2})\s*(AM|PM)', schedule_str, _re.IGNORECASE)
        if not m:
            m = _re.search(r'(\d{1,2}):?(\d{0,2})\s*(AM|PM)', schedule_str, _re.IGNORECASE)
        if m:
            h = int(m.group(1))
            ap = m.group(3).upper()
            if ap == 'PM' and h != 12:
                h += 12
            elif ap == 'AM' and h == 12:
                h = 0
            return h
        return None

    rows = []
    for target in CRON_TARGETS:
        is_jain = target.get('jain', False)
        listing = jain_listing if is_jain else josh_listing
        source = target.get('source', 'cron')
        hermes_job = hermes_jobs.get(target.get('hermesName', '')) if source == 'hermes' else None
        present = bool(hermes_job) if source == 'hermes' else target['pattern'] in listing

        # Compute runStatus for daily jobs
        sched = target.get('schedule', '')
        run_status = None  # 'done' | 'missed' | 'upcoming' | None
        last_run = x_log_runs.get(target['name'])
        if not last_run and hermes_job:
            _hlast = hermes_job.get('last_run_at')
            if _hlast and hermes_job.get('last_status') == 'ok':
                last_run = _hlast
        last_run_today = False
        if last_run:
            try:
                _last_run_dt = _dt.datetime.fromisoformat(str(last_run).replace('Z', '+00:00')).astimezone(ZoneInfo("America/New_York"))
                last_run_today = _last_run_dt.strftime('%Y-%m-%d') == today_str
            except Exception:
                last_run_today = False

        is_jaimes_agent = target.get('agent') == 'JAIMES'
        if sched.startswith('Daily'):
            sched_hour = parse_daily_hour(sched)
            if sched_hour is not None:
                now_hour = now_et.hour
                now_min = now_et.minute
                if last_run_today:
                    run_status = 'done'
                elif is_jaimes_agent and not present:
                    # JAIMES jobs run via Hermes — show paused if no last_run confirmed today
                    run_status = 'paused'
                elif now_hour > sched_hour or (now_hour == sched_hour and now_min >= 10):
                    run_status = 'missed'
                else:
                    run_status = 'upcoming'
        elif target['name'] == 'X Strategic Replies':
            if last_run_today:
                run_status = 'done'
            elif now_et.hour >= 9:
                run_status = 'upcoming'

        if source == 'hermes' and hermes_job and not hermes_job.get('enabled', True):
            row_status = 'paused'
        else:
            row_status = 'ok' if (present or last_run) else 'paused'
        row = {
            'name': target['name'],
            'schedule': target['schedule'],
            'description': target.get('description', ''),
            'category': target.get('category', 'Other'),
            'agent': target.get('agent', 'JOSH 2.0'),
            'status': row_status,
            'errors': 1 if (hermes_job and hermes_job.get('last_status') not in {None, '', 'ok'}) else 0,
            'lastError': hermes_job.get('last_error') if hermes_job else None,
        }
        if run_status:
            row['runStatus'] = run_status
        if last_run:
            row['lastRun'] = last_run

        if target.get('multiRun'):
            multi = target['multiRun']
            if 'runs' in multi:
                runs = list(multi['runs'])
            elif now_et.weekday() < 5 and 'weekdayRuns' in multi:
                runs = list(multi['weekdayRuns'])
            else:
                runs = list(multi.get('weekendRuns', []))

            # For X Strategic Replies: mark slots done based on actual replies posted today
            if target['name'] == 'X Strategic Replies':
                # Collect reply timestamps from both machines
                reply_hours_today: list[int] = []

                def _utc_to_et_date_hour(posted_iso: str):
                    """Convert UTC ISO timestamp to (ET_date_str, ET_hour). Returns (None, None) on error."""
                    try:
                        dt_utc = _dt.datetime.fromisoformat(posted_iso.replace('Z', '+00:00'))
                        dt_et = dt_utc.astimezone(ZoneInfo("America/New_York"))
                        return dt_et.strftime('%Y-%m-%d'), dt_et.hour
                    except Exception:
                        return None, None

                for rs_path in [
                    ROOT.parent / 'data' / 'x_reply_state.json',
                ]:
                    if rs_path.exists():
                        try:
                            rs_data = json.loads(rs_path.read_text())
                            for r in rs_data.get('replies', []):
                                posted = r.get('posted_at', '')
                                et_date, et_hour = _utc_to_et_date_hour(posted)
                                if et_date == today_str and et_hour is not None:
                                    reply_hours_today.append(et_hour)
                        except Exception:
                            pass
                # Also pull J.A.I.N replies (fetched above)
                for rh in getattr(fetch_crons, '_jain_replies_today', []):
                    reply_hours_today.append(rh)

                # Slot schedule hours (ET)
                slot_hours = [9, 10, 11, 13, 14, 15, 16, 17, 18, 19, 21, 23]
                replies_done = sorted(reply_hours_today)

                # Greedily assign each reply to the earliest slot it could cover
                assigned = [False] * len(slot_hours)
                for rh in replies_done:
                    for i, sh in enumerate(slot_hours):
                        if not assigned[i] and rh >= sh:
                            assigned[i] = True
                            break

                for i, run in enumerate(runs):
                    run['done'] = assigned[i] if i < len(assigned) else False
                if any(assigned):
                    row['runStatus'] = 'done'
                elif now_et.hour >= 9 and 'runStatus' not in row:
                    row['runStatus'] = 'upcoming'
            else:
                for run in runs:
                    t_str = run['time']
                    try:
                        t = _dt.datetime.strptime(t_str, "%I:%M %p").replace(
                            year=now_et.year, month=now_et.month, day=now_et.day,
                            tzinfo=_dt.timezone(_dt.timedelta(hours=-4))
                        )
                        run['done'] = now_et >= t
                    except Exception:
                        run['done'] = False
            row['multiRun'] = {'runs': runs}
        rows.append(row)
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


def fetch_agent_bus_tasks(limit: int = 12) -> List[Dict[str, Any]]:
    query = urllib.parse.urlencode({
        "select": "id,origin_node,target_node,task_type,status,payload,created_at,result,error_log",
        "order": "created_at.desc",
        "limit": str(limit),
    })
    url = f"{AGENT_BUS_URL}/rest/v1/agent_tasks?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "apikey": AGENT_BUS_KEY,
            "Authorization": f"Bearer {AGENT_BUS_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"[warn] fetch_agent_bus_tasks failed: {exc}", file=sys.stderr)
        return []


def fetch_context_watchdog_status() -> Dict[str, Any]:
    status = {
        "loaded": False,
        "label": CONTEXT_WATCHDOG_LABEL,
        "threshold": 0.50,
        "lastTriggeredAt": None,
        "handoffExists": CONTEXT_HANDOFF_PATH.exists(),
        "sessionKey": None,
        "usedTokens": 0,
        "limitTokens": 0,
        "pct": 0.0,
    }
    try:
        uid = str(os.getuid())
        proc = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{CONTEXT_WATCHDOG_LABEL}"],
            capture_output=True, text=True, timeout=6,
        )
        status["loaded"] = proc.returncode == 0
    except Exception:
        pass

    if CONTEXT_WATCHDOG_STATE_PATH.exists():
        try:
            state = json.loads(CONTEXT_WATCHDOG_STATE_PATH.read_text())
            status["sessionKey"] = state.get("sessionKey")
            status["usedTokens"] = int(state.get("usedTokens") or 0)
            status["limitTokens"] = int(state.get("limitTokens") or 0)
            limit = status["limitTokens"] or 1
            status["pct"] = round(status["usedTokens"] / limit, 4)
            status["threshold"] = 0.50
            status["lastTriggeredAt"] = dt.datetime.fromtimestamp(
                CONTEXT_WATCHDOG_STATE_PATH.stat().st_mtime, tz=dt.timezone.utc
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except Exception as exc:
            print(f"[warn] fetch_context_watchdog_status failed: {exc}", file=sys.stderr)
    elif CONTEXT_HANDOFF_PATH.exists():
        status["lastTriggeredAt"] = dt.datetime.fromtimestamp(
            CONTEXT_HANDOFF_PATH.stat().st_mtime, tz=dt.timezone.utc
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return status


def fetch_coding_visibility() -> Dict[str, Any]:
    def recent_code_files() -> List[str]:
        cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - (3 * 60 * 60)
        roots = [WORKSPACE_ROOT / "scripts", WORKSPACE_ROOT / "mission-control"]
        seen: set[str] = set()
        files: list[tuple[float, str]] = []
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if any(part in {"node_modules", "data", "logs", "__pycache__"} for part in path.parts):
                    continue
                if path.suffix.lower() not in {".py", ".sh", ".js", ".ts", ".html", ".css", ".json", ".md"}:
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                rel = str(path.relative_to(WORKSPACE_ROOT))
                if mtime >= cutoff and rel not in seen:
                    seen.add(rel)
                    files.append((mtime, rel))
        files.sort(key=lambda item: item[0], reverse=True)
        return [name for _, name in files[:6]]

    def git_dirty_count(repo: Path) -> int:
        if not (repo / ".git").exists():
            return 0
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo), "status", "--short", "--untracked-files=no"],
                capture_output=True, text=True, timeout=6,
            )
            if proc.returncode != 0:
                return 0
            return len([line for line in proc.stdout.splitlines() if line.strip()])
        except Exception:
            return 0

    codexbar_summary = "CodexBar available"
    codexbar_data: Dict[str, Any] = {"available": False}
    try:
        proc = subprocess.run(
            ["/bin/zsh", "-lc", "/opt/homebrew/bin/codexbar usage --provider codex --pretty | sed -n '1,8p'"],
            capture_output=True, text=True, timeout=8,
        )
        output = "\n".join([proc.stdout.strip(), proc.stderr.strip()]).strip()
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if lines:
            codexbar_data = {"available": True, "raw": lines[:8]}
            resets: list[str] = []
            for line in lines:
                if line.startswith("Session:"):
                    codexbar_data["session"] = line.split(":", 1)[1].strip().split("[")[0].strip()
                elif line.startswith("Weekly:"):
                    codexbar_data["weekly"] = line.split(":", 1)[1].strip().split("[")[0].strip()
                elif line.startswith("Resets in"):
                    resets.append(line.replace("Resets in", "").strip())
                elif line.startswith("Credits:"):
                    codexbar_data["credits"] = line.split(":", 1)[1].strip()
                elif line.startswith("Account:"):
                    codexbar_data["account"] = line.split(":", 1)[1].strip()
                elif line.startswith("Plan:"):
                    codexbar_data["plan"] = line.split(":", 1)[1].strip()
            if resets:
                codexbar_data["sessionReset"] = resets[0]
            if len(resets) > 1:
                codexbar_data["weeklyReset"] = resets[1]
            codexbar_summary = " · ".join(
                part for part in [
                    codexbar_data.get("plan"),
                    codexbar_data.get("session"),
                    f"wk {codexbar_data.get('weekly')}" if codexbar_data.get("weekly") else None,
                ] if part
            ) or "CodexBar available"
            codexbar_data["summary"] = codexbar_summary
        else:
            codexbar_summary = "Codex auth required"
    except Exception:
        codexbar_summary = "Codex auth required"

    recent_files = recent_code_files()
    return {
        "workspaceDirty": git_dirty_count(WORKSPACE_ROOT),
        "missionControlDirty": git_dirty_count(WORKSPACE_ROOT / "mission-control"),
        "recentFiles": recent_files,
        "codexbarStatus": codexbar_summary,
        "codexbar": codexbar_data,
        "updatedAt": utc_iso(),
    }


def build_visibility_agents(
    agent_bus_tasks: List[Dict[str, Any]],
    coding_visibility: Dict[str, Any],
    watchdog: Dict[str, Any],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    now = dt.datetime.now(dt.timezone.utc)

    for task in agent_bus_tasks:
        status = (task.get("status") or "").lower()
        created_at = task.get("created_at") or utc_iso()
        try:
            created_dt = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            age_secs = max(0, int((now - created_dt).total_seconds()))
        except Exception:
            age_secs = 0
        stale_queue = status in {"queued", "retry"} and age_secs > 6 * 3600
        is_active = status in {"queued", "retry", "in_progress", "running"} and not stale_queue
        is_recent_done = status == "completed" and age_secs < 1800
        if not (is_active or is_recent_done):
            continue
        payload = task.get("payload") or {}
        label = payload.get("task") or task.get("task_type") or "Bus task"
        target = str(task.get("target_node") or "AGENT").replace("_", ".")
        rows.append({
            "id": f"bus-{task.get('id', '')[:8]}",
            "label": label,
            "status": "running" if is_active else "done",
            "elapsedSecs": age_secs,
            "tool": "bus",
            "model": status.upper(),
            "agentLabel": target,
            "agentClass": "agent",
        })

    wf = coding_visibility.get("recentFiles") or []
    if wf:
        rows.append({
            "id": "coding-visibility",
            "label": "Editing: " + ", ".join(wf[:2]),
            "status": "running",
            "elapsedSecs": 0,
            "tool": "code",
            "model": "live",
            "agentLabel": "CODING",
            "agentClass": "agent",
        })

    if watchdog.get("loaded"):
        pct = round(float(watchdog.get("pct") or 0) * 100)
        rows.append({
            "id": "context-watchdog",
            "label": f"Context watchdog armed at 50% · last trigger {pct}%",
            "status": "done" if watchdog.get("lastTriggeredAt") else "running",
            "elapsedSecs": 0,
            "tool": "watchdog",
            "model": "50% rule",
            "agentLabel": "WATCHDOG",
            "agentClass": "agent",
        })
    return rows[:6]


def build_recent_activity(
    now_iso: str,
    model_usage: Dict[str, Any] | None,
    focus: Dict[str, Any] | None,
    events: List[Dict[str, Any]],
    crons: List[Dict[str, Any]],
    devices: List[Dict[str, Any]],
    agent_bus_tasks: List[Dict[str, Any]] | None = None,
    coding_visibility: Dict[str, Any] | None = None,
    watchdog: Dict[str, Any] | None = None,
) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []

    if focus and focus.get("status"):
        items.append({
            "time": focus.get("updatedAt") or now_iso,
            "event": f"Brain: {focus.get('status')}",
        })

    if events:
        next_event = events[0]
        items.append({
            "time": next_event.get("time") or now_iso,
            "event": f"Upcoming: {next_event.get('title') or 'Calendar event'}",
        })

    if model_usage:
        session_cost = model_usage.get("session") or 0
        items.append({
            "time": model_usage.get("lastUpdated") or now_iso,
            "event": f"Session spend: ${session_cost:.2f}",
        })

    if watchdog and watchdog.get("loaded"):
        items.append({
            "time": watchdog.get("lastTriggeredAt") or now_iso,
            "event": "Context watchdog armed at 50%",
        })

    if agent_bus_tasks:
        now_dt = dt.datetime.now(dt.timezone.utc)
        active_bus = []
        for task in agent_bus_tasks:
            status = (task.get("status") or "").lower()
            if status not in {"queued", "retry", "in_progress", "running"}:
                continue
            created_at = task.get("created_at") or now_iso
            try:
                created_dt = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age_secs = max(0, int((now_dt - created_dt).total_seconds()))
            except Exception:
                age_secs = 0
            if status in {"queued", "retry"} and age_secs > 6 * 3600:
                continue
            active_bus.append(task)
        if active_bus:
            items.append({
                "time": active_bus[0].get("created_at") or now_iso,
                "event": f"Agent bus: {len(active_bus)} queued task{'s' if len(active_bus) != 1 else ''}",
            })

    if coding_visibility:
        recent_files = coding_visibility.get("recentFiles") or []
        if recent_files:
            items.append({
                "time": coding_visibility.get("updatedAt") or now_iso,
                "event": f"Coding: {', '.join(recent_files[:2])}",
            })
        if coding_visibility.get("codexbarStatus"):
            items.append({
                "time": coding_visibility.get("updatedAt") or now_iso,
                "event": f"CodexBar: {coding_visibility.get('codexbarStatus')}",
            })

    error_crons = [cron for cron in crons if (cron.get("errors") or 0) > 0 or cron.get("status") == "error"]
    if error_crons:
        items.append({
            "time": now_iso,
            "event": f"{len(error_crons)} cron job{'s' if len(error_crons) != 1 else ''} need attention",
        })
    else:
        items.append({
            "time": now_iso,
            "event": f"{len(crons)} scheduled jobs healthy",
        })

    if devices:
        attention = [device for device in devices if device.get("status") not in (None, "ok")]
        if attention:
            items.append({
                "time": now_iso,
                "event": f"{len(attention)} device alert{'s' if len(attention) != 1 else ''}",
            })
        else:
            items.append({
                "time": now_iso,
                "event": "Device layer nominal",
            })

    items.append({"time": now_iso, "event": "Mission Control refresh published"})
    # Sort most recent first
    items.sort(key=lambda x: x.get("time", ""), reverse=True)
    return items[:6]


BRAIN_FEED_PATH = ROOT.parent / "data" / "brain-feed.json"


def load_brain_feed_file() -> Dict[str, Any] | None:
    """Load brainFeed state from the sidecar file written by the agent.

    READ-ONLY — does NOT write back to brain-feed.json.
    Supabase is the source of truth for active state.
    The cron must never overwrite an active brain feed.
    """
    if not BRAIN_FEED_PATH.exists():
        return None
    try:
        data = json.loads(BRAIN_FEED_PATH.read_text())
        if not isinstance(data, dict):
            return None
        # Auto-expire active flag only if agent hasn't updated in 2h (safety net only)
        updated = data.get("updatedAt")
        if updated and data.get("active"):
            try:
                ts = dt.datetime.fromisoformat(updated.replace("Z", "+00:00"))
                age = dt.datetime.now(dt.timezone.utc) - ts
                if age.total_seconds() > 7200:
                    data["active"] = False
            except (ValueError, TypeError):
                pass
        return data
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[warn] failed to read {BRAIN_FEED_PATH}: {exc}", file=sys.stderr)
        return None


# Context window limits per model (tokens)
CONTEXT_LIMITS: Dict[str, int] = {
    "claude-sonnet-4-6":   1_000_000,
    "claude-sonnet-4-5":     200_000,
    "claude-opus-4":       1_000_000,
    "claude-haiku-3-5":      200_000,
    "gpt-5.4":               128_000,
    "gpt-5.1-codex":         128_000,
    "gpt-4.1":               128_000,
    "gpt-4o":                128_000,
    "gemini-2.5-flash":    1_000_000,
    "gemini-2.5-pro":      1_000_000,
    "gemini-2.0-flash":    1_000_000,
    "grok-3":                131_072,
    "grok-3-mini":           131_072,
}

MOLTWORLD_AGENT_ID = "agent_9bon7uvreysrf2z6"
MOLTWORLD_API_BASE = "https://moltworld.io"
MOLTWORLD_STATE_PATH = ROOT.parent / "data" / "moltworld-state.json"

def fetch_moltworld_data() -> Dict[str, Any]:
    try:
        # 1. Fetch balance data
        balance_url = f"{MOLTWORLD_API_BASE}/api/agents/balance?agentId={MOLTWORLD_AGENT_ID}"
        with urllib.request.urlopen(balance_url, timeout=5) as resp:
            balance_data = json.load(resp)
        balance = balance_data.get("balance", {})
        tokenomics = balance_data.get("tokenomics", {}).get("projection", {})

        # 2. Read moltworld-state.json
        state_data = {}
        if MOLTWORLD_STATE_PATH.exists():
            try:
                state_data = json.loads(MOLTWORLD_STATE_PATH.read_text())
            except json.JSONDecodeError:
                pass # Will use empty dict

        # 3. Construct the return dict
        return {
            "sim_balance":        float(balance.get("sim", 0.0)),
            "total_earned":       float(balance.get("totalEarned", 0.0)),
            "online_time":        str(balance.get("totalOnlineTime", "0h 0m")),
            "is_online":          bool(balance.get("isOnline", False)),
            "status":             "online" if bool(balance.get("isOnline", False)) else "offline",
            "earning_rate":       str(balance.get("earningRate", "0 SIM/hour")),
            "position_x":         int(state_data.get("x", 0)),
            "position_y":         int(state_data.get("y", 0)),
            "run_count":          int(state_data.get("run_count", 0)),
            "nearby_agents":      list(state_data.get("nearby_agents", [])),
            "last_thought":       str(state_data.get("last_thought", "...")),
            "blocks_built":       int(state_data.get("blocks_built", 0)),
            "projection_per_day": float(tokenomics.get("perDay", 0.0)),
            "updatedAt":          utc_iso(),
        }
    except Exception as exc:
        print(f"[warn] fetch_moltworld_data failed: {exc}", file=sys.stderr)
        return { # Safe defaults on failure
            "sim_balance": 0.0, "total_earned": 0.0, "online_time": "0h 0m",
            "is_online": False, "status": "offline", "earning_rate": "0 SIM/hour",
            "position_x": 0, "position_y": 0, "run_count": 0,
            "nearby_agents": [], "last_thought": "Error fetching data",
            "blocks_built": 0, "projection_per_day": 0.0,
            "updatedAt": utc_iso(),
        }

def fetch_context_window() -> Dict[str, Any]:
    """Read contextTokens + model from the most recent OpenClaw session."""
    result = {"usedTokens": 0, "limitTokens": 0, "pct": 0.0, "model": "", "status": "green"}
    try:
        sessions = json.loads(OPENCLAW_SESSIONS_PATH.read_text())
        preferred_key = "agent:main:telegram:direct:6218150306"
        best = sessions.get(preferred_key)
        # Fallback: pick the most-recently-updated session if the Josh DM session is absent.
        if not best:
            best = max(sessions.values(), key=lambda s: s.get("updatedAt", ""), default=None)
        if not best:
            return result
        model = (best.get("modelOverride") or best.get("model") or "").lower().replace("anthropic/", "").replace("google/", "").replace("openai/", "")
        ctx_tokens = int(best.get("contextTokens") or 0)
        total_tokens = int(best.get("totalTokens") or 0)
        used = total_tokens  # OpenClaw session store uses totalTokens as current session context size.

        # Prefer the real live context limit recorded by OpenClaw for this session.
        # Only fall back to model heuristics if the field is missing.
        limit = ctx_tokens if ctx_tokens > 0 else 0
        if limit == 0:
            for key, lim in CONTEXT_LIMITS.items():
                if key in model:
                    limit = lim
                    break
        if limit == 0:
            limit = 200_000

        pct = round(used / limit, 4) if limit > 0 else 0.0
        pct = min(pct, 1.0)

        # Josh's hard rule: 50% is the ceiling.
        if pct >= 0.50:
            status = "red"    # new session immediately recommended
        elif pct >= 0.40:
            status = "amber"  # approaching hard ceiling
        else:
            status = "green"  # plenty of room

        result = {
            "usedTokens": used,
            "limitTokens": limit,
            "pct": pct,
            "model": model,
            "status": status,
        }
    except Exception as exc:
        print(f"[warn] fetch_context_window failed: {exc}", file=sys.stderr)
    return result


DEFAULT_BRAIN_FEED: Dict[str, Any] = {
    "active": False,
    "messageReceived": None,
    "objective": "",
    "status": "idle",
    "steps": [],
    "currentTool": None,
    "updatedAt": None,
}

REQUIRED_DASHBOARD_FIELDS = [
    "actionRequired", "activeNow", "upcomingEvents", "focus",
    "brainFeed", "contextWindow", "devices", "products", "crons", "recentActivity", "lastUpdated",
]


def validate_dashboard(dashboard: Dict[str, Any], now_iso: str) -> None:
    """Ensure all required fields are present; fill defaults if missing."""
    defaults: Dict[str, Any] = {
        "actionRequired": [],
        "activeNow": [],
        "upcomingEvents": [],
        "focus": {
            "status": "System nominal",
            "context": "Mission Control is running.",
            "runway": 0.98,
            "updatedAt": now_iso,
        },
        "brainFeed": dict(DEFAULT_BRAIN_FEED),
        "devices": [],
        "products": [],
        "crons": [],
        "recentActivity": [],
        "lastUpdated": now_iso,
    }
    for field, default in defaults.items():
        if field not in dashboard or dashboard[field] is None:
            print(f"[warn] dashboard missing required field '{field}', using default", file=sys.stderr)
            dashboard[field] = default

    # Validate top-level timestamps
    if not is_valid_iso8601(dashboard.get("lastUpdated")):
        dashboard["lastUpdated"] = now_iso
    focus_ts = dashboard.get("focus", {}).get("updatedAt")
    if not is_valid_iso8601(focus_ts):
        dashboard["focus"]["updatedAt"] = now_iso


def main() -> None:
    DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    merge_jain_newsfeed()
    now_iso = utc_iso()
    dashboard: Dict[str, Any] = {
        "actionRequired": [],
        "activeNow": [],
        "upcomingEvents": [],
    }

    # Always populate brainFeed — load sidecar, fall back to existing dashboard, then empty default
    brain_feed = load_brain_feed_file()
    if not brain_feed and DASHBOARD_PATH.exists():
        try:
            existing = json.loads(DASHBOARD_PATH.read_text())
            brain_feed = existing.get("brainFeed")
        except (json.JSONDecodeError, OSError):
            pass
    dashboard["brainFeed"] = brain_feed or dict(DEFAULT_BRAIN_FEED)

    brain = fetch_brain_feed()
    dashboard["focus"] = brain or build_focus_fallback(dashboard["brainFeed"], now_iso)
    dashboard["contextWindow"] = fetch_context_window()
    agent_bus_tasks = fetch_agent_bus_tasks()
    context_watchdog = fetch_context_watchdog_status()
    coding_visibility = fetch_coding_visibility()
    dashboard["agentBus"] = agent_bus_tasks
    dashboard["contextWatchdog"] = context_watchdog
    dashboard["codingVisibility"] = coding_visibility

    model_usage = fetch_model_usage() or {
        "session": 0.0,
        "daily": 0.0,
        "weekly": 0.0,
        "topModels": [],
        "breakdown": [],
        "lastUpdated": now_iso,
    }
    dashboard["modelUsage"] = model_usage

    moltworld_data = fetch_moltworld_data()
    dashboard["moltWorld"] = moltworld_data

    # Run independent fetches in parallel
    import concurrent.futures as _cf2
    with _cf2.ThreadPoolExecutor(max_workers=6) as _pool2:
        _f_health   = _pool2.submit(fetch_machine_health)
        _f_events   = _pool2.submit(fetch_upcoming_events)
        _f_devices  = _pool2.submit(build_devices)
        _f_products = _pool2.submit(build_products, now_iso)
        _f_crons    = _pool2.submit(fetch_crons)
        _f_agents   = _pool2.submit(fetch_active_subagents)
        # Eight Sleep — run as subprocess in parallel
        _eight_proc = subprocess.Popen(
            [sys.executable, str(ROOT / "fetch_eight_sleep.py")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

    dashboard["machineHealth"]  = _f_health.result()
    dashboard["upcomingEvents"] = _f_events.result()
    dashboard["calendarHealth"] = getattr(fetch_upcoming_events, '_status', {"status": "unknown", "message": "Unknown"})
    dashboard["devices"]        = _f_devices.result()
    dashboard["products"]       = _f_products.result()
    dashboard["crons"]          = _f_crons.result()
    dashboard["activeAgents"]   = _f_agents.result() + build_visibility_agents(agent_bus_tasks, coding_visibility, context_watchdog)

    jain_brain_feed = load_json_file(ROOT.parent / "data" / "jain-brain-feed.json", {})
    jaimes_brain_feed = load_json_file(ROOT.parent / "data" / "jaimes-brain-feed.json", {})
    agent_comms = build_agent_comms(
        load_json_file(AGENT_COMMS_PATH, []),
        agent_bus_tasks,
        jain_brain_feed if isinstance(jain_brain_feed, dict) else {},
        jaimes_brain_feed if isinstance(jaimes_brain_feed, dict) else {},
    )

    # Wait for Eight Sleep subprocess
    try:
        _eight_proc.wait(timeout=20)
        if _eight_proc.returncode != 0:
            _err = _eight_proc.stderr.read().decode(errors='replace').strip()
            print(f"[warn] fetch_eight_sleep exited {_eight_proc.returncode}: {_err}", file=sys.stderr)
    except Exception as _e:
        print(f"[warn] fetch_eight_sleep failed: {_e}", file=sys.stderr)
        try: _eight_proc.kill()
        except Exception: pass

    dashboard["recentActivity"] = build_recent_activity(
        now_iso,
        model_usage,
        dashboard["focus"],
        dashboard["upcomingEvents"],
        dashboard["crons"],
        dashboard["devices"],
        agent_bus_tasks,
        coding_visibility,
        context_watchdog,
    )
    dashboard["lastUpdated"] = now_iso

    # Final validation — fills any missing required fields with safe defaults
    validate_dashboard(dashboard, now_iso)

    DASHBOARD_PATH.write_text(json.dumps(dashboard, indent=2))
    MODEL_USAGE_PATH.write_text(json.dumps(model_usage, indent=2))
    AGENT_COMMS_PATH.write_text(json.dumps(agent_comms, indent=2))
    MOLTWORLD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True) # Ensure data dir exists
    (ROOT.parent / "data" / "moltworld-data.json").write_text(json.dumps(moltworld_data, indent=2))
    print(f"Updated {DASHBOARD_PATH}")
    print(f"Updated {MODEL_USAGE_PATH}")
    print(f"Updated {AGENT_COMMS_PATH}")
    print(f"Updated {ROOT.parent / 'data' / 'moltworld-data.json'}")

    # ── Sync browser reply state into x-progress.json ──────────────────────
    try:
        xp_path = ROOT.parent / "data" / "x-progress.json"
        reply_state_path = ROOT.parent / "data" / "x_reply_state.json"
        if xp_path.exists():
            xp = json.loads(xp_path.read_text())
            total_browser_replies = 0
            browser_recent = []
            if reply_state_path.exists():
                rs = json.loads(reply_state_path.read_text())
                total_browser_replies = len(rs.get('replied_tweet_ids', []))
                for r in rs.get('replies', [])[-10:]:
                    browser_recent.append({
                        'id': r.get('tweet_id', ''),
                        'text': r.get('reply_text', ''),
                        'type': 'engagement',
                        'posted_at': r.get('posted_at', ''),
                        'impressions': 0, 'likes': 0, 'retweets': 0, 'replies': 0,
                        'reply_to': f"@{r.get('tweet_author','')}",
                    })

            # Also pull reply state from J.A.I.N if available
            try:
                import subprocess as _sp
                r2 = _sp.run(
                    ['ssh', '-o', 'ConnectTimeout=5', '-o', 'BatchMode=yes',
                     'jc_agent@100.121.89.84',
                     'cat /Users/jc_agent/.openclaw/workspace/mission-control/data/x_reply_state.json 2>/dev/null || echo "{}"'],
                    capture_output=True, text=True, timeout=8
                )
                if r2.returncode == 0 and r2.stdout.strip().startswith('{'):
                    jain_rs = json.loads(r2.stdout)
                    jain_total = len(jain_rs.get('replied_tweet_ids', []))
                    total_browser_replies = max(total_browser_replies, jain_total)
                    # Add J.A.I.N replies to recent list
                    for r in jain_rs.get('replies', [])[-10:]:
                        browser_recent.append({
                            'id': r.get('tweet_id', ''),
                            'text': r.get('reply_text', ''),
                            'type': 'engagement',
                            'posted_at': r.get('posted_at', ''),
                            'impressions': 0, 'likes': 0, 'retweets': 0, 'replies': 0,
                            'reply_to': f"@{r.get('tweet_author','')}",
                        })
            except Exception:
                pass

            # Merge into recentPosts — deduplicate by id
            existing_ids = {p.get('id', '') for p in xp.get('recentPosts', [])}
            new_posts = [p for p in browser_recent if p.get('id') and p['id'] not in existing_ids]
            merged_posts = new_posts + xp.get('recentPosts', [])
            # Sort by posted_at desc, keep top 8
            merged_posts.sort(key=lambda p: p.get('posted_at', ''), reverse=True)
            xp['recentPosts'] = merged_posts[:8]

            # Update lead metrics
            lead = xp.setdefault('lead', {})
            lead['total_replies_all_time'] = total_browser_replies

            # Today's browser reply count
            today_iso = now_iso[:10]
            today_replies = [p for p in browser_recent if p.get('posted_at', '')[:10] == today_iso]
            strategy = xp.setdefault('strategy', {})
            strategy['replies_today'] = len(today_replies)
            strategy['engagement_today'] = len(today_replies)

            # 7-day reply count
            import datetime as _dt
            week_ago = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=7)).isoformat()
            week_replies = [p for p in browser_recent if p.get('posted_at', '') >= week_ago]
            strategy['replies_7d'] = len(week_replies)

            # Top reply targets (most replied-to authors this week)
            from collections import Counter as _Counter
            author_counts = _Counter(p.get('reply_to', '').lstrip('@') for p in week_replies if p.get('reply_to'))
            strategy['best_reply_targets'] = [a for a, _ in author_counts.most_common(5)]

            # Brand voice + slot config (for dashboard display)
            strategy['brand_voice'] = 'AI character — sharp, irreverent, owns AI identity'
            strategy['reply_slots_per_day'] = 12
            strategy['target_accounts_count'] = 23

            # Update milestone for total replies
            for ms in xp.get('milestones', []):
                if ms.get('metric') == 'total_replies':
                    ms['current'] = total_browser_replies
                    ms['achieved'] = total_browser_replies >= ms.get('target', 9999)

            xp['updatedAt'] = now_iso
            xp_path.write_text(json.dumps(xp, indent=2, default=str))
            print(f"Updated x-progress.json (replies={total_browser_replies})")
    except Exception as _xe:
        print(f"x-progress sync warning: {_xe}")


if __name__ == "__main__":
    main()
