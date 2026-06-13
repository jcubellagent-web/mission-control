#!/usr/bin/env python3
"""Publish dashboard-safe agent events, jobs, handoffs, and Brain Feed status."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
INDEX = ROOT / "index.html"
EVENTS_PATH = DATA_DIR / "shared-events.json"
CODEX_JOBS_PATH = DATA_DIR / "codex-jobs.json"
DECISIONS_PATH = DATA_DIR / "decisions.json"
HANDOFF_QUEUE_PATH = DATA_DIR / "handoff-queue.json"
DAILY_ROLLUP_PATH = DATA_DIR / "daily-rollup.json"
HANDOFF_DIR = ROOT / "docs" / "handoffs"
BRAIN_FEED_PATHS = {
    "josh": DATA_DIR / "brain-feed.json",
    "josh2": DATA_DIR / "brain-feed.json",
    "joshex": DATA_DIR / "joshex-brain-feed.json",
    "jaimes": DATA_DIR / "jaimes-brain-feed.json",
    "jain": DATA_DIR / "jain-brain-feed.json",
}
HEARTBEATS_PATH = DATA_DIR / "agent-heartbeats.json"

AGENTS = {
    "josh": "JOSH 2.0",
    "josh2": "JOSH 2.0",
    "josh2.0": "JOSH 2.0",
    "jaimes": "JAIMES",
    "jain": "J.A.I.N",
    "j.a.i.n": "J.A.I.N",
    "joshex": "JOSHeX",
    "codex": "JOSHeX",
}
AGENT_IDS = {
    "josh": "josh2",
    "josh2": "josh2",
    "josh2.0": "josh2",
    "jaimes": "jaimes",
    "jain": "jain",
    "j.a.i.n": "jain",
    "joshex": "joshex",
    "codex": "joshex",
}
STATUS_TO_ACTIVE = {"active", "running", "working", "pending", "live"}
STATUS_TO_HEARTBEAT = {
    "active": "active",
    "ready": "ok",
    "done": "ok",
    "info": "ok",
    "blocked": "blocked",
    "error": "error",
}
SECRET_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?<![A-Za-z0-9])sb_secret_[A-Za-z0-9_-]+"),
    re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)(password|client_secret|access_token|refresh_token|authorization)\s*[:=]"),
]
LOW_SIGNAL_STEP_PATTERNS = re.compile(
    r"daily gemini routing audit|gemini .*auth|required before execution|"
    r"weekly autonomy self-test|remote probe|operation not permitted|"
    r"local routing policy passed|daily agent ecosystem health sweep|"
    r"stale brain feed freshness guard|brain feed needs refresh|sandbox|"
    r"intelligence feedback loop|josh health check",
    re.IGNORECASE,
)
BLOCKED_PUBLIC_X_JOB_PATTERNS = re.compile(
    r"\bx[_ -]?strategic[_ -]?repl(?:y|ies)\b|"
    r"\bstrategic repl(?:y|ies)\b|"
    r"\bx repl(?:y|ies)\b|"
    r"\bx post(?:ing)?\b|"
    r"\bpost to x\b|"
    r"\btweet(?:ing)?\b",
    re.IGNORECASE,
)
ALLOWED_X_INTEL_PATTERNS = re.compile(
    r"\bx watchlist\b|\bwatchlist monitor\b|\bintelligence\b|\bbreaking news\b|\bsignal\b|\bscanner\b",
    re.IGNORECASE,
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compact(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


SCRIPT_LABELS = {
    "intel_feedback_loop": "intelligence feedback loop",
    "feedback_loop": "feedback loop",
    "check_josh_health": "Josh health check",
    "breaking_news_scanner": "breaking news scanner",
    "x_feedback_ml": "X feedback model check",
    "launch_scheduler": "launch scheduler",
    "host_local_maintenance": "host maintenance",
    "sorare_missions": "Sorare mission sweep",
    "sorare_lineups": "Sorare lineup check",
    "agent_heartbeat": "status check",
}
PLAIN_TEXT_REPLACEMENTS = {
    "Heartbeat: josh2-lan": "Josh 2.0 is online and ready",
    "Heartbeat: jaimes-via-josh": "JAIMES is online and ready",
    "Heartbeat: macbook-codex": "JOSHeX is online and ready",
    "agent heartbeat": "status check",
    "josh2-lan": "Josh 2.0",
    "jaimes-via-josh": "JAIMES",
    "macbook-codex": "JOSHeX",
}


def humanize_token(token: str) -> str:
    base = Path(token).name
    stem = re.sub(r"\.(py|sh|js|ts|tsx|json|yaml|yml|md)$", "", base, flags=re.IGNORECASE)
    label = SCRIPT_LABELS.get(stem, stem.replace("_", " ").replace("-", " "))
    return " ".join(label.split()).strip()


def dashboard_text(value: Any, limit: int = 220) -> str:
    text = compact(value, limit * 2)
    for raw, plain in PLAIN_TEXT_REPLACEMENTS.items():
        text = re.sub(re.escape(raw), plain, text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?i)\b([a-z0-9_.-]+)\s+cron:\s+((?:/[^ ]+/)?[A-Za-z0-9_.-]+\.(?:py|sh|js|ts|tsx))",
        lambda match: f"{match.group(1)} scheduled: {humanize_token(match.group(2))}",
        text,
    )
    text = re.sub(
        r"(?<![\w./-])((?:/[^ ]+/)?[A-Za-z0-9_-]+\.(?:py|sh|js|ts|tsx))(?![\w./-])",
        lambda match: humanize_token(match.group(1)),
        text,
    )
    text = re.sub(r"\s*(?:>>|2>&1|&&|\|\|)\s*.*$", "", text).strip()
    return compact(text, limit)


def agent_dashboard_text(agent: str, value: Any, limit: int = 220) -> str:
    text = dashboard_text(value, limit * 2)
    if agent == "jain":
        text = re.sub(r"(?i)\b(?:jaimes|jain|j\.a\.i\.n)\s+scheduled:", "J.A.I.N scheduled:", text)
    elif agent in {"josh", "josh2"}:
        text = re.sub(r"(?i)\b(?:josh2-lan|josh|josh\s*2\.0)\s+scheduled:", "Josh 2.0 scheduled:", text)
    return compact(text, limit)


def blocked_public_x_text(agent: str, value: Any) -> bool:
    """Suppress old X posting/reply lanes while leaving X intelligence checks visible."""
    if agent != "jain":
        return False
    text = dashboard_text(value, 800)
    return bool(BLOCKED_PUBLIC_X_JOB_PATTERNS.search(text)) and not bool(ALLOWED_X_INTEL_PATTERNS.search(text))


def blocked_public_x_event(event: dict[str, Any]) -> bool:
    text = " ".join(str(event.get(key) or "") for key in ("title", "tool", "detail", "type"))
    return blocked_public_x_text(event.get("agent", ""), text)


def scrub_blocked_public_x_payload(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
    if agent != "jain":
        return payload
    cleaned = dict(payload)
    steps = cleaned.get("steps") if isinstance(cleaned.get("steps"), list) else []
    cleaned["steps"] = [
        step for step in steps
        if not blocked_public_x_text(agent, " ".join(str(step.get(key) or "") for key in ("label", "title", "tool", "kind")) if isinstance(step, dict) else step)
    ]
    top_text = " ".join(str(cleaned.get(key) or "") for key in ("objective", "detail", "currentTool"))
    if blocked_public_x_text(agent, top_text):
        cleaned.update({
            "active": False,
            "reportedActive": False,
            "status": "ready",
            "objective": "J.A.I.N is online and ready",
            "detail": "X posting and reply jobs are disabled; intelligence-only X monitoring remains available.",
            "currentTool": "status check",
        })
    return cleaned


def dashboard_steps(rows: Any) -> list[dict[str, Any]]:
    safe_rows: list[dict[str, Any]] = []
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        text = " ".join(str(row.get(key) or "") for key in ("label", "title", "tool", "status", "kind"))
        if str(row.get("status") or "").lower() in {"blocked", "error"} and LOW_SIGNAL_STEP_PATTERNS.search(text):
            continue
        safe = dict(row)
        if "label" in safe:
            safe["label"] = dashboard_text(safe.get("label"), 180)
        if "title" in safe:
            safe["title"] = dashboard_text(safe.get("title"), 180)
        if "tool" in safe:
            safe["tool"] = dashboard_text(safe.get("tool"), 44)
        safe_rows.append(safe)
        if len(safe_rows) >= 7:
            break
    return safe_rows


READY_HEARTBEAT_STEP_PATTERNS = re.compile(
    r"online and ready|no active queued worker tasks|not actively working|standing by|standby",
    re.IGNORECASE,
)
STALE_ATTENTION_HEARTBEAT_STEP_PATTERNS = re.compile(
    r"is needs attention|needs attention: gateway=|visibility heartbeat needs attention|gateway=attention|telegram=attention",
    re.IGNORECASE,
)


def brain_feed_step_history(rows: Any, event: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep ready heartbeats from carrying stale active chips forward."""
    steps = dashboard_steps(rows)
    if event["status"] in STATUS_TO_ACTIVE:
        return steps
    replacement = "ready" if event["status"] in {"ok", "ready", "info"} else event["status"]
    cleaned: list[dict[str, Any]] = []
    for step in steps:
        step = dict(step)
        if "label" in step:
            step["label"] = agent_dashboard_text(event["agent"], step.get("label"), 180)
        if "title" in step:
            step["title"] = agent_dashboard_text(event["agent"], step.get("title"), 180)
        if "tool" in step:
            step["tool"] = agent_dashboard_text(event["agent"], step.get("tool"), 44)
        text = " ".join(str(step.get(key) or "") for key in ("label", "title", "tool", "status", "kind"))
        if STALE_ATTENTION_HEARTBEAT_STEP_PATTERNS.search(text):
            continue
        if blocked_public_x_text(event["agent"], text):
            continue
        if str(step.get("status") or "").lower() == "active" and READY_HEARTBEAT_STEP_PATTERNS.search(text):
            step["status"] = replacement
        cleaned.append(step)
    return cleaned


