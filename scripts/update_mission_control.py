#!/usr/bin/env python3
"""Update Control Tower dashboard JSON with live data."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback
    tomllib = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[0]
def utc_iso(delta: dt.timedelta | None = None) -> str:
    base = dt.datetime.now(dt.timezone.utc)
    if delta:
        base += delta
    return base.replace(microsecond=0).isoformat().replace('+00:00', 'Z')

DATA_DIR = ROOT.parent / "data"
DASHBOARD_PATH = DATA_DIR / "dashboard-data.json"
MODEL_USAGE_PATH = DATA_DIR / "modelUsage.json"
EIGHT_SLEEP_PATH = ROOT.parent / "data" / "eight-sleep-data.json"
AGENT_COMMS_PATH = ROOT.parent / "data" / "agent-comms.json"
PERSONAL_CODEX_PATH = ROOT.parent / "data" / "personal-codex.json"
JOSHEX_BRAIN_FEED_PATH = ROOT.parent / "data" / "joshex-brain-feed.json"
AGENT_CONTROL_STATUS_PATH = ROOT.parent / "data" / "agent-control-status.json"
CODEX_JOBS_PATH = ROOT.parent / "data" / "codex-jobs.json"
SHARED_EVENTS_PATH = ROOT.parent / "data" / "shared-events.json"
DECISIONS_PATH = ROOT.parent / "data" / "decisions.json"
KNOWLEDGE_INDEX_PATH = ROOT.parent / "data" / "knowledge-index.json"
HANDOFF_QUEUE_PATH = ROOT.parent / "data" / "handoff-queue.json"
DAILY_ROLLUP_PATH = ROOT.parent / "data" / "daily-rollup.json"
SHARED_LAYER_ADOPTION_PATH = ROOT.parent / "data" / "shared-layer-adoption.json"
AGENT_TASK_QUEUE_PATH = ROOT.parent / "data" / "agent-task-queue.json"
AGENT_CAPABILITIES_PATH = ROOT.parent / "data" / "agent-capabilities.json"
AGENT_ROUTING_POLICY_PATH = ROOT.parent / "data" / "agent-routing-policy.json"
MODEL_PROVIDER_BUDGETS_PATH = ROOT.parent / "data" / "model-provider-budgets.json"
XAI_ECOSYSTEM_PATH = ROOT.parent / "data" / "xai-ecosystem.json"
XAI_SPECIALIST_RUNS_PATH = ROOT.parent / "data" / "xai-specialist-runs.json"
ECOSYSTEM_HEALTH_SWEEP_PATH = ROOT.parent / "data" / "ecosystem-health-sweep.json"
AGENT_HEARTBEATS_PATH = ROOT.parent / "data" / "agent-heartbeats.json"
AGENT_CONTEXT_REGISTRY_PATH = ROOT.parent / "data" / "agent-context-registry.json"
AGENT_BRAIN_FEED_STALE_HOURS = 4
CAPABILITY_INVENTORY_PATH = ROOT.parent / "data" / "capability-inventory.json"
CAPABILITY_WATCH_PATH = ROOT.parent / "data" / "capability-watch.json"
AUTOMATION_ROLLOUT_PATH = ROOT.parent / "data" / "automation-rollout.json"
RELIABILITY_UPGRADES_PATH = ROOT.parent / "data" / "reliability-upgrades.json"
TELEGRAM_AI_BOT_FEATURES_PATH = ROOT.parent / "data" / "telegram-ai-bot-features.json"
RUNTIME_LAYOUT_PATH = ROOT.parent / "data" / "mission-control-runtime-layout.json"
CODEX_AUTOMATIONS_DIR = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "automations"
CODEX_AUTOMATION_STATUS_PATH = DATA_DIR / "codex-automation-status.json"
JOSH_OPS_GMAIL_STATUS_PATH = DATA_DIR / "josh2-ops-gmail-status.json"
NEXT_BASE = "http://127.0.0.1:3030"
WORKSPACE_ROOT = ROOT.parent.parent
KIOSK_MODEL_USAGE_PATH = WORKSPACE_ROOT / "kiosk-dashboard" / "data" / "modelUsage.json"
CONTEXT_WATCHDOG_STATE_PATH = WORKSPACE_ROOT / "memory" / "context-watchdog-state.json"
CONTEXT_HANDOFF_PATH = WORKSPACE_ROOT / "memory" / "context-handoff-latest.md"
CONTEXT_WATCHDOG_LABEL = "com.josh20.context-watchdog"
TASKS_PATH = WORKSPACE_ROOT / "memory" / "tasks.md"
GOG_KEYRING_ENV_PATHS = [
    Path.home() / ".openclaw" / "secrets" / "gog-keyring.env",
    WORKSPACE_ROOT / "secrets" / "gog-keyring.env",
]

PLAIN_TEXT_REPLACEMENTS = {
    "Heartbeat: josh2-lan": "Josh 2.0 is online and ready",
    "Heartbeat: jaimes-via-josh": "JAIMES is online and ready",
    "Heartbeat: macbook-codex": "JOSHeX is online and ready",
    "agent_heartbeat.py": "status check",
    "agent heartbeat": "status check",
    "josh2-lan": "Josh 2.0",
    "jaimes-via-josh": "JAIMES",
    "macbook-codex": "JOSHeX",
    "jaimes-ops-drift-check": "JAIMES ops drift check",
    "jaimes-model-efficiency-guard": "JAIMES model efficiency guard",
}
SCRIPT_LABELS = {
    "agent_heartbeat": "status check",
    "intel_feedback_loop": "intelligence feedback loop",
    "feedback_loop": "feedback loop",
    "check_josh_health": "Josh health check",
    "breaking_news_scanner": "breaking news scanner",
    "x_feedback_ml": "X feedback model check",
    "launch_scheduler": "launch scheduler",
    "host_local_maintenance": "host maintenance",
    "sorare_missions": "Sorare mission sweep",
    "sorare_lineups": "Sorare lineup check",
    "jaimes-ops-drift-check": "JAIMES ops drift check",
    "jaimes-model-efficiency-guard": "JAIMES model efficiency guard",
}


def plain_dashboard_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    for raw, plain in PLAIN_TEXT_REPLACEMENTS.items():
        text = re.sub(re.escape(raw), plain, text, flags=re.IGNORECASE)

    def script_label(match: re.Match[str]) -> str:
        token = match.group(1)
        stem = Path(token).name
        stem = re.sub(r"\.(py|sh|js|ts|tsx)$", "", stem, flags=re.IGNORECASE)
        return SCRIPT_LABELS.get(stem, stem.replace("_", " ").replace("-", " "))

    text = re.sub(
        r"(?<![\w./-])((?:/[^ ]+/)?[A-Za-z0-9_-]+\.(?:py|sh|js|ts|tsx))(?![\w./-])",
        script_label,
        text,
    )
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def plain_dashboard_value(value: Any) -> Any:
    if isinstance(value, str):
        return plain_dashboard_text(value, 500)
    if isinstance(value, list):
        return [plain_dashboard_value(item) for item in value]
    if isinstance(value, dict):
        return {key: plain_dashboard_value(item) for key, item in value.items()}
    return value


def load_env_file_values(paths: List[Path], env: Dict[str, str]) -> Dict[str, str]:
    merged = dict(env)
    for path in paths:
        try:
            rows = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for row in rows:
            stripped = row.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.replace("export ", "").strip()
            if key and key not in merged:
                merged[key] = value.strip().strip('"').strip("'")
    return merged

CRON_TARGETS = [
    # ── Codex automations ───────────────────────────────────────────────────
    {"name": "Gmail Morning Inbox Triage", "pattern": "gmail-morning-inbox-triage", "schedule": "Daily 8:30 AM ET", "description": "Reviews the last 24 hours of Personal Gmail, quiets low-signal mail, and surfaces anything that needs attention", "category": "Personal Inbox", "agent": "JOSHeX", "source": "codex_automation", "automationId": "gmail-morning-inbox-triage", "assumePresent": True},

    # ── JOSH 2.0 (local) ────────────────────────────────────────────────────
    {"name": "Control Tower Refresh", "pattern": "mission-control/scripts/update_and_push.sh", "schedule": "Every 5 min", "description": "Refreshes Control Tower data and pushes local dashboard updates", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "J.A.I.N Context Sync", "pattern": "com.josh20.mission-control-signal-refresh", "schedule": "Every 5 min", "description": "Keeps J.A.I.N alert state available for Telegram and agent context", "category": "Agent Context", "agent": "JOSH 2.0", "source": "launchd", "logPath": "/Users/josh2.0/.openclaw/workspace/logs/mission-control-signal-refresh.log"},
    {"name": "Brain Feed Server", "pattern": "brain_feed_server.py", "schedule": "Every 2 min (keepalive)", "description": "Keeps the live Brain Feed endpoint available for Control Tower", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "Chiro Invite Sync", "pattern": "scripts/chiro_invite_sync.sh", "schedule": "Hourly", "description": "Syncs chiropractic client invites into calendar", "category": "Appointments", "agent": "JOSH 2.0"},
    {"name": "J.A.I.N Silence Detector", "pattern": "jain_silence_detector.py", "schedule": "Hourly", "description": "Alerts if J.A.I.N stops reporting or goes quiet unexpectedly", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "J.A.I.N Medic", "pattern": "jain_medic.sh", "schedule": "Hourly", "description": "Runs local watchdog and recovery checks for J.A.I.N", "category": "Maintenance", "agent": "JOSH 2.0"},

    # ── J.A.I.N intelligence + maintenance ──────────────────────────────────
    {"name": "Breaking News Scanner", "pattern": "breaking_news_scanner.py", "schedule": "Every 5 min (6:00 AM–11:15 PM ET)", "description": "Scores breaking items and pushes high-priority alerts to @JAIN_BREAKING_BOT", "category": "J.A.I.N Alerts", "agent": "J.A.I.N", "jain": True},
    {"name": "X Watchlist Monitor", "pattern": "x_watchlist_monitor.py", "schedule": "Every 5 min (6:00 AM–11:15 PM ET)", "description": "Watches priority X accounts and routes urgent hits into J.A.I.N alerts", "category": "J.A.I.N Alerts", "agent": "J.A.I.N", "jain": True},
    {"name": "J.A.I.N Briefing", "pattern": "intelligence_feed.py", "schedule": "Weekdays 7:15a/10a/12p/2p/4:15p/6p/9p/11p · Weekends 10a/4:15p/9p/11p ET", "description": "AI, macro, crypto, and market briefings pushed to J.A.I.N Intelligence Telegram", "category": "J.A.I.N Alerts", "agent": "J.A.I.N", "jain": True,
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
    {"name": "J.A.I.N Feedback Loop", "pattern": "intel_feedback_loop.py", "schedule": "Every 5 min (keepalive)", "description": "Restarts the persistent J.A.I.N Telegram feedback loop if it drops", "category": "J.A.I.N Alerts", "agent": "J.A.I.N", "jain": True},
    {"name": "JOSH Health Check", "pattern": "check_josh_health.sh", "schedule": "Every 30 min", "description": "Remote health check from J.A.I.N back to Josh 2.0", "category": "Maintenance", "agent": "J.A.I.N", "jain": True},
    {"name": "Error Rate Monitor", "pattern": "error_rate_monitor.py", "schedule": "Daily 11:00 PM ET", "description": "Nightly scan for elevated error rates across automations", "category": "Maintenance", "agent": "J.A.I.N", "jain": True},
    {"name": "Log Rotation", "pattern": "rotate_logs.sh", "schedule": "Sun 3:00 AM ET", "description": "Weekly log rotation on J.A.I.N", "category": "Maintenance", "agent": "J.A.I.N", "jain": True},
    {"name": "XMCP Boot", "pattern": "xmcp", "schedule": "On boot", "description": "Boot-time XMCP startup on J.A.I.N so agent services recover after restart", "category": "Maintenance", "agent": "J.A.I.N", "jain": True},

    # X is intelligence-only in Control Tower. Posting/reply automations stay
    # out of Today's Jobs unless a human explicitly re-enables a posting lane.

    # ── Sorare MLB ──────────────────────────────────────────────────────────
    {"name": "Sorare ML Training", "pattern": "sorare_ml/train.py", "schedule": "Daily 2:00 AM ET", "description": "Hermes retrains the Sorare MLB model on the latest results", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-train-model"},
    {"name": "Sorare All-Rarity Claim Sweep", "pattern": "sorare_missions.py --claim-only", "schedule": "Daily 3:30 AM ET", "description": "JAIMES claim sweep for all available Sorare Daily Mission rewards across supported scarcities", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-all-rarity-claim-sweep"},
    {"name": "Sorare Daily Missions", "pattern": "ml_bot.py --missions-only", "schedule": "Daily 10:00 AM ET", "description": "JAIMES / Hermes optimizes, submits, and verifies Daily Mission picks with same-day game-lock awareness", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-daily-missions-optimize-submit"},
    {"name": "Sorare Daily Missions Late Refresh", "pattern": "sorare_missions.py --skip-claims --owner-override", "schedule": "Daily 4:30 PM ET", "description": "JAIMES revalidates Daily Missions after probable-starter, lineup, and bullpen context changes while preserving locked/in-progress games", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-daily-missions-late-refresh"},
    {"name": "Sorare Daily Missions Final Refresh", "pattern": "sorare_missions.py --skip-claims --owner-override", "schedule": "Daily 6:30 PM ET", "description": "JAIMES performs a final lock-aware Daily Missions refresh for players whose games remain unstarted", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-daily-missions-final-refresh"},
    {"name": "Sorare Limited GW Lineups", "pattern": "ml_bot.py --lineups-only", "schedule": "Daily 11:00 AM ET", "description": "JAIMES / Hermes builds limited game-week lineups for Sorare competitions", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "sorare-ml-lineups"},
    {"name": "Sorare GW Draft Report", "pattern": "gw_pipeline/run_gw_pipeline.py", "schedule": "Daily 8:00 AM ET", "description": "JAIMES / Hermes builds the no-submit game-week draft report and asks for approval when action is needed", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "Sorare MLB GW draft report"},
    {"name": "Sorare GW Evening First Submit", "pattern": "gw_evening_first_submit.py", "schedule": "Daily 8:30 PM ET", "description": "JAIMES submits the first version of open GW lineups the evening before lock when no slate is already populated", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "Sorare GW evening first submit"},
    {"name": "Sorare Pre-Lock Monitor", "pattern": "gw_pipeline/run_gw_pipeline.py --artifacts-dir artifacts/gw_pipeline_prelock", "schedule": "Every 4 hours", "description": "JAIMES / Hermes reruns the strict GW validator and reports only actionable lock-window risks", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "Sorare MLB pre-lock monitor"},
    {"name": "Sorare/Fantasy Fast Lane", "pattern": "jaimes_sorare_fast_lane.py", "schedule": "Every 2 hours", "description": "JAIMES refreshes read-only Sorare and fantasy cache so Telegram/Codex answers can be fast before deep optimization is needed", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "launchd", "logPath": "/Users/jc_agent/.openclaw/workspace/mission-control/logs/jaimes-sorare-fast-lane.out.log"},
    {"name": "Sorare Edge Outcome Cycle", "pattern": "edge_outcome_cycle.py", "schedule": "Daily 11:20 PM ET", "description": "JAIMES / Hermes syncs Sorare outcome labels and retrains the shadow edge calibrator when useful", "category": "Sorare MLB", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "Sorare edge outcome cycle"},

    # ── Fantasy baseball ────────────────────────────────────────────────────
    {"name": "Fantasy Waiver Scan (post-process)", "pattern": "fantasy_waiver_scan.py", "schedule": "Sun 8:00 PM ET", "description": "Post-waiver scan right after the Sunday-night processing window", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Weekly Recap", "pattern": "fantasy_weekly_recap.py", "schedule": "Sun 8:00 AM ET", "description": "Raw weekly recap sent to Josh", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Injury Monitor", "pattern": "fantasy_injury_monitor.py", "schedule": "Mon 8:45 AM ET", "description": "Monday injury check before setting the weekly roster", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Lineup Check", "pattern": "fantasy_lineup_check.py", "schedule": "Mon 9:00 AM ET", "description": "Monday lineup review on the live cron path", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Waiver Injury Alert", "pattern": "waiver_injury_alert.py", "schedule": "Daily 1:00 PM ET", "description": "Surfaces injured-player replacement opportunities", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Fantasy Waiver Review (Hermes)", "pattern": "fantasy_waiver_scan.py", "schedule": "Wed/Fri 1:00 PM ET", "description": "Hermes waiver review lane that runs mid-week", "category": "Fantasy Baseball", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "fantasy-waiver-scan"},
    {"name": "Fantasy Waiver Scan (pre-game)", "pattern": "fantasy_waiver_scan.py", "schedule": "Mon 7:00 AM ET", "description": "Final waiver review before first-pitch lineup lock", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},

    # ── JAIMES / Hermes maintenance ─────────────────────────────────────────
    {"name": "Daily Agent Readiness Check", "pattern": "daily_health_check.py", "schedule": "Daily 5:50 AM ET", "description": "Checks JAIMES/Hermes readiness and flags handoff or system issues", "category": "Maintenance", "agent": "JAIMES", "jain": True, "source": "hermes", "hermesName": "daily-health-check"},
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


def fetch_codex_jobs(now_iso: str) -> List[Dict[str, Any]]:
    """Load today's Personal Codex-triggered automations for the jobs panel."""
    if not CODEX_JOBS_PATH.exists():
        return []
    try:
        raw = json.loads(CODEX_JOBS_PATH.read_text())
        entries = raw.get("jobs", raw) if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            return []

        today_local = dt.datetime.now().strftime("%Y-%m-%d")
        jobs: List[Dict[str, Any]] = []
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            ts = str(entry.get("time") or entry.get("updatedAt") or entry.get("startedAt") or now_iso)
            if today_local not in ts and not ts.startswith(now_iso[:10]):
                continue
            title = str(entry.get("title") or entry.get("name") or "").strip()
            if not title:
                continue
            status = str(entry.get("status") or "done").strip().lower()
            jobs.append({
                "id": entry.get("id") or f"codex-job-{idx}",
                "title": plain_dashboard_text(title, 120),
                "status": status[:32],
                "time": ts,
                "tool": plain_dashboard_text(entry.get("tool") or "codex", 48),
                "detail": plain_dashboard_text(entry.get("detail") or entry.get("summary") or "", 220),
                "owner": str(entry.get("owner") or "Personal Codex")[:64],
            })

        jobs.sort(key=lambda item: str(item.get("time") or ""), reverse=True)
        latest_by_work: Dict[tuple[str, str, str], Dict[str, Any]] = {}
        for item in jobs:
            key = (
                str(item.get("owner") or "").lower(),
                str(item.get("tool") or "").lower(),
                str(item.get("title") or "").lower(),
            )
            latest_by_work.setdefault(key, item)
        return list(latest_by_work.values())[:10]
    except Exception as exc:
        print(f"[warn] fetch_codex_jobs failed: {exc}", file=sys.stderr)
        return []


