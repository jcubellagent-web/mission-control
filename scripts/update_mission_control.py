#!/usr/bin/env python3
"""Update Mission Control dashboard JSON with live data."""
from __future__ import annotations

import datetime as dt
import json
import os
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
MODEL_USAGE_PATH = ROOT.parent / "data" / "modelUsage.json"
NEXT_BASE = "http://127.0.0.1:3030"
WORKSPACE_ROOT = ROOT.parent.parent
KIOSK_MODEL_USAGE_PATH = WORKSPACE_ROOT / "kiosk-dashboard" / "data" / "modelUsage.json"

CRON_TARGETS = [
    # ── JOSH 2.0 (this machine) ──────────────────────────────────────────────
    {"name": "Mission Control Refresh", "pattern": "mission-control/scripts/update_and_push.sh", "schedule": "Every 10 min", "description": "Pushes live dashboard data to GitHub Pages", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "Brain Feed Server", "pattern": "brain_feed_server.py", "schedule": "Every 2 min (keepalive)", "description": "Real-time brain feed server for live dashboard laser + status updates", "category": "Maintenance", "agent": "JOSH 2.0"},
    {"name": "Chiro Invite Sync", "pattern": "scripts/chiro_invite_sync.sh", "schedule": "Hourly", "description": "Syncs chiropractic client invites to calendar", "category": "Appointments", "agent": "JOSH 2.0"},
    # ── J.A.I.N (background compute) ────────────────────────────────────────
    {"name": "Lineup Check", "pattern": "fantasy_lineup_check.py", "schedule": "Mon 9:15 AM ET", "description": "Reviews starting lineup, flags IL players in active slots", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Injury Monitor", "pattern": "fantasy_injury_monitor.py", "schedule": "Mon 9:00 AM", "description": "Checks for injuries before Monday lineup lock — runs 15 min before lineup check", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Waiver Scan", "pattern": "fantasy_waiver_scan.py", "schedule": "Wed + Fri 9am", "description": "Scans top free agents and recommends add/drop moves", "category": "Fantasy Baseball", "agent": "J.A.I.N", "jain": True},
    {"name": "Sorare Daily Missions", "pattern": "sorare_mlb_bot.py --missions-only", "schedule": "Daily 10:00 AM ET", "description": "Submits all 5 daily missions (Save Picker, SP Classic, etc.) via sorare_mlb_bot.py --missions-only", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},
    {"name": "Sorare Competition Lineups", "pattern": "sorare_mlb_bot.py --lineups-only", "schedule": "Daily 11:00 AM ET", "description": "Sets Champion L1, Champion L2, Challenger, and Hot Streak lineups (priority order, no card reuse) via sorare_mlb_bot.py --lineups-only", "category": "Sorare MLB", "agent": "J.A.I.N", "jain": True},
    {"name": "Breaking News Scanner", "pattern": "breaking_news_scanner.py", "schedule": "Every 5 min", "description": "Scans high-signal breaking news + Trump statements. Pushes score ≥8.5 to @JAIN_BREAKING_BOT", "category": "Intelligence Feed", "agent": "J.A.I.N", "jain": True},
    {"name": "X Watchlist Monitor", "pattern": "x_watchlist_monitor.py", "schedule": "Every 5 min", "description": "Monitors X/Twitter watchlist for high-signal posts (score ≥8), pushes to @JAIN_BREAKING_BOT", "category": "Intelligence Feed", "agent": "J.A.I.N", "jain": True},
    # ── X Account Growth Strategy ──────────────────────────────────────────────
    # Originals: 7/day via x_post_agent.py (J.A.I.N)
    # Strategic Replies: 8/day via x_strategic_reply.py (J.A.I.N) — browser/cookie session
    #   - xAI live search finds fresh tweets (<4h) from 20+ high-follower target accounts
    #   - Scores by freshness + engagement signal, skips already-replied tweets
    #   - Gemini generates sharp value-adding reply (220 char max, no hashtags)
    #   - Posts via Playwright browser with human-like delays
    # Quote Tweets: 4/day via x_post_agent.py (J.A.I.N)
    {"name": "X Feedback Loop",   "pattern": "x_post_agent.py", "schedule": "Daily 6:00 AM ET",  "description": "Pulls analytics, updates strategy, fires milestone alerts", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    # Originals (7/day)
    {"name": "X Pre-Market",      "pattern": "x_post_agent.py", "schedule": "Daily 7:00 AM ET",  "description": "[Original] Futures + overnight signals", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Market Open",     "pattern": "x_post_agent.py", "schedule": "Daily 8:00 AM ET",  "description": "[Original] Market open macro take", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Mover",           "pattern": "x_post_agent.py", "schedule": "Daily 11:00 AM ET", "description": "[Original] Mid-morning mover / stat", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Hot Take",        "pattern": "x_post_agent.py", "schedule": "Daily 12:00 PM ET", "description": "[Original] Bold contrarian take — max reply bait", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Market Close",    "pattern": "x_post_agent.py", "schedule": "Daily 5:00 PM ET",  "description": "[Original] Close wrap + next-day outlook", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Prime Take",      "pattern": "x_post_agent.py", "schedule": "Daily 9:00 PM ET",  "description": "[Original] Prime time hot take", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "X Nightcap",        "pattern": "x_post_agent.py", "schedule": "Daily 10:00 PM ET", "description": "[Original] One sharp insight to end the day", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    # Strategic Replies (8 slots/day — browser/cookie, fresh <4h tweets only)
    {"name": "X Strategic Replies", "pattern": "x_strategic_reply.py", "schedule": "8x daily (9am–11pm ET)", "description": "[Reply] xAI finds fresh high-visibility tweets from @elonmusk, @sama, @pmarca, @saylor + 20 others → Gemini reply → Playwright browser post. Scores by freshness + likes. Never double-replies.", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    # Quote Tweets (4 slots/day — breaking news + viral AI/finance posts)
    {"name": "X Quote Tweets",    "pattern": "x_post_agent.py", "schedule": "Daily 10am/1pm/6pm/8pm ET", "description": "[QT] Find breaking/viral posts, quote with our take (3-4 slots)", "category": "X Account", "agent": "J.A.I.N", "jain": True},
    {"name": "Intelligence Feed", "pattern": "intelligence_feed.py", "schedule": "8x weekday / 2x weekend", "description": "Full AI/macro/crypto/market intelligence briefing pushed to @Jain_win_news_bot", "category": "Intelligence Feed", "agent": "J.A.I.N", "jain": True,
     "multiRun": {
         "runs": [
             {"time": "7:15 AM",  "mode": "Full",  "label": "Market open (weekday)"},
             {"time": "10:00 AM", "mode": "Full",  "label": "Mid-morning"},
             {"time": "12:00 PM", "mode": "Full",  "label": "Midday (weekday)"},
             {"time": "2:00 PM",  "mode": "Full",  "label": "Pulse (weekday)"},
             {"time": "4:15 PM",  "mode": "Full",  "label": "Close"},
             {"time": "6:00 PM",  "mode": "Full",  "label": "Evening (weekday)"},
             {"time": "6:15 PM",  "mode": "Full",  "label": "Evening (weekend)"},
             {"time": "9:00 PM",  "mode": "Full",  "label": "Late (weekday)"},
             {"time": "11:00 PM", "mode": "Full",  "label": "Wrap (weekday)"},
         ]
     }
    },
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
            raw_model = sess.get("model") or sess.get("modelOverride") or ""
            provider = sess.get("modelProvider") or ""
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
        # Reset weekly if new week
        if a.get("weekKey") != week:
            a["weekly"] = 0.0
            a["weekKey"] = week
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
    # Primary: ~/.secrets/openrouter_api_key.txt
    key_file = Path.home() / ".secrets" / "openrouter_api_key.txt"
    if key_file.exists():
        k = key_file.read_text().strip()
        if k: keys_to_try.append(k)
    # Secondary: openclaw auth-profiles
    auth_path = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if auth_path.exists():
        try:
            import json as _json
            d = _json.loads(auth_path.read_text())
            k = d.get("profiles", {}).get("openrouter:default", {}).get("key", "")
            if k and k not in keys_to_try: keys_to_try.append(k)
        except: pass
    # Fallback: secrets env file
    sec_key_path = Path(os.path.expanduser("~/.openclaw/workspace/secrets/openrouter.env"))
    if sec_key_path.exists():
        for line in sec_key_path.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                sec_key = line.split("=", 1)[1].strip()
                if sec_key and sec_key not in keys_to_try:
                    keys_to_try.append(sec_key)

    agg: Dict[str, float] = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0,
                              "byok_daily": 0.0, "byok_weekly": 0.0, "byok_monthly": 0.0}
    any_ok = False
    seen_user_ids: set = set()

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
            print(f"[warn] fetch_openrouter_usage key={key[:20]}... failed: {exc}", file=sys.stderr)

    if not any_ok:
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


def fetch_model_usage() -> Dict[str, Any] | None:
    # Primary: merge OpenClaw sessions (all models incl. Gemini) + codexbar (precise Codex costs)
    session_rows = fetch_model_usage_from_sessions()
    codexbar_rows = fetch_model_usage_from_codexbar()
    breakdown = merge_model_rows(session_rows, codexbar_rows)

    if breakdown:
        session_cost = sum(r.get("weeklyCost", 0) for r in breakdown)

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
        jain = fetch_jain_model_usage()
        openrouter = fetch_openrouter_usage()
        elevenlabs = fetch_elevenlabs_usage()
        ollama_rows = fetch_ollama_usage()

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
        total_monthly = round(monthly_cost + or_monthly, 6)

        payload = {
            "session": round(session_cost, 6),
            "daily":   round(daily_cost,   6),
            "weekly":  round(weekly_cost,  6),
            "monthly": round(monthly_cost, 6),
            "topModels": [{"name": r["name"], "window": "session", "cost": r.get("weeklyCost", 0)} for r in breakdown[:5]],
            "breakdown": breakdown,
            "lastUpdated": utc_iso(),
            "jain": jain,
            "openrouter": openrouter,
            "elevenlabs": elevenlabs,
            "aggregate": {
                "daily":   round(daily_cost + jain.get("daily", 0) + or_daily, 6),
                "total":   round(session_cost + jain.get("total", 0) + or_weekly, 6),
                "monthly": total_monthly,
            },
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
    try:
        result = subprocess.run(
            [
                "gog", "calendar", "events", "list",
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
    except subprocess.CalledProcessError as exc:
        print(f"[warn] gog calendar list failed: {exc.stderr}", file=sys.stderr)
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
    # J.A.I.N crontab via SSH
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "jc_agent@100.121.89.84", "crontab -l 2>/dev/null || true"],
            capture_output=True, text=True, timeout=8
        )
        jain_listing = r.stdout if r.returncode == 0 else ""
    except Exception:
        jain_listing = ""
    import datetime as _dt
    import re as _re

    now_et = _dt.datetime.now(_dt.timezone.utc).astimezone(_dt.timezone(_dt.timedelta(hours=-4)))
    today_str = now_et.strftime('%Y-%m-%d')

    # Parse X post log from J.A.I.N for lastRun data
    x_log_runs: dict[str, str] = {}  # cron name → ISO timestamp of last successful run today
    try:
        log_r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "jc_agent@100.121.89.84",
             f"grep -E '\\[([0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}})\\] X Post Agent|✅ Posted' /Users/jc_agent/.openclaw/workspace/logs/x_post_agent.log 2>/dev/null || true"],
            capture_output=True, text=True, timeout=8
        )
        if log_r.returncode == 0:
            log_lines = log_r.stdout.strip().splitlines()
            # Map run times to job names by hour
            hour_to_job = {
                6:  "X Feedback Loop",
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
            for line in log_lines:
                m = _re.match(r'\[(\d{2}):(\d{2}):\d{2}\] X Post Agent', line)
                if m:
                    h = int(m.group(1))
                    job_name = hour_to_job.get(h)
                    if job_name:
                        iso = f"{today_str}T{m.group(1)}:{m.group(2)}:00"
                        x_log_runs[job_name] = iso
    except Exception:
        pass

    # Parse strategic reply log from J.A.I.N
    try:
        reply_log_r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "jc_agent@100.121.89.84",
             f"grep -E '✅ Done — posted [1-9]' /Users/jc_agent/.openclaw/workspace/logs/x_strategic_reply.log 2>/dev/null | tail -5 || true"],
            capture_output=True, text=True, timeout=8
        )
        if reply_log_r.returncode == 0 and reply_log_r.stdout.strip():
            x_log_runs["X Strategic Replies"] = f"{today_str}T{reply_log_r.stdout.strip()[:8]}"
    except Exception:
        pass

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
        present = target['pattern'] in listing

        # Compute runStatus for daily jobs
        sched = target.get('schedule', '')
        run_status = None  # 'done' | 'missed' | 'upcoming' | None
        last_run = x_log_runs.get(target['name'])

        if sched.startswith('Daily'):
            sched_hour = parse_daily_hour(sched)
            if sched_hour is not None:
                now_hour = now_et.hour
                now_min = now_et.minute
                if last_run:
                    run_status = 'done'
                elif now_hour > sched_hour or (now_hour == sched_hour and now_min >= 10):
                    run_status = 'missed'
                else:
                    run_status = 'upcoming'

        row = {
            'name': target['name'],
            'schedule': target['schedule'],
            'description': target.get('description', ''),
            'category': target.get('category', 'Other'),
            'agent': target.get('agent', 'JOSH 2.0'),
            'status': 'ok' if present else 'paused',
            'errors': 0,
            'lastError': None,
        }
        if run_status:
            row['runStatus'] = run_status
        if last_run:
            row['lastRun'] = last_run

        if target.get('multiRun'):
            runs = target['multiRun']['runs']
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


def build_recent_activity(now_iso: str, model_usage: Dict[str, Any] | None, focus: Dict[str, Any] | None, events: List[Dict[str, Any]], crons: List[Dict[str, Any]], devices: List[Dict[str, Any]]) -> List[Dict[str, str]]:
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
    "claude-sonnet-4-6":     200_000,
    "claude-sonnet-4-5":     200_000,
    "claude-opus-4":         200_000,
    "claude-haiku-3-5":      200_000,
    "gpt-5.4":               128_000,
    "gpt-5.1-codex":         128_000,
    "gpt-4o":                128_000,
    "gemini-2.5-flash":    1_000_000,
    "gemini-2.5-pro":      1_000_000,
    "gemini-2.0-flash":    1_000_000,
    "grok-3":                131_072,
    "grok-3-mini":           131_072,
}

