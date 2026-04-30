#!/usr/bin/env python3
"""Publish a safe live Brain Feed row for any Mission Control agent.

This uses the existing public Supabase `brain_feed` table and keeps JSON files as
fallbacks. It does not read or print private credentials; provide
MISSION_CONTROL_SUPABASE_URL / MISSION_CONTROL_SUPABASE_KEY when running outside
the Mission Control repo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"

AGENT_IDS = {
    "josh": "josh",
    "josh2": "josh",
    "josh2.0": "josh",
    "josh 2.0": "josh",
    "jaimes": "jaimes",
    "jain": "jain",
    "j.a.i.n": "jain",
    "joshex": "joshex",
    "codex": "joshex",
}

AGENT_LABELS = {
    "josh": "JOSH 2.0",
    "jaimes": "JAIMES",
    "jain": "J.A.I.N",
    "joshex": "JOSHeX",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compact(value: str, limit: int = 220) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def agent_id(value: str) -> str:
    key = " ".join(str(value or "").strip().lower().replace("_", " ").split())
    if key in AGENT_IDS:
        return AGENT_IDS[key]
    key = key.replace(" ", "")
    return AGENT_IDS.get(key, key or "josh")


def candidate_index_paths() -> list[Path]:
    paths = [
        INDEX,
        Path.cwd() / "index.html",
    ]
    if os.environ.get("MISSION_CONTROL_REPO"):
        paths.append(Path(os.environ["MISSION_CONTROL_REPO"]) / "index.html")
    home = Path.home()
    paths.extend([
        home / ".openclaw" / "workspace" / "mission-control-joshex-agent-board-polish" / "index.html",
        home / ".openclaw" / "workspace" / "mission-control-joshex-live-preview" / "index.html",
        home / ".openclaw" / "workspace" / "mission-control" / "index.html",
    ])

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser())
        if key not in seen:
            seen.add(key)
            unique.append(path.expanduser())
    return unique


def read_frontend_config() -> tuple[str | None, str | None]:
    for index_path in candidate_index_paths():
        if not index_path.exists():
            continue
        html = index_path.read_text(encoding="utf-8", errors="replace")
        url_match = re.search(r"SUPABASE_URL:\s*['\"]([^'\"]+)['\"]", html)
        key_match = re.search(r"SUPABASE_KEY:\s*['\"]([^'\"]+)['\"]", html)
        if url_match and key_match:
            return url_match.group(1), key_match.group(1)
    return None, None


def supabase_config() -> tuple[str, str]:
    url = os.environ.get("MISSION_CONTROL_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = os.environ.get("MISSION_CONTROL_SUPABASE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        frontend_url, frontend_key = read_frontend_config()
        url = url or frontend_url
        key = key or frontend_key
    if not url or not key:
        raise SystemExit("Missing Supabase config. Set MISSION_CONTROL_SUPABASE_URL and MISSION_CONTROL_SUPABASE_KEY.")
    return url.rstrip("/"), key


def request_json(url: str, key: str, method: str = "GET", body: Any | None = None, prefer: str | None = None) -> Any:
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8", "replace")
            if not raw:
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"Supabase HTTP {exc.code}: {compact(detail, 500)}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Supabase request failed: {exc.reason}") from exc


def fetch_existing(url: str, key: str, row_id: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(row_id, safe="")
    rows = request_json(f"{url}/rest/v1/brain_feed?id=eq.{encoded}&select=data", key) or []
    if rows and isinstance(rows[0].get("data"), dict):
        return rows[0]["data"]
    return {}


def make_step(label: str, status: str, tool: str, kind: str = "tool") -> dict[str, str]:
    return {
        "label": compact(label, 180),
        "status": status,
        "tool": compact(tool, 44),
        "kind": compact(kind, 28),
    }


def merge_steps(existing: dict[str, Any], new_steps: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for step in new_steps + list(existing.get("steps") or []):
        label = compact(step.get("label") or "", 180)
        if not label:
            continue
        key = f"{step.get('tool','')}|{label}|{step.get('status','')}"
        if key in seen:
            continue
        seen.add(key)
        merged.append({
            "label": label,
            "status": compact(step.get("status") or "done", 24),
            "tool": compact(step.get("tool") or "ops", 44),
            "kind": compact(step.get("kind") or "tool", 28),
        })
        if len(merged) >= 8:
            break
    return merged


def build_payload(args: argparse.Namespace, existing: dict[str, Any]) -> dict[str, Any]:
    row_id = agent_id(args.agent)
    label = AGENT_LABELS.get(row_id, args.agent)
    now = utc_now()
    status = compact(args.status or "active", 32).lower()
    active = status in {"active", "running", "working", "pending", "live"}
    tool = compact(args.tool or ("cron" if args.cron else "ops"), 44)
    objective = compact(args.objective or args.decision or args.step or f"{label} heartbeat", 220)
    detail_bits = [args.detail, f"Cron: {args.cron}" if args.cron else "", f"Host: {socket.gethostname()}"]
    detail = compact(" · ".join(bit for bit in detail_bits if bit), 260)

    new_steps: list[dict[str, str]] = []
    if args.decision:
        new_steps.append(make_step(args.decision, "done" if not active else "active", "decision", "decision"))
    for step in args.step or []:
        new_steps.append(make_step(step, "active" if active else "done", tool, "tool"))
    if not new_steps:
        new_steps.append(make_step(objective, "active" if active else "done", tool, "tool"))

    payload: dict[str, Any] = {
        **existing,
        "agentId": row_id,
        "agent": label,
        "active": active,
        "reportedActive": active,
        "status": "active" if active else status,
        "objective": objective,
        "detail": detail or existing.get("detail") or objective,
        "updatedAt": now,
        "currentTool": tool,
        "model": compact(args.model or existing.get("model") or "", 64),
        "steps": merge_steps(existing, new_steps),
        "source": "supabase-agent-feed",
        "supabaseBacked": True,
    }
    if args.context_pct is not None:
        payload["contextPct"] = max(0, min(100, args.context_pct))
    if args.cron:
        payload["cron"] = args.cron
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a Mission Control Brain Feed update to Supabase.")
    parser.add_argument("--agent", required=True, help="josh, jaimes, jain, or joshex")
    parser.add_argument("--status", default="active", help="active, running, done, idle, blocked, stale")
    parser.add_argument("--objective", default="", help="Visible current objective")
    parser.add_argument("--tool", default="", help="Current tool or lane, e.g. shell, cron, codex, browser")
    parser.add_argument("--decision", default="", help="Decision being made by the agent")
    parser.add_argument("--detail", default="", help="Additional detail for the Live Actions trace")
    parser.add_argument("--step", action="append", default=[], help="Tool/action row. May be repeated.")
    parser.add_argument("--cron", default="", help="Cron/job name when publishing from scheduled work")
    parser.add_argument("--model", default="", help="Optional model/provider label")
    parser.add_argument("--context-pct", type=int, default=None, help="Optional agent context percentage")
    parser.add_argument("--dry-run", action="store_true", help="Print the payload without publishing")
    args = parser.parse_args()

    row_id = agent_id(args.agent)
    url, key = supabase_config()
    existing = {} if args.dry_run else fetch_existing(url, key, row_id)
    payload = build_payload(args, existing)
    row = {"id": row_id, "data": payload, "updated_at": payload["updatedAt"]}

    if args.dry_run:
        print(json.dumps(row, indent=2, sort_keys=True))
        return 0

    request_json(
        f"{url}/rest/v1/brain_feed",
        key,
        method="POST",
        body=[row],
        prefer="resolution=merge-duplicates,return=minimal",
    )
    print(f"Published Supabase Brain Feed row for {AGENT_LABELS.get(row_id, row_id)} at {payload['updatedAt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