def fetch_shared_events(now_iso: str) -> List[Dict[str, Any]]:
    if not SHARED_EVENTS_PATH.exists():
        return []
    try:
        raw = json.loads(SHARED_EVENTS_PATH.read_text())
        entries = raw.get("events", raw) if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            return []
        today = dt.datetime.now().strftime("%Y-%m-%d")
        events: List[Dict[str, Any]] = []
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            if entry.get("privacy") != "dashboard-safe":
                continue
            ts = str(entry.get("time") or now_iso)
            if today not in ts and not ts.startswith(now_iso[:10]):
                continue
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            events.append({
                "id": str(entry.get("id") or f"shared-event-{idx}")[:140],
                "time": ts,
                "agent": str(entry.get("agent") or "joshex")[:24],
                "agentLabel": str(entry.get("agentLabel") or entry.get("agent") or "Agent")[:80],
                "type": str(entry.get("type") or "status")[:32],
                "title": plain_dashboard_text(title, 160),
                "status": str(entry.get("status") or "info")[:32],
                "tool": plain_dashboard_text(entry.get("tool") or "", 80),
                "detail": plain_dashboard_text(entry.get("detail") or "", 240),
            })
        events.sort(key=lambda item: str(item.get("time") or ""), reverse=True)
        return events[:20]
    except Exception as exc:
        print(f"[warn] fetch_shared_events failed: {exc}", file=sys.stderr)
        return []


LOW_SIGNAL_SHARED_EVENT_PATTERNS = re.compile(
    r"heartbeat checks complete|stale/persistent warnings|specialist pass|gemini .*auth|"
    r"daily gemini routing audit|morning digest pass|smoke|receipt not confirmed|"
    r"weekly autonomy self-test|remote probe|operation not permitted|"
    r"repo write access|push permission|allowlist requires explicit approval|"
    r"daily agent ecosystem health sweep|ecosystem health sweep|"
    r"stale brain feed freshness guard|brain feed needs refresh|"
    r"heartbeat check complete|mission control latest commit|repo dirty tree|"
    r"dirty/untracked entries|no auto-push was attempted",
    re.IGNORECASE,
)


def is_actionable_shared_event(event: Dict[str, Any]) -> bool:
    """Only promote current user-actionable shared-layer failures into alerts."""
    text = " ".join(str(event.get(key) or "") for key in ("title", "detail", "tool", "type"))
    if LOW_SIGNAL_SHARED_EVENT_PATTERNS.search(text):
        return False
    return True


def event_key(event: Dict[str, Any]) -> tuple[str, str, str]:
    tool = str(event.get("tool") or "").lower()
    title = str(event.get("title") or "").lower()
    if "screen check" in tool or "screen check" in title:
        title = "screen check"
    return (
        str(event.get("agent") or "").lower(),
        tool,
        title,
    )


def superseded_blocked_event_ids(events: List[Dict[str, Any]]) -> set[str]:
    """Return blocked/error event ids that have a newer ok/done event for same lane."""
    latest_clear_by_key: Dict[tuple[str, str, str], str] = {}
    for event in events:
        status = str(event.get("status") or "").lower()
        etype = str(event.get("type") or "").lower()
        key = event_key(event)
        event_time = str(event.get("time") or "")
        if status in {"ok", "done", "ready"} and etype != "blocked":
            latest_clear_by_key.setdefault(key, event_time)

    superseded: set[str] = set()
    for event in events:
        status = str(event.get("status") or "").lower()
        etype = str(event.get("type") or "").lower()
        if status not in {"blocked", "error"} and etype != "blocked":
            continue
        clear_time = latest_clear_by_key.get(event_key(event))
        if clear_time and clear_time > str(event.get("time") or ""):
            superseded.add(str(event.get("id") or ""))
    return superseded