def fetch_context_window() -> Dict[str, Any]:
    """Read contextTokens + model from the most recent OpenClaw session."""
    result = {"usedTokens": 0, "limitTokens": 0, "pct": 0.0, "model": "", "status": "green"}
    try:
        sessions = json.loads(OPENCLAW_SESSIONS_PATH.read_text())
        # Pick the most-recently-updated session
        best = max(sessions.values(), key=lambda s: s.get("updatedAt", ""), default=None)
        if not best:
            return result
        model = (best.get("modelOverride") or best.get("model") or "").lower().replace("anthropic/", "").replace("google/", "").replace("openai/", "")
        ctx_tokens = int(best.get("contextTokens") or 0)
        total_tokens = int(best.get("totalTokens") or 0)
        used = total_tokens  # totalTokens = cumulative context used in session

        # Find limit
        limit = 0
        for key, lim in CONTEXT_LIMITS.items():
            if key in model:
                limit = lim
                break
        if limit == 0:
            limit = ctx_tokens if ctx_tokens > 0 else 200_000  # fallback

        pct = round(used / limit, 4) if limit > 0 else 0.0
        pct = min(pct, 1.0)

        # RAG status
        if pct >= 0.85:
            status = "red"    # new session strongly recommended
        elif pct >= 0.60:
            status = "amber"  # getting full, consider /new soon
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

    model_usage = fetch_model_usage() or {
        "session": 0.0,
        "daily": 0.0,
        "weekly": 0.0,
        "topModels": [],
        "breakdown": [],
        "lastUpdated": now_iso,
    }
    dashboard["modelUsage"] = model_usage

    # Machine health metrics (CPU, RAM, disk, uptime)
    dashboard["machineHealth"] = fetch_machine_health()

    dashboard["upcomingEvents"] = fetch_upcoming_events()
    dashboard["devices"] = build_devices()
    dashboard["products"] = build_products(now_iso)
    dashboard["crons"] = fetch_crons()
    dashboard["activeAgents"] = fetch_active_subagents()
    dashboard["recentActivity"] = build_recent_activity(
        now_iso,
        model_usage,
        dashboard["focus"],
        dashboard["upcomingEvents"],
        dashboard["crons"],
        dashboard["devices"],
    )
    dashboard["lastUpdated"] = now_iso

    # Final validation — fills any missing required fields with safe defaults
    validate_dashboard(dashboard, now_iso)

    DASHBOARD_PATH.write_text(json.dumps(dashboard, indent=2))
    MODEL_USAGE_PATH.write_text(json.dumps(model_usage, indent=2))
    print(f"Updated {DASHBOARD_PATH}")
    print(f"Updated {MODEL_USAGE_PATH}")

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
