#!/usr/bin/env python3
"""Update Mission Control dashboard JSON with live data."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import shutil
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

DATA_DIR = ROOT.parent / "data"
DASHBOARD_PATH = DATA_DIR / "dashboard-data.json"
PERSONAL_CODEX_PATH = DATA_DIR / "personal-codex.json"
MODEL_USAGE_PATH = DATA_DIR / "modelUsage.json"
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
TASKS_PATH = WORKSPACE_ROOT / "memory" / "tasks.md"
CAPABILITY_CANARY_PATH = DATA_DIR / "capability-canary.json"

CRON_TARGETS = [
    # ── JOSH 2.0 (local) ────────────────────────────────────────────────────
    {"name": "Mission Control Refresh", "pattern": "mission-control/scripts/update_and_push.sh", "schedule": "Every 5 min", "description": "Refreshes Mission Control data and pushes local dashboard updates", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "Brain Feed Server", "pattern": "brain_feed_server.py", "schedule": "Every 2 min (keepalive)", "description": "Keeps the live Brain Feed endpoint available for Mission Control", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "Chiro Invite Sync", "pattern": "scripts/chiro_invite_sync.sh", "schedule": "Hourly", "description": "Syncs chiropractic client invites into calendar", "category": "Appointments", "agent": "JOSH 2.0"},
    {"name": "J.A.I.N Silence Detector", "pattern": "jain_silence_detector.py", "schedule": "Hourly", "description": "Alerts if J.A.I.N stops reporting or goes quiet unexpectedly", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "Sorare Cookie Freshness", "pattern": "sorare_cookie_freshness.py", "schedule": "Daily 9:00 AM ET", "description": "Checks Sorare cookie age before it turns into a submission blocker", "category": "Sorare MLB", "agent": "JOSH 2.0"},
    {"name": "J.A.I.N Medic", "pattern": "jain_medic.sh", "schedule": "Hourly", "description": "Runs local watchdog and recovery checks for J.A.I.N", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "Sorare Cookie Auto-Refresh", "pattern": "sorare_cookie_autorefresh.py", "schedule": "Sun 2:00 PM ET", "description": "Weekly forced refresh for Sorare auth cookies", "category": "Sorare MLB", "agent": "JOSH 2.0"},

    # ── J.A.I.N intelligence + maintenance ──────────────────────────────────
    {"name": "Breaking News Scanner", "pattern": "breaking_news_scanner.py", "schedule": "Every 5 min (6:00 AM–11:15 PM ET)", "description": "Scores breaking items and pushes high-signal alerts to @JAIN_BREAKING_BOT", "category": "Intelligence Feed", "agent": "J.A.I.N", "jain": True},
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
    {"name": "Error Rate Monitor", "pattern": "error_rate_monitor.py", "schedule": "Daily 11:00 PM ET", "description": "Nightly scan for elevated error rates across automations", "category": "Maintenance", "agent": "J.A.I.N", "jain": True},
    {"name": "Log Rotation", "pattern": "rotate_logs.sh", "schedule": "Sun 3:00 AM ET", "description": "Weekly log rotation on J.A.I.N", "category": "Maintenance", "agent": "J.A.I.N", "jain": True},
    {"name": "XMCP Boot", "pattern": "xmcp", "schedule": "On boot", "description": "Boot-time XMCP startup on J.A.I.N so agent services recover after restart", "category": "Maintenance", "agent": "J.A.I.N", "jain": True},

    # ── Sorare MLB ──────────────────────────────────────────────────────────
    {"name": "Sorare ML Training", "pattern": "sorare_ml/train.py", "schedule": "Daily 2:00 AM ET", "description": "Hermes retrains the Sorare MLB model on the latest results", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-train-model"},
    {"name": "Sorare Nightly Claim", "pattern": "sorare_missions.py --claim-only --rarity limited", "schedule": "Daily 3:35 AM ET", "description": "LaunchAgent claim sweep for overnight Sorare rewards", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},
    {"name": "Sorare Sheet Updater", "pattern": "sorare_sheet_updater_v2.py", "schedule": "Daily 3:30 AM ET", "description": "Writes fresh Sorare data into the tracker sheet", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},
    {"name": "Sorare Daily Prep", "pattern": "sorare_daily_prep.sh", "schedule": "Daily 9:00 AM ET", "description": "Raw prep pipeline before model-driven Sorare submissions", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},
    {"name": "Sorare ML Missions", "pattern": "ml_bot.py --missions-only", "schedule": "Daily 10:00 AM ET", "description": "Hermes ML mission picker for Sorare", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-ml-missions"},
    {"name": "Sorare ML Lineups", "pattern": "ml_bot.py --lineups-only", "schedule": "Daily 11:00 AM ET", "description": "Hermes ML lineup builder for Sorare competitions", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-ml-lineups"},
    {"name": "Sorare Champion Submit", "pattern": "sorare_missions.py --sp-classic", "schedule": "Daily 11:00 AM ET", "description": "Champion lineup submitter running from raw crontab", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},
    {"name": "Sorare Canonical Reflector", "pattern": "sorare_canonical_reflector.py", "schedule": "Every 15 min (8:00 AM–10:45 PM ET)", "description": "Keeps canonical Sorare state mirrored into Mission Control data", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},
    {"name": "Sorare Deadline Guard", "pattern": "sorare_deadline_guard.py", "schedule": "Mon 5:45 PM ET", "description": "Late lineup-deadline safety check for Sorare", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},

    # ── Fantasy baseball ────────────────────────────────────────────────────
    {"name": "Fantasy Waiver Scan (post-process)", "pattern": "fantasy_waiver_scan.py", "schedule": "Sun 8:00 PM ET", "description": "Post-waiver scan right after the Sunday-night processing window", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Weekly Recap", "pattern": "fantasy_weekly_recap.py", "schedule": "Sun 8:00 AM ET", "description": "Raw weekly recap sent to Josh", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Injury Monitor", "pattern": "fantasy_injury_monitor.py", "schedule": "Mon 8:45 AM ET", "description": "Monday injury check before setting the weekly roster", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Lineup Check", "pattern": "fantasy_lineup_check.py", "schedule": "Mon 9:00 AM ET", "description": "Monday lineup review on the live cron path", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Waiver Injury Alert", "pattern": "waiver_injury_alert.py", "schedule": "Daily 1:00 PM ET", "description": "Surfaces injured-player replacement opportunities", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Waiver Review (Hermes)", "pattern": "fantasy_waiver_scan.py", "schedule": "Wed/Fri 1:00 PM ET", "description": "Hermes waiver review lane that runs mid-week", "category": "Fantasy Baseball", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "fantasy-waiver-scan"},
    {"name": "Fantasy Waiver Scan (pre-game)", "pattern": "fantasy_waiver_scan.py", "schedule": "Mon 7:00 AM ET", "description": "Final waiver review before first-pitch lineup lock", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Results Fetch", "pattern": "fetch_fantasy_results.py", "schedule": "Mon 2:15 AM ET", "description": "Captures weekly matchup outcomes for fantasy model feedback", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy ML Prediction Build", "pattern": "fantasy_ml/cron/run_weekly_prediction.sh", "schedule": "Fri 7:00 PM ET", "description": "Builds the weekly fantasy matchup prediction report", "category": "Fantasy Baseball", "agent": "JAIMES", "jain": True},
    {"name": "Fantasy ML Prediction Refresh", "pattern": "fantasy_ml/cron/run_weekly_prediction.sh", "schedule": "Sat 7:00 AM ET", "description": "Refreshes fantasy matchup predictions before weekend moves", "category": "Fantasy Baseball", "agent": "JAIMES", "jain": True},

    # ── JAIMES / Hermes maintenance ─────────────────────────────────────────
    {"name": "Daily Health Check", "pattern": "daily_health_check.py", "schedule": "Daily 5:50 AM ET", "description": "Hermes daily system-health pass", "category": "Maintenance", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "daily-health-check"},
    {"name": "JAIMES Weekly Report", "pattern": "jaimes_weekly_report.py", "schedule": "Sat 9:00 AM ET", "description": "Weekly JAIMES summary sent back to Josh", "category": "Maintenance", "agent": "JAIMES", "jain": True},
]

DAY_NAME_TO_INDEX = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


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


def infer_task_owner(title: str, notes: str = "", background: str = "") -> str:
    blob = f"{title} {notes} {background}".lower()
    compact = re.sub(r"\s+", " ", blob)
    if re.search(r"owner\s*:\s*jaimes\b|target\s*:\s*jaimes\b", compact):
        return "JAIMES"
    if any(token in compact for token in [
        "handoff queued to jaimes",
        "queued to jaimes",
        "target=jaimes",
        "target jaimes",
        "via hermes",
        "hermes_chat",
    ]):
        return "JAIMES"
    if re.search(r"owner\s*:\s*j\.?a\.?i\.?n\b|target\s*:\s*j\.?a\.?i\.?n\b", compact):
        return "J.A.I.N"
    if any(token in compact for token in ["jc_agent", "on j.a.i.n", "on jain", "target=jain", "target jain"]):
        return "J.A.I.N"
    return "JOSH 2.0"


def fetch_tracked_tasks() -> List[Dict[str, Any]]:
    if not TASKS_PATH.exists():
        return []
    try:
        import re as _re

        today_local = dt.datetime.now().strftime('%Y-%m-%d')

        text = TASKS_PATH.read_text()
        active_block = text.split("# Completed", 1)[0]
        tasks: List[Dict[str, Any]] = []
        current: Dict[str, Any] | None = None

        for raw_line in active_block.splitlines():
            line = raw_line.rstrip()
            if line.startswith("## "):
                if current:
                    current["owner"] = infer_task_owner(
                        current.get("title", ""),
                        current.get("notes", ""),
                        current.get("background", ""),
                    )
                    tasks.append(current)
                m = _re.match(r"^##\s+(T-\d+-\d+)\s+(.*)$", line)
                if not m:
                    current = None
                    continue
                current = {
                    "id": m.group(1),
                    "title": m.group(2).strip(),
                    "status": "active",
                    "statusLabel": "Active",
                }
                continue

            if not current or not line.startswith("- **"):
                continue

            field_match = _re.match(r"^- \*\*(.+?)\*\*: ?(.*)$", line)
            if not field_match:
                continue
            field = field_match.group(1).strip().lower()
            value = field_match.group(2).strip()
            if field == "status":
                current["statusLabel"] = value or "Active"
            elif field == "requested":
                current["requestedAt"] = value
            elif field == "updated":
                current["updatedAt"] = value
            elif field == "background":
                current["background"] = value
            elif field == "notes":
                current["notes"] = value

        if current:
            current["owner"] = infer_task_owner(
                current.get("title", ""),
                current.get("notes", ""),
                current.get("background", ""),
            )
            tasks.append(current)

        if not tasks:
            for raw_line in active_block.splitlines():
                line = raw_line.strip()
                if not line.startswith("- T-"):
                    continue
                m = _re.match(r"^- (T-\d+-\d+)\s+(.*?)\s+—\s+([🔄✅❌⏸️][^()]*)\s*(?:\(([^)]*)\))?\s*(.*)$", line)
                if not m:
                    continue
                status_label = (m.group(3) or "").strip()
                if "🔄" not in status_label:
                    continue
                meta = (m.group(4) or "").strip()
                notes = (m.group(5) or "").strip()
                requested_match = _re.search(r"requested\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", meta)
                updated_match = _re.search(r"updated\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", meta)
                task = {
                    "id": m.group(1),
                    "title": m.group(2).strip(),
                    "status": "active",
                    "statusLabel": status_label,
                    "notes": notes,
                    "background": meta,
                    "requestedAt": requested_match.group(1) if requested_match else None,
                    "updatedAt": updated_match.group(1) if updated_match else None,
                }
                task["owner"] = infer_task_owner(task.get("title", ""), task.get("notes", ""), task.get("background", ""))
                tasks.append(task)

        def _is_live_today(task: Dict[str, Any]) -> bool:
            status_label = str(task.get("statusLabel", ""))
            if "✅" in status_label or "❌" in status_label or "⏸️" in status_label:
                return False
            requested_at = str(task.get("requestedAt") or "")
            updated_at = str(task.get("updatedAt") or "")
            if requested_at.startswith(today_local) or updated_at.startswith(today_local):
                return True
            return not requested_at and not updated_at

        def _task_sort_key(task: Dict[str, Any]) -> str:
            return str(task.get("updatedAt") or task.get("requestedAt") or "")

        live_tasks = [task for task in tasks if _is_live_today(task)]
        live_tasks.sort(key=_task_sort_key, reverse=True)
        return live_tasks[:6]
    except Exception as exc:
        print(f"[warn] fetch_tracked_tasks failed: {exc}", file=sys.stderr)
        return []


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
        if message.lower() in {"standby", "idle", "standing by"} and str(entry.get("status") or "").lower() in {"done", "idle", "sent"}:
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

    if (
        jain_brain_feed
        and jain_brain_feed.get("active")
        and is_recent_ts(jain_brain_feed.get("updatedAt"), hours=6)
    ):
        push({
            "timestamp": jain_brain_feed.get("updatedAt") or utc_iso(),
            "direction": "jain→josh",
            "message": jain_brain_feed.get("objective") or "J.A.I.N standing by",
            "status": "active",
        })

    if (
        jaimes_brain_feed
        and jaimes_brain_feed.get("active")
        and is_recent_ts(jaimes_brain_feed.get("updatedAt"), hours=12)
    ):
        push({
            "timestamp": jaimes_brain_feed.get("updatedAt") or utc_iso(),
            "direction": "jaimes→josh",
            "message": jaimes_brain_feed.get("objective") or "JAIMES standing by",
            "status": "active",
        })

    merged.sort(key=lambda item: iso_to_dt(item.get("timestamp")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    compacted: List[Dict[str, Any]] = []
    seen_messages: set[tuple[str, str, str]] = set()
    for item in merged:
        key = (item.get("direction", ""), item.get("message", ""), item.get("status", ""))
        if key in seen_messages:
            continue
        seen_messages.add(key)
        compacted.append(item)
    return compacted[:24]


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


LOW_SIGNAL_BRAIN_OBJECTIVES = {
    "sent mission control open link",
    "mission control open link sent",
    "opened mission control link",
    "session startup completed",
    "startup completed",
    "standby",
    "idle",
    "heartbeat_ok",
}

LOW_SIGNAL_BRAIN_PREFIXES = (
    "sent mission control open link",
    "opened mission control",
    "session startup",
    "idle · last:",
)


def is_low_signal_brain_objective(objective: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(objective or "").strip().lower())
    if not normalized:
        return True
    if normalized in LOW_SIGNAL_BRAIN_OBJECTIVES:
        return True
    return any(normalized.startswith(prefix) for prefix in LOW_SIGNAL_BRAIN_PREFIXES)


def normalize_agent_brain_feed(feed: Dict[str, Any] | None, fallback_agent: str) -> Dict[str, Any]:
    raw = feed if isinstance(feed, dict) else {}
    updated_at = raw.get("updatedAt")
    reported_active = bool(raw.get("active"))
    stale = bool(updated_at) and not is_recent_ts(updated_at, hours=2)
    objective = str(raw.get("objective") or "").strip()
    low_signal = is_low_signal_brain_objective(objective)
    active = reported_active and not stale and not low_signal
    status = "stale" if stale and reported_active else str(raw.get("status") or "idle")
    if low_signal and status == "active":
        status = "idle"
    return {
        "agent": str(raw.get("agent") or fallback_agent),
        "active": active,
        "reportedActive": reported_active,
        "lowSignal": low_signal,
        "objective": objective,
        "status": status,
        "stale": stale,
        "updatedAt": updated_at,
        "messageReceived": raw.get("messageReceived"),
        "currentTool": raw.get("currentTool"),
        "model": raw.get("model"),
        "steps": raw.get("steps") if isinstance(raw.get("steps"), list) else [],
    }


def build_live_objectives(agent_feeds: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    def is_live(feed: Dict[str, Any]) -> bool:
        return bool(feed.get("active") and is_recent_ts(feed.get("updatedAt"), hours=2))

    def is_primary_operator_ready(feed: Dict[str, Any] | None) -> bool:
        if not feed:
            return False
        if is_live(feed):
            return True
        objective = str(feed.get("objective") or "").strip()
        if not objective or feed.get("stale"):
            return False
        return is_recent_ts(feed.get("updatedAt"), hours=12)

    def score(feed: Dict[str, Any]) -> tuple[int, float]:
        ts = iso_to_dt(feed.get("updatedAt"))
        return (1 if is_live(feed) else 0, ts.timestamp() if ts else 0.0)

    ordered = sorted(agent_feeds.values(), key=score, reverse=True)
    active_agents = [feed["agent"] for feed in ordered if is_live(feed)]
    dual_pair: List[str] = []
    josh_feed = next((feed for feed in agent_feeds.values() if feed.get("agent") == "JOSH 2.0"), None)
    jaimes_feed = next((feed for feed in agent_feeds.values() if feed.get("agent") == "JAIMES"), None)
    if is_live(josh_feed or {}) and is_primary_operator_ready(jaimes_feed):
        dual_pair = ["JOSH 2.0", "JAIMES"]
    elif len(active_agents) >= 2:
        remote = next((agent for agent in ["JAIMES", "J.A.I.N"] if agent in active_agents and agent != "JOSH 2.0"), None)
        if "JOSH 2.0" in active_agents and remote:
            dual_pair = ["JOSH 2.0", remote]
        else:
            dual_pair = active_agents[:2]
    return {
        "activeAgents": active_agents,
        "activeCount": len(active_agents),
        "primaryAgent": ordered[0]["agent"] if ordered else None,
        "dualMode": bool(dual_pair),
        "dualAgents": dual_pair,
        "agents": ordered,
    }


def apply_tracked_tasks_to_agent_feeds(
    agent_feeds: Dict[str, Dict[str, Any]],
    tracked_tasks: List[Dict[str, Any]],
    now_iso: str,
) -> Dict[str, Dict[str, Any]]:
    """Represent delegated active tasks as live agent objectives.

    JAIMES can be working through Hermes/J.A.I.N while its own brain-feed
    sidecar briefly says Standby. The dashboard hero should reflect the
    ownership in memory/tasks.md, not only the last brain-feed writer.
    """
    owner_to_key = {"JOSH 2.0": "josh", "J.A.I.N": "jain", "JAIMES": "jaimes"}
    merged = {key: dict(value) for key, value in agent_feeds.items()}
    for task in tracked_tasks:
        if str(task.get("status") or "active").lower() != "active":
            continue
        owner = str(task.get("owner") or "JOSH 2.0")
        key = owner_to_key.get(owner)
        if not key:
            continue
        current = merged.get(key, {})
        current_recent = is_recent_ts(current.get("updatedAt"), hours=2)
        # A fresh explicit brain-feed state wins over task-file inference.
        # This prevents old/stale tracked tasks from resurrecting noisy
        # "active" cards after an agent has reported idle/done.
        if current_recent:
            continue
        title = str(task.get("title") or current.get("objective") or "Working").strip()
        merged[key] = {
            **current,
            "agent": owner,
            "active": True,
            "reportedActive": bool(current.get("reportedActive")),
            "objective": title,
            "status": "active",
            "stale": False,
            "updatedAt": now_iso,
            "currentTool": current.get("currentTool") or "tracked task",
            "model": current.get("model"),
            "steps": [{"label": title, "status": "active", "tool": "tracked task"}],
            "taskBacked": True,
            "taskId": task.get("id"),
        }
    return merged


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
                    tokens = int(mb.get("totalTokens") or 0)
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
                            "dailyTokens": 0,
                            "costEstimated": False,
                            "subscriptionIncluded": cost == 0 and source == "codex",
                        }
                    by_model[name]["weeklyCost"] += cost
                    by_model[name]["sessionCost"] += cost
                    by_model[name]["totalTokens"] += tokens
                    if is_today:
                        by_model[name]["dailyCost"] += cost
                        by_model[name]["dailyTokens"] += tokens
                    if cost > 0:
                        by_model[name]["subscriptionIncluded"] = False

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
    """OpenRouter usage disabled.

    Josh moved JAIMES away from OpenRouter. Returning a stable disabled
    payload avoids stale-key 401 warnings and prevents dashboard refreshes
    from reading old OpenRouter secret files.
    """
    return {
        "daily": 0.0,
        "weekly": 0.0,
        "monthly": 0.0,
        "byok_daily": 0.0,
        "byok_weekly": 0.0,
        "byok_monthly": 0.0,
        "available": False,
        "disabled": True,
        "lastError": "disabled: OpenRouter usage fetch retired",
    }

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

        fixed_codex_monthly = 200.0

        def _is_local_row(row: Dict[str, Any]) -> bool:
            src = str(row.get("source", "")).lower()
            name = str(row.get("name", "")).lower()
            return row.get("isLocal") is True or src in {"ollama", "local"} or name.startswith("local/")

        def _is_codex_subscription_row(row: Dict[str, Any]) -> bool:
            src = str(row.get("source", "")).lower()
            name = str(row.get("name", "")).lower()
            return (
                src in {"codex", "codexbar"}
                or "openai-codex" in name
                or name.startswith("codex/")
                or row.get("subscriptionIncluded") is True
            )

        codex_rows = [r for r in breakdown if _is_codex_subscription_row(r)]
        local_rows = [r for r in breakdown if _is_local_row(r)]
        metered_rows = [r for r in breakdown if not _is_local_row(r) and not _is_codex_subscription_row(r)]
        codex_equivalent_weekly = round(sum(float(r.get("weeklyCost") or 0) for r in codex_rows), 6)
        codex_equivalent_daily = round(sum(float(r.get("dailyCost") or 0) for r in codex_rows), 6)
        codex_tokens_weekly = int(sum(int(r.get("totalTokens") or 0) for r in codex_rows))
        codex_tokens_daily = int(sum(int(r.get("dailyTokens") or 0) for r in codex_rows))
        # Metered rows already include synthetic OpenRouter and J.A.I.N API rows when
        # those lanes report usage, so do not add their source totals a second time.
        metered_weekly = round(sum(float(r.get("weeklyCost") or 0) for r in metered_rows), 6)
        metered_daily = round(sum(float(r.get("dailyCost") or 0) for r in metered_rows), 6)
        metered_source_totals = {
            "openrouterWeekly": round(or_weekly, 6),
            "openrouterDaily": round(or_daily, 6),
            "openrouterMonthly": round(or_monthly, 6),
            "jainApiWeekly": round(jain_api.get("weekly", 0), 6),
            "jainApiDaily": round(jain_api_daily, 6),
            "jainApiMonthly": round(jain_api_monthly, 6),
            "trackedRowsWeekly": metered_weekly,
            "trackedRowsDaily": metered_daily,
        }
        metered_monthly_projection = round(max(metered_weekly * (30 / 7), or_monthly, jain_api_monthly), 2)
        effective_monthly_projection = round(fixed_codex_monthly + metered_monthly_projection, 2)
        codex_value_projection = round(codex_equivalent_weekly * (30 / 7), 2)

        def _row_weekly_total(rows: List[Dict[str, Any]]) -> float:
            return round(sum(float(r.get("weeklyCost") or 0) for r in rows), 6)

        def _row_daily_total(rows: List[Dict[str, Any]]) -> float:
            return round(sum(float(r.get("dailyCost") or 0) for r in rows), 6)

        def _row_token_total(rows: List[Dict[str, Any]]) -> int:
            return int(sum(int(r.get("totalTokens") or 0) for r in rows))

        def _row_models(rows: List[Dict[str, Any]], limit: int = 3) -> List[str]:
            ordered = sorted(rows, key=lambda r: float(r.get("weeklyCost") or 0), reverse=True)
            return [str(r.get("name") or "unknown") for r in ordered[:limit]]

        def _monthly_from_weekly(weekly: float) -> float:
            return round(float(weekly or 0) * (30 / 7), 2)

        def _is_openai_api_row(row: Dict[str, Any]) -> bool:
            name = str(row.get("name", "")).lower()
            source = str(row.get("source", "")).lower()
            if _is_local_row(row) or _is_codex_subscription_row(row):
                return False
            return name.startswith("openai/") or name.startswith("gpt-") or source == "openclaw"

        def _is_gemini_row(row: Dict[str, Any]) -> bool:
            name = str(row.get("name", "")).lower()
            source = str(row.get("source", "")).lower()
            return "gemini" in name or "google" in source or "gemini" in source

        def _is_grok_or_router_row(row: Dict[str, Any]) -> bool:
            name = str(row.get("name", "")).lower()
            source = str(row.get("source", "")).lower()
            return "grok" in name or "openrouter" in name or "xai" in source or "openrouter" in source

        openai_api_rows = [r for r in metered_rows if _is_openai_api_row(r)]
        gemini_rows = [r for r in metered_rows if _is_gemini_row(r)]
        router_rows = [r for r in metered_rows if _is_grok_or_router_row(r)]
        known_metered_ids = {id(r) for r in openai_api_rows + gemini_rows + router_rows}
        other_metered_rows = [r for r in metered_rows if id(r) not in known_metered_ids]

        spend_lanes = [
            {
                "key": "codex_subscription",
                "label": "Codex subscription",
                "authPath": "OpenAI OAuth / subscription",
                "billing": "fixed",
                "status": "covered",
                "monthlyProjection": fixed_codex_monthly,
                "weeklyEquivalent": codex_equivalent_weekly,
                "dailyEquivalent": codex_equivalent_daily,
                "tokensWeekly": codex_tokens_weekly,
                "models": _row_models(codex_rows),
                "modelCount": len(codex_rows),
                "note": "Fixed subscription outlay; equivalent API value is informational only.",
            },
            {
                "key": "openai_api",
                "label": "OpenAI API",
                "authPath": "API key / metered",
                "billing": "metered",
                "status": "active" if _row_weekly_total(openai_api_rows) > 0 else "idle",
                "monthlyProjection": _monthly_from_weekly(_row_weekly_total(openai_api_rows)),
                "weeklyCost": _row_weekly_total(openai_api_rows),
                "dailyCost": _row_daily_total(openai_api_rows),
                "tokensWeekly": _row_token_total(openai_api_rows),
                "models": _row_models(openai_api_rows),
                "modelCount": len(openai_api_rows),
                "note": "Direct OpenAI API spend outside the subscription lane.",
            },
            {
                "key": "realtime_research",
                "label": "Grok / OpenRouter",
                "authPath": "BYOK / direct API",
                "billing": "metered",
                "status": "active" if max(_row_weekly_total(router_rows), or_weekly) > 0 else "idle",
                "monthlyProjection": round(max(_monthly_from_weekly(_row_weekly_total(router_rows)), or_monthly), 2),
                "weeklyCost": round(max(_row_weekly_total(router_rows), or_weekly), 6),
                "dailyCost": round(max(_row_daily_total(router_rows), or_daily), 6),
                "tokensWeekly": _row_token_total(router_rows),
                "models": _row_models(router_rows),
                "modelCount": len(router_rows),
                "note": "Freshness and realtime research lane; disabled when OpenRouter usage is unavailable.",
            },
            {
                "key": "gemini_automation",
                "label": "Gemini / automation",
                "authPath": "Google API / J.A.I.N scripts",
                "billing": "metered",
                "status": "active" if max(_row_weekly_total(gemini_rows), jain_api.get("weekly", 0)) > 0 else "idle",
                "monthlyProjection": round(max(_monthly_from_weekly(_row_weekly_total(gemini_rows)), jain_api_monthly), 2),
                "weeklyCost": round(max(_row_weekly_total(gemini_rows), jain_api.get("weekly", 0)), 6),
                "dailyCost": round(max(_row_daily_total(gemini_rows), jain_api_daily), 6),
                "tokensWeekly": _row_token_total(gemini_rows),
                "models": _row_models(gemini_rows),
                "modelCount": len(gemini_rows),
                "note": "Long-context and background analysis lane.",
            },
            {
                "key": "local_models",
                "label": "Local models",
                "authPath": "on-device",
                "billing": "free",
                "status": "available" if local_rows else "missing",
                "monthlyProjection": 0.0,
                "weeklyCost": 0.0,
                "dailyCost": 0.0,
                "tokensWeekly": _row_token_total(local_rows),
                "models": _row_models(local_rows),
                "modelCount": len(local_rows),
                "note": "No model bill; useful as a pressure-release lane when quality is enough.",
            },
        ]

        if other_metered_rows:
            spend_lanes.append({
                "key": "other_metered",
                "label": "Other metered",
                "authPath": "mixed API",
                "billing": "metered",
                "status": "active",
                "monthlyProjection": _monthly_from_weekly(_row_weekly_total(other_metered_rows)),
                "weeklyCost": _row_weekly_total(other_metered_rows),
                "dailyCost": _row_daily_total(other_metered_rows),
                "tokensWeekly": _row_token_total(other_metered_rows),
                "models": _row_models(other_metered_rows),
                "modelCount": len(other_metered_rows),
                "note": "Tracked metered usage that does not map cleanly to the primary lanes.",
            })

        posture = "calm"
        if metered_monthly_projection > 75:
            posture = "hot"
        elif metered_monthly_projection > 25:
            posture = "watch"
        elif metered_monthly_projection > 1:
            posture = "active"

        active_metered_lanes = [
            lane for lane in spend_lanes
            if lane.get("billing") == "metered" and float(lane.get("monthlyProjection") or 0) > 0.01
        ]
        lead_metered_lane = max(
            active_metered_lanes,
            key=lambda lane: float(lane.get("monthlyProjection") or 0),
            default=None,
        )
        lane_stack = [
            {
                "key": str(lane.get("key") or "lane"),
                "label": str(lane.get("label") or "Lane"),
                "billing": str(lane.get("billing") or "metered"),
                "status": str(lane.get("status") or "idle"),
                "monthlyProjection": round(float(lane.get("monthlyProjection") or 0), 2),
                "weeklyCost": round(float(lane.get("weeklyCost") or lane.get("weeklyEquivalent") or 0), 6),
                "modelCount": int(lane.get("modelCount") or 0),
                "models": list(lane.get("models") or [])[:2],
            }
            for lane in spend_lanes
        ]
        kiosk_summary = {
            "title": "Model spend cockpit",
            "posture": posture,
            "statusLabel": "Calm" if posture == "calm" else posture.title(),
            "trueOutlayMonthly": effective_monthly_projection,
            "fixedMonthly": fixed_codex_monthly,
            "meteredMonthly": metered_monthly_projection,
            "meteredWeekly": metered_weekly,
            "codexValueMonthly": codex_value_projection,
            "codexTokensWeekly": codex_tokens_weekly,
            "activeMeteredLaneCount": len(active_metered_lanes),
            "leadMeteredLane": lead_metered_lane.get("label") if lead_metered_lane else "Metered lanes idle",
            "laneStack": lane_stack,
            "mixLabel": f"{len(codex_rows)} Codex · {len(metered_rows)} API · {len(local_rows)} local",
        }

        spend_overview = {
            "subscriptionMonthly": fixed_codex_monthly,
            "effectiveMonthlyProjection": effective_monthly_projection,
            "meteredMonthlyProjection": metered_monthly_projection,
            "meteredWeekly": metered_weekly,
            "meteredDaily": metered_daily,
            "codexEquivalentWeekly": codex_equivalent_weekly,
            "codexEquivalentDaily": codex_equivalent_daily,
            "codexEquivalentMonthlyProjection": codex_value_projection,
            "codexTokensWeekly": codex_tokens_weekly,
            "codexTokensDaily": codex_tokens_daily,
            "localModelCount": len(local_rows),
            "meteredModelCount": len(metered_rows),
            "codexModelCount": len(codex_rows),
            "meteredSources": metered_source_totals,
            "posture": posture,
            "kioskSummary": kiosk_summary,
            "lanes": spend_lanes,
            "insights": [
                f"True projected outlay is ${effective_monthly_projection:.2f}/mo: ${fixed_codex_monthly:.0f} fixed Codex + ${metered_monthly_projection:.2f} metered.",
                f"Codex subscription is absorbing roughly ${codex_value_projection:.2f}/mo of API-equivalent usage.",
                f"Tracked metered spend is ${metered_weekly:.2f}/wk across {len(metered_rows)} metered model rows.",
            ],
            "note": "Codex subscription spend is fixed at $200/mo. CodexBar costs are shown as subscription-equivalent value, not extra metered spend; metered projection covers API/BYOK/direct model calls outside the subscription.",
        }

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
            "spendOverview": spend_overview,
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


def _calendar_events_command() -> tuple[List[str], str]:
    account = os.environ.get("MC_CALENDAR_ACCOUNT", "jcubell16@gmail.com")
    secure_gog = Path.home() / "scripts" / "gog_secure.sh"
    gog_bin = os.environ.get("GOG_BIN") or (
        str(secure_gog) if secure_gog.exists() and os.access(secure_gog, os.X_OK) else shutil.which("gog")
    )
    args = [
        "calendar", "events", "primary",
        "--account", account,
        "--from", "today",
        "--days", "3",
        "--max", "10",
        "-j", "--results-only",
    ]
    if gog_bin:
        return [gog_bin, *args], "local gog"
    # Mission Control often refreshes from JAIMES, while gog lives on JOSH 2.0.
    # Prefer JOSH's secure wrapper so non-interactive jobs can read the file keyring
    # password locally without exposing it over SSH or committing it to this repo.
    remote_gog = os.environ.get("JOSH_GOG_BIN", "~/scripts/gog_secure.sh")
    remote = " ".join([remote_gog, *[shlex.quote(str(part)) for part in args]])
    return [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
        "josh2.0@100.114.50.48", remote,
    ], "JOSH 2.0 gog"


def fetch_upcoming_events(limit: int = 3) -> List[Dict[str, Any]]:
    fetch_upcoming_events._status = {"status": "unknown", "message": "Unknown"}  # type: ignore[attr-defined]
    source = "unknown"
    try:
        cmd, source = _calendar_events_command()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=18,
        )
        fetch_upcoming_events._status = {"status": "ok", "message": f"Connected via {source}"}  # type: ignore[attr-defined]
    except FileNotFoundError as exc:
        fetch_upcoming_events._status = {"status": "unavailable", "message": f"Calendar CLI unavailable: {exc.filename or 'command missing'}"}  # type: ignore[attr-defined]
        print(f"[warn] calendar command missing via {source}: {exc}", file=sys.stderr)
        return []
    except subprocess.TimeoutExpired:
        fetch_upcoming_events._status = {"status": "timeout", "message": f"Calendar fetch timed out via {source}"}  # type: ignore[attr-defined]
        print(f"[warn] calendar fetch timed out via {source}", file=sys.stderr)
        return []
    except subprocess.CalledProcessError as exc:
        err = ((exc.stderr or '') + '\n' + (exc.stdout or '')).strip()
        lower = err.lower()
        if 'no auth for calendar' in lower or 'invalid_grant' in lower or 'expired or revoked' in lower:
            fetch_upcoming_events._status = {"status": "auth_expired", "message": "Calendar re-auth required"}  # type: ignore[attr-defined]
            print(f"[info] gog calendar auth needs re-login via {source}", file=sys.stderr)
            return []
        fetch_upcoming_events._status = {"status": "error", "message": f"Calendar fetch failed via {source}"}  # type: ignore[attr-defined]
        print(f"[warn] gog calendar list failed via {source}: {err}", file=sys.stderr)
        return []
    try:
        raw = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        fetch_upcoming_events._status = {"status": "error", "message": "Calendar JSON invalid"}  # type: ignore[attr-defined]
        print(f"[warn] gog calendar JSON parse failed: {exc}", file=sys.stderr)
        return []
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
    except FileNotFoundError:
        return {"name": "Airpoint", "status": "unknown", "detail": "airpoint CLI missing"}
    except subprocess.CalledProcessError as exc:
        return {"name": "Airpoint", "status": "attention", "detail": f"Status check failed ({exc.returncode})"}
    except json.JSONDecodeError:
        return {"name": "Airpoint", "status": "attention", "detail": "Status JSON invalid"}


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
    # JOSH 2.0 crontab — prefer the real JOSH host when this runs from JAIMES;
    # fall back to local crontab when Mission Control refresh runs on JOSH itself.
    josh_listing = ""
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "josh2.0@100.114.50.48", "crontab -l 2>/dev/null || true"],
            capture_output=True, text=True, timeout=8
        )
        if result.returncode == 0 and result.stdout.strip():
            josh_listing = result.stdout
    except (subprocess.CalledProcessError, OSError, PermissionError, subprocess.TimeoutExpired):
        josh_listing = ""
    if not josh_listing:
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True)
            josh_listing = result.stdout
        except (subprocess.CalledProcessError, OSError, PermissionError):
            josh_listing = ""
    # J.A.I.N — single batched SSH call for crontab, Sorare tails, and Hermes jobs.
    import datetime as _dt
    import re as _re
    from zoneinfo import ZoneInfo

    now_et = _dt.datetime.now(ZoneInfo("America/New_York"))
    today_str = now_et.strftime('%Y-%m-%d')

    jain_listing = ""
    hermes_jobs: dict[str, dict[str, Any]] = {}
    jain_verified_runs: dict[str, dict[str, Any]] = {}
    josh_verified_runs: dict[str, dict[str, Any]] = {}
    try:
        josh_verify_cmd = r"""python3 - <<'PY'
