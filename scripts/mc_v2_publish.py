#!/usr/bin/env python3
"""Publish dashboard-safe Mission Control v2 state to Supabase."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"

AGENTS = {
    "joshex": "JOSHeX",
    "codex": "JOSHeX",
    "josh": "JOSH 2.0",
    "josh2": "JOSH 2.0",
    "josh2.0": "JOSH 2.0",
    "jaimes": "JAIMES",
    "jain": "J.A.I.N",
    "j.a.i.n": "J.A.I.N",
}
AGENT_IDS = {
    "joshex": "joshex",
    "codex": "joshex",
    "josh": "josh",
    "josh2": "josh",
    "josh2.0": "josh",
    "jaimes": "jaimes",
    "jain": "jain",
    "j.a.i.n": "jain",
}
ACTIVE_STATUSES = {"active", "queued", "accepted"}
EVENT_TYPES = {"status", "job", "decision", "handoff", "blocked", "complete", "note", "heartbeat"}
STATUSES = {"active", "queued", "accepted", "done", "blocked", "error", "info", "cancelled"}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"sb_secret_[A-Za-z0-9_-]+"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)(password|client_secret|access_token|refresh_token|authorization)\s*[:=]"),
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def canonical_agent(raw: str) -> str:
    key = str(raw or "").strip().lower().replace("_", "").replace(" ", "")
    if key in AGENT_IDS:
        return AGENT_IDS[key]
    raise SystemExit(f"Unknown agent '{raw}'. Use josh, jaimes, jain, or joshex.")


def event_id(agent: str, event_type: str, now: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:54] or "event"
    stamp = now.replace("-", "").replace(":", "").replace("Z", "").replace("T", "-")
    return f"v2-{agent}-{event_type}-{stamp}-{slug}"


def ensure_dashboard_safe(*values: str) -> None:
    blob = "\n".join(str(v or "") for v in values)
    for pattern in SECRET_PATTERNS:
        if pattern.search(blob):
            raise SystemExit("Refusing v2 publish: text looks like it contains a secret or credential.")


def frontend_supabase_url() -> str:
    html = INDEX.read_text(errors="replace")
    url_match = re.search(r"SUPABASE_URL:\s*['\"]([^'\"]+)['\"]", html)
    if not url_match:
        raise SystemExit("Missing Supabase URL in index.html.")
    return url_match.group(1).rstrip("/")


def supabase_service_key() -> str:
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    if not key:
        raise SystemExit(
            "Missing SUPABASE_SERVICE_ROLE_KEY. v2 writes require a server-side key; "
            "use --dry-run for local validation."
        )
    return key


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


def upsert(url: str, key: str, table: str, rows: list[dict[str, Any]]) -> None:
    request_json(
        f"{url}/rest/v1/{urllib.parse.quote(table, safe='')}",
        key,
        method="POST",
        body=rows,
        prefer="resolution=merge-duplicates,return=minimal",
    )


def insert(url: str, key: str, table: str, rows: list[dict[str, Any]]) -> None:
    request_json(
        f"{url}/rest/v1/{urllib.parse.quote(table, safe='')}",
        key,
        method="POST",
        body=rows,
        prefer="return=minimal",
    )


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    agent = canonical_agent(args.agent)
    handoff_to = canonical_agent(args.handoff_to) if args.handoff_to else ""
    now = utc_now()
    event_type = args.type
    status = args.status
    if event_type not in EVENT_TYPES:
        raise SystemExit(f"Unknown event type: {event_type}")
    if status not in STATUSES:
        raise SystemExit(f"Unknown status: {status}")
    ensure_dashboard_safe(args.title, args.detail, args.tool, args.handoff_to)
    event = {
        "id": event_id(agent, event_type, now, args.title),
        "agent_id": agent,
        "event_type": event_type,
        "status": status,
        "title": compact(args.title, 180),
        "detail": compact(args.detail, 500),
        "tool": compact(args.tool, 80),
        "privacy": "dashboard-safe",
        "created_at": now,
        "metadata": {"source": "mc_v2_publish.py", **({"handoffTo": handoff_to} if handoff_to else {})},
    }
    status_row = {
        "agent_id": agent,
        "status": "active" if status in ACTIVE_STATUSES else "done" if event_type == "complete" else status,
        "objective": event["title"],
        "detail": event["detail"],
        "current_tool": event["tool"],
        "active": status in ACTIVE_STATUSES,
        "updated_at": now,
        "source": "mc_v2_publish.py",
        "steps": [{"label": event["title"], "status": status, "tool": event["tool"], "kind": event_type}],
        "metadata": {"eventId": event["id"]},
    }
    job_row = None
    if args.job or event_type == "job":
        job_row = {
            "id": event["id"],
            "event_id": event["id"],
            "agent_id": agent,
            "title": event["title"],
            "status": "active" if status in ACTIVE_STATUSES else status,
            "detail": event["detail"],
            "tool": event["tool"],
            "started_at": now if status in ACTIVE_STATUSES else None,
            "completed_at": now if status in {"done", "blocked", "error", "cancelled"} else None,
            "updated_at": now,
            "metadata": {"source": "mc_v2_publish.py"},
        }
    approval_row = None
    if args.approval or event_type == "handoff":
        approval_row = {
            "id": event["id"],
            "agent_id": handoff_to or agent,
            "title": event["title"],
            "detail": event["detail"],
            "requested_by": agent,
            "status": "pending" if status in ACTIVE_STATUSES else "approved" if status == "done" else "cancelled" if status == "cancelled" else "pending",
            "risk_tier": "dashboard-safe",
            "created_at": now,
            "decided_at": now if status in {"done", "cancelled"} else None,
            "metadata": {"source": "mc_v2_publish.py", "eventId": event["id"], **({"handoffTo": handoff_to} if handoff_to else {})},
        }
    return {"event": event, "status": status_row, "job": job_row, "approval": approval_row}


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish dashboard-safe Mission Control v2 state.")
    parser.add_argument("--agent", required=True, help="josh, jaimes, jain, or joshex")
    parser.add_argument("--type", default="status", choices=sorted(EVENT_TYPES))
    parser.add_argument("--status", default="active", choices=sorted(STATUSES))
    parser.add_argument("--title", required=True)
    parser.add_argument("--detail", default="")
    parser.add_argument("--tool", default="mc_v2_publish.py")
    parser.add_argument("--job", action="store_true")
    parser.add_argument("--approval", action="store_true", help="Also write a dashboard-safe pending approval row")
    parser.add_argument("--handoff-to", default="", help="Target agent for handoff/approval inbox rows")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = build_payload(args)
    if args.dry_run:
        print(json.dumps({"ok": True, "dryRun": True, **payload}, indent=2))
        return 0

    url, key = frontend_supabase_url(), supabase_service_key()
    try:
        upsert(url, key, "mc_v2_agent_status", [payload["status"]])
        insert(url, key, "mc_v2_events", [payload["event"]])
        if payload["job"]:
            upsert(url, key, "mc_v2_jobs", [payload["job"]])
        if payload["approval"]:
            upsert(url, key, "mc_v2_approvals", [payload["approval"]])
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"Mission Control v2 publish failed: HTTP {exc.code} {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Mission Control v2 publish failed: {exc}") from exc

    print(json.dumps({"ok": True, **payload}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
