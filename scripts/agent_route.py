#!/usr/bin/env python3
"""Route dashboard-safe tasks to the best agent lane."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
POLICY_PATH = DATA_DIR / "agent-routing-policy.json"
CAPABILITIES_PATH = DATA_DIR / "agent-capabilities.json"

GEMINI_FIRST_TASK_TYPES = {
    "review",
    "summary",
    "summarization",
    "report",
    "handoff",
    "runbook",
    "digest",
    "log-summary",
    "stale-task-compression",
    "specialist-summary",
    "model-analysis",
    "non-sensitive-log-review",
    "gemini-review",
    "gemini-long-context",
    "gemini-research",
    "gemini-evaluation",
    "gemini-scheduled-summary",
}

GEMINI_FIRST_CAPABILITIES = {
    "gemini-cli",
    "gemini-review",
    "gemini-long-context",
    "gemini-research",
    "gemini-evaluator",
    "gemini-scheduled-summary",
    "report-generation",
    "non-sensitive-log-review",
}

CODEX_ONLY_TASK_TYPES = {
    "repo-patch",
    "dashboard-update",
    "validation",
    "connected-account-triage",
    "sensitive-coordination",
    "account-mutation",
    "auth-cookie-refresh",
    "browser-auth-workflow",
    "destructive-maintenance",
    "destructive-git",
    "posting-side-effect",
    "raw-private-data-forwarding",
    "raw-secret-handling",
    "sensitive-account-action",
    "unapproved-account-mutation",
    "gemini-private-context",
    "gemini-raw-connector-data",
}


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def compact(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def score_route(route: dict[str, Any], task_type: str, capabilities: set[str], privacy: str) -> int:
    score = int(route.get("priority") or 0)
    if task_type and task_type in set(route.get("taskTypes") or []):
        score += 100
    route_caps = set(route.get("capabilities") or [])
    score += 15 * len(capabilities & route_caps)
    if privacy in set(route.get("privacyTiers") or []):
        score += 20
    elif privacy not in {"dashboard-safe", "agent-private"}:
        score -= 100
    return score


def choose_agent(args: argparse.Namespace) -> tuple[str, dict[str, Any], bool]:
    policy = read_json(POLICY_PATH, {"routes": []})
    caps = set(args.capability or [])
    risky_privacy = args.privacy in set(policy.get("approvalRequiredPrivacy") or [])
    risky_type = args.task_type in set(policy.get("approvalRequiredTaskTypes") or [])
    needs_approval = risky_privacy or risky_type
    routes = [r for r in policy.get("routes", []) if isinstance(r, dict)]
    if needs_approval and args.approval != "approved":
        routes = [r for r in routes if r.get("agent") == "joshex"]
    if args.prefer:
        routes = [r for r in routes if r.get("agent") == args.prefer] + [r for r in routes if r.get("agent") != args.prefer]
    ranked = sorted(
        routes,
        key=lambda route: score_route(route, args.task_type, caps, args.privacy),
        reverse=True,
    )
    if not ranked:
        raise SystemExit("No route available.")
    return str(ranked[0]["agent"]), ranked[0], needs_approval


def choose_model_route(args: argparse.Namespace, owner: str, needs_approval: bool) -> dict[str, Any]:
    caps = set(args.capability or [])
    task_type = args.task_type
    unsafe_privacy = args.privacy != "dashboard-safe"
    codex_only = task_type in CODEX_ONLY_TASK_TYPES
    gemini_hint = task_type in GEMINI_FIRST_TASK_TYPES or bool(caps & GEMINI_FIRST_CAPABILITIES)
    gemini_first = bool(gemini_hint and not codex_only and not unsafe_privacy and not needs_approval)

    if not gemini_first:
        reasons: list[str] = []
        if not gemini_hint:
            reasons.append("task is not synthesis/review/digest classified")
        if codex_only:
            reasons.append("task requires execution or trusted integration")
        if unsafe_privacy:
            reasons.append(f"privacy tier is {args.privacy}")
        if needs_approval:
            reasons.append("approval is required")
        return {
            "firstStop": "codex",
            "provider": "codex",
            "owner": "joshex" if owner == "joshex" else owner,
            "enforced": True,
            "reason": compact("; ".join(reasons) or "Codex-owned task"),
            "guardrails": [
                "Codex owns execution, private connectors, approvals, repo edits, terminal actions, and final integration.",
            ],
        }

    if task_type in {"gemini-evaluation", "model-analysis"}:
        role = "gemini-evaluation"
        model = "gemini-2.5-pro"
    elif task_type in {"gemini-long-context", "gemini-research"}:
        role = "gemini-long-context"
        model = "gemini-2.5-flash"
    elif task_type in {"log-summary", "digest", "scheduled-summary", "gemini-scheduled-summary", "stale-task-compression"}:
        role = "gemini-scheduled-summary"
        model = "gemini-2.5-flash-lite"
    else:
        role = "gemini-review"
        model = "gemini-2.5-flash"

    return {
        "firstStop": "gemini",
        "provider": "gemini",
        "model": model,
        "role": role,
        "owner": owner,
        "enforced": True,
        "privacy": "dashboard-safe",
        "reason": compact(f"{task_type} is dashboard-safe synthesis/review work; use Gemini before Codex."),
        "guardrails": [
            "Send sanitized briefs, summaries, or selected non-sensitive files only.",
            "Do not send secrets, OAuth payloads, raw emails, raw connector data, private account contents, or customer/account data.",
            "Codex/JOSHeX still owns execution, approvals, repo edits, and final integration.",
        ],
    }


def create_task(args: argparse.Namespace, owner: str, approval: str, model_route: dict[str, Any]) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_task.py"),
        "create",
        "--owner", owner,
        "--requester", args.requester,
        "--title", args.title,
        "--objective", args.objective,
        "--priority", args.priority,
        "--privacy", args.privacy,
        "--approval", approval,
        "--note", compact(
            f"Autopilot model route: {model_route.get('firstStop')} "
            f"{model_route.get('model') or model_route.get('provider')}; {model_route.get('reason')}",
            500,
        ),
    ]
    for cap in args.capability or []:
        cmd += ["--capability", cap]
    if args.brain_feed:
        cmd.append("--brain-feed")
    if args.job:
        cmd.append("--job")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or proc.stdout.strip() or f"agent_task.py failed: {proc.returncode}")
    return json.loads(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description="Choose an agent route and optionally create a queued task.")
    parser.add_argument("--task-type", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--privacy", default="dashboard-safe")
    parser.add_argument("--priority", default="normal")
    parser.add_argument("--requester", default="joshex")
    parser.add_argument("--prefer", default="")
    parser.add_argument("--approval", default="none", choices=["none", "required", "approved", "rejected"])
    parser.add_argument("--create-task", action="store_true")
    parser.add_argument("--brain-feed", action="store_true")
    parser.add_argument("--job", action="store_true")
    args = parser.parse_args()

    agent, route, needs_approval = choose_agent(args)
    approval = "approved" if args.approval == "approved" else "required" if needs_approval else args.approval
    model_route = choose_model_route(args, agent, needs_approval)
    result: dict[str, Any] = {
        "agent": agent,
        "approval": approval,
        "needsApproval": needs_approval,
        "modelRoute": model_route,
        "route": {
            "agent": route.get("agent"),
            "taskTypes": route.get("taskTypes", [])[:8],
            "capabilities": route.get("capabilities", [])[:8],
        },
        "reason": compact(
            f"{args.task_type} routed to {agent}; firstStop={model_route.get('firstStop')}; "
            f"capabilities={','.join(args.capability or []) or 'none'}; privacy={args.privacy}"
        ),
    }
    if args.create_task:
        result["task"] = create_task(args, agent, approval, model_route).get("task")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