import datetime as dt, json, pathlib
from zoneinfo import ZoneInfo
et = ZoneInfo('America/New_York')
jobs = {
    'Mission Control Refresh': '/Users/josh2.0/.openclaw/workspace/logs/mission-control-cron.log',
    'Brain Feed Server': '/Users/josh2.0/.openclaw/workspace/logs/brain_feed_server.log',
    'Chiro Invite Sync': '/Users/josh2.0/.openclaw/workspace/logs/chiro_invite_sync.log',
    'J.A.I.N Silence Detector': '/Users/josh2.0/.openclaw/workspace/logs/jain_silence_detector.log',
    'Sorare Cookie Freshness': '/Users/josh2.0/.openclaw/workspace/logs/sorare_cookie_freshness.log',
    'J.A.I.N Medic': '/Users/josh2.0/.openclaw/workspace/logs/jain_medic.log',
    'Sorare Cookie Auto-Refresh': '/Users/josh2.0/.openclaw/workspace/logs/sorare_cookie_autorefresh.log',
}
out = {}
for name, raw in jobs.items():
    p = pathlib.Path(raw)
    info = {'verifiedToday': False}
    if p.exists():
        mtime = dt.datetime.fromtimestamp(p.stat().st_mtime, tz=dt.timezone.utc)
        info['lastRun'] = mtime.isoformat()
        info['verifiedToday'] = mtime.astimezone(et).strftime('%Y-%m-%d') == dt.datetime.now(et).strftime('%Y-%m-%d')
    out[name] = info