def shared_layer_attention_item(shared_layer: Dict[str, Any]) -> Dict[str, Any]:
    """Return the single most useful Josh-facing shared-layer alert."""
    counts = shared_layer.get("counts", {}) if isinstance(shared_layer.get("counts"), dict) else {}
    blocked_events = shared_layer.get("blockedEvents", []) if isinstance(shared_layer.get("blockedEvents"), list) else []
    attention_handoffs = shared_layer.get("attentionHandoffs", []) if isinstance(shared_layer.get("attentionHandoffs"), list) else []
    blocked_tasks = (shared_layer.get("tasks", {}) or {}).get("blocked", []) if isinstance(shared_layer.get("tasks"), dict) else []
    approval_tasks = (shared_layer.get("tasks", {}) or {}).get("approvalNeeded", []) if isinstance(shared_layer.get("tasks"), dict) else []

    if blocked_events:
        event = blocked_events[0]
        title = plain_dashboard_text(event.get("title") or "Shared-layer follow-up needed", 96)
        detail = plain_dashboard_text(event.get("detail") or "Review the latest shared event.", 180)
        text = f"{title} {detail} {event.get('tool') or ''}".lower()
        if "base mcp" in text:
            title = "Base MCP login needed"
            detail = "Base MCP is staged on Josh 2.0, but Base Account sign-in timed out. Fresh login/approval is needed before account-aware proposals are live."
        return {
            "priority": "medium",
            "title": title,
            "detail": detail,
            "url": "#jobs",
        }
    if attention_handoffs:
        handoff = attention_handoffs[0]
        return {
            "priority": "medium",
            "title": plain_dashboard_text(handoff.get("title") or "Agent handoff needs attention", 96),
            "detail": plain_dashboard_text(handoff.get("detail") or "A shared handoff is blocked.", 180),
            "url": "#jobs",
        }
    if blocked_tasks:
        task = blocked_tasks[0]
        return {
            "priority": "medium",
            "title": plain_dashboard_text(task.get("title") or task.get("id") or "Shared task blocked", 96),
            "detail": plain_dashboard_text(task.get("detail") or task.get("status") or "A shared task reported blocked.", 180),
            "url": "#jobs",
        }
    if approval_tasks:
        task = approval_tasks[0]
        return {
            "priority": "medium",
            "title": plain_dashboard_text(task.get("title") or task.get("id") or "Approval needed", 96),
            "detail": "A shared task is waiting for explicit approval before it continues.",
            "url": "#jobs",
        }
    return {
        "priority": "medium",
        "title": "Shared layer needs attention",
        "detail": f"{counts.get('attentionHandoffs', 0)} blocked handoff(s), {counts.get('blocked', 0)} blocked event(s), {counts.get('blockedTasks', 0)} blocked task(s), {counts.get('approvalNeeded', 0)} approval(s).",
        "url": "#jobs",
    }


def fetch_shared_operating_layer(now_iso: str) -> Dict[str, Any]:
    decisions = load_json_file(DECISIONS_PATH, {"decisions": []}).get("decisions", [])
    knowledge = load_json_file(KNOWLEDGE_INDEX_PATH, {"entries": []})
    handoffs = load_json_file(HANDOFF_QUEUE_PATH, {"handoffs": []}).get("handoffs", [])
    rollup = plain_dashboard_value(load_json_file(DAILY_ROLLUP_PATH, {}))
    adoption = load_json_file(SHARED_LAYER_ADOPTION_PATH, {})
    task_queue = load_json_file(AGENT_TASK_QUEUE_PATH, {"tasks": []})
    capability_registry = load_json_file(AGENT_CAPABILITIES_PATH, {"agents": []})
    routing_policy = load_json_file(AGENT_ROUTING_POLICY_PATH, {"routes": []})
    heartbeats_payload = load_json_file(AGENT_HEARTBEATS_PATH, {"heartbeats": [], "staleAfterMinutes": 120})
    inventory = load_json_file(CAPABILITY_INVENTORY_PATH, {"nodes": []})
    capability_watch = load_json_file(CAPABILITY_WATCH_PATH, {
        "updatedAt": now_iso,
        "status": "pending",
        "summary": "Capability Watch has not run yet.",
        "recommendations": [],
    })
    automation_rollout = load_json_file(AUTOMATION_ROLLOUT_PATH, {"rollouts": []})
    events = fetch_shared_events(now_iso)
    tasks = [task for task in task_queue.get("tasks", []) if isinstance(task, dict)]
    terminal_task_statuses = {"done", "cancelled", "canceled", "superseded"}
    task_status_by_id = {
        str(task.get("id")): str(task.get("status") or "").lower()
        for task in tasks
        if task.get("id")
    }

    def handoff_points_to_closed_task(handoff: Dict[str, Any]) -> bool:
        text = " ".join(
            str(handoff.get(key) or "")
            for key in ("id", "title", "detail", "path")
        )
        task_ids = re.findall(r"\btask-[a-z0-9-]+", text.lower())
        return any(task_status_by_id.get(task_id) in terminal_task_statuses for task_id in task_ids)

    open_handoffs = [
        h for h in handoffs
        if h.get("privacy") == "dashboard-safe"
        and h.get("status") in {"open", "blocked"}
        and not handoff_points_to_closed_task(h)
    ]
    attention_handoffs = [h for h in open_handoffs if h.get("status") == "blocked"]
    superseded_event_ids = superseded_blocked_event_ids(events)
    blocked_events = [
        e for e in events
        if (e.get("status") in {"blocked", "error"} or e.get("type") == "blocked")
        and str(e.get("id") or "") not in superseded_event_ids
        and is_actionable_shared_event(e)
    ]
    latest_event_at = events[0].get("time") if events else None
    fresh = bool(latest_event_at and is_recent_ts(latest_event_at, hours=6))
    status = "attention" if blocked_events or attention_handoffs else "ready" if fresh else "stale"
    active_statuses = {"queued", "accepted", "active", "blocked", "error"}
    active_tasks = [task for task in tasks if task.get("status") in active_statuses]
    blocked_tasks = [task for task in tasks if task.get("status") in {"blocked", "error"}]
    approval_tasks = [task for task in tasks if task.get("approval") == "required" and task.get("status") not in {"done", "cancelled"}]
    task_counts_by_owner: Dict[str, int] = {}
    for task in active_tasks:
        owner = str(task.get("owner") or "unknown")
        task_counts_by_owner[owner] = task_counts_by_owner.get(owner, 0) + 1
    heartbeat_rows = [row for row in heartbeats_payload.get("heartbeats", []) if isinstance(row, dict)]
    stale_after = int(heartbeats_payload.get("staleAfterMinutes") or 120)
    now_dt = dt.datetime.now(dt.timezone.utc)
    stale_heartbeats = []
    fresh_heartbeats = []
    for row in heartbeat_rows:
        stamp = iso_to_dt(row.get("updatedAt"))
        stale = not bool(stamp and (now_dt - stamp) <= dt.timedelta(minutes=stale_after))
        row["stale"] = stale
        if stale:
            stale_heartbeats.append(row)
        else:
            fresh_heartbeats.append(row)
    rollout_rows = [row for row in automation_rollout.get("rollouts", []) if isinstance(row, dict)]
    wrapped_rollouts = [row for row in rollout_rows if row.get("status") == "wrapped"]
    if blocked_tasks or approval_tasks:
        status = "attention"
    return {
        "status": status,
        "updatedAt": latest_event_at or now_iso,
        "counts": {
            "eventsToday": len(events),
            "decisions": len([d for d in decisions if d.get("privacy") == "dashboard-safe"]),
            "knowledgeEntries": len(knowledge.get("entries", [])) if isinstance(knowledge, dict) else 0,
            "openHandoffs": len(open_handoffs),
            "attentionHandoffs": len(attention_handoffs),
            "blocked": len(blocked_events),
            "activeTasks": len(active_tasks),
            "blockedTasks": len(blocked_tasks),
            "approvalNeeded": len(approval_tasks),
            "capabilityAgents": len(capability_registry.get("agents", [])) if isinstance(capability_registry, dict) else 0,
            "routingRules": len(routing_policy.get("routes", [])) if isinstance(routing_policy, dict) else 0,
            "freshHeartbeats": len(fresh_heartbeats),
            "staleHeartbeats": len(stale_heartbeats),
            "inventoryNodes": len(inventory.get("nodes", [])) if isinstance(inventory, dict) else 0,
            "wrappedAutomations": len(wrapped_rollouts),
        },
        "latestEvents": events[:6],
        "blockedEvents": blocked_events[:6],
        "recentDecisions": [d for d in decisions if d.get("privacy") == "dashboard-safe"][:6],
        "knowledgeEntries": (knowledge.get("entries", []) if isinstance(knowledge, dict) else [])[:8],
        "openHandoffs": open_handoffs[:6],
        "attentionHandoffs": attention_handoffs[:6],
        "dailyRollup": rollup,
        "adoption": adoption,
        "tasks": {
            "active": active_tasks[:8],
            "blocked": blocked_tasks[:8],
            "approvalNeeded": approval_tasks[:8],
            "byOwner": task_counts_by_owner,
        },
        "capabilities": capability_registry,
        "routingPolicy": routing_policy,
        "heartbeats": {
            "staleAfterMinutes": stale_after,
            "fresh": fresh_heartbeats[:8],
            "stale": stale_heartbeats[:8],
        },
        "capabilityInventory": inventory,
        "capabilityWatch": capability_watch,
        "automationRollout": automation_rollout,
    }


def provider_from_model_name(name: str, source: str = "") -> str:
    text = f"{name} {source}".lower()
    if "openrouter" in text:
        return "openrouter"
    if "grok" in text or "xai" in text:
        return "xai"
    if "gemini" in text or "google/" in text:
        return "gemini"
    if "codex" in text or "openai" in text or "gpt-" in text:
        return "codex"
    if "ollama" in text:
        return "ollama"
    return "other"