def preserve_top_level_brain_feed(event: dict[str, Any]) -> bool:
    """Keep routine wrapped cron updates from replacing the agent's visible objective."""
    if event["agent"] not in {"jaimes", "jain"}:
        return False
    if event["type"] != "job":
        return False
    if blocked_public_x_event(event):
        return True
    text = " ".join(str(event.get(key) or "") for key in ("title", "tool", "detail", "status", "type"))
    return bool(LOW_SIGNAL_STEP_PATTERNS.search(text))


def preserve_active_telegram_task(existing: dict[str, Any], event: dict[str, Any]) -> bool:
    """Keep a user-initiated Telegram task visible while background jobs run."""
    if event["agent"] not in {"josh", "josh2", "jaimes"}:
        return False
    if event["type"] != "job":
        return False
    if not existing.get("active"):
        return False
    tool = str(existing.get("currentTool") or "").lower()
    source = str(existing.get("source") or "").lower()
    return "telegram work card" in tool or "telegram" in source


def recent_live_agent_push(existing: dict[str, Any], hold_seconds: int = 1200) -> bool:
    """Protect explicit JAIMES live pushes from generic heartbeat/status rows."""
    raw = existing.get("liveAgentPush") or existing.get("liveAgentPushAt")
    if not raw:
        return False
    try:
        parsed = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return False
    return (dt.datetime.now(dt.timezone.utc) - parsed.astimezone(dt.timezone.utc)).total_seconds() <= hold_seconds


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")