print(json.dumps(out))
PY"""
        jv = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "josh2.0@100.114.50.48", josh_verify_cmd],
            capture_output=True, text=True, timeout=8
        )
        if jv.returncode == 0:
            josh_verified_runs = json.loads(jv.stdout.strip() or "{}")
    except Exception:
        pass
    if not josh_verified_runs:
        try:
            et = ZoneInfo("America/New_York")
            today_local = dt.datetime.now(et).strftime('%Y-%m-%d')
            local_jobs = {
                'Mission Control Refresh': Path('/Users/josh2.0/.openclaw/workspace/logs/mission-control-cron.log'),
                'Brain Feed Server': Path('/Users/josh2.0/.openclaw/workspace/logs/brain_feed_server.log'),
                'Chiro Invite Sync': Path('/Users/josh2.0/.openclaw/workspace/logs/chiro_invite_sync.log'),
                'J.A.I.N Silence Detector': Path('/Users/josh2.0/.openclaw/workspace/logs/jain_silence_detector.log'),
                'Sorare Cookie Freshness': Path('/Users/josh2.0/.openclaw/workspace/logs/sorare_cookie_freshness.log'),
                'J.A.I.N Medic': Path('/Users/josh2.0/.openclaw/workspace/logs/jain_medic.log'),
                'Sorare Cookie Auto-Refresh': Path('/Users/josh2.0/.openclaw/workspace/logs/sorare_cookie_autorefresh.log'),
            }
            for name, path in local_jobs.items():
                info = {'verifiedToday': False}
                if path.exists():
                    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
                    info['lastRun'] = mtime.isoformat()
                    info['verifiedToday'] = mtime.astimezone(et).strftime('%Y-%m-%d') == today_local
                josh_verified_runs[name] = info
        except Exception:
            pass
    try:
        jain_batch_cmd = (
            "echo '===CRON==='; crontab -l 2>/dev/null || true; "
            f"echo '===SORAREMISSIONS==='; tail -8 /Users/jc_agent/scripts/logs/sorare_missions.log 2>/dev/null || echo ''; "
            f"echo '===SORARELINEUPS==='; tail -8 /Users/jc_agent/scripts/logs/sorare_lineups.log 2>/dev/null || echo ''; "
            f"echo '===HERMESJOBS==='; cat /Users/jc_agent/.hermes/cron/jobs.json 2>/dev/null || echo '{{}}'"
        )
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "jc_agent@100.121.89.84", jain_batch_cmd],
            capture_output=True, text=True, timeout=12
        )
        if r.returncode == 0:
            raw = r.stdout
            parts = raw.split("===SORAREMISSIONS===")
            jain_listing = parts[0].replace("===CRON===", "").strip() if parts else ""
            rest = parts[1] if len(parts) > 1 else ""
            lineups_split = rest.split("===SORARELINEUPS===")
            sorare_missions_tail = lineups_split[0].strip()
            rest2 = lineups_split[1] if len(lineups_split) > 1 else ""
            hermes_split = rest2.split("===HERMESJOBS===")
            sorare_lineups_tail = hermes_split[0].strip()
            hermes_jobs_raw = hermes_split[1].strip() if len(hermes_split) > 1 else "{}"
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

    try:
        jain_verify_cmd = rf"""python3 - <<'PY'