def build_model_router_status(model_usage: Dict[str, Any] | None, now_iso: str) -> Dict[str, Any]:
    budgets = load_json_file(MODEL_PROVIDER_BUDGETS_PATH, {"providers": [], "policy": {}})
    xai_ecosystem = load_json_file(XAI_ECOSYSTEM_PATH, {})
    ecosystem_health = load_json_file(ECOSYSTEM_HEALTH_SWEEP_PATH, {})
    rows = budgets.get("providers", []) if isinstance(budgets, dict) else []
    breakdown = (model_usage or {}).get("breakdown", []) if isinstance(model_usage, dict) else []
    spend_by_provider: Dict[str, Dict[str, float]] = {}
    last_by_provider: Dict[str, Dict[str, str]] = {}
    for item in breakdown if isinstance(breakdown, list) else []:
        if not isinstance(item, dict):
            continue
        provider = provider_from_model_name(str(item.get("name") or ""), str(item.get("source") or ""))
        bucket = spend_by_provider.setdefault(provider, {"daily": 0.0, "weekly": 0.0, "monthly": 0.0})
        bucket["daily"] += float(item.get("dailyCost") or 0)
        bucket["weekly"] += float(item.get("weeklyCost") or item.get("cost") or 0)
        if provider not in last_by_provider:
            last_by_provider[provider] = {
                "model": str(item.get("name") or ""),
                "source": str(item.get("source") or ""),
            }
    provider_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        provider_id = str(row.get("id") or "")
        spend = spend_by_provider.get(provider_id, {"daily": 0.0, "weekly": 0.0, "monthly": 0.0})
        daily_cap = float(row.get("dailyCapUsd") or 0)
        monthly_cap = float(row.get("monthlyCapUsd") or 0)
        remaining = row.get("remainingCreditUsd")
        reserve = float(row.get("reserveUsd") or 0)
        if provider_id == "xai" and remaining is not None:
            remaining = max(0.0, float(remaining) - spend["daily"])
        daily_pct = round((spend["daily"] / daily_cap) * 100, 1) if daily_cap else 0.0
        monthly_pct = round((spend["weekly"] / monthly_cap) * 100, 1) if monthly_cap else 0.0
        xai_api = xai_ecosystem.get("api", {}) if provider_id == "xai" and isinstance(xai_ecosystem, dict) else {}
        xai_key = xai_api.get("key", {}) if isinstance(xai_api, dict) else {}
        xai_last_test = xai_ecosystem.get("lastTest", {}) if provider_id == "xai" and isinstance(xai_ecosystem, dict) else {}
        host_xai_ok = False
        if provider_id == "xai" and isinstance(ecosystem_health, dict):
            hosts = ecosystem_health.get("hosts", [])
            host_xai_ok = bool(hosts) and all(
                isinstance(host, dict) and (host.get("checks") or {}).get("xaiApi") is True
                for host in hosts
                if isinstance(host, dict) and host.get("agent") in {"josh", "jaimes", "jain"}
            )
        xai_missing_key = provider_id == "xai" and not host_xai_ok and isinstance(xai_key, dict) and xai_key.get("present") is False
        if xai_missing_key:
            status = "missing-key"
        elif daily_cap and spend["daily"] >= daily_cap:
            status = "blocked"
        elif remaining is not None and float(remaining) <= reserve:
            status = "reserve"
        elif daily_cap and daily_pct >= 80:
            status = "watch"
        else:
            status = "ready"
        last = last_by_provider.get(provider_id, {})
        provider_rows.append({
            **row,
            "dailySpendUsd": round(spend["daily"], 6),
            "weeklySpendUsd": round(spend["weekly"], 6),
            "monthlySpendUsd": round(spend["monthly"], 6),
            "dailyUtilizationPct": daily_pct,
            "monthlyUtilizationPct": monthly_pct,
            "remainingCreditUsd": None if remaining is None else round(float(remaining), 6),
            "status": status,
            "authStatus": "host-keys-ok" if provider_id == "xai" and host_xai_ok else "missing-key" if xai_missing_key else row.get("authStatus"),
            "keyPresent": True if provider_id == "xai" and host_xai_ok else xai_key.get("present") if isinstance(xai_key, dict) else None,
            "keySuffix": xai_key.get("suffix") if isinstance(xai_key, dict) else "",
            "lastTestStatus": xai_last_test.get("status") if isinstance(xai_last_test, dict) else "",
            "lastModelUsed": last.get("model") or row.get("lastModelUsed"),
            "lastSource": last.get("source") or "",
            "whyChosen": row.get("lastRouteReason") or row.get("role") or "",
        })
    policy = budgets.get("policy", {}) if isinstance(budgets, dict) else {}
    codex_mode = str(policy.get("codexAllowanceMode") or "normal")
    return {
        "updatedAt": now_iso,
        "policy": policy,
        "codexAllowanceMode": codex_mode,
        "providers": provider_rows,
        "guardrails": budgets.get("guardrails", []) if isinstance(budgets, dict) else [],
        "summary": (
            "Codex exhausted mode active: Gemini/xAI/OpenRouter handle safe work; Codex/API spend is reserved for execution."
            if codex_mode in {"conserve", "exhausted"}
            else "Codex default; Gemini reviewer; xAI current-events/X-native specialist; OpenRouter fallback."
        ),
    }


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


def load_agent_control_status(now_iso: str) -> Dict[str, Any]:
    payload = load_json_file(AGENT_CONTROL_STATUS_PATH, {})
    generated_at = (payload.get("generatedAt") or payload.get("updatedAt")) if isinstance(payload, dict) else None
    if isinstance(payload, dict) and payload.get("summary") and is_recent_ts(generated_at, hours=6):
        return payload

    heartbeats_payload = load_json_file(AGENT_HEARTBEATS_PATH, {"heartbeats": [], "staleAfterMinutes": 120})
    capability_inventory = load_json_file(CAPABILITY_INVENTORY_PATH, {"nodes": []})
    stale_after = int(heartbeats_payload.get("staleAfterMinutes") or 120)
    now_dt = dt.datetime.now(dt.timezone.utc)
    heartbeats = [row for row in heartbeats_payload.get("heartbeats", []) if isinstance(row, dict)]
    nodes = [row for row in capability_inventory.get("nodes", []) if isinstance(row, dict)]
    labels = {
        "joshex": "JOSHeX",
        "josh2": "Josh 2.0",
        "jaimes": "JAIMES",
        "jain": "J.A.I.N",
    }
    by_agent: Dict[str, list[Dict[str, Any]]] = {}
    for row in heartbeats:
        agent = normalize_node_slug(row.get("agent"))
        by_agent.setdefault(agent, []).append(row)

    agent_rows: Dict[str, Dict[str, Any]] = {}
    tracked_agents = sorted(set(by_agent) | {normalize_node_slug(row.get("agent")) for row in nodes})
    for agent in tracked_agents:
        if agent == "system":
            continue
        rows = by_agent.get(agent, [])
        latest = max(rows, key=lambda row: row.get("updatedAt") or "", default={})
        stamp = iso_to_dt(latest.get("updatedAt"))
        stale = not bool(stamp and (now_dt - stamp) <= dt.timedelta(minutes=stale_after))
        status = str(latest.get("status") or "").lower()
        ok = bool(rows) and not stale and status not in {"blocked", "error", "attention", "failed"}
        node = next((row for row in nodes if normalize_node_slug(row.get("agent")) == agent), {})
        agent_rows[agent] = {
            "id": agent,
            "label": labels.get(agent, agent.upper()),
            "status": "ready" if ok else "attention",
            "available": ok,
            "probedAt": latest.get("updatedAt") or node.get("checkedAt") or now_iso,
            "summary": plain_dashboard_text(latest.get("summary") or "No fresh heartbeat yet.", 180),
            "source": "live-heartbeats",
            "stale": stale,
        }

    ready_agents = sum(1 for row in agent_rows.values() if row.get("status") == "ready")
    total_agents = len(agent_rows)
    overall = "ready" if total_agents and ready_agents == total_agents else "attention"
    return {
        "generatedAt": now_iso,
        "statusSource": "live-heartbeats",
        "staleSourceSuppressed": bool(generated_at),
        "summary": {
            "overall": overall,
            "readyAgents": ready_agents,
            "totalAgents": total_agents,
            "offlineAgents": max(0, total_agents - ready_agents),
            "degradedServices": max(0, total_agents - ready_agents),
            "authRequiredServices": 0,
            "failedQueues": 0,
            "dirtyRepos": 0,
            "localModels": sum(len(row.get("ollamaModels") or []) for row in nodes),
            "source": "live-heartbeats",
        },
        "agents": agent_rows,
    }


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
        "context": "Control Tower is syncing live CodexBar usage and publishing refreshes automatically.",
        "updatedAt": updated_at,
    }


def agent_feed_is_ready_heartbeat(raw: Dict[str, Any]) -> bool:
    """Detect health-check rows that mean ready, even if an old active flag remains."""
    status = str(raw.get("status") or "").strip().lower()
    text = " ".join(
        str(raw.get(key) or "")
        for key in ("objective", "detail", "currentTool", "source")
    ).lower()
    if status in {"ready", "ok", "done", "idle", "info"}:
        return True
    return any(
        phrase in text
        for phrase in (
            "online and ready",
            "no active queued worker tasks",
            "not actively working",
            "standing by",
            "standby",
        )
    )


def normalize_agent_brain_feed(feed: Dict[str, Any] | None, fallback_agent: str) -> Dict[str, Any]:
    raw = feed if isinstance(feed, dict) else {}
    updated_at = raw.get("updatedAt")
    ready_heartbeat = agent_feed_is_ready_heartbeat(raw)
    reported_active = bool(raw.get("active")) and not ready_heartbeat
    stale = bool(updated_at) and not is_recent_ts(updated_at, hours=AGENT_BRAIN_FEED_STALE_HOURS)
    raw_status = str(raw.get("status") or "idle")
    status = "ready" if ready_heartbeat and raw_status.lower() in {"active", "working", "running", "queued"} else raw_status
    raw_steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
    visible_steps = []
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        text = " ".join(str(step.get(key) or "") for key in ("label", "title", "tool", "status", "kind"))
        if str(step.get("status") or "").lower() in {"blocked", "error"} and LOW_SIGNAL_SHARED_EVENT_PATTERNS.search(text):
            continue
        visible_steps.append(step)
    return {
        "agent": plain_dashboard_text(raw.get("agent") or fallback_agent, 80),
        "active": reported_active and not stale,
        "reportedActive": reported_active,
        "objective": plain_dashboard_text(raw.get("objective") or "", 220),
        "status": "stale" if stale and reported_active else status,
        "stale": stale,
        "updatedAt": updated_at,
        "messageReceived": plain_dashboard_text(raw.get("messageReceived") or "", 220) or None,
        "currentTool": plain_dashboard_text(raw.get("currentTool") or "", 80) or None,
        "model": raw.get("model"),
        "steps": [
            {
                **step,
                "label": plain_dashboard_text(step.get("label") or step.get("title") or "", 180),
                "tool": plain_dashboard_text(step.get("tool") or "", 80),
            }
            for step in visible_steps
        ],
    }


def agent_feed_key(feed: Dict[str, Any] | None) -> str:
    if not isinstance(feed, dict):
        return ""
    text = " ".join(
        str(feed.get(key) or "")
        for key in ("agentId", "agent_id", "agent", "source")
    ).lower()
    if "joshex" in text or "codex" in text:
        return "joshex"
    if "jaimes" in text:
        return "jaimes"
    if "j.a.i.n" in text or "jain" in text:
        return "jain"
    if "josh" in text:
        return "josh"
    return ""


HEARTBEAT_STATUS_LABELS = {
    "ready": "online and ready",
    "ok": "online and ready",
    "active": "working now",
    "queued": "queued",
    "blocked": "needs attention",
    "error": "needs attention",
    "idle": "standing by",
}


def heartbeat_status_label(status: Any) -> str:
    return HEARTBEAT_STATUS_LABELS.get(str(status or "").lower(), "checked in")


def heartbeat_summary(row: Dict[str, Any], fallback_agent: str) -> str:
    summary = str(row.get("summary") or "").strip()
    if summary:
        return summary
    return f"{fallback_agent} is {heartbeat_status_label(row.get('status'))}"


def heartbeat_brain_feed(agent_key: str, fallback_agent: str) -> Dict[str, Any]:
    payload = load_json_file(AGENT_HEARTBEATS_PATH, {"heartbeats": []})
    rows = payload.get("heartbeats", []) if isinstance(payload, dict) else []
    aliases = {
        "josh": {"josh", "josh2", "josh2.0", "josh 2.0"},
        "josh2": {"josh", "josh2", "josh2.0", "josh 2.0"},
        "joshex": {"joshex", "codex"},
        "jaimes": {"jaimes"},
        "jain": {"jain", "j.a.i.n"},
    }.get(agent_key, {agent_key})
    row = next(
        (
            item
            for item in rows
            if isinstance(item, dict) and str(item.get("agent") or "").lower() in aliases
        ),
        {},
    )
    if not row:
        return normalize_agent_brain_feed({}, fallback_agent)
    return normalize_agent_brain_feed(
        {
            "agent": fallback_agent,
            "active": row.get("status") in {"ready", "ok", "active"},
            "objective": heartbeat_summary(row, fallback_agent),
            "status": row.get("status") or "idle",
            "updatedAt": row.get("updatedAt"),
            "currentTool": "status check",
            "steps": [
                {
                    "label": heartbeat_summary(row, fallback_agent),
                    "status": row.get("status") or "idle",
                    "tool": "status check",
                }
            ],
        },
        fallback_agent,
    )


