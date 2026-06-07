#!/usr/bin/env python3
"""Route dashboard-safe tasks to the best agent lane."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
POLICY_PATH = DATA_DIR / "agent-routing-policy.json"
CAPABILITIES_PATH = DATA_DIR / "agent-capabilities.json"
BUDGETS_PATH = DATA_DIR / "model-provider-budgets.json"
MODEL_USAGE_PATH = DATA_DIR / "modelUsage.json"
JAIMES_GEMINI_POLICY_PATH = DATA_DIR / "jaimes-gemini-policy.json"

GEMINI_FIRST_TASK_TYPES = {
    "review",
    "ui-readability-review",
    "dashboard-readability-review",
    "decision-review",
    "handoff-review",
    "brain-feed-digest",
    "summary",
    "summarization",
    "report",
    "handoff",
    "runbook",
    "digest",
    "daily-digest",
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
    "codex-keychain-alert",
    "macos-keychain-alert",
    "device-alert",
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

XAI_FIRST_TASK_TYPES = {
    "current-events",
    "x-search",
    "x-native-research",
    "x-intelligence",
    "x-post-context",
    "x-sentiment",
    "public-social-sentiment",
    "public-news-monitor",
    "market-narrative",
    "breaking-news-context",
}

XAI_FIRST_CAPABILITIES = {
    "xai-current-events",
    "xai-x-search",
    "xai-public-sentiment",
    "xai-market-narrative",
}

OPENROUTER_FALLBACK_TASK_TYPES = {
    "model-fallback",
    "provider-fallback",
    "outside-model-check",
}

OPENROUTER_FALLBACK_CAPABILITIES = {
    "openrouter-fallback",
    "outside-model-check",
}

DEDICATED_HOST_EXECUTION_TYPES = {
    "repo-patch",
    "dashboard-update",
    "validation",
    "health-check",
    "dashboard-refresh",
    "service-status",
    "host-maintenance",
    "non-sensitive-log-review",
}

JOSH2_PREFERRED_TYPES = {
    "dashboard-refresh",
    "dashboard-update",
    "health-check",
    "service-status",
    "host-maintenance",
    "codex-keychain-alert",
    "macos-keychain-alert",
    "device-alert",
}

JOSHEX_LOCAL_ONLY_TYPES = {
    "connected-account-triage",
    "sensitive-coordination",
    "account-mutation",
    "auth-cookie-refresh",
    "browser-auth-workflow",
    "raw-private-data-forwarding",
    "raw-secret-handling",
    "sensitive-account-action",
    "unapproved-account-mutation",
}


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def compact(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def score_route(route: dict[str, Any], task_type: str, capabilities: set[str], privacy: str, requester: str = "") -> int:
    score = int(route.get("priority") or 0)
    agent = str(route.get("agent") or "")
    requester = str(requester or "").lower()
    if task_type and task_type in set(route.get("taskTypes") or []):
        score += 100
    route_caps = set(route.get("capabilities") or [])
    score += 15 * len(capabilities & route_caps)
    if agent == "jaimes" and privacy == "dashboard-safe" and task_type in GEMINI_FIRST_TASK_TYPES and task_type not in CODEX_ONLY_TASK_TYPES:
        score += 80
    if agent == "joshex" and task_type in CODEX_ONLY_TASK_TYPES and task_type in JOSHEX_LOCAL_ONLY_TYPES:
        score += 80
    if agent in {"josh", "jaimes"} and privacy in {"dashboard-safe", "agent-private"} and task_type in DEDICATED_HOST_EXECUTION_TYPES:
        score += 75
    if agent == "josh" and privacy in {"dashboard-safe", "agent-private"} and task_type in JOSH2_PREFERRED_TYPES:
        score += 90
    if requester == "joshex" and privacy in {"dashboard-safe", "agent-private"} and task_type not in JOSHEX_LOCAL_ONLY_TYPES:
        if agent == "joshex":
            score -= 60
        elif agent in {"josh", "jaimes"}:
            score += 35
    if requester in {"josh", "josh2", "josh2.0"} and privacy == "agent-private" and task_type == "connected-account-triage":
        if agent == "josh":
            score += 180
        elif agent == "joshex":
            score -= 60
    if requester == "jaimes" and privacy == "agent-private" and task_type == "connected-account-triage":
        if agent == "jaimes":
            score += 180
        elif agent == "joshex":
            score -= 60
    if privacy in set(route.get("privacyTiers") or []):
        score += 20
    elif privacy not in {"dashboard-safe", "agent-private"}:
        score -= 100
    return score


def provider_budget(provider_id: str) -> dict[str, Any]:
    budgets = read_json(BUDGETS_PATH, {"providers": []})
    for row in budgets.get("providers", []) if isinstance(budgets, dict) else []:
        if isinstance(row, dict) and row.get("id") == provider_id:
            return row
    return {}


def gemini_model(alias: str = "fast") -> str:
    policy = read_json(JAIMES_GEMINI_POLICY_PATH, {})
    aliases = policy.get("modelAliases") if isinstance(policy, dict) else {}
    if isinstance(aliases, dict):
        value = aliases.get(alias) or aliases.get("fast")
        if value:
            return str(value)
    budget_value = provider_budget("gemini").get("lastModelUsed")
    return str(budget_value or "gemini-3-flash-preview")


def provider_budget_guard(provider_id: str) -> tuple[bool, str]:
    row = provider_budget(provider_id)
    daily_cap = float(row.get("dailyCapUsd") or 0)
    daily_spend = float(row.get("dailySpendUsd") or 0)
    remaining = row.get("remainingCreditUsd")
    if daily_cap > 0 and daily_spend >= daily_cap:
        return False, f"{provider_id} daily cap reached (${daily_spend:.2f}/${daily_cap:.2f})"
    if remaining is not None and float(remaining or 0) <= 0:
        return False, f"{provider_id} has no remaining prepaid credit"
    return True, "budget available"


def codex_allowance_mode(args: argparse.Namespace) -> str:
    requested = getattr(args, "codex_allowance", "auto")
    if requested != "auto":
        return requested
    env_mode = os.environ.get("CODEX_ALLOWANCE_MODE", "").strip().lower()
    if env_mode in {"normal", "conserve", "exhausted"}:
        return env_mode
    budgets = read_json(BUDGETS_PATH, {"policy": {}})
    policy = budgets.get("policy", {}) if isinstance(budgets, dict) else {}
    policy_mode = str(policy.get("codexAllowanceMode") or "normal").strip().lower()
    if policy_mode in {"normal", "conserve", "exhausted"}:
        return policy_mode
    usage = read_json(MODEL_USAGE_PATH, {})
    codexbar = (((usage.get("codingVisibility") or {}).get("codexbar") or {}) if isinstance(usage, dict) else {})
    weekly = " ".join(str(codexbar.get(key) or "") for key in ("weekly", "summary")).lower()
    if "run out" in weekly or "exhaust" in weekly or "0% left" in weekly:
        return "exhausted"
    if "deficit" in weekly or "2% left" in weekly or "1% left" in weekly or "3% left" in weekly:
        return "conserve"
    return "normal"


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
        key=lambda route: score_route(route, args.task_type, caps, args.privacy, args.requester),
        reverse=True,
    )
    if not ranked:
        raise SystemExit("No route available.")
    return str(ranked[0]["agent"]), ranked[0], needs_approval


def choose_model_route(args: argparse.Namespace, owner: str, needs_approval: bool) -> dict[str, Any]:
    caps = set(args.capability or [])
    task_type = args.task_type
    allowance_mode = codex_allowance_mode(args)
    codex_constrained = allowance_mode in {"conserve", "exhausted"}
    unsafe_privacy = args.privacy != "dashboard-safe"
    codex_only = task_type in CODEX_ONLY_TASK_TYPES
    gemini_hint = task_type in GEMINI_FIRST_TASK_TYPES or bool(caps & GEMINI_FIRST_CAPABILITIES)
    xai_hint = task_type in XAI_FIRST_TASK_TYPES or bool(caps & XAI_FIRST_CAPABILITIES)
    openrouter_hint = task_type in OPENROUTER_FALLBACK_TASK_TYPES or bool(caps & OPENROUTER_FALLBACK_CAPABILITIES)
    gemini_first = bool(gemini_hint and not codex_only and not unsafe_privacy and not needs_approval)
    xai_first = bool(xai_hint and not codex_only and not unsafe_privacy and not needs_approval)
    openrouter_fallback = bool(openrouter_hint and not codex_only and not unsafe_privacy and not needs_approval)

    if xai_first:
        budget_ok, budget_reason = provider_budget_guard("xai")
        if budget_ok:
            if task_type in {"x-post-context", "x-sentiment", "public-social-sentiment", "market-narrative"}:
                role = "xai-public-sentiment"
            else:
                role = "xai-current-events"
            return {
                "firstStop": "xai",
                "provider": "xai",
                "model": provider_budget("xai").get("lastModelUsed") or "grok-4.3",
                "role": role,
                "owner": owner,
                "enforced": True,
                "codexAllowanceMode": allowance_mode,
                "spendClass": "codex-sparing" if codex_constrained else "normal",
                "privacy": "dashboard-safe",
                "reason": compact(f"{task_type} depends on public current-events, X-native, social sentiment, or market narrative context; {budget_reason}."),
                "guardrails": [
                    "Send dashboard-safe public context or sanitized briefs only.",
                    "Do not send secrets, OAuth payloads, raw emails, raw connector data, private account contents, or customer/account data.",
                    "The selected owner still owns execution, approvals, repo edits, and final integration.",
                ],
            }
        gemini_first = True

    if openrouter_fallback:
        budget_ok, budget_reason = provider_budget_guard("openrouter")
        if budget_ok:
            return {
                "firstStop": "openrouter",
                "provider": "openrouter",
                "model": provider_budget("openrouter").get("lastModelUsed") or "openrouter/auto",
                "role": "provider-fallback",
                "owner": owner,
                "enforced": True,
                "codexAllowanceMode": allowance_mode,
                "spendClass": "fallback-check",
                "privacy": "dashboard-safe",
                "reason": compact(f"{task_type} explicitly requested fallback/outside model routing; {budget_reason}."),
                "guardrails": [
                    "Use only for dashboard-safe fallback checks or specific outside-model comparisons.",
                    "Do not send secrets, OAuth payloads, raw emails, raw connector data, private account contents, or customer/account data.",
                    "The selected owner still owns execution, approvals, repo edits, and final integration.",
                ],
            }

    if codex_constrained and not unsafe_privacy and not needs_approval and not codex_only and not gemini_first and not xai_first:
        gemini_first = True

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
            "codexAllowanceMode": allowance_mode,
            "spendClass": "reserved-execution" if codex_constrained else "normal",
            "fallbackProviders": ["openai-api", "openrouter"] if codex_constrained and (codex_only or unsafe_privacy or needs_approval) else [],
            "reason": compact("; ".join(reasons) or "Codex-owned task"),
            "guardrails": [
                "Codex on the selected host owns execution, private connectors, approvals, repo edits, terminal actions, and final integration.",
                "When Codex allowance is constrained, use metered API or OpenRouter fallback only for execution/private actions Josh has authorized.",
            ],
        }

    if task_type in {"gemini-evaluation", "model-analysis"}:
        role = "gemini-evaluation"
        model = gemini_model("deep")
    elif task_type in {"gemini-long-context", "gemini-research"}:
        role = "gemini-long-context"
        model = gemini_model("longContext")
    elif task_type in {"log-summary", "digest", "daily-digest", "brain-feed-digest", "scheduled-summary", "gemini-scheduled-summary", "stale-task-compression"}:
        role = "gemini-scheduled-summary"
        model = gemini_model("fast")
    elif task_type in {"ui-readability-review", "dashboard-readability-review", "decision-review", "handoff-review"}:
        role = "gemini-review"
        model = gemini_model("review")
    else:
        role = "gemini-review"
        model = gemini_model("fast")

    return {
        "firstStop": "gemini",
        "provider": "gemini",
        "model": model,
        "role": role,
        "owner": owner,
        "enforced": True,
        "codexAllowanceMode": allowance_mode,
        "spendClass": "codex-sparing" if codex_constrained else "normal",
        "privacy": "dashboard-safe",
        "reason": compact(
            f"{task_type} is dashboard-safe synthesis/review work; use Gemini before Codex."
            if not codex_constrained
            else f"Codex allowance mode is {allowance_mode}; route dashboard-safe non-execution work to Gemini first."
        ),
        "guardrails": [
            "Send sanitized briefs, summaries, or selected non-sensitive files only.",
            "Do not send secrets, OAuth payloads, raw emails, raw connector data, private account contents, or customer/account data.",
            "The selected owner still owns execution, approvals, repo edits, and final integration.",
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
    parser.add_argument("--codex-allowance", default="auto", choices=["auto", "normal", "conserve", "exhausted"])
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