import datetime as dt
import json
import pathlib
import re
from zoneinfo import ZoneInfo

today = {today_str!r}
et = ZoneInfo('America/New_York')
jobs = {{
    'Sorare Sheet Updater': '/Users/jc_agent/.openclaw/workspace/logs/sorare_sheet_updater.log',
    'Sorare Daily Prep': '/Users/jc_agent/.openclaw/workspace/logs/sorare_daily_prep.log',
    'Sorare Champion Submit': '/Users/jc_agent/.openclaw/workspace/logs/sorare_missions.log',
    'Sorare Canonical Reflector': '/Users/jc_agent/.openclaw/workspace/logs/sorare_canonical_reflector.log',
    'Sorare Deadline Guard': '/Users/jc_agent/.openclaw/workspace/logs/sorare_deadline_guard.log',
}}
ops_runs_path = pathlib.Path('/Users/jc_agent/.openclaw/workspace/data/sorare-ops/latest-runs.json')
ops_submissions_path = pathlib.Path('/Users/jc_agent/.openclaw/workspace/data/sorare-ops/latest-submissions.json')

def load_json_list(path: pathlib.Path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []

latest_runs = load_json_list(ops_runs_path)
latest_submissions = load_json_list(ops_submissions_path)

def is_today(ts: str | None) -> bool:
    if not ts:
        return False
    try:
        parsed = dt.datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return parsed.astimezone(et).strftime('%Y-%m-%d') == today
    except Exception:
        return False

def latest_matching_submission(kind: str, source_lane: str):
    for item in latest_submissions:
        if not isinstance(item, dict):
            continue
        if item.get('submission_kind') != kind or item.get('source_lane') != source_lane:
            continue
        if is_today(item.get('created_at') or item.get('updated_at')):
            return item
    return None

def read_tail(path: pathlib.Path, lines: int = 160) -> str:
    try:
        return '\n'.join(path.read_text(errors='ignore').splitlines()[-lines:])
    except Exception:
        return ''

out = {{}}
for name, raw_path in jobs.items():
    path = pathlib.Path(raw_path)
    info = {{'verifiedToday': False}}
    if path.exists():
        mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
        info['lastRun'] = mtime.isoformat()
        tail = read_tail(path)
        mtime_today = mtime.astimezone(et).strftime('%Y-%m-%d') == today
        if name == 'Sorare Sheet Updater':
            info['verifiedToday'] = f'✅ Row appended for {{today}}' in tail
        elif name == 'Sorare Daily Prep':
            info['verifiedToday'] = today in tail and 'Sorare daily prep complete' in tail
        elif name == 'Sorare Champion Submit':
            structured = latest_matching_submission('lineup', 'live_submitter')
            if structured and structured.get('status') in ('submitted', 'unchanged'):
                info['lastRun'] = structured.get('updated_at') or structured.get('created_at') or info.get('lastRun')
                info['verifiedToday'] = True
            else:
                info['verifiedToday'] = mtime_today and 'MISSIONS COMPLETE' in tail
        elif name == 'Sorare Canonical Reflector':
            matches = re.findall(r'\[(\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}:\d{{2}})\].*?sorare_canonical_reflector\\.py — done', tail)
            if matches:
                last_local = dt.datetime.strptime(matches[-1], '%Y-%m-%d %H:%M:%S').replace(tzinfo=et)
                info['lastRun'] = last_local.astimezone(dt.timezone.utc).isoformat()
                info['verifiedToday'] = matches[-1].startswith(today)
            else:
                info['verifiedToday'] = mtime_today
        elif name == 'Sorare Deadline Guard':
            info['verifiedToday'] = mtime_today and bool(tail.strip())
    out[name] = info

print(json.dumps(out))
PY"""
        vr = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "jc_agent@100.121.89.84", jain_verify_cmd],
            capture_output=True, text=True, timeout=12
        )
        if vr.returncode == 0:
            jain_verified_runs = json.loads(vr.stdout.strip() or "{}")
    except Exception:
        pass


    def parse_schedule_time(schedule_str: str) -> tuple[int, int] | None:
        m = _re.search(r'(\d{1,2})(?::(\d{2}))?\s*(AM|PM)', schedule_str, _re.IGNORECASE)
        if not m:
            return None
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ap = m.group(3).upper()
        if ap == 'PM' and hour != 12:
            hour += 12
        elif ap == 'AM' and hour == 12:
            hour = 0
        return hour, minute

    def schedule_today_meta(schedule_str: str) -> dict[str, Any]:
        raw = str(schedule_str or "").strip()
        text = raw.lower()
        if not text:
            return {"todayRelevant": True, "kind": "unknown"}
        if text.startswith("every ") or text.startswith("hourly"):
            return {"todayRelevant": True, "kind": "recurring"}
        if text.startswith("daily"):
            return {"todayRelevant": True, "kind": "daily"}
        if "weekdays" in text and "weekends" in text:
            return {"todayRelevant": True, "kind": "calendar_split"}
        if "weekdays" in text:
            return {"todayRelevant": now_et.weekday() < 5, "kind": "weekday"}
        if "weekends" in text:
            return {"todayRelevant": now_et.weekday() >= 5, "kind": "weekend"}
        matched_days = sorted({
            idx for token, idx in DAY_NAME_TO_INDEX.items()
            if _re.search(rf"\b{_re.escape(token)}\b", text)
        })
        if matched_days:
            return {
                "todayRelevant": now_et.weekday() in matched_days,
                "kind": "weekly",
                "days": matched_days,
            }
        return {"todayRelevant": True, "kind": "unknown"}

    rows = []
    for target in CRON_TARGETS:
        is_jain = target.get('jain', False)
        listing = jain_listing if is_jain else josh_listing
        source = target.get('source', 'cron')
        hermes_job = hermes_jobs.get(target.get('hermesName', '')) if source == 'hermes' else None
        jain_verified = jain_verified_runs.get(target['name']) if is_jain else None
        josh_verified = josh_verified_runs.get(target['name']) if not is_jain else None
        present = bool(hermes_job) if source == 'hermes' else target['pattern'] in listing
        sched_meta = schedule_today_meta(target.get('schedule', ''))
        today_relevant = bool(sched_meta.get('todayRelevant', True))
        source_label = 'Hermes' if source == 'hermes' else 'J.A.I.N Cron' if is_jain else 'Josh Local Cron'

        # Compute runStatus for daily jobs
        sched = target.get('schedule', '')
        run_status = None  # 'done' | 'missed' | 'upcoming' | None
        last_run = None
        if hermes_job:
            _hlast = hermes_job.get('last_run_at')
            if _hlast and hermes_job.get('last_status') == 'ok':
                last_run = _hlast
        if not last_run and jain_verified and jain_verified.get('lastRun'):
            last_run = jain_verified.get('lastRun')
        if not last_run and josh_verified and josh_verified.get('lastRun'):
            last_run = josh_verified.get('lastRun')
        last_run_today = False
        if last_run:
            try:
                _last_run_dt = _dt.datetime.fromisoformat(str(last_run).replace('Z', '+00:00')).astimezone(ZoneInfo("America/New_York"))
                last_run_today = _last_run_dt.strftime('%Y-%m-%d') == today_str
            except Exception:
                last_run_today = False
        verified_today = bool(
            (jain_verified and jain_verified.get('verifiedToday')) or
            (josh_verified and josh_verified.get('verifiedToday'))
        )

        is_jaimes_agent = target.get('agent') == 'JAIMES'
        can_verify_run = bool(last_run) or hermes_job is not None or bool(jain_verified) or bool(josh_verified)
        schedule_time = parse_schedule_time(sched)
        if today_relevant and sched_meta.get('kind') in {'daily', 'weekly', 'weekday', 'weekend'}:
            if schedule_time is not None:
                sched_hour, sched_min = schedule_time
                now_hour = now_et.hour
                now_min = now_et.minute
                if last_run_today or verified_today:
                    run_status = 'done'
                elif is_jaimes_agent and not present:
                    # JAIMES jobs run via Hermes — show paused if no last_run confirmed today
                    run_status = 'paused'
                elif now_hour > sched_hour or (now_hour == sched_hour and now_min >= sched_min + 10):
                    run_status = 'missed' if can_verify_run else 'due'
                else:
                    run_status = 'upcoming'
        elif today_relevant and sched_meta.get('kind') in {'recurring', 'calendar_split'}:
            run_status = 'active' if present else 'due'
        if source == 'hermes' and hermes_job and not hermes_job.get('enabled', True):
            row_status = 'paused'
        elif last_run_today or verified_today:
            row_status = 'ok'
        else:
            row_status = 'ok' if present else 'paused'

        # Hermes persists the last job result until the next scheduled run. Treat
        # old failures as historical context, not active Mission Control cron
        # errors, otherwise a Friday/Saturday empty-response can keep Sunday
        # dashboard health red even when the next run is still upcoming.
        hermes_last_failed = bool(
            hermes_job and hermes_job.get('last_status') not in {None, '', 'ok'}
        )
        hermes_error_is_current = bool(
            hermes_last_failed
            and today_relevant
            and last_run_today
        )
        row = {
            'name': target['name'],
            'schedule': target['schedule'],
            'description': target.get('description', ''),
            'category': target.get('category', 'Other'),
            'agent': target.get('agent', 'JOSH 2.0'),
            'status': row_status,
            'source': source,
            'sourceLabel': source_label,
            'todayRelevant': today_relevant,
            'errors': 1 if hermes_error_is_current else 0,
            'lastError': hermes_job.get('last_error') if hermes_error_is_current and hermes_job else None,
        }
        if hermes_last_failed and not hermes_error_is_current and hermes_job:
            row['lastHistoricalError'] = hermes_job.get('last_error')
            row['lastHistoricalErrorAt'] = hermes_job.get('last_run_at')
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

            for run in runs:
                t_str = run['time']
                try:
                    t = _dt.datetime.strptime(t_str, "%I:%M %p").replace(
                        year=now_et.year, month=now_et.month, day=now_et.day,
                        tzinfo=_dt.timezone(_dt.timedelta(hours=-4))
                    )
                    run['past'] = now_et >= t
                    run['done'] = False
                except Exception:
                    run['past'] = False
                    run['done'] = False
            row['multiRun'] = {'runs': runs}
        rows.append(row)
    return rows

def build_devices() -> List[Dict[str, str]]:
    return [airpoint_status()]


def fetch_visual_canaries() -> Dict[str, Any]:
    data = load_json_file(DATA_DIR / "mission-control-canaries.json", {})
    if isinstance(data, dict) and data:
        return data
    return {
        "ok": False,
        "status": "unknown",
        "summary": "Canary data pending",
        "checkedAt": None,
        "checks": [],
    }


def fetch_capability_canary() -> Dict[str, Any]:
    data = load_json_file(CAPABILITY_CANARY_PATH, {})
    if isinstance(data, dict) and data:
        checks = data.get("checks") if isinstance(data.get("checks"), list) else []
        counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
        data["checks"] = checks
        data["counts"] = counts
        return data
    return {
        "ok": False,
        "status": "unknown",
        "summary": "Capability canary pending",
        "checkedAt": None,
        "counts": {},
        "checks": [],
    }


def fetch_sorare_ml_cockpit() -> Dict[str, Any]:
    artifacts = [
        Path("/Users/jc_agent/sorare_ml/artifacts/segment_scoring_policy_backtest_2026-04-28.json"),
        Path("/Users/jc_agent/sorare_ml/artifacts/gw11_validation_summary_v3.json"),
    ]
    policy = load_json_file(artifacts[0], {}) if artifacts[0].exists() else {}
    validation = load_json_file(artifacts[1], {}) if artifacts[1].exists() else {}
    return {
        "status": "active",
        "owner": "JAIMES",
        "lane": "Sorare MLB",
        "summary": "JAIMES owns production scoring policy",
        "policyMae": policy.get("policy_mae") or policy.get("mae") or 6.808,
        "baselineMae": policy.get("season_avg_mae") or policy.get("baseline_mae") or 6.824,
        "rfMae": policy.get("rf_mae") or 7.186,
        "validationStatus": validation.get("status") or "gw11 outcome pending",
        "nextCheck": "After GW11 resolves on 2026-05-01",
        "artifacts": [str(path) for path in artifacts if path.exists()],
    }


def fetch_voice_router_status() -> Dict[str, Any]:
    router_path = ROOT / "telegram_voice_task_router.py"
    return {
        "status": "ready" if router_path.exists() else "planned",
        "summary": "Telegram voice notes can become structured tasks",
        "entrypoint": str(router_path),
        "modes": ["task", "calendar", "jaimes", "mission-control"],
    }


def fetch_ops_inbox_status(calendar_health: Dict[str, Any] | None, crons: List[Dict[str, Any]]) -> Dict[str, Any]:
    cal_ok = (calendar_health or {}).get("status") == "ok"
    due = [c for c in crons if c.get("todayRelevant") and c.get("status") != "paused" and c.get("runStatus") == "missed"]
    return {
        "status": "clear" if cal_ok and not due else "attention",
        "summary": "Unified Gmail/Calendar/Drive/Tasks command queue foundation",
        "calendar": "connected" if cal_ok else "needs attention",
        "jobIssues": len(due),
        "sources": ["calendar", "gmail", "drive", "tasks"],
    }


def fetch_joshex_patch_status(now_iso: str) -> Dict[str, Any]:
    """Expose Mission Control patch state without creating a live-agent slot."""
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.splitlines()
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except Exception as exc:
        return {
            "status": "unknown",
            "summary": "Patch state unavailable",
            "detail": str(exc)[:120],
            "dirtyCount": 0,
            "head": "unknown",
            "updatedAt": now_iso,
        }

    volatile = {"data/jaimes-brain-feed.json"}
    files = [line[3:] for line in status if len(line) >= 4]
    source_files = [path for path in files if path not in volatile]
    dirty_count = len(source_files)
    state = "clean" if dirty_count == 0 else "pending"
    summary = f"Clean at {head}" if dirty_count == 0 else f"{dirty_count} source file(s) changed"
    return {
        "status": state,
        "summary": summary,
        "detail": "Volatile brain telemetry ignored" if files and dirty_count == 0 else "Ready for validation" if dirty_count else "No source patch pending",
        "dirtyCount": dirty_count,
        "files": source_files[:6],
        "head": head,
        "updatedAt": now_iso,
    }


def fetch_personal_codex_status(now_iso: str) -> Dict[str, Any]:
    """Load local JOSHeX visibility without promoting it as an agent."""
    fallback: Dict[str, Any] = {
        "status": "idle",
        "objective": "JOSHeX contribution lane ready",
        "validation": "pending",
        "actionRequired": [],
        "recentActivity": [],
        "capabilities": ["inspect", "edit", "validate", "prepare patches"],
        "updatedAt": now_iso,
    }
    raw = load_json_file(PERSONAL_CODEX_PATH, fallback)
    if not isinstance(raw, dict):
        raw = fallback
    data = {**fallback, **raw}
    data["agentSlot"] = False
    data["promoteToBrainFeed"] = False
    data["updatedAt"] = data.get("updatedAt") or now_iso
    data["patchStatus"] = fetch_joshex_patch_status(now_iso)
    for key in ["actionRequired", "recentActivity", "capabilities"]:
        if not isinstance(data.get(key), list):
            data[key] = []
    patch_summary = data["patchStatus"].get("summary")
    if patch_summary:
        data["recentActivity"] = [{"event": f"Patch feed: {patch_summary}", "time": now_iso}, *data["recentActivity"]]
    return data


def build_capability_stack(
    visual_canaries: Dict[str, Any],
    sorare_ml: Dict[str, Any],
    voice_router: Dict[str, Any],
    ops_inbox: Dict[str, Any],
    capability_canary: Dict[str, Any] | None = None,
    personal_codex: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    stack = [
        {
            "id": "visual-canaries",
            "name": "Visual Canaries",
            "status": visual_canaries.get("status") or ("ok" if visual_canaries.get("ok") else "attention"),
            "summary": visual_canaries.get("summary") or "Mission Control guardrails",
            "detail": f"{len([c for c in visual_canaries.get('checks', []) if c.get('ok')])}/{len(visual_canaries.get('checks', []))} checks passing",
        },
        {
            "id": "sorare-ml-cockpit",
            "name": "Sorare ML Cockpit",
            "status": sorare_ml.get("status") or "active",
            "summary": sorare_ml.get("summary"),
            "detail": f"Policy MAE {sorare_ml.get('policyMae')} vs baseline {sorare_ml.get('baselineMae')}",
        },
        {
            "id": "voice-router",
            "name": "Voice Router",
            "status": voice_router.get("status") or "planned",
            "summary": voice_router.get("summary"),
            "detail": "Telegram voice -> tasks / calendar / JAIMES",
        },
        {
            "id": "ops-inbox",
            "name": "Ops Inbox",
            "status": ops_inbox.get("status") or "planned",
            "summary": ops_inbox.get("summary"),
            "detail": f"Calendar {ops_inbox.get('calendar')} · {ops_inbox.get('jobIssues', 0)} job issue(s)",
        },
    ]
    capability = capability_canary or {}
    if capability:
        counts = capability.get("counts") or {}
        stack.append({
            "id": "capability-canary",
            "name": "Capability Canary",
            "status": capability.get("status") or "unknown",
            "summary": capability.get("summary") or "OpenClaw capability drift check",
            "detail": f"{counts.get('ok', 0)} ok · {counts.get('warn', 0)} warn · {counts.get('error', 0)} err",
            "source": "capabilityCanary",
        })
    pc = personal_codex or {}
    if pc:
        stack.append({
            "id": "personal-codex",
            "name": "JOSHeX",
            "status": pc.get("status") or "idle",
            "summary": pc.get("objective") or "Local Codex contribution visibility",
            "detail": f"JOSHeX: {pc.get('validation') or 'validation pending'}",
            "source": "personalCodex",
        })
    return stack


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
            "label": "Recently edited: " + ", ".join(wf[:2]),
            "status": "done",
            "elapsedSecs": 0,
            "tool": "code",
            "model": "recent",
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

def build_action_required(
    now_iso: str,
    calendar_health: Dict[str, Any] | None,
    crons: List[Dict[str, Any]],
    moltworld_data: Dict[str, Any] | None,
    personal_codex: Dict[str, Any] | None = None,
) -> List[Dict[str, str]]:
    """High-signal one-stop-shop alerts for Josh-facing ops health."""
    items: List[Dict[str, str]] = []

    cal = calendar_health or {}
    cal_status = str(cal.get("status") or "unknown").lower()
    cal_msg = str(cal.get("message") or "Calendar lane unavailable")
    if cal_status not in {"ok", "green", "healthy"}:
        title = "Calendar auth needs refresh" if "auth" in cal_msg.lower() else f"Calendar issue: {cal_msg}"
        items.append({"priority": "high", "title": title, "url": "#calendar"})

    missed = [c for c in crons if c.get("todayRelevant") and c.get("status") != "paused" and c.get("runStatus") == "missed"]
    due = [c for c in crons if c.get("todayRelevant") and c.get("status") != "paused" and c.get("runStatus") == "due"]
    errored = [c for c in crons if c.get("status") != "paused" and ((c.get("errors") or 0) > 0 or c.get("status") == "error")]
    if missed:
        sample = ", ".join(c.get("name", "job") for c in missed[:3])
        items.append({"priority": "high", "title": f"{len(missed)} scheduled job(s) missed: {sample}", "url": "#jobs"})
    if errored:
        sample = ", ".join(c.get("name", "job") for c in errored[:3])
        items.append({"priority": "high", "title": f"{len(errored)} job error(s): {sample}", "url": "#jobs"})
    stale_verified: List[Dict[str, Any]] = []
    now_dt = dt.datetime.now(dt.timezone.utc)
    for cron in crons:
        if not cron.get("todayRelevant") or cron.get("status") == "paused":
            continue
        sched_text = str(cron.get("schedule") or "").lower()
        if sched_text.startswith("every ") or sched_text.startswith("hourly"):
            continue
        last_run = cron.get("lastRun")
        if not last_run:
            continue
        try:
            last_dt = dt.datetime.fromisoformat(str(last_run).replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=dt.timezone.utc)
            age_hours = (now_dt - last_dt.astimezone(dt.timezone.utc)).total_seconds() / 3600
        except Exception:
            continue
        if cron.get("runStatus") == "active" and age_hours > 3:
            stale_verified.append(cron)
    if stale_verified:
        sample = ", ".join(c.get("name", "job") for c in stale_verified[:3])
        items.append({"priority": "medium", "title": f"{len(stale_verified)} active job(s) stale >3h: {sample}", "url": "#jobs"})

    mw = moltworld_data or {}
    mw_status = str(mw.get("status") or "unknown").lower()
    mw_error = str(mw.get("last_error") or mw.get("lastError") or "")
    if mw.get("stale") or mw_status in {"auth_error", "server_down", "observe_error", "offline", "unknown"}:
        if "registration failed" in mw_error.lower() or mw_status == "auth_error":
            title = "MoltWorld API key missing; agent already exists"
        elif mw_error:
            title = f"MoltWorld stale: {mw_error[:90]}"
        else:
            title = f"MoltWorld status: {mw_status}"
        items.append({"priority": "medium", "title": title, "url": "#moltworld"})

    for item in (personal_codex or {}).get("actionRequired") or []:
        if isinstance(item, str):
            title = item
            priority = "medium"
            url = "#personal-codex"
        elif isinstance(item, dict):
            title = str(item.get("title") or item.get("message") or "JOSHeX needs review")
            priority = str(item.get("priority") or "medium")
            url = str(item.get("url") or "#personal-codex")
        else:
            continue
        if title.strip():
            items.append({"priority": priority, "title": f"JOSHeX: {title.strip()}", "url": url})

    return items[:8]



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
    personal_codex: Dict[str, Any] | None = None,
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

    for activity in (personal_codex or {}).get("recentActivity") or []:
        if isinstance(activity, str):
            event = activity
            when = (personal_codex or {}).get("updatedAt") or now_iso
        elif isinstance(activity, dict):
            event = str(activity.get("event") or activity.get("title") or activity.get("message") or "")
            when = str(activity.get("time") or activity.get("updatedAt") or (personal_codex or {}).get("updatedAt") or now_iso)
        else:
            continue
        if event.strip():
            items.append({"time": when, "event": f"JOSHeX: {event.strip()}"})

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
    deduped: List[Dict[str, str]] = []
    seen_events: set[str] = set()
    for item in items:
        event = item.get("event", "")
        if event in seen_events:
            continue
        seen_events.add(event)
        deduped.append(item)
    # Sort most recent first
    deduped.sort(key=lambda x: x.get("time", ""), reverse=True)
    return deduped[:6]


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
MOLTWORLD_CACHE_PATH = ROOT.parent / "data" / "moltworld-cache.json"

def fetch_moltworld_data() -> Dict[str, Any]:
    """Build MoltWorld dashboard data without letting the old v1 API add noise."""
    state_data: Dict[str, Any] = {}
    if MOLTWORLD_STATE_PATH.exists():
        try:
            state_data = json.loads(MOLTWORLD_STATE_PATH.read_text())
        except json.JSONDecodeError:
            pass

    existing_data: Dict[str, Any] = {}
    current_data_path = ROOT.parent / "data" / "moltworld-data.json"
    if current_data_path.exists():
        try:
            existing_data = json.loads(current_data_path.read_text())
        except json.JSONDecodeError:
            pass

    balance_data: Dict[str, Any] = {}
    balance_error = ""
    try:
        balance_url = f"{MOLTWORLD_API_BASE}/api/agents/balance?agentId={MOLTWORLD_AGENT_ID}"
        with urllib.request.urlopen(balance_url, timeout=5) as resp:
            balance_data = json.load(resp)
    except Exception as exc:
        balance_error = str(exc)

    balance = balance_data.get("balance", {}) if isinstance(balance_data, dict) else {}
    tokenomics = (balance_data.get("tokenomics", {}).get("projection", {}) if isinstance(balance_data, dict) else {})
    is_online = bool(balance.get("isOnline", existing_data.get("is_online", False)))
    status = "online" if is_online else existing_data.get("status", "offline")

    payload = {
        "sim_balance":        float(balance.get("sim", existing_data.get("sim_balance", 0.0))),
        "total_earned":       float(balance.get("totalEarned", existing_data.get("total_earned", 0.0))),
        "online_time":        str(balance.get("totalOnlineTime", existing_data.get("online_time", "0h 0m"))),
        "is_online":          is_online,
        "status":             status,
        "earning_rate":       str(balance.get("earningRate", existing_data.get("earning_rate", "0 SIM/hour"))),
        "position_x":         int(state_data.get("x", existing_data.get("position_x", 0))),
        "position_y":         int(state_data.get("y", existing_data.get("position_y", 0))),
        "run_count":          int(state_data.get("run_count", existing_data.get("run_count", 0))),
        "nearby_agents":      list(state_data.get("nearby_agents", existing_data.get("nearby_agents", []))),
        "last_thought":       str(state_data.get("last_thought", existing_data.get("last_thought", "..."))),
        "blocks_built":       int(state_data.get("blocks_built", existing_data.get("blocks_built", 0))),
        "projection_per_day": float(tokenomics.get("perDay", existing_data.get("projection_per_day", 0.0))),
        "updatedAt":          utc_iso(),
    }
    for extra_key in ["statusMessage", "last_action", "biome", "health", "hunger", "thirst", "stamina", "system_warning", "tick", "world", "last_error"]:
        if extra_key in existing_data:
            payload[extra_key] = existing_data[extra_key]
    if balance_error and not is_online:
        payload["stale"] = True
        payload["lastError"] = balance_error
    else:
        payload["stale"] = False
        payload["lastError"] = None
    try:
        MOLTWORLD_CACHE_PATH.write_text(json.dumps(payload, indent=2))
    except OSError:
        pass
    return payload

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
    dashboard["personalCodex"] = fetch_personal_codex_status(now_iso)
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
    dashboard["visualCanaries"] = fetch_visual_canaries()
    dashboard["capabilityCanary"] = fetch_capability_canary()
    dashboard["sorareMlCockpit"] = fetch_sorare_ml_cockpit()
    dashboard["voiceRouter"] = fetch_voice_router_status()
    dashboard["opsInbox"] = fetch_ops_inbox_status(dashboard["calendarHealth"], dashboard["crons"])
    dashboard["capabilityStack"] = build_capability_stack(
        dashboard["visualCanaries"],
        dashboard["sorareMlCockpit"],
        dashboard["voiceRouter"],
        dashboard["opsInbox"],
        dashboard["capabilityCanary"],
        dashboard["personalCodex"],
    )
    dashboard["actionRequired"] = build_action_required(
        now_iso,
        dashboard["calendarHealth"],
        dashboard["crons"],
        moltworld_data,
        dashboard["personalCodex"],
    )
    if dashboard["visualCanaries"].get("status") == "attention":
        dashboard["actionRequired"].insert(0, {
            "priority": "high",
            "title": f"Mission Control canary issue: {dashboard['visualCanaries'].get('summary', 'check dashboard')}",
            "url": "#canaries",
        })
    dashboard["trackedTasks"]   = fetch_tracked_tasks()
    dashboard["activeAgents"]   = _f_agents.result() + build_visibility_agents(agent_bus_tasks, coding_visibility, context_watchdog)

    josh_brain_feed = normalize_agent_brain_feed(dashboard["brainFeed"], "JOSH 2.0")
    jain_brain_feed = normalize_agent_brain_feed(load_json_file(ROOT.parent / "data" / "jain-brain-feed.json", {}), "J.A.I.N")
    jaimes_brain_feed = normalize_agent_brain_feed(load_json_file(ROOT.parent / "data" / "jaimes-brain-feed.json", {}), "JAIMES")
    dashboard["agentBrainFeeds"] = apply_tracked_tasks_to_agent_feeds(
        {
            "josh": josh_brain_feed,
            "jain": jain_brain_feed,
            "jaimes": jaimes_brain_feed,
        },
        dashboard["trackedTasks"],
        now_iso,
    )
    jaimes_feed_for_hero = dashboard["agentBrainFeeds"].get("jaimes", {})
    if (
        not jaimes_feed_for_hero.get("active")
        and dashboard.get("sorareMlCockpit", {}).get("promoteToHero") is True
    ):
        # Capability tiles are readiness/status, not live agent work.
        # Only promote Sorare into Brain Feed if explicitly requested.
        dashboard["agentBrainFeeds"]["jaimes"] = {
            **jaimes_feed_for_hero,
            "agent": "JAIMES",
            "active": True,
            "reportedActive": bool(jaimes_feed_for_hero.get("reportedActive")),
            "objective": "Sorare ML Cockpit: model ownership and validation",
            "status": "active",
            "stale": False,
            "updatedAt": now_iso,
            "currentTool": jaimes_feed_for_hero.get("currentTool") or "capability cockpit",
            "model": jaimes_feed_for_hero.get("model") or "gpt-4.1",
            "steps": [{"label": "Sorare ML Cockpit", "status": "active", "tool": "capability cockpit"}],
            "capabilityBacked": True,
        }
    josh_brain_feed = dashboard["agentBrainFeeds"].get("josh", josh_brain_feed)
    jain_brain_feed = dashboard["agentBrainFeeds"].get("jain", jain_brain_feed)
    jaimes_brain_feed = dashboard["agentBrainFeeds"].get("jaimes", jaimes_brain_feed)
    dashboard["liveObjectives"] = build_live_objectives(dashboard["agentBrainFeeds"])
    agent_comms = build_agent_comms(
        load_json_file(AGENT_COMMS_PATH, []),
        agent_bus_tasks,
        jain_brain_feed,
        jaimes_brain_feed,
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
        dashboard["personalCodex"],
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


if __name__ == "__main__":
    main()