def canonical_agent(raw: str) -> str:
    key = " ".join(str(raw or "").strip().lower().replace("_", " ").split())
    key = key.replace(" ", "")
    if key in AGENT_IDS:
        return AGENT_IDS[key]
    raise SystemExit(f"Unknown agent '{raw}'. Use josh2, jaimes, jain, or joshex.")


def agent_label(agent: str) -> str:
    return {
        "josh": "JOSH 2.0",
        "josh2": "JOSH 2.0",
        "jaimes": "JAIMES",
        "jain": "J.A.I.N",
        "joshex": "JOSHeX",
    }[agent]


def ensure_safe(*values: str, privacy: str) -> None:
    if privacy != "dashboard-safe":
        return
    blob = "\n".join(str(v or "") for v in values)
    for pattern in SECRET_PATTERNS:
        if pattern.search(blob):
            raise SystemExit("Refusing to publish dashboard-safe event: text looks like it contains a secret or credential.")


def event_id(agent: str, event_type: str, now: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "event"
    stamp = now.replace("-", "").replace(":", "").replace("Z", "").replace("T", "-")
    return f"{agent}-{event_type}-{stamp}-{slug}"


def append_event(event: dict[str, Any]) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = EVENTS_PATH.with_suffix(".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = read_json(EVENTS_PATH, {"events": []})
        events = ledger.get("events", []) if isinstance(ledger, dict) else []
        events = [item for item in events if isinstance(item, dict) and item.get("id") != event["id"]]
        events.insert(0, event)
        write_json(EVENTS_PATH, {"events": events[:500]})
        fcntl.flock(lock, fcntl.LOCK_UN)


def append_codex_job(event: dict[str, Any]) -> None:
    CODEX_JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = CODEX_JOBS_PATH.with_suffix(".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        jobs_data = read_json(CODEX_JOBS_PATH, {"jobs": []})
        jobs = jobs_data.get("jobs", []) if isinstance(jobs_data, dict) else []
        job = {
            "id": event["id"],
            "time": event["time"],
            "title": event["title"],
            "status": event["status"],
            "tool": event.get("tool") or "agent_publish.py",
            "owner": event.get("agentLabel") or agent_label(event["agent"]),
            "detail": event.get("detail") or "",
        }
        jobs = [item for item in jobs if isinstance(item, dict) and item.get("id") != job["id"]]
        jobs.insert(0, job)
        write_json(CODEX_JOBS_PATH, {"jobs": jobs[:100]})
        fcntl.flock(lock, fcntl.LOCK_UN)


def locked_update(path: Path, key: str, record: dict[str, Any], limit: int = 300) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_json(path, {key: []})
        rows = data.get(key, []) if isinstance(data, dict) else []
        rows = [item for item in rows if isinstance(item, dict) and item.get("id") != record["id"]]
        rows.insert(0, record)
        write_json(path, {key: rows[:limit]})
        fcntl.flock(lock, fcntl.LOCK_UN)


def append_decision(event: dict[str, Any], tags: list[str]) -> None:
    record = {
        "id": event["id"],
        "time": event["time"],
        "agent": event["agent"],
        "agentLabel": event["agentLabel"],
        "title": event["title"],
        "status": "accepted" if event["status"] == "done" else event["status"],
        "detail": event.get("detail") or "",
        "privacy": event["privacy"],
        "tags": tags[:12],
    }
    locked_update(DECISIONS_PATH, "decisions", record)


def append_handoff_record(event: dict[str, Any], target: str, path: Path | None = None) -> None:
    record = {
        "id": event["id"],
        "time": event["time"],
        "from": event["agent"],
        "fromLabel": event["agentLabel"],
        "to": target,
        "title": event["title"],
        "status": "done" if event["status"] == "done" else "open",
        "detail": event.get("detail") or "",
        "path": str(path.relative_to(ROOT)) if path else "",
        "privacy": event["privacy"],
    }
    locked_update(HANDOFF_QUEUE_PATH, "handoffs", record)


def frontend_supabase_config() -> tuple[str, str] | None:
    if not INDEX.exists():
        return None
    html = INDEX.read_text(errors="replace")
    url_match = re.search(r"SUPABASE_URL:\s*['\"]([^'\"]+)['\"]", html)
    key_match = re.search(r"SUPABASE_KEY:\s*['\"]([^'\"]+)['\"]", html)
    if not url_match or not key_match:
        return None
    return url_match.group(1).rstrip("/"), key_match.group(1)


def request_json(url: str, key: str, method: str = "GET", body: Any | None = None, prefer: str | None = None) -> Any:
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310 - configured dashboard endpoint
        raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw) if raw else None


def fetch_existing_feed(url: str, key: str, row_id: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(row_id, safe="")
    rows = request_json(f"{url}/rest/v1/brain_feed?id=eq.{encoded}&select=data", key) or []
    if rows and isinstance(rows[0].get("data"), dict):
        return rows[0]["data"]
    return {}


def publish_brain_feed(event: dict[str, Any]) -> None:
    config = frontend_supabase_config()
    if not config:
        raise SystemExit("Missing Supabase config in index.html; event was logged locally only.")
    url, key = config
    agent = event["agent"]
    row_id = agent
    existing = fetch_existing_feed(url, key, row_id)
    active = event["status"] in STATUS_TO_ACTIVE
    step = {
        "label": agent_dashboard_text(agent, event["title"], 180),
        "status": "active" if active else event["status"],
        "tool": agent_dashboard_text(agent, event.get("tool") or "agent_publish.py", 44),
        "kind": event["type"],
    }
    preserve_top = (
        recent_live_agent_push(existing)
        or preserve_top_level_brain_feed(event)
        or preserve_active_telegram_task(existing, event)
    ) and isinstance(existing, dict) and bool(existing.get("objective"))
    payload = {
        **existing,
        "agentId": agent,
        "agent": agent_label(agent),
        "active": existing.get("active") if preserve_top else active,
        "reportedActive": existing.get("reportedActive") if preserve_top else active,
        "status": existing.get("status") if preserve_top else ("active" if active else event["status"]),
        "objective": agent_dashboard_text(agent, existing.get("objective") if preserve_top else event["title"], 220),
        "detail": agent_dashboard_text(agent, existing.get("detail") if preserve_top else (event.get("detail") or event["title"]), 260),
        "updatedAt": existing.get("updatedAt") if preserve_top else event["time"],
        "checkedAt": event["time"],
        "currentTool": agent_dashboard_text(agent, existing.get("currentTool") if preserve_top else (event.get("tool") or "agent_publish.py"), 44),
        "steps": [step] + brain_feed_step_history(existing.get("steps"), event),
        "source": "shared-agent-event-ledger",
        "supabaseBacked": True,
    }
    payload = scrub_blocked_public_x_payload(agent, payload)
    row = {"id": row_id, "data": payload, "updated_at": event["time"]}
    request_json(
        f"{url}/rest/v1/brain_feed",
        key,
        method="POST",
        body=[row],
        prefer="resolution=merge-duplicates,return=minimal",
    )


def publish_local_brain_feed(event: dict[str, Any]) -> None:
    path = BRAIN_FEED_PATHS.get(event["agent"])
    if event["agent"] in {"josh", "josh2"} and Path.home().name != "josh2.0":
        path = None
    if not path:
        return
    existing = read_json(path, {})
    if not isinstance(existing, dict):
        existing = {}
    active = event["status"] in STATUS_TO_ACTIVE
    step = {
        "label": agent_dashboard_text(event["agent"], event["title"], 180),
        "status": "active" if active else event["status"],
        "tool": agent_dashboard_text(event["agent"], event.get("tool") or "agent_publish.py", 44),
        "kind": event["type"],
    }
    preserve_top = (
        recent_live_agent_push(existing)
        or preserve_top_level_brain_feed(event)
        or preserve_active_telegram_task(existing, event)
    ) and bool(existing.get("objective"))
    payload = {
        **existing,
        "agent": agent_label(event["agent"]),
        "agentId": event["agent"],
        "active": existing.get("active") if preserve_top else active,
        "reportedActive": existing.get("reportedActive") if preserve_top else active,
        "objective": agent_dashboard_text(event["agent"], existing.get("objective") if preserve_top else event["title"], 220),
        "status": existing.get("status") if preserve_top else ("active" if active else event["status"]),
        "detail": agent_dashboard_text(event["agent"], existing.get("detail") if preserve_top else (event.get("detail") or event["title"]), 260),
        "steps": [step] + brain_feed_step_history(existing.get("steps"), event),
        "currentTool": agent_dashboard_text(event["agent"], existing.get("currentTool") if preserve_top else (event.get("tool") or "agent_publish.py"), 44),
        "updatedAt": existing.get("updatedAt") if preserve_top else event["time"],
        "checkedAt": event["time"],
        "source": "josh2-local-live-feed",
        "supabaseBacked": False,
        "localLiveFeed": True,
    }
    payload = scrub_blocked_public_x_payload(event["agent"], payload)
    write_json(path, payload)


def mirror_publish_heartbeat(event: dict[str, Any]) -> None:
    """A Brain Feed publish is also a live check-in for that agent."""
    record = {
        "agent": event["agent"],
        "node": f"{event['agent']}-brain-feed-publish",
        "status": STATUS_TO_HEARTBEAT.get(event["status"], "ok"),
        "summary": agent_dashboard_text(event["agent"], event.get("detail") or event["title"], 260),
        "updatedAt": event["time"],
        "stale": False,
        "source": "agent_publish.py",
    }
    lock_path = HEARTBEATS_PATH.with_suffix(".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_json(HEARTBEATS_PATH, {"updatedAt": event["time"], "staleAfterMinutes": 120, "heartbeats": []})
        rows = data.get("heartbeats", []) if isinstance(data, dict) else []
        rows = [
            row for row in rows
            if isinstance(row, dict)
            and not (row.get("agent") == record["agent"] and row.get("node") == record["node"])
        ]
        rows.insert(0, record)
        data["heartbeats"] = rows[:100]
        data["updatedAt"] = event["time"]
        data.setdefault("staleAfterMinutes", 120)
        write_json(HEARTBEATS_PATH, data)
        fcntl.flock(lock, fcntl.LOCK_UN)


def should_mirror_supabase_brain_feed() -> bool:
    return os.environ.get("MISSION_CONTROL_SUPABASE_BRAIN_FEED", "").lower() in {"1", "true", "yes", "on"}


def should_publish_v2(args: argparse.Namespace) -> bool:
    has_v2_key = bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY"))
    return bool(
        args.v2
        or os.environ.get("MISSION_CONTROL_V2_DUAL_WRITE") in {"1", "true", "yes", "on"}
        or (args.brain_feed and has_v2_key)
    )


def publish_v2(event: dict[str, Any], job: bool, handoff_to: str = "") -> dict[str, Any]:
    status = event["status"]
    if event["type"] == "complete":
        status = "done"
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "mc_v2_publish.py"),
        "--agent", event["agent"],
        "--type", event["type"],
        "--status", status,
        "--title", event["title"],
        "--tool", event.get("tool") or "agent_publish.py",
        "--detail", event.get("detail") or event["title"],
    ]
    if job:
        cmd.append("--job")
    if handoff_to:
        cmd.extend(["--handoff-to", handoff_to])
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {
            "ok": False,
            "error": compact(result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}", 500),
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"raw": compact(result.stdout, 500)}
    return {"ok": True, "result": payload}


def write_handoff(event: dict[str, Any], target: str) -> Path:
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    safe_target = re.sub(r"[^a-zA-Z0-9_.-]+", "-", target.strip().lower())[:60] or "agent"
    path = HANDOFF_DIR / f"{event['time'][:10]}-{safe_target}-{event['id']}.md"
    path.write_text(
        "\n".join([
            f"# Handoff: {event['title']}",
            "",
            f"- Time: {event['time']}",
            f"- From: {event['agentLabel']}",
            f"- To: {target}",
            f"- Status: {event['status']}",
            f"- Tool: {event.get('tool') or 'agent_publish.py'}",
            "",
            "## Detail",
            event.get("detail") or "No additional detail.",
            "",
            "## Privacy",
            "Dashboard-safe only. Do not add secrets or raw private account contents here.",
            "",
        ])
        + "\n"
    )
    return path


def generate_daily_rollup() -> dict[str, Any]:
    today = dt.datetime.now().strftime("%Y-%m-%d")
    events = [e for e in read_json(EVENTS_PATH, {"events": []}).get("events", []) if str(e.get("time", "")).startswith(today)]
    jobs = [j for j in read_json(CODEX_JOBS_PATH, {"jobs": []}).get("jobs", []) if str(j.get("time", "")).startswith(today)]
    decisions = [d for d in read_json(DECISIONS_PATH, {"decisions": []}).get("decisions", []) if str(d.get("time", "")).startswith(today)]
    handoffs = [h for h in read_json(HANDOFF_QUEUE_PATH, {"handoffs": []}).get("handoffs", []) if str(h.get("time", "")).startswith(today)]
    blocked = [e for e in events if e.get("status") in {"blocked", "error"} or e.get("type") == "blocked"]
    open_handoffs = [h for h in handoffs if h.get("status") in {"open", "blocked"}]
    highlights = []
    for row in events[:8]:
        title = row.get("title")
        if title and title not in highlights:
            highlights.append(title)
    rollup = {
        "date": today,
        "generatedAt": utc_now(),
        "summary": f"{len(events)} shared event(s), {len(jobs)} job(s), {len(decisions)} decision(s), {len(open_handoffs)} open handoff(s).",
        "counts": {
            "events": len(events),
            "jobs": len(jobs),
            "decisions": len(decisions),
            "handoffs": len(handoffs),
            "blocked": len(blocked),
        },
        "highlights": highlights[:8],
        "openHandoffs": open_handoffs[:8],
        "blockedItems": blocked[:8],
    }
    write_json(DAILY_ROLLUP_PATH, rollup)
    return rollup


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a shared, dashboard-safe agent event.")
    parser.add_argument("--agent", required=True, help="josh2, jaimes, jain, or joshex")
    parser.add_argument("--type", default="status", choices=["status", "job", "decision", "handoff", "blocked", "complete", "note"])
    parser.add_argument("--title", required=True, help="Short dashboard-safe title/objective")
    parser.add_argument("--status", default="done", choices=["active", "ready", "done", "blocked", "error", "info"])
    parser.add_argument("--tool", default="agent_publish.py")
    parser.add_argument("--detail", default="")
    parser.add_argument("--privacy", default="dashboard-safe", choices=["dashboard-safe", "agent-private", "josh-only"])
    parser.add_argument("--brain-feed", action="store_true", help="Publish to Josh 2.0's local live Brain Feed row")
    parser.add_argument("--job", action="store_true", help="Also log as a Today Jobs entry")
    parser.add_argument("--handoff-to", default="", help="Write a markdown handoff doc for this target")
    parser.add_argument("--tag", action="append", default=[], help="Decision/knowledge tag. May be repeated.")
    parser.add_argument("--rollup", action="store_true", help="Regenerate data/daily-rollup.json after publishing")
    parser.add_argument("--v2", action="store_true", help="Also publish dashboard-safe state to Control Tower canonical tables")
    args = parser.parse_args()

    agent = canonical_agent(args.agent)
    now = utc_now()
    ensure_safe(args.title, args.detail, args.tool, privacy=args.privacy)
    event = {
        "id": event_id(agent, args.type, now, args.title),
        "time": now,
        "agent": agent,
        "agentLabel": agent_label(agent),
        "type": args.type,
        "title": agent_dashboard_text(agent, args.title, 160),
        "status": args.status,
        "tool": agent_dashboard_text(agent, args.tool, 80),
        "detail": agent_dashboard_text(agent, args.detail, 500),
        "privacy": args.privacy,
    }

    append_event(event)
    if args.job or args.type == "job":
        append_codex_job(event)
    if args.type == "decision":
        append_decision(event, args.tag)
    if args.handoff_to or args.type == "handoff":
        target = args.handoff_to or "agent"
        handoff = write_handoff(event, target)
        event.setdefault("links", []).append({"label": "handoff", "url": str(handoff.relative_to(ROOT))})
        append_event(event)
        append_handoff_record(event, target, handoff)
    if args.brain_feed:
        publish_local_brain_feed(event)
        mirror_publish_heartbeat(event)
        if should_mirror_supabase_brain_feed():
            try:
                publish_brain_feed(event)
            except (urllib.error.URLError, TimeoutError) as exc:
                print(f"Optional Supabase Brain Feed mirror failed; Josh 2.0 local live feed was updated: {exc}", file=sys.stderr)
    if args.rollup:
        generate_daily_rollup()
    v2_result = None
    if should_publish_v2(args):
        v2_result = publish_v2(event, args.job or args.type == "job", args.handoff_to)

    response = {"ok": True, "event": event}
    if v2_result is not None:
        response["v2"] = v2_result
    print(json.dumps(response, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
