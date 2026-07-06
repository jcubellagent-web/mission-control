#!/usr/bin/env python3
"""Run a dashboard-safe Gemini specialist pass and store metadata only."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RUNS_PATH = DATA_DIR / "gemini-specialist-runs.json"
JAIMES_GEMINI_POLICY_PATH = DATA_DIR / "jaimes-gemini-policy.json"

MODE_CONFIG = {
    "daily-digest": {
        "role": "gemini-scheduled-summary",
        "model": "gemini-2.5-flash",
        "modelAlias": "fast",
        "timeout": 90,
        "instruction": "Summarize the dashboard-safe operating picture in 4 tight bullets. Focus on stale work, live agent status, and one practical next step.",
    },
    "ui-readability-review": {
        "role": "gemini-review",
        "model": "gemini-2.5-flash",
        "modelAlias": "review",
        "timeout": 90,
        "instruction": "Review the dashboard-safe Control Tower state for readability and minimalism. Return 3 concise opportunities, no implementation code.",
    },
    "stale-task-compression": {
        "role": "gemini-scheduled-summary",
        "model": "gemini-2.5-flash",
        "modelAlias": "fast",
        "timeout": 90,
        "instruction": "Compress stale or noisy dashboard-safe tasks into a short triage note. Separate keep, quiet, and needs-owner items.",
    },
    "routing-review": {
        "role": "gemini-evaluation",
        "model": "gemini-2.5-flash",
        "modelAlias": "review",
        "timeout": 120,
        "instruction": "Evaluate whether dashboard-safe work is being routed to Gemini first and execution/private work stays Codex-first. Return pass/warn/fail plus one fix.",
    },
    "pro-routing-smoke": {
        "role": "gemini-evaluation",
        "model": "gemini-2.5-pro",
        "modelAlias": "deep",
        "timeout": 90,
        "instruction": "Evaluate this dashboard-safe routing policy in 3 bullets: Flash handles routine summaries/digests; Pro handles explicit deeper evaluations; Codex handles execution/private work.",
        "compactPrompt": True,
    },
}

SENSITIVE_MARKERS = [
    "api_key",
    "authorization:",
    "bearer ",
    "client_secret",
    "cookie:",
    "oauth",
    "password",
    "private key",
    "refresh_token",
    "secret",
    "token",
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def safe_text(value: Any, limit: int = 240) -> str:
    text = compact(value, limit)
    lower = text.lower()
    if any(marker in lower for marker in SENSITIVE_MARKERS):
        return "[redacted-sensitive-marker]"
    return text


def safe_list(values: Any, limit: int = 12) -> list[str]:
    if not isinstance(values, list):
        return []
    rows = []
    for value in values[:limit]:
        text = safe_text(value, 80)
        if text == "[redacted-sensitive-marker]":
            text = "[sensitive-route-redacted]"
        rows.append(text)
    return rows


def model_for_mode(mode: str) -> str:
    config = MODE_CONFIG[mode]
    policy = read_json(JAIMES_GEMINI_POLICY_PATH, {})
    aliases = policy.get("modelAliases") if isinstance(policy, dict) else {}
    alias = str(config.get("modelAlias") or "fast")
    if isinstance(aliases, dict):
        value = aliases.get(alias) or aliases.get("fast")
        if value:
            return str(value)
    return str(config["model"])


def recent_rows(rows: Any, limit: int) -> list[dict[str, Any]]:
    return [row for row in rows if isinstance(row, dict)][:limit] if isinstance(rows, list) else []


def build_prompt(mode: str) -> str:
    config = MODE_CONFIG[mode]
    if config.get("compactPrompt"):
        return (
            f"{config['instruction']}\n\n"
            "Privacy: dashboard-safe only; credential material and private account content are excluded.\n"
            "Policy: JAIMES uses Gemini Flash for routine dashboard-safe throughput, "
            "Gemini Pro for explicit deeper compact evaluation, and Codex/OpenAI for execution/private work."
        )
    dashboard = read_json(DATA_DIR / "dashboard-data.json", {})
    shared = read_json(DATA_DIR / "shared-events.json", {"events": []})
    jobs = read_json(DATA_DIR / "codex-jobs.json", {"jobs": []})
    routing = read_json(DATA_DIR / "agent-routing-policy.json", {})
    gemini = read_json(DATA_DIR / "gemini-ecosystem.json", {})

    agent_feeds = dashboard.get("agentBrainFeeds") if isinstance(dashboard, dict) else {}
    feed_summary = []
    if isinstance(agent_feeds, dict):
        for key, feed in agent_feeds.items():
            if not isinstance(feed, dict):
                continue
            feed_summary.append(
                {
                    "agent": safe_text(feed.get("agent") or key, 40),
                    "status": safe_text(feed.get("status"), 40),
                    "objective": safe_text(feed.get("objective"), 180),
                    "updatedAt": safe_text(feed.get("updatedAt"), 40),
                }
            )

    prompt_payload = {
        "instruction": config["instruction"],
        "privacy": "dashboard-safe only; credential material, private inbox content, and private account content are excluded.",
        "agentFeeds": feed_summary[:6],
        "actionRequired": [
            {
                "priority": safe_text(row.get("priority"), 40),
                "title": safe_text(row.get("title"), 180),
            }
            for row in recent_rows(dashboard.get("actionRequired") if isinstance(dashboard, dict) else [], 6)
        ],
        "recentEvents": [
            {
                "agent": safe_text(row.get("agentLabel") or row.get("agent"), 40),
                "type": safe_text(row.get("type"), 40),
                "title": safe_text(row.get("title"), 180),
                "status": safe_text(row.get("status"), 40),
            }
            for row in recent_rows(shared.get("events"), 8)
            if row.get("privacy") == "dashboard-safe"
        ],
        "recentJobs": [
            {
                "owner": safe_text(row.get("owner"), 40),
                "title": safe_text(row.get("title"), 180),
                "status": safe_text(row.get("status"), 40),
            }
            for row in recent_rows(jobs.get("jobs"), 8)
        ],
        "routingPolicy": {
            "geminiFirstTaskTypes": safe_list((routing.get("modelRouting") or {}).get("geminiFirstTaskTypes", []), 12),
            "codexOnly": safe_list((routing.get("modelRouting") or {}).get("codexOnly", []), 12),
            "guardrail": safe_text((routing.get("modelRouting") or {}).get("guardrail"), 240),
        },
        "lastGeminiTest": gemini.get("lastTest") if isinstance(gemini, dict) else {},
    }
    return json.dumps(prompt_payload, indent=2)


def run_gemini(mode: str, show_output: bool, timeout_override: int | None = None) -> dict[str, Any]:
    config = MODE_CONFIG[mode]
    model = model_for_mode(mode)
    timeout = int(timeout_override or config.get("timeout") or 90)
    prompt = build_prompt(mode)
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "gemini_agent.py"),
        "smoke",
        "--model",
        model,
        "--role",
        config["role"],
        "--prompt",
        prompt,
        "--timeout",
        str(timeout),
        "--write-status",
    ]
    if show_output:
        cmd.append("--show-output")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "error": compact(proc.stderr or proc.stdout, 500)}
    payload["returnCode"] = proc.returncode
    payload["promptHash"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    return payload


def append_run(mode: str, result: dict[str, Any]) -> dict[str, Any]:
    config = MODE_CONFIG[mode]
    model = model_for_mode(mode)
    now = utc_now()
    smoke = result.get("smoke") if isinstance(result.get("smoke"), dict) else {}
    record = {
        "id": f"gemini-{mode}-{now.replace(':', '').replace('-', '')}",
        "time": now,
        "mode": mode,
        "role": config["role"],
        "model": model,
        "status": smoke.get("status") or ("pass" if result.get("ok") else "fail"),
        "ok": bool(result.get("ok")),
        "privacy": smoke.get("privacy") or "dashboard-safe",
        "promptStored": False,
        "outputStored": False,
        "outputChars": smoke.get("outputChars", 0),
        "stderrChars": smoke.get("stderrChars", 0),
        "promptHash": result.get("promptHash"),
    }
    data = read_json(RUNS_PATH, {"runs": []})
    runs = recent_rows(data.get("runs"), 60)
    write_json(RUNS_PATH, {"updatedAt": now, "runs": [record] + runs[:59]})
    return record


def publish_brain_feed(record: dict[str, Any], job: bool, agent: str) -> None:
    status = "done" if record["ok"] else "error"
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_publish.py"),
        "--agent",
        agent,
        "--type",
        "status",
        "--status",
        status,
        "--title",
        f"Gemini specialist pass: {record['mode']}",
        "--tool",
        f"{record['model']} / {record['role']}",
        "--detail",
        (
            f"Dashboard-safe Gemini pass completed with status={record['status']}; outputChars={record['outputChars']}; "
            "raw prompt/output not stored; Codex remains execution owner."
        ),
        "--brain-feed",
    ]
    if job:
        cmd.append("--job")
    subprocess.run(cmd, cwd=ROOT, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a dashboard-safe Gemini specialist pass.")
    parser.add_argument("--mode", choices=sorted(MODE_CONFIG), default="daily-digest")
    parser.add_argument("--agent", choices=["joshex", "josh2", "jaimes", "jain"], default="joshex")
    parser.add_argument("--timeout", type=int, default=0)
    parser.add_argument("--show-output", action="store_true", help="Print Gemini output preview from gemini_agent.py without storing it.")
    parser.add_argument("--brain-feed", action="store_true")
    parser.add_argument("--job", action="store_true")
    args = parser.parse_args()

    result = run_gemini(args.mode, args.show_output, args.timeout or None)
    record = append_run(args.mode, result)
    if args.brain_feed:
        publish_brain_feed(record, args.job, args.agent)
    output = {"ok": record["ok"], "run": record}
    if args.show_output and "outputPreview" in result:
        output["outputPreview"] = result["outputPreview"]
    print(json.dumps(output, indent=2))
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
