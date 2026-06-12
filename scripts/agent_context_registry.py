#!/usr/bin/env python3
"""Build a dashboard-safe shared context registry for the agent ecosystem."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_PATH = DATA_DIR / "agent-context-registry.json"
CHAT_SOURCES_PATH = DATA_DIR / "agent-chat-sources.json"

AGENTS = {
    "joshex": {
        "label": "JOSHeX",
        "role": "trusted coordinator",
        "node": "macbook-codex",
        "brainFeedPath": DATA_DIR / "personal-codex.json",
        "fallbackBrainFeedPath": DATA_DIR / "brain-feed.json",
        "contextScope": "Private coordinator context, Mission Control implementation, cross-agent handoffs, validation, and sensitive-account routing decisions.",
    },
    "josh": {
        "label": "Josh 2.0",
        "role": "front door and Mission Control host",
        "node": "josh2-lan",
        "brainFeedPath": DATA_DIR / "brain-feed.json",
        "contextScope": "Telegram front door, Mission Control kiosk, OpenCLAW services, Josh-side jobs, user-visible routing, and live dashboard publishing.",
    },
    "jaimes": {
        "label": "JAIMES",
        "role": "Hermes workhorse",
        "node": "jaimes-via-josh",
        "brainFeedPath": DATA_DIR / "jaimes-brain-feed.json",
        "contextScope": "Hermes specialist work, Sorare/fantasy workflows, scheduled heavy jobs, reports, and durable work packets.",
    },
    "jain": {
        "label": "J.A.I.N",
        "role": "background intelligence worker",
        "node": "jaimes-via-josh",
        "brainFeedPath": DATA_DIR / "jain-brain-feed.json",
        "contextScope": "Breaking-news scans, intelligence feeds, X/watchlist monitoring, background automations, and worker health.",
    },
}

OPEN_TASK_STATUSES = {"queued", "accepted", "active", "blocked", "error"}
OPEN_HANDOFF_STATUSES = {"open", "accepted", "blocked"}
PLAIN_TEXT_REPLACEMENTS = {
    "jaimes-model-efficiency-guard": "JAIMES model efficiency guard",
    "jaimes-ops-drift-check": "JAIMES ops drift check",
    "jaimes-brain-feed-self-test": "JAIMES Brain Feed self-test",
    "jaimes-brain-feed-stale-alert": "JAIMES Brain Feed stale alert",
    "sorare-canonical-reflector": "Sorare canonical sync",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def age_minutes(value: Any) -> int | None:
    stamp = parse_ts(value)
    if not stamp:
        return None
    return int((dt.datetime.now(dt.timezone.utc) - stamp).total_seconds() // 60)


def compact(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    for raw, plain in PLAIN_TEXT_REPLACEMENTS.items():
        text = re.sub(re.escape(raw), plain, text, flags=re.IGNORECASE)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def feed_timestamp(feed: dict[str, Any]) -> str:
    return str(feed.get("updatedAt") or feed.get("checkedAt") or feed.get("time") or "")


def normalize_feed(agent: str, config: dict[str, Any]) -> dict[str, Any]:
    feeds = [read_json(config["brainFeedPath"], {})]
    if config.get("fallbackBrainFeedPath"):
        feeds.append(read_json(config["fallbackBrainFeedPath"], {}))
    feeds = [feed for feed in feeds if isinstance(feed, dict) and feed]
    feed = max(
        feeds,
        key=lambda item: parse_ts(feed_timestamp(item)) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        default={},
    )
    if not isinstance(feed, dict):
        feed = {}
    stamp = feed_timestamp(feed)
    return {
        "status": compact(feed.get("status") or ("active" if feed.get("active") else "unknown"), 40),
        "objective": compact(feed.get("objective") or feed.get("summary") or "No recent objective", 260),
        "detail": compact(feed.get("detail") or feed.get("summary") or "", 320),
        "updatedAt": stamp,
        "ageMinutes": age_minutes(stamp),
        "currentTool": compact(feed.get("currentTool") or feed.get("mode") or "", 80),
        "active": bool(feed.get("active") or feed.get("reportedActive")),
    }


def latest_heartbeat(agent: str, node: str) -> dict[str, Any]:
    payload = read_json(DATA_DIR / "agent-heartbeats.json", {"heartbeats": []})
    rows = payload.get("heartbeats", []) if isinstance(payload, dict) else []
    matches = [
        row for row in rows
        if isinstance(row, dict) and row.get("agent") == agent and (row.get("node") == node or not node)
    ]
    if not matches:
        return {"status": "unknown", "updatedAt": "", "ageMinutes": None, "summary": ""}
    row = max(matches, key=lambda item: parse_ts(item.get("updatedAt")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    return {
        "status": compact(row.get("status") or "unknown", 40),
        "updatedAt": row.get("updatedAt") or "",
        "ageMinutes": age_minutes(row.get("updatedAt")),
        "summary": compact(row.get("summary") or "", 260),
        "stale": bool(row.get("stale")),
    }


def task_rows(agent: str) -> list[dict[str, Any]]:
    payload = read_json(DATA_DIR / "agent-task-queue.json", {"tasks": []})
    rows = payload.get("tasks", []) if isinstance(payload, dict) else []
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("owner") != agent or row.get("status") not in OPEN_TASK_STATUSES:
            continue
        out.append({
            "id": row.get("id"),
            "title": compact(row.get("title"), 120),
            "status": row.get("status"),
            "priority": row.get("priority"),
            "updatedAt": row.get("updatedAt") or row.get("createdAt") or "",
            "objective": compact(row.get("objective"), 220),
        })
    return out[:6]


def handoff_rows(agent: str) -> list[dict[str, Any]]:
    payload = read_json(DATA_DIR / "handoff-queue.json", {"handoffs": []})
    rows = payload.get("handoffs", []) if isinstance(payload, dict) else []
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("status") not in OPEN_HANDOFF_STATUSES:
            continue
        from_agent = str(row.get("from") or "").lower()
        to_agent = str(row.get("to") or "").lower()
        title = str(row.get("title") or "").lower()
        if agent not in {from_agent, to_agent} and agent not in title:
            continue
        out.append({
            "id": row.get("id"),
            "title": compact(row.get("title"), 140),
            "status": row.get("status"),
            "from": row.get("from"),
            "to": row.get("to"),
            "time": row.get("time") or row.get("updatedAt") or "",
        })
    return out[:6]


def event_rows(agent: str) -> list[dict[str, Any]]:
    payload = read_json(DATA_DIR / "shared-events.json", {"events": []})
    rows = payload.get("events", []) if isinstance(payload, dict) else []
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("agent") != agent:
            continue
        out.append({
            "id": row.get("id"),
            "time": row.get("time"),
            "type": row.get("type"),
            "status": row.get("status"),
            "title": compact(row.get("title"), 140),
            "detail": compact(row.get("detail"), 220),
        })
    return out[:8]


def control_node(agent: str) -> dict[str, Any]:
    payload = read_json(DATA_DIR / "agent-control-status.json", {"agents": {}})
    agents = payload.get("agents", {}) if isinstance(payload, dict) else {}
    key = "josh2" if agent == "josh" else "jaimes" if agent in {"jaimes", "jain"} else ""
    row = agents.get(key, {}) if isinstance(agents, dict) and key else {}
    if not isinstance(row, dict):
        return {}
    return {
        "status": row.get("status") or "unknown",
        "probedAt": row.get("probedAt") or "",
        "available": bool(row.get("available")),
        "role": row.get("role") or "",
    }


def freshness_status(feed: dict[str, Any], heartbeat: dict[str, Any]) -> str:
    ages = [age for age in (feed.get("ageMinutes"), heartbeat.get("ageMinutes")) if isinstance(age, int)]
    if not ages:
        return "unknown"
    age = min(ages)
    if age <= 90:
        return "fresh"
    if age <= 240:
        return "aging"
    return "stale"


def build_registry() -> dict[str, Any]:
    generated = utc_now()
    agents: dict[str, Any] = {}
    stale_agents: list[str] = []
    open_counts = {"tasks": 0, "handoffs": 0}
    for agent, config in AGENTS.items():
        feed = normalize_feed(agent, config)
        heartbeat = latest_heartbeat(agent, config["node"])
        tasks = task_rows(agent)
        handoffs = handoff_rows(agent)
        events = event_rows(agent)
        freshness = freshness_status(feed, heartbeat)
        if freshness == "stale":
            stale_agents.append(agent)
        open_counts["tasks"] += len(tasks)
        open_counts["handoffs"] += len(handoffs)
        agents[agent] = {
            "id": agent,
            "label": config["label"],
            "role": config["role"],
            "node": config["node"],
            "contextScope": config["contextScope"],
            "freshness": freshness,
            "control": control_node(agent),
            "brainFeed": feed,
            "heartbeat": heartbeat,
            "openTasks": tasks,
            "openHandoffs": handoffs,
            "recentEvents": events,
            "pickupPrompt": (
                f"Review {config['label']} brainFeed, heartbeat, openTasks, openHandoffs, and recentEvents from this registry before taking over."
            ),
        }
    return {
        "generatedAt": generated,
        "canonicalSource": "Mission Control shared sidecars plus visible Brain Feed lane; Josh 2.0 live Mission Control is the operational source of truth.",
        "privacy": "dashboard-safe summaries only; no raw emails, tokens, OAuth payloads, cookies, or private account contents.",
        "chatSources": read_json(CHAT_SOURCES_PATH, {"sources": []}),
        "summary": {
            "agents": len(agents),
            "staleAgents": stale_agents,
            "openTasks": open_counts["tasks"],
            "openHandoffs": open_counts["handoffs"],
            "status": "attention" if stale_agents or open_counts["handoffs"] else "ready",
        },
        "agents": agents,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build dashboard-safe shared context registry.")
    parser.add_argument("--output", default=str(OUT_PATH))
    args = parser.parse_args()
    registry = build_registry()
    out = Path(args.output)
    write_json(out, registry)
    print(json.dumps({"ok": True, "path": str(out), "summary": registry["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