def agent_specific_brain_feed(
    feed: Dict[str, Any] | None,
    expected_key: str,
    fallback_agent: str,
) -> Dict[str, Any]:
    heartbeat = heartbeat_brain_feed(expected_key, fallback_agent)
    if agent_feed_key(feed) != expected_key:
        return heartbeat

    explicit = normalize_agent_brain_feed(feed, fallback_agent)
    heartbeat_ts = iso_to_dt(heartbeat.get("updatedAt"))
    explicit_ts = iso_to_dt(explicit.get("updatedAt"))
    explicit_active = bool(explicit.get("active") or explicit.get("reportedActive"))

    if explicit_active and is_recent_ts(explicit.get("updatedAt"), hours=AGENT_BRAIN_FEED_STALE_HOURS):
        return explicit
    if heartbeat_ts and (not explicit_ts or heartbeat_ts > explicit_ts):
        prior_steps = [
            step for step in explicit.get("steps", [])
            if isinstance(step, dict) and str(step.get("status") or "").lower() in {"done", "complete", "completed", "ready", "ok"}
        ][:3]
        return {
            **heartbeat,
            "model": explicit.get("model") or heartbeat.get("model"),
            "steps": [*(heartbeat.get("steps") or []), *prior_steps],
        }
    return explicit


def personal_codex_brain_feed(personal_codex: Dict[str, Any], now_iso: str) -> Dict[str, Any]:
    recent_activity = personal_codex.get("recentActivity") if isinstance(personal_codex.get("recentActivity"), list) else []
    steps = []
    for item in recent_activity[:4]:
        if not isinstance(item, dict):
            continue
        steps.append({
            "label": item.get("event") or item.get("title") or "JOSHeX update",
            "status": personal_codex.get("status") or "ready",
            "tool": personal_codex.get("mode") or "personal coordination",
        })
    return normalize_agent_brain_feed(
        {
            "agent": "JOSHeX",
            "agentId": "joshex",
            "active": False,
            "reportedActive": False,
            "objective": personal_codex.get("objective") or "JOSHeX visibility current; awaiting direct instruction",
            "detail": personal_codex.get("summary") or "",
            "status": personal_codex.get("status") or "ready",
            "updatedAt": personal_codex.get("updatedAt") or now_iso,
            "currentTool": personal_codex.get("mode") or "personal coordination",
            "steps": steps,
        },
        "JOSHeX",
    )


