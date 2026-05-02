#!/usr/bin/env python3
"""Verify dashboard-safe Mission Control canonical rows are readable."""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
AGENTS = {"joshex", "josh", "jaimes", "jain"}


def frontend_supabase_config() -> tuple[str, str]:
    html = INDEX.read_text(errors="replace")
    url_match = re.search(r"SUPABASE_URL:\s*['\"]([^'\"]+)['\"]", html)
    key_match = re.search(r"SUPABASE_KEY:\s*['\"]([^'\"]+)['\"]", html)
    if not url_match or not key_match:
        raise SystemExit("Missing Supabase URL/key in index.html.")
    return url_match.group(1).rstrip("/"), key_match.group(1)


def request_json(url: str, key: str) -> Any:
    req = urllib.request.Request(url, headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310 - configured dashboard endpoint
        raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw) if raw else None


def eq(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Mission Control canonical readable state.")
    parser.add_argument("--agent", default="joshex", choices=sorted(AGENTS))
    parser.add_argument("--expect-title", default="", help="Optional exact objective/event title to verify")
    parser.add_argument("--expect-job-title", default="", help="Optional exact v2 job title to verify")
    parser.add_argument("--expect-handoff-title", default="", help="Optional exact v2 handoff event title to verify")
    parser.add_argument("--expect-approval-title", default="", help="Optional exact v2 approval title to verify")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    url, key = frontend_supabase_config()
    status_rows = request_json(
        f"{url}/rest/v1/mc_v2_agent_status?agent_id=eq.{eq(args.agent)}&select=*&limit=1",
        key,
    ) or []
    events = request_json(
        f"{url}/rest/v1/mc_v2_events?agent_id=eq.{eq(args.agent)}&privacy=eq.dashboard-safe&select=*&order=created_at.desc&limit={args.limit}",
        key,
    ) or []
    jobs = request_json(
        f"{url}/rest/v1/mc_v2_jobs?agent_id=eq.{eq(args.agent)}&select=*&order=updated_at.desc&limit={args.limit}",
        key,
    ) or []
    approvals = request_json(
        f"{url}/rest/v1/mc_v2_approvals?risk_tier=eq.dashboard-safe&select=*&order=created_at.desc&limit={args.limit}",
        key,
    ) or []
    status = status_rows[0] if status_rows else None
    ok = bool(status)
    checks = []
    if args.expect_title:
        status_match = bool(status and status.get("objective") == args.expect_title)
        event_match = any(row.get("title") == args.expect_title for row in events)
        checks.extend([
            {"name": "status_objective_matches", "ok": status_match},
            {"name": "event_title_visible", "ok": event_match},
        ])
        ok = ok and status_match and event_match
    if args.expect_job_title:
        job_match = any(row.get("title") == args.expect_job_title for row in jobs)
        checks.append({"name": "job_title_visible", "ok": job_match})
        ok = ok and job_match
    if args.expect_handoff_title:
        handoff_match = any(
            row.get("title") == args.expect_handoff_title and row.get("event_type") == "handoff"
            for row in events
        )
        checks.append({"name": "handoff_event_visible", "ok": handoff_match})
        ok = ok and handoff_match
    if args.expect_approval_title:
        approval_match = any(row.get("title") == args.expect_approval_title for row in approvals)
        checks.append({"name": "approval_title_visible", "ok": approval_match})
        ok = ok and approval_match
    print(json.dumps({
        "ok": ok,
        "agent": args.agent,
        "status": status,
        "events": events,
        "jobs": jobs,
        "approvals": approvals,
        "checks": checks,
    }, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