def build_live_objectives(agent_feeds: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    def is_live(feed: Dict[str, Any]) -> bool:
        return bool(feed.get("active") and is_recent_ts(feed.get("updatedAt"), hours=AGENT_BRAIN_FEED_STALE_HOURS))

    def score(feed: Dict[str, Any]) -> tuple[int, float]:
        ts = iso_to_dt(feed.get("updatedAt"))
        return (1 if is_live(feed) else 0, ts.timestamp() if ts else 0.0)

    ordered = sorted(agent_feeds.values(), key=score, reverse=True)
    active_agents = [feed["agent"] for feed in ordered if is_live(feed)]
    dual_pair: List[str] = []
    if len(active_agents) >= 2:
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
    owner_to_key = {"JOSHeX": "joshex", "JOSH 2.0": "josh", "J.A.I.N": "jain", "JAIMES": "jaimes"}
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


def _is_today_et(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        today_et = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).strftime("%Y-%m-%d")
        return (parsed.astimezone(dt.timezone.utc) - dt.timedelta(hours=4)).strftime("%Y-%m-%d") == today_et
    except Exception:
        return False


def fetch_xai_specialist_usage() -> Dict[str, Any]:
    """Read dashboard-safe xAI run metadata and expose it to Model Usage."""
    empty = {
        "daily": 0.0,
        "weekly": 0.0,
        "monthly": 0.0,
        "callsToday": 0,
        "callsWeekly": 0,
        "callsMonthly": 0,
        "okToday": 0,
        "failedToday": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "totalTokens": 0,
        "outputChars": 0,
        "sourceCount": 0,
        "lastModel": "",
        "lastStatus": "",
        "lastRunAt": "",
        "available": False,
    }
    data = load_json_file(XAI_SPECIALIST_RUNS_PATH, {"runs": []})
    runs = data.get("runs") if isinstance(data, dict) else []
    if not isinstance(runs, list):
        return empty
    now = dt.datetime.now(dt.timezone.utc)
    usage = dict(empty)
    usage["available"] = True
    monthly_cutoff = now - dt.timedelta(days=30)
    weekly_cutoff = now - dt.timedelta(days=7)
    for run in runs:
        if not isinstance(run, dict):
            continue
        raw_time = str(run.get("time") or "")
        try:
            run_dt = dt.datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            if run_dt.tzinfo is None:
                run_dt = run_dt.replace(tzinfo=dt.timezone.utc)
        except Exception:
            run_dt = None
        cost = float(run.get("costUsd") or 0.0)
        is_today = _is_today_et(raw_time)
        is_week = bool(run_dt and run_dt >= weekly_cutoff)
        is_month = bool(run_dt and run_dt >= monthly_cutoff)
        if is_today:
            usage["daily"] += cost
            usage["callsToday"] += 1
            usage["okToday"] += 1 if run.get("ok") else 0
            usage["failedToday"] += 0 if run.get("ok") else 1
        if is_week:
            usage["weekly"] += cost
            usage["callsWeekly"] += 1
        if is_month:
            usage["monthly"] += cost
            usage["callsMonthly"] += 1
        usage["inputTokens"] += int(run.get("inputTokens") or 0)
        usage["outputTokens"] += int(run.get("outputTokens") or 0)
        usage["totalTokens"] += int(run.get("totalTokens") or 0)
        usage["outputChars"] += int(run.get("outputChars") or 0)
        usage["sourceCount"] += int(run.get("sourceCount") or 0)
        if not usage["lastRunAt"] or raw_time > usage["lastRunAt"]:
            usage["lastRunAt"] = raw_time
            usage["lastModel"] = str(run.get("model") or "")
            usage["lastStatus"] = str(run.get("status") or "")
    for key in ("daily", "weekly", "monthly"):
        usage[key] = round(float(usage[key]), 6)
    return usage


def inject_xai_usage_row(breakdown: List[Dict[str, Any]], xai_usage: Dict[str, Any]) -> None:
    if not xai_usage.get("available"):
        return
    model = xai_usage.get("lastModel") or "grok-4.20-reasoning"
    row_name = f"xai/{model}"
    row = {
        "name": row_name,
        "source": "xai-specialist",
        "weeklyCost": round(float(xai_usage.get("weekly") or 0.0), 6),
        "dailyCost": round(float(xai_usage.get("daily") or 0.0), 6),
        "sessionCost": round(float(xai_usage.get("weekly") or 0.0), 6),
        "totalTokens": int(xai_usage.get("totalTokens") or 0),
        "inputTokens": int(xai_usage.get("inputTokens") or 0),
        "outputTokens": int(xai_usage.get("outputTokens") or 0),
        "sessions": int(xai_usage.get("callsWeekly") or 0),
        "callsToday": int(xai_usage.get("callsToday") or 0),
        "callsWeekly": int(xai_usage.get("callsWeekly") or 0),
        "lastStatus": xai_usage.get("lastStatus") or "",
        "lastRunAt": xai_usage.get("lastRunAt") or "",
        "costEstimated": False,
        "_note": "xAI specialist broker usage; raw prompts and outputs are not stored.",
    }
    for existing in breakdown:
        if str(existing.get("name") or "").lower() == row_name.lower():
            existing.update(row)
            return
    breakdown.append(row)


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
    xai_usage = fetch_xai_specialist_usage()
    inject_xai_usage_row(breakdown, xai_usage)

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
        xai_weekly = xai_usage.get("weekly", 0.0) if xai_usage.get("available") else 0.0

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

        # Subscription-backed Codex/OpenAI usage is not marginal spend. Keep the
        # token-priced equivalent for capacity/context, but separate it from the
        # actual monthly bill Josh pays.
        subscription_monthly_fee = 200.0
        subscription_usage_equiv_daily = round(daily_cost, 6)
        subscription_usage_equiv_weekly = round(weekly_cost, 6)
        subscription_usage_equiv_monthly = round(monthly_cost, 6)
        metered_daily = round(or_daily + jain_api_daily + (xai_usage.get("daily", 0.0) if xai_usage.get("available") else 0.0), 6)
        metered_weekly = round(or_weekly + jain_api.get("weekly", 0) + xai_weekly, 6)
        metered_monthly = round(or_monthly + jain_api_monthly + (xai_usage.get("monthly", 0.0) if xai_usage.get("available") else 0.0), 6)
        total_monthly = round(subscription_monthly_fee + metered_monthly, 6)

        for row in breakdown:
            source = str(row.get("source") or "").lower()
            name = str(row.get("name") or "").lower()
            if source in {"openclaw", "codexbar"} or name.startswith("openai/"):
                row["billingMode"] = "subscription"
                row["usageEquivalentCost"] = round(float(row.get("weeklyCost") or row.get("sessionCost") or 0.0), 6)
                row["marginalCost"] = 0.0
                row["_note"] = "OpenAI/Codex subscription-backed usage; shown as usage-equivalent, not incremental spend."
            elif row.get("isLocal"):
                row["billingMode"] = "local"
                row["marginalCost"] = 0.0
            else:
                row["billingMode"] = "metered"
                row["marginalCost"] = round(float(row.get("weeklyCost") or row.get("sessionCost") or 0.0), 6)

        # ── Weekly Run Rate: actual paid layers vs subscription usage equiv ───
        automation_weekly = round(metered_weekly, 6)
        interactive_weekly = round(subscription_usage_equiv_weekly, 6)
        total_weekly_all = round(metered_weekly, 6)
        weekly_run_rate = {
            "total":       total_weekly_all,
            "automation":  automation_weekly,
            "interactive": interactive_weekly,
            "projectedMonthly": round(metered_weekly * (30 / 7), 2),
            "subscriptionUsageEquivalentWeekly": subscription_usage_equiv_weekly,
            "subscriptionUsageEquivalentProjectedMonthly": round(subscription_usage_equiv_weekly * (30 / 7), 2),
        }

        payload = {
            "session": round(current_session_cost, 6),
            "daily":   round(metered_daily,   6),
            "weekly":  round(metered_weekly,  6),
            "monthly": round(total_monthly, 6),
            "topModels": [{"name": r["name"], "window": "session", "cost": r.get("weeklyCost", 0)} for r in breakdown[:5]],
            "breakdown": breakdown,
            "lastUpdated": utc_iso(),
            "jain": jain,
            "jainApi": jain_api,
            "xai": xai_usage,
            "openrouter": openrouter,
            "elevenlabs": elevenlabs,
            "aggregate": {
                "daily":   metered_daily,
                "total":   round(metered_weekly, 6),
                "monthly": total_monthly,
            },
            "subscription": {
                "provider": "OpenAI Pro / Codex",
                "monthlyFee": subscription_monthly_fee,
                "billingMode": "subscription",
                "usageEquivalentDaily": subscription_usage_equiv_daily,
                "usageEquivalentWeekly": subscription_usage_equiv_weekly,
                "usageEquivalentMonthly": subscription_usage_equiv_monthly,
                "note": "Subscription-backed usage is capacity consumption, not incremental API spend.",
            },
            "metered": {
                "daily": metered_daily,
                "weekly": metered_weekly,
                "monthly": metered_monthly,
                "providers": {
                    "openrouter": round(or_monthly, 6),
                    "jainApi": round(jain_api_monthly, 6),
                    "xai": round(xai_usage.get("monthly", 0.0) if xai_usage.get("available") else 0.0, 6),
                },
            },
            "usageEquivalent": {
                "daily": subscription_usage_equiv_daily,
                "weekly": subscription_usage_equiv_weekly,
                "monthly": subscription_usage_equiv_monthly,
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
    env = load_env_file_values(GOG_KEYRING_ENV_PATHS, dict(os.environ))
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
            env=env,
        )
        fetch_upcoming_events._status = {"status": "ok", "message": "Connected"}  # type: ignore[attr-defined]
    except FileNotFoundError:
        fetch_upcoming_events._status = {"status": "unavailable", "message": "gog CLI missing"}  # type: ignore[attr-defined]
        print("[warn] gog CLI missing; skipping calendar fetch", file=sys.stderr)
        return []
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or '').strip()
        if 'no auth for calendar' in err.lower() or 'gog auth add' in err.lower():
            fetch_upcoming_events._status = {"status": "optional", "message": "Local calendar helper not configured"}  # type: ignore[attr-defined]
            print("[info] local calendar helper not configured; plugin-backed calendar checks remain available", file=sys.stderr)
            return []
        if 'no tty available for keyring' in err.lower() or 'gog_keyring_password' in err.lower():
            fetch_upcoming_events._status = {"status": "optional", "message": "Local calendar helper keyring locked"}  # type: ignore[attr-defined]
            print("[info] local calendar helper keyring locked; skipping calendar fetch", file=sys.stderr)
            return []
        if 'invalid_grant' in err or 'expired or revoked' in err:
            fetch_upcoming_events._status = {"status": "optional", "message": "Local calendar helper sign-in optional"}  # type: ignore[attr-defined]
            print("[info] local calendar helper sign-in is optional; skipping calendar fetch", file=sys.stderr)
            return []
        fetch_upcoming_events._status = {"status": "error", "message": "Calendar fetch failed"}  # type: ignore[attr-defined]
        print(f"[warn] gog calendar list failed: {err}", file=sys.stderr)
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
        return {
            "name": "Airpoint",
            "status": "optional",
            "detail": f"Optional service offline ({exc.returncode})",
        }
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
    # fall back to local crontab when Control Tower refresh runs on JOSH itself.
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
    josh_launch_listing = ""
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "josh2.0@100.114.50.48", "launchctl list 2>/dev/null || true"],
            capture_output=True, text=True, timeout=8
        )
        if result.returncode == 0 and result.stdout.strip():
            josh_launch_listing = result.stdout
    except (subprocess.CalledProcessError, OSError, PermissionError, subprocess.TimeoutExpired):
        josh_launch_listing = ""
    if not josh_launch_listing:
        try:
            result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, check=False)
            josh_launch_listing = result.stdout
        except (subprocess.CalledProcessError, OSError, PermissionError):
            josh_launch_listing = ""
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
    x_log_hours: dict[str, list[int]] = {}
    _jain_replies_today_from_log: list[int] = []
    hermes_jobs: dict[str, dict[str, Any]] = {}
    jain_verified_runs: dict[str, dict[str, Any]] = {}
    josh_verified_runs: dict[str, dict[str, Any]] = {}
    try:
        josh_verify_cmd = r"""python3 - <<'PY'
import datetime as dt, json, pathlib
from zoneinfo import ZoneInfo
et = ZoneInfo('America/New_York')
    jobs = {
    'Control Tower Refresh': '/Users/josh2.0/.openclaw/workspace/logs/mission-control-cron.log',
    'J.A.I.N Context Sync': '/Users/josh2.0/.openclaw/workspace/logs/mission-control-signal-refresh.log',
    'Brain Feed Server': '/Users/josh2.0/.openclaw/workspace/logs/brain_feed_server.log',
    'Chiro Invite Sync': '/Users/josh2.0/.openclaw/workspace/logs/chiro_invite_sync.log',
    'J.A.I.N Silence Detector': '/Users/josh2.0/.openclaw/workspace/logs/jain_silence_detector.log',
    'Sorare Cookie Freshness': '/Users/josh2.0/.openclaw/workspace/.sorare_cookies_fresh.json',
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
                'Control Tower Refresh': Path('/Users/josh2.0/.openclaw/workspace/logs/mission-control-cron.log'),
                'J.A.I.N Context Sync': Path('/Users/josh2.0/.openclaw/workspace/logs/mission-control-signal-refresh.log'),
                'Brain Feed Server': Path('/Users/josh2.0/.openclaw/workspace/logs/brain_feed_server.log'),
                'Chiro Invite Sync': Path('/Users/josh2.0/.openclaw/workspace/logs/chiro_invite_sync.log'),
                'J.A.I.N Silence Detector': Path('/Users/josh2.0/.openclaw/workspace/logs/jain_silence_detector.log'),
                'Sorare Cookie Freshness': Path('/Users/josh2.0/.openclaw/workspace/.sorare_cookies_fresh.json'),
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
            "echo '===XLOG==='; grep -E '[0-9]{2}:[0-9]{2}:[0-9]{2}.*X Post Agent|Posted' "
            "  /Users/jc_agent/.openclaw/workspace/logs/x_post_agent.log 2>/dev/null | tail -50 || true; "
            "echo '===REPLY==='; cat /Users/jc_agent/.openclaw/workspace/mission-control/data/x_reply_state.json 2>/dev/null || echo '{}'; "
            f"echo '===SORAREMISSIONS==='; tail -8 /Users/jc_agent/scripts/logs/sorare_missions.log 2>/dev/null || echo ''; "
            f"echo '===SORARELINEUPS==='; tail -8 /Users/jc_agent/scripts/logs/sorare_lineups.log 2>/dev/null || echo ''; "
            f"echo '===STRATEGICREPLIES==='; grep -E '^\\[([0-9]{{2}}):' /Users/jc_agent/.openclaw/workspace/logs/x_strategic_reply.log 2>/dev/null | tail -20 || echo ''; "
            f"echo '===HERMESJOBS==='; cat /Users/jc_agent/.hermes/cron/jobs.json 2>/dev/null || echo '{{}}'"
        )
        r = None
        for host in ("jaimes-via-josh", "jc_agent@100.121.89.84"):
            candidate = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                 host, jain_batch_cmd],
                capture_output=True, text=True, timeout=12
            )
            if candidate.returncode == 0:
                r = candidate
                break
        if r is not None and r.returncode == 0:
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
             "jaimes-via-josh", jain_verify_cmd],
            capture_output=True, text=True, timeout=12
        )
        if vr.returncode == 0:
            jain_verified_runs = json.loads(vr.stdout.strip() or "{}")
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
                    x_log_hours.setdefault(job_name, []).append(h)
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


    def codex_automation_state(target: Dict[str, Any]) -> Dict[str, Any]:
        automation_id = str(target.get("automationId") or target.get("pattern") or "").strip()
        path = CODEX_AUTOMATIONS_DIR / automation_id
        toml_path = path / "automation.toml"
        memory_path = path / "memory.md"
        state: Dict[str, Any] = {
            "present": bool(target.get("assumePresent")),
            "active": bool(target.get("assumePresent")),
            "lastRun": None,
            "verifiedToday": False,
        }
        try:
            status_data = load_json_file(CODEX_AUTOMATION_STATUS_PATH, {"automations": {}})
            status_rows = status_data.get("automations") if isinstance(status_data, dict) else {}
            status_row = status_rows.get(automation_id) if isinstance(status_rows, dict) else None
            if isinstance(status_row, dict):
                state["present"] = bool(status_row.get("present", state["present"]))
                state["active"] = bool(status_row.get("active", state["active"]))
                if status_row.get("lastRun"):
                    latest = str(status_row.get("lastRun")).rstrip(".")
                    state["lastRun"] = latest
                    parsed = _dt.datetime.fromisoformat(latest.replace("Z", "+00:00"))
                    state["verifiedToday"] = parsed.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d") == today_str
        except Exception:
            pass
        try:
            if toml_path.exists():
                state["present"] = True
                if tomllib is not None:
                    with toml_path.open("rb") as fh:
                        meta = tomllib.load(fh)
                    status = str(meta.get("status") or "").upper()
                    state["active"] = status == "ACTIVE"
                else:
                    text = toml_path.read_text(errors="ignore")
                    state["active"] = 'status = "ACTIVE"' in text
        except Exception:
            pass
        try:
            if memory_path.exists():
                text = memory_path.read_text(errors="ignore")
                matches = _re.findall(r"Current run time:\s*([0-9T:Z+.-]+)", text)
                if matches:
                    latest = matches[-1].rstrip(".")
                    state["lastRun"] = latest
                    parsed = _dt.datetime.fromisoformat(latest.replace("Z", "+00:00"))
                    state["verifiedToday"] = parsed.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d") == today_str
        except Exception:
            pass
        return state


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
        codex_state = codex_automation_state(target) if source == 'codex_automation' else None
        hermes_job = hermes_jobs.get(target.get('hermesName', '')) if source == 'hermes' else None
        jain_verified = jain_verified_runs.get(target['name']) if is_jain else None
        josh_verified = josh_verified_runs.get(target['name']) if not is_jain else None
        if source == 'hermes':
            present = bool(hermes_job)
        elif source == 'launchd':
            present = target['pattern'] in josh_launch_listing
        elif source == 'codex_automation':
            present = bool(codex_state and codex_state.get("present"))
        else:
            present = target['pattern'] in listing
        sched_meta = schedule_today_meta(target.get('schedule', ''))
        today_relevant = bool(sched_meta.get('todayRelevant', True))
        source_label = 'Codex Automation' if source == 'codex_automation' else 'Josh LaunchAgent' if source == 'launchd' else 'Hermes' if source == 'hermes' else 'J.A.I.N Cron' if is_jain else 'Josh Local Cron'

        # Compute runStatus for daily jobs
        sched = target.get('schedule', '')
        run_status = None  # 'done' | 'missed' | 'upcoming' | None
        last_run = x_log_runs.get(target['name'])
        if not last_run and codex_state and codex_state.get("lastRun"):
            last_run = str(codex_state.get("lastRun"))
        if not last_run and hermes_job:
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
        hermes_next_run = hermes_job.get('next_run_at') if hermes_job else None
        hermes_next_dt = None
        if hermes_next_run:
            try:
                hermes_next_dt = _dt.datetime.fromisoformat(str(hermes_next_run).replace('Z', '+00:00')).astimezone(ZoneInfo("America/New_York"))
            except Exception:
                hermes_next_dt = None
        verified_today = bool(
            (codex_state and codex_state.get('verifiedToday')) or
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
                elif hermes_next_dt is not None and hermes_next_dt > now_et:
                    run_status = 'upcoming'
                elif now_hour > sched_hour or (now_hour == sched_hour and now_min >= sched_min + 10):
                    run_status = 'missed' if can_verify_run else 'due'
                else:
                    run_status = 'upcoming'
        elif today_relevant and sched_meta.get('kind') in {'recurring', 'calendar_split'}:
            run_status = 'active' if present else 'due'
        elif target['name'] == 'X Strategic Replies':
            if last_run_today:
                run_status = 'done'
            elif now_et.hour >= 9:
                run_status = 'upcoming'

        if source == 'codex_automation' and codex_state and not codex_state.get('active'):
            row_status = 'paused'
        elif source == 'hermes' and hermes_job and not hermes_job.get('enabled', True):
            row_status = 'paused'
        elif last_run_today or verified_today:
            row_status = 'ok'
        else:
            row_status = 'ok' if present else 'paused'

        if row_status == 'paused' and run_status == 'due':
            run_status = 'upcoming'

        # Hermes persists the last job result until the next scheduled run. Treat
        # old failures as historical context, not active Control Tower cron
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
        if hermes_next_run:
            row['nextRun'] = hermes_next_run

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
            elif target['name'] == 'X Quote Tweets':
                done_hours = set(x_log_hours.get(target['name'], []))
                for run in runs:
                    t_str = run['time']
                    try:
                        t = _dt.datetime.strptime(t_str, "%I:%M %p").replace(
                            year=now_et.year, month=now_et.month, day=now_et.day,
                            tzinfo=_dt.timezone(_dt.timedelta(hours=-4))
                        )
                        run['past'] = now_et >= t
                        run['done'] = t.hour in done_hours
                    except Exception:
                        run['past'] = False
                        run['done'] = False
                if any(run.get('done') for run in runs):
                    row['runStatus'] = 'done'
                elif any(run.get('past') for run in runs):
                    row['runStatus'] = 'due'
                elif 'runStatus' not in row:
                    row['runStatus'] = 'upcoming'
            else:
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


def fetch_runtime_layout_status() -> Dict[str, Any]:
    data = load_json_file(RUNTIME_LAYOUT_PATH, {})
    if isinstance(data, dict) and data:
        issues = data.get("issues") if isinstance(data.get("issues"), list) else []
        return {
            **data,
            "status": "ok" if data.get("ok") and not issues else "attention",
            "summary": (
                "Live kiosk layout fits the 24-inch screen"
                if data.get("ok") and not issues
                else f"Live kiosk layout needs attention: {plain_dashboard_text('; '.join(map(str, issues)) or 'check layout', 160)}"
            ),
        }
    return {
        "ok": False,
        "status": "unknown",
        "checkedAt": None,
        "summary": "Live kiosk layout check has not run yet.",
        "issues": [],
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
    cal = calendar_health or {}
    cal_status = str(cal.get("status") or "").lower()
    cal_message = str(cal.get("message") or "").lower()
    cal_optional = (
        cal_status in {"unavailable", "unknown", "optional"}
        or "gog cli missing" in cal_message
        or "local calendar helper" in cal_message
    )
    cal_ok = cal_status == "ok"
    cal_clear = cal_ok or cal_optional
    gmail = load_json_file(JOSH_OPS_GMAIL_STATUS_PATH, {})
    gmail_status = str(gmail.get("status") or "planned").lower() if isinstance(gmail, dict) else "planned"
    gmail_ok = gmail_status == "done"
    checked_at = gmail.get("checkedAt") if isinstance(gmail, dict) else None

    def checked_today(value: Any) -> bool:
        try:
            parsed = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.astimezone(ZoneInfo("America/New_York")).date() == _dt.datetime.now(ZoneInfo("America/New_York")).date()
        except Exception:
            return False

    def actionable_due(row: Dict[str, Any]) -> bool:
        if not (row.get("todayRelevant") and row.get("status") != "paused" and row.get("runStatus") == "missed"):
            return False
        text = f"{row.get('name', '')} {row.get('description', '')}".lower()
        if row.get("source") == "codex_automation":
            return False
        if gmail_ok and checked_today(checked_at) and ("gmail" in text or "inbox" in text):
            return False
        return True

    due = [c for c in crons if actionable_due(c)]
    return {
        "status": "clear" if cal_clear and gmail_ok and not due else "attention",
        "summary": "Shared Gmail/Calendar/Drive/Tasks command queue foundation",
        "calendar": "connected" if cal_ok else "optional helper skipped" if cal_optional else "needs attention",
        "sharedGmail": "monitored" if gmail_ok else ("blocked" if gmail_status == "blocked" else "needs setup"),
        "sharedGmailAccount": "jcubellagent@gmail.com",
        "sharedGmailCheckedAt": checked_at,
        "sharedGmailUnreadBefore": gmail.get("unreadBeforeCapped") if isinstance(gmail, dict) else None,
        "sharedGmailMarkedRead": gmail.get("markedRead") if isinstance(gmail, dict) else None,
        "jobIssues": len(due),
        "sources": ["calendar", "gmail", "drive", "tasks"],
    }


def normalize_priority(value: Any) -> str:
    priority = str(value or "medium").lower()
    if priority in {"high", "medium", "low"}:
        return priority
    return "medium"


def normalize_personal_codex(raw: Any, now_iso: str) -> Dict[str, Any]:
    """Load the local Personal Codex lane without requiring live-system access."""
    if not isinstance(raw, dict):
        raw = {}

    status = str(raw.get("status") or "offline").lower()
    if status not in {"ready", "working", "blocked", "needs_josh", "offline"}:
        status = "ready" if raw else "offline"

    repo = raw.get("repo") if isinstance(raw.get("repo"), dict) else {}
    validation = raw.get("validation") if isinstance(raw.get("validation"), dict) else {}
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    links = raw.get("links") if isinstance(raw.get("links"), list) else []

    action_items: List[Dict[str, str]] = []
    for item in raw.get("actionRequired", []) if isinstance(raw.get("actionRequired"), list) else []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        action_items.append({
            "priority": normalize_priority(item.get("priority")),
            "title": title,
            "url": str(item.get("url") or "#personal-codex"),
        })

    recent_items: List[Dict[str, str]] = []
    for item in raw.get("recentActivity", []) if isinstance(raw.get("recentActivity"), list) else []:
        if not isinstance(item, dict):
            continue
        event = str(item.get("event") or item.get("title") or "").strip()
        if not event:
            continue
        recent_items.append({
            "time": str(item.get("time") or raw.get("updatedAt") or now_iso),
            "event": event,
        })

    updated_at = raw.get("updatedAt") if is_valid_iso8601(raw.get("updatedAt")) else now_iso
    return {
        "status": status,
        "objective": str(raw.get("objective") or ""),
        "updatedAt": updated_at,
        "summary": str(raw.get("summary") or "Personal Codex sidecar is not reporting yet."),
        "mode": str(raw.get("mode") or "local"),
        "repo": {
            "path": str(repo.get("path") or ""),
            "branch": str(repo.get("branch") or ""),
            "dirty": bool(repo.get("dirty", False)),
        },
        "validation": {
            "pyCompile": str(validation.get("pyCompile") or "unknown"),
            "regression": str(validation.get("regression") or "unknown"),
            "visualCanaries": str(validation.get("visualCanaries") or "unknown"),
        },
        "actionRequired": action_items[:5],
        "recentActivity": recent_items[:5],
        "metrics": {
            "openQuestions": int(metrics.get("openQuestions") or 0),
            "localChanges": int(metrics.get("localChanges") or 0),
            "lastValidationMinutesAgo": int(metrics.get("lastValidationMinutesAgo") or 0),
        },
        "links": [link for link in links if isinstance(link, dict)][:5],
    }


def node_display_name(value: Any) -> str:
    text = str(value or "").strip()
    return {
        "josh2-lan": "Josh 2.0",
        "jaimes-via-josh": "JAIMES/J.A.I.N",
        "joshex": "JOSHeX",
        "macbook-codex": "JOSHeX",
    }.get(text, text or "agent node")


def capability_tool_ready(node: Dict[str, Any], key: str) -> bool:
    tool = node.get(key)
    return isinstance(tool, dict) and bool(tool.get("available")) and str(tool.get("status") or "ready") == "ready"


def capability_version(node: Dict[str, Any], key: str) -> str:
    tool = node.get(key)
    if not isinstance(tool, dict):
        return ""
    return str(tool.get("version") or "").strip()


def inventory_nodes(capability_inventory: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not isinstance(capability_inventory, dict):
        return []
    return [node for node in capability_inventory.get("nodes", []) if isinstance(node, dict)]


def build_runtime_inventory_capability(capability_inventory: Dict[str, Any] | None) -> Dict[str, Any] | None:
    nodes = inventory_nodes(capability_inventory)
    if not nodes:
        return None
    openclaw_nodes = [node for node in nodes if capability_tool_ready(node, "openclawCli")]
    hermes_nodes = [node for node in nodes if capability_tool_ready(node, "hermesCli")]
    codex_nodes = [node for node in nodes if capability_tool_ready(node, "codexCli")]
    gemini_nodes = [node for node in nodes if capability_tool_ready(node, "geminiCli")]
    health_attention = [
        node_display_name(node.get("node"))
        for node in nodes
        if isinstance(node.get("openclawHealth"), dict)
        and node["openclawHealth"].get("available")
        and node["openclawHealth"].get("status") not in {"ok", "ready"}
    ]
    versions = []
    for node in openclaw_nodes[:4]:
        version = capability_version(node, "openclawCli").replace("OpenClaw ", "")
        if version:
            versions.append(f"{node_display_name(node.get('node'))} {version}")
    summary = (
        f"{len(openclaw_nodes)} OpenCLAW · {len(hermes_nodes)} Hermes · "
        f"{len(codex_nodes)} Codex · {len(gemini_nodes)} Gemini-ready"
    )
    return {
        "id": "runtime-inventory",
        "name": "Runtime Inventory",
        "status": "attention" if health_attention else "ok",
        "summary": summary,
        "detail": "; ".join(versions[:3]) or f"{len(nodes)} node(s) inventoried",
    }


def build_task_ledger_capability(capability_inventory: Dict[str, Any] | None) -> Dict[str, Any] | None:
    nodes = inventory_nodes(capability_inventory)
    ledgers = [
        (node_display_name(node.get("node")), node.get("openclawTaskLedger"))
        for node in nodes
        if isinstance(node.get("openclawTaskLedger"), dict) and node["openclawTaskLedger"].get("available")
    ]
    if not ledgers:
        return None
    total_errors = 0
    total_warnings = 0
    details: list[str] = []
    for label, ledger in ledgers:
        summary = ledger.get("summary") if isinstance(ledger.get("summary"), dict) else {}
        errors = int(summary.get("errors") or 0)
        warnings = int(summary.get("warnings") or 0)
        total_errors += errors
        total_warnings += warnings
        details.append(f"{label}: {errors} errors / {warnings} warnings")
    return {
        "id": "task-ledger",
        "name": "Task Ledger",
        "status": "attention" if total_errors else "watch" if total_warnings else "ok",
        "summary": f"{total_errors} active ledger error(s), {total_warnings} warning(s)",
        "detail": " · ".join(details[:3]),
    }


def build_capability_watch_capability(capability_watch: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not isinstance(capability_watch, dict):
        return None
    recommendations = capability_watch.get("recommendations") if isinstance(capability_watch.get("recommendations"), list) else []
    attention = [row for row in recommendations if isinstance(row, dict) and row.get("status") in {"new", "upgrade", "attention"}]
    return {
        "id": "capability-watch",
        "name": "Capability Watch",
        "status": "watch" if attention else capability_watch.get("status") or "ok",
        "summary": capability_watch.get("summary") or f"{len(recommendations)} recommendation(s) tracked",
        "detail": f"Updated {plain_dashboard_text(capability_watch.get('checkedAt') or capability_watch.get('updatedAt') or 'pending', 80)}",
    }


def build_capability_stack(
    visual_canaries: Dict[str, Any],
    sorare_ml: Dict[str, Any],
    voice_router: Dict[str, Any],
    ops_inbox: Dict[str, Any],
    agent_control: Dict[str, Any] | None = None,
    personal_codex: Dict[str, Any] | None = None,
    capability_inventory: Dict[str, Any] | None = None,
    capability_watch: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    agent_summary = agent_control.get("summary", {}) if isinstance(agent_control, dict) else {}
    stack = [
        {
            "id": "visual-canaries",
            "name": "Visual Canaries",
            "status": visual_canaries.get("status") or ("ok" if visual_canaries.get("ok") else "attention"),
            "summary": visual_canaries.get("summary") or "Control Tower guardrails",
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
            "detail": (
                f"Calendar {ops_inbox.get('calendar')} · "
                f"Shared Gmail {ops_inbox.get('sharedGmail')} · "
                f"{ops_inbox.get('jobIssues', 0)} job issue(s)"
            ),
        },
    ]
    if agent_summary:
        ready = agent_summary.get("readyAgents", 0)
        total = agent_summary.get("totalAgents", 0)
        failed = agent_summary.get("failedQueues", 0)
        dirty = agent_summary.get("dirtyRepos", 0)
        live_heartbeat_source = (
            agent_summary.get("source") == "live-heartbeats"
            or (isinstance(agent_control, dict) and agent_control.get("statusSource") == "live-heartbeats")
        )
        stack.append({
            "id": "agent-control",
            "name": "Agent Control",
            "status": agent_summary.get("overall") or "unknown",
            "summary": f"{ready}/{total} live lanes ready" if live_heartbeat_source else f"{ready}/{total} agent nodes ready",
            "detail": "Fresh Brain Feed heartbeats from the tracked agents" if live_heartbeat_source else f"{failed} queue item(s) · {dirty} dirty repo(s)",
        })
    if personal_codex and personal_codex.get("status") != "offline":
        stack.append({
            "id": "personal-codex",
            "name": "Personal Codex",
            "status": personal_codex.get("status") or "ready",
            "summary": personal_codex.get("summary"),
            "detail": personal_codex.get("objective") or "Local Control Tower contribution lane",
        })
    for item in (
        build_runtime_inventory_capability(capability_inventory),
        build_task_ledger_capability(capability_inventory),
        build_capability_watch_capability(capability_watch),
    ):
        if item:
            stack.append(item)
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
            "name": "Control Tower",
            "url": mission_control_url,
            "status": "live",
            "lastChecked": now_iso,
        },
        {
            "name": "Control Tower PWA",
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

    recent_files = [plain_dashboard_text(path, 120) for path in recent_code_files()]
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
) -> List[Dict[str, str]]:
    """High-signal one-stop-shop alerts for Josh-facing ops health."""
    items: List[Dict[str, str]] = []

    cal = calendar_health or {}
    cal_status = str(cal.get("status") or "unknown").lower()
    cal_msg = str(cal.get("message") or "Calendar lane unavailable")
    local_calendar_optional = (
        cal_status in {"unavailable", "unknown", "optional"}
        or "gog cli missing" in cal_msg.lower()
        or "local calendar helper" in cal_msg.lower()
    )
    if cal_status not in {"ok", "green", "healthy"} and not local_calendar_optional:
        title = "Calendar auth needs refresh" if "auth" in cal_msg.lower() else f"Calendar issue: {cal_msg}"
        items.append({"priority": "high", "title": title, "url": "#calendar"})

    missed = [
        c for c in crons
        if c.get("todayRelevant")
        and c.get("status") != "paused"
        and c.get("runStatus") == "missed"
        and c.get("source") != "codex_automation"
    ]
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
        attention = [
            device
            for device in devices
            if device.get("status") in {"attention", "blocked", "error"}
        ]
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

    items.append({"time": now_iso, "event": "Control Tower refresh published"})
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
    Josh 2.0 local live Brain Feed is the source of truth for active state.
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
            "context": "Control Tower is running.",
            "runway": 0.98,
            "updatedAt": now_iso,
        },
        "brainFeed": dict(DEFAULT_BRAIN_FEED),
        "devices": [],
        "products": [],
        "crons": [],
        "codexJobs": [],
        "sharedEvents": [],
        "sharedOperatingLayer": {},
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
    dashboard["modelRouter"] = build_model_router_status(model_usage, now_iso)
    if isinstance(model_usage, dict):
        model_usage["providerBudgets"] = dashboard["modelRouter"].get("providers", [])
        model_usage["routerPolicy"] = dashboard["modelRouter"].get("policy", {})

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
    dashboard["codexJobs"]      = fetch_codex_jobs(now_iso)
    dashboard["sharedEvents"]   = fetch_shared_events(now_iso)
    dashboard["sharedOperatingLayer"] = fetch_shared_operating_layer(now_iso)
    dashboard["visualCanaries"] = fetch_visual_canaries()
    dashboard["runtimeLayout"] = fetch_runtime_layout_status()
    dashboard["sorareMlCockpit"] = fetch_sorare_ml_cockpit()
    dashboard["voiceRouter"] = fetch_voice_router_status()
    dashboard["opsInbox"] = fetch_ops_inbox_status(dashboard["calendarHealth"], dashboard["crons"])
    dashboard["personalCodex"] = normalize_personal_codex(load_json_file(PERSONAL_CODEX_PATH, {}), now_iso)
    dashboard["agentControl"] = load_agent_control_status(now_iso)
    dashboard["agentContextRegistry"] = load_json_file(AGENT_CONTEXT_REGISTRY_PATH, {
        "generatedAt": now_iso,
        "canonicalSource": "Control Tower shared sidecars plus Josh 2.0 local live Brain Feed lane.",
        "privacy": "dashboard-safe summaries only",
        "summary": {"status": "unknown", "agents": 0, "staleAgents": [], "openTasks": 0, "openHandoffs": 0},
        "agents": {},
    })
    dashboard["reliabilityUpgrades"] = load_json_file(RELIABILITY_UPGRADES_PATH, {
        "updatedAt": now_iso,
        "summary": "Reliability upgrade probes have not run yet.",
        "items": [],
        "metrics": [],
    })
    dashboard["capabilityInventory"] = load_json_file(CAPABILITY_INVENTORY_PATH, {
        "updatedAt": now_iso,
        "nodes": [],
    })
    dashboard["capabilityWatch"] = load_json_file(CAPABILITY_WATCH_PATH, {
        "updatedAt": now_iso,
        "status": "pending",
        "summary": "Capability Watch has not run yet.",
        "recommendations": [],
    })
    dashboard["telegramAiBotFeatures"] = load_json_file(TELEGRAM_AI_BOT_FEATURES_PATH, {
        "updatedAt": now_iso,
        "status": "unknown",
        "summary": "Telegram AI bot feature policy has not been generated yet.",
        "features": [],
    })
    dashboard["capabilityStack"] = build_capability_stack(
        dashboard["visualCanaries"],
        dashboard["sorareMlCockpit"],
        dashboard["voiceRouter"],
        dashboard["opsInbox"],
        dashboard["agentControl"],
        dashboard["personalCodex"],
        dashboard["capabilityInventory"],
        dashboard["capabilityWatch"],
    )
    dashboard["actionRequired"] = build_action_required(
        now_iso,
        dashboard["calendarHealth"],
        dashboard["crons"],
        moltworld_data,
    )
    for item in dashboard["personalCodex"].get("actionRequired", []):
        dashboard["actionRequired"].append({
            "priority": item.get("priority", "medium"),
            "title": f"Personal Codex: {item.get('title')}",
            "url": item.get("url") or "#personal-codex",
        })
    dashboard["actionRequired"] = dashboard["actionRequired"][:8]
    if dashboard["visualCanaries"].get("status") == "attention":
        dashboard["actionRequired"].insert(0, {
            "priority": "high",
            "title": f"Control Tower canary issue: {dashboard['visualCanaries'].get('summary', 'check dashboard')}",
            "url": "#canaries",
        })
    if dashboard["runtimeLayout"].get("status") == "attention":
        dashboard["actionRequired"].insert(0, {
            "priority": "high",
            "title": "Control Tower layout issue: live kiosk no longer fits cleanly",
            "detail": dashboard["runtimeLayout"].get("summary") or "Check the Josh 2.0 display layout.",
            "url": "#brain-feed",
        })
    shared_layer = dashboard.get("sharedOperatingLayer", {})
    if shared_layer.get("status") == "attention":
        dashboard["actionRequired"].insert(0, shared_layer_attention_item(shared_layer))
    dashboard["actionRequired"] = dashboard["actionRequired"][:8]
    dashboard["trackedTasks"]   = fetch_tracked_tasks()
    dashboard["activeAgents"]   = _f_agents.result() + build_visibility_agents(agent_bus_tasks, coding_visibility, context_watchdog)

    joshex_brain_feed = normalize_agent_brain_feed(load_json_file(JOSHEX_BRAIN_FEED_PATH, {}), "JOSHeX")
    if not joshex_brain_feed.get("updatedAt"):
        joshex_brain_feed = personal_codex_brain_feed(dashboard["personalCodex"], now_iso)
    josh_brain_feed = agent_specific_brain_feed(dashboard["brainFeed"], "josh", "JOSH 2.0")
    jain_brain_feed = normalize_agent_brain_feed(load_json_file(ROOT.parent / "data" / "jain-brain-feed.json", {}), "J.A.I.N")
    jaimes_brain_feed = normalize_agent_brain_feed(load_json_file(ROOT.parent / "data" / "jaimes-brain-feed.json", {}), "JAIMES")
    dashboard["agentBrainFeeds"] = apply_tracked_tasks_to_agent_feeds(
        {
            "joshex": joshex_brain_feed,
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
    dashboard["joshBrainFeed"] = josh_brain_feed
    dashboard["jainBrainFeed"] = jain_brain_feed
    dashboard["jaimesBrainFeed"] = jaimes_brain_feed
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
    )
    if dashboard["personalCodex"].get("status") != "offline":
        codex_activity = dashboard["personalCodex"].get("recentActivity") or [{
            "time": dashboard["personalCodex"].get("updatedAt") or now_iso,
            "event": f"Personal Codex: {dashboard['personalCodex'].get('status', 'ready')}",
        }]
        dashboard["recentActivity"] = [
            {
                "time": item.get("time") or dashboard["personalCodex"].get("updatedAt") or now_iso,
                "event": item.get("event") if str(item.get("event", "")).startswith("Personal Codex:")
                else f"Personal Codex: {item.get('event', dashboard['personalCodex'].get('summary', 'updated'))}",
            }
            for item in codex_activity[:2]
        ] + dashboard["recentActivity"]
        seen_activity: set[str] = set()
        dashboard["recentActivity"] = [
            item for item in dashboard["recentActivity"]
            if not (item.get("event", "") in seen_activity or seen_activity.add(item.get("event", "")))
        ][:6]
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
