#!/usr/bin/env python3
"""Route dashboard-safe tasks to the best agent lane."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
POLICY_PATH = DATA_DIR / "agent-routing-policy.json"
CAPABILITIES_PATH = DATA_DIR / "agent-capabilities.json"
BUDGETS_PATH = DATA_DIR / "model-provider-budgets.json"
MODEL_USAGE_PATH = DATA_DIR / "modelUsage.json"
JAIMES_GEMINI_POLICY_PATH = DATA_DIR / "jaimes-gemini-policy.json"
ROUTE_TELEMETRY_PATH = Path(
    os.environ.get("AGENT_ROUTE_TELEMETRY_PATH", DATA_DIR / "agent-route-decisions.jsonl")
)

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
    "gemini-antigravity",
    "gemini-cli",  # legacy compatibility alias
    "gemini-review",
    "gemini-long-context",
    "gemini-research",
    "gemini-evaluator",
    "gemini-scheduled-summary",
    "report-generation",
    "non-sensitive-log-review",
}

#JAIMES: GLM 5.2 Cloud is the deliberate long-context technical-reasoning sub-agent,
# while Gemini owns synthesis and Codex owns mutation, permissions, and integration.
GLM_FIRST_TASK_TYPES = {
    "architecture-analysis",
    "large-context-technical-analysis",
    "multi-file-planning",
    "parallel-technical-reasoning",
    "structured-code-review",
    "technical-second-opinion",
}

GLM_FIRST_CAPABILITIES = {
    "glm-cloud",
    "large-context-technical-reasoning",
    "multi-file-planning",
    "structured-code-review",
}

CODEX_ONLY_TASK_TYPES = {
    "code",
    "repair",
    "multi-step",
    "control-tower-code",
    "control-tower-repair",
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
    "gmail",
    "personal-gmail",
    "personal-inbox",
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

INBOX_FRONTDOOR_TYPES = {
    "frontdesk",
    "inbox",
    "inbox-triage",
    "telegram-inbox",
    "group-chat-inbox",
    "shared-agent-inbox",
}

CONTROL_TOWER_TYPES = {
    "control-tower",
    "control-tower-code",
    "control-tower-repair",
    "dashboard-refresh",
    "dashboard-update",
    "health-check",
    "service-status",
}

SORARE_TYPES = {
    "sorare",
    "sorare-lineup",
    "sorare-research",
    "sorare-monitor",
}

CODE_OR_REPAIR_TYPES = {
    "code",
    "repair",
    "multi-step",
    "repo-patch",
    "validation",
    "host-maintenance",
    "control-tower-code",
    "control-tower-repair",
}

EXECUTION_CAPABILITIES = {
    "repo-edit",
    "terminal",
    "service-repair",
    "browser-auth",
    "multi-step",
    "tool-execution",
}

HIGH_BLAST_CAPABILITIES = {
    "high-blast-radius",
    "incident-command",
    "security-critical",
    "production-migration",
}


#JAIMES: Ollama is a first-class explicit fresh-lane provider alongside Codex, Gemini, and Grok.
REQUESTED_PROVIDER_ALIASES = {
    "gpt": "codex",
    "gpt-5.6": "codex",
    "gpt-5.6-luna": "codex",
    "gpt-5.6-terra": "codex",
    "gpt-5.6-sol": "codex",
    "gpt-5.5": "codex",
    "gpt-5.4": "codex",
    "codex": "codex",
    "openai": "codex",
    "openai-codex": "codex",
    "ollama": "ollama",
    "ollama-local": "ollama",
    "local": "ollama",
    "gemini": "gemini",
    "google": "gemini",
    "google-gemini-cli": "gemini",  # legacy Hermes/OpenCLAW provider id
    "gemini-antigravity": "gemini",
    "antigravity": "gemini",
    "grok": "xai",
    "xai": "xai",
    "x": "xai",
    "openrouter": "openrouter",
}

PROVIDER_DEFAULT_MODELS = {
    "codex": "gpt-5.6-terra",
    "gemini": "gemini-3.6-flash-medium",
    "ollama": "qwen2.5:7b",
    "xai": "grok-4.20-reasoning",
    "openrouter": "openrouter/auto",
}

PROVIDER_AUTH_LABELS = {
    "codex": "OpenAI Codex OAuth/subscription",
    "gemini": "Antigravity-authenticated Gemini subscription",
    "ollama": "Local Ollama runtime",
    "xai": "SuperGrok CLI OAuth/subscription",
    "openrouter": "OpenRouter metered API",
}

JAIMES_SPECIALIST_HOST = os.environ.get("MODEL_LANE_JAIMES_HOST", "jaimes")


def provider_auth_label(provider: str, model: str = "") -> str:
    if provider == "ollama" and str(model or "").strip().lower().endswith(":cloud"):
        return "Ollama Cloud"
    return PROVIDER_AUTH_LABELS.get(provider, provider)



def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def compact(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def optional_ms(value: Optional[float]) -> Optional[int]:
    if value is None:
        return None
    return max(0, round(float(value)))


def append_route_telemetry(
    args: argparse.Namespace,
    result: dict[str, Any],
    routing_duration_ms: int,
) -> None:
    """Append one dashboard-safe routing decision without storing prompt text."""
    model_route = result.get("modelRoute") if isinstance(result.get("modelRoute"), dict) else {}
    provider = str(model_route.get("provider") or model_route.get("firstStop") or "unknown")
    model = str(model_route.get("model") or PROVIDER_DEFAULT_MODELS.get(provider) or "unknown")
    signature_source = "\x1f".join(
        [args.task_type, args.title, args.objective, args.privacy, result.get("agent", "")]
    )
    record = {
        "schemaVersion": 2,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "routeDecisionId": hashlib.sha256(
            f"{signature_source}\x1f{time.time_ns()}".encode("utf-8")
        ).hexdigest()[:20],
        "requestSignature": hashlib.sha256(signature_source.encode("utf-8")).hexdigest()[:16],
        "sourceAgent": args.requester,
        "owner": result.get("agent"),
        "taskType": args.task_type,
        "privacy": args.privacy,
        "priority": args.priority,
        "provider": provider,
        "model": model,
        "firstStop": model_route.get("firstStop"),
        "role": model_route.get("role"),
        "approval": result.get("approval"),
        "needsApproval": bool(result.get("needsApproval")),
        "outcome": "routed",
        "routeLabel": model_route.get("role") or model_route.get("firstStop") or provider,
        "reason": compact(model_route.get("reason") or result.get("reason") or "deterministic policy route"),
        "queueDurationMs": optional_ms(args.queue_duration_ms),
        "routingDurationMs": routing_duration_ms,
        "routeDurationMs": routing_duration_ms,
        "memoryDurationMs": optional_ms(args.memory_duration_ms),
        "toolDurationMs": optional_ms(args.tool_duration_ms),
        "modelDurationMs": optional_ms(args.model_duration_ms),
        "timingComplete": all(
            value is not None
            for value in (
                args.queue_duration_ms,
                args.memory_duration_ms,
                args.tool_duration_ms,
                args.model_duration_ms,
            )
        ),
        "telemetryPolicy": "no raw prompts, secrets, OAuth payloads, cookies, passwords, tokens, raw emails, or private account contents",
    }
    ROUTE_TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ROUTE_TELEMETRY_PATH.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


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
    #JAIMES: Antigravity model ids are executable; human labels and retired
    # google-gemini-cli ids must never leak into a fresh lane command.
    antigravity_models = {
        "deep": "gemini-3.1-pro-high",
        "judgment": "gemini-3.1-pro-high",
        "longContext": "gemini-3.1-pro-high",
        "review": "gemini-3.6-flash-high",
        "fast": "gemini-3.6-flash-medium",
    }
    if alias in antigravity_models:
        return antigravity_models[alias]
    policy = read_json(JAIMES_GEMINI_POLICY_PATH, {})
    aliases = policy.get("modelAliases") if isinstance(policy, dict) else {}
    if isinstance(aliases, dict):
        value = aliases.get(alias) or aliases.get("fast")
        if value:
            return str(value)
    budget = provider_budget("gemini")
    preferred = budget.get("preferredModels") if isinstance(budget.get("preferredModels"), dict) else {}
    budget_aliases = {
        "fast": "routine",
        "review": "review",
        "deep": "deep",
        "longContext": "longContext",
    }
    preferred_value = preferred.get(budget_aliases.get(alias, alias)) if isinstance(preferred, dict) else None
    if preferred_value:
        return str(preferred_value)
    budget_value = str(budget.get("lastModelUsed") or "")
    if "gemini" in budget_value.lower() and "subscription" not in budget_value.lower():
        return budget_value
    return PROVIDER_DEFAULT_MODELS["gemini"]


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

    #JAIMES: exact CodexBar windows outrank a stale static "normal" policy.
    usage = read_json(MODEL_USAGE_PATH, {})
    limits = ((usage.get("codexbarLimits") or {}).get("codex") or {}) if isinstance(usage, dict) else {}
    windows = limits.get("usageWindows") if isinstance(limits, dict) else []
    weekly_remaining: Optional[float] = None
    for window in windows if isinstance(windows, list) else []:
        if not isinstance(window, dict):
            continue
        label = str(window.get("label") or "").strip().lower()
        if label != "weekly":
            continue
        try:
            weekly_remaining = float(window.get("remainingPercent"))
        except (TypeError, ValueError):
            weekly_remaining = None
        break
    if weekly_remaining is not None:
        if weekly_remaining <= 0:
            return "exhausted"
        if weekly_remaining <= 20:
            return "conserve"

    codexbar = (((usage.get("codingVisibility") or {}).get("codexbar") or {}) if isinstance(usage, dict) else {})
    weekly = " ".join(str(codexbar.get(key) or "") for key in ("weekly", "summary")).lower()
    if "run out" in weekly or "exhaust" in weekly or "0% left" in weekly:
        return "exhausted"
    remaining_match = re.search(r"\b(\d+(?:\.\d+)?)%\s+left\b", weekly)
    if "deficit" in weekly or (remaining_match and float(remaining_match.group(1)) <= 20):
        return "conserve"

    budgets = read_json(BUDGETS_PATH, {"policy": {}})
    policy = budgets.get("policy", {}) if isinstance(budgets, dict) else {}
    policy_mode = str(policy.get("codexAllowanceMode") or "normal").strip().lower()
    if policy_mode in {"normal", "conserve", "exhausted"}:
        return policy_mode
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
    else:
        hard_owner = hard_owner_for(args)
        if hard_owner:
            exact = [r for r in routes if r.get("agent") == hard_owner]
            if exact:
                return hard_owner, exact[0], needs_approval
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


def hard_owner_for(args: argparse.Namespace) -> str:
    """Apply ecosystem ownership boundaries before heuristic scoring."""
    task_type = str(args.task_type or "").strip().lower()
    requester = str(args.requester or "").strip().lower()
    privacy = str(args.privacy or "").strip().lower()

    #JAIMES: GLM specialist passes stay owned by the Telegram/agent lane that requested them.
    if task_type in GLM_FIRST_TASK_TYPES:
        if requester in {"josh", "josh2", "josh2.0"}:
            return "josh"
        if requester in {"jaimes", "jain", "j.a.i.n"}:
            return "jaimes"
        return "joshex"
    if task_type == "connected-account-triage" and privacy == "agent-private":
        if requester in {"josh", "josh2", "josh2.0"}:
            return "josh"
        if requester == "jaimes":
            return "jaimes"
        return "joshex"
    if task_type in JOSHEX_LOCAL_ONLY_TYPES or task_type in {"gmail", "personal-gmail", "personal-inbox"}:
        return "joshex"
    if task_type in INBOX_FRONTDOOR_TYPES or task_type in CONTROL_TOWER_TYPES:
        return "josh"
    if task_type in SORARE_TYPES:
        return "jain" if task_type == "sorare-monitor" else "jaimes"
    if task_type in {"code", "repair", "multi-step"}:
        return "josh"
    return ""



def normalize_requested_provider(value: str = "", model: str = "") -> str:
    text = str(value or "").strip().lower()
    model_text = str(model or "").strip().lower()
    if text:
        return REQUESTED_PROVIDER_ALIASES.get(text, text)
    if model_text.startswith(("gpt-", "o", "codex/", "openai/")):
        return "codex"
    if "gemini" in model_text:
        return "gemini"
    if model_text.startswith("ollama/") or any(name in model_text for name in ("qwen", "llama", "gemma", "glm")):
        return "ollama"
    if "grok" in model_text or model_text.startswith("xai/"):
        return "xai"
    if "openrouter" in model_text:
        return "openrouter"
    return ""


def explicit_model_request(args: argparse.Namespace) -> tuple[str, str, str]:
    requested_model = str(getattr(args, "requested_model", "") or "").strip()
    requested_provider = normalize_requested_provider(getattr(args, "requested_provider", "") or "", requested_model)
    requested_reason = str(getattr(args, "requested_reason", "") or "requested by Josh").strip()
    if requested_provider and not requested_model:
        if requested_provider == "gemini":
            requested_model = gemini_model("review") or PROVIDER_DEFAULT_MODELS[requested_provider]
        elif requested_provider == "xai":
            requested_model = provider_budget("xai").get("lastModelUsed") or PROVIDER_DEFAULT_MODELS[requested_provider]
        elif requested_provider == "openrouter":
            requested_model = provider_budget("openrouter").get("lastModelUsed") or PROVIDER_DEFAULT_MODELS[requested_provider]
        else:
            requested_model = PROVIDER_DEFAULT_MODELS.get(requested_provider, "")
    return requested_provider, requested_model, requested_reason


def remote_specialist_available(provider: str, model: str = "") -> bool:
    if Path.home().name == "jc_agent":
        return False
    if provider == "gemini":
        requested = str(model or "gemini-3.6-flash-medium").lower()
        command = (
            "curl -fsS --max-time 10 http://127.0.0.1:11435/v1/models "
            "-H 'Authorization: Bearer agy-local' "
            f"| grep -Fq {shlex.quote(requested)}"
        )
    elif provider == "ollama" and str(model or "").lower().endswith(":cloud"):
        payload = json.dumps({
            "model": str(model).lower().removeprefix("ollama/"),
            "prompt": "",
            "stream": False,
            "options": {"num_predict": 0},
        })
        command = (
            "curl -fsS --max-time 10 http://127.0.0.1:11434/api/generate "
            f"-H 'Content-Type: application/json' -d {shlex.quote(payload)} >/dev/null"
        )
    elif provider == "xai":
        command = "test -x ~/.local/bin/grok && ~/.local/bin/grok models >/dev/null 2>&1"
    else:
        return False
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", JAIMES_SPECIALIST_HOST, command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def explicit_route_unavailable(provider: str, model: str = "") -> str:
    if provider == "gemini":
        if Path.home().name == "jc_agent":
            request = urllib.request.Request(
                "http://127.0.0.1:11435/v1/models",
                headers={"Authorization": "Bearer agy-local"},
            )
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    payload = json.loads(response.read())
                names = {
                    str(row.get("id") or "").lower()
                    for row in payload.get("data", []) if isinstance(row, dict)
                }
                requested = str(model or "gemini-3.6-flash-medium").lower()
                if requested in names:
                    return ""
            except Exception:
                pass
        return "" if remote_specialist_available(provider, model) else "Antigravity subscription proxy is unavailable on JAIMES"
    if provider == "xai":
        row = provider_budget("xai")
        auth = str(row.get("authStatus") or "").lower()
        if "pending" in auth or "missing" in auth:
            return f"xAI/Grok auth is {row.get('authStatus')}"
    if provider == "ollama":
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as response:
                tags = json.loads(response.read())
            names = {str(row.get("name") or "").lower() for row in tags.get("models", []) if isinstance(row, dict)}
            requested = str(model or "").lower().removeprefix("ollama/")
            if requested and requested not in names:
                if remote_specialist_available(provider, model):
                    return ""
                return f"Ollama model {requested} is not installed on this host or JAIMES"
            if requested.endswith(":cloud"):
                payload = json.dumps({
                    "model": requested,
                    "prompt": "",
                    "stream": False,
                    "options": {"num_predict": 0},
                }).encode("utf-8")
                request = urllib.request.Request(
                    "http://127.0.0.1:11434/api/generate",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=8) as response:
                    if not 200 <= response.status < 300:
                        return f"Ollama Cloud model {requested} is not authenticated"
            return ""
        except Exception:
            if remote_specialist_available(provider, model):
                return ""
            return "Ollama runtime or cloud authentication is unavailable on this host and JAIMES"
    if provider == "openrouter":
        ok, reason = provider_budget_guard("openrouter")
        return "" if ok else reason
    return ""


def explicit_route_payload(provider: str, model: str, owner: str, args: argparse.Namespace, allowance_mode: str, reason: str) -> dict[str, Any]:
    auth = provider_auth_label(provider, model)
    first_stop = "grok" if provider == "xai" else provider
    return {
        "firstStop": first_stop,
        "provider": provider,
        "model": model,
        "auth": auth,
        "role": "explicit-model-request",
        "owner": owner,
        "enforced": True,
        "explicitRequest": True,
        "requestedBy": "Josh",
        "freshLaneRequired": True,
        "checkpointRequired": True,
        "verifyBeforeWork": True,
        "verification": {
            "required": True,
            "method": "launch fresh session with provider/model override and require first visible Active Model/Auth line",
            "mustMatch": f"{provider}/{model}" if model else provider,
        },
        "codexAllowanceMode": allowance_mode,
        "spendClass": "explicit-request",
        "privacy": args.privacy,
        "reason": compact(reason or f"{provider}/{model} explicitly requested by Josh."),
        "telegramDisclosure": f"Model: {model or provider} - requested by Josh; Auth: {auth}",
        "guardrails": [
            "Do not mutate the current session to simulate a switch; checkpoint and launch a fresh lane/session.",
            "Verify the requested provider/model before doing substantive work.",
            "If verification fails, report unavailable and use only an approved fallback.",
        ],
    }


def codex_model_for(args: argparse.Namespace) -> str:
    """Choose Luna/Terra/Sol from explicit, auditable task signals."""
    task_type = str(args.task_type or "").strip().lower()
    caps = {str(value).strip().lower() for value in (args.capability or [])}
    complexity = str(getattr(args, "complexity", "auto") or "auto").strip().lower()
    blast_radius = str(getattr(args, "blast_radius", "auto") or "auto").strip().lower()
    priority = str(args.priority or "normal").strip().lower()

    sol_earned = (
        complexity == "hard"
        and blast_radius == "high"
        and (priority in {"high", "critical"} or bool(caps & HIGH_BLAST_CAPABILITIES))
    )
    if sol_earned:
        return "gpt-5.6-sol"

    terra_required = (
        complexity in {"multi-step", "hard"}
        or task_type in CODE_OR_REPAIR_TYPES
        or task_type in CODEX_ONLY_TASK_TYPES
        or bool(caps & EXECUTION_CAPABILITIES)
        or blast_radius == "high"
    )
    if terra_required:
        return "gpt-5.6-terra"

    bounded_inbox = task_type in INBOX_FRONTDOOR_TYPES and complexity in {"auto", "bounded"}
    if bounded_inbox:
        return "gpt-5.6-luna"
    return "gpt-5.6-terra"


def xai_verified_available() -> tuple[bool, str]:
    """Require an explicit enabled signal and a current verified auth signal."""
    row = provider_budget("xai")
    auth = str(row.get("authStatus") or "").strip().lower()
    verified_auth = auth.startswith("available") or auth in {"verified", "healthy"}
    enabled_value = os.environ.get("XAI_ENABLED", "").strip().lower()
    if enabled_value in {"0", "false", "no", "off"}:
        return False, "xAI/Grok route is explicitly disabled"
    enabled = enabled_value in {"1", "true", "yes", "on"} or (not enabled_value and verified_auth)
    if not enabled:
        return False, "xAI/Grok route is not enabled or verified"
    env_verified = os.environ.get("XAI_VERIFIED", "").strip().lower() in {"1", "true", "yes", "on"}
    if not (env_verified or verified_auth):
        return False, f"xAI/Grok availability is not verified ({auth or 'no auth status'})"
    budget_ok, budget_reason = provider_budget_guard("xai")
    if not budget_ok:
        return False, budget_reason
    allowance_ok, allowance_reason = xai_live_allowance_status()
    if allowance_ok is False:
        return False, allowance_reason
    detail = f"; {allowance_reason}" if allowance_ok is True and allowance_reason else ""
    return True, f"xAI/Grok route is enabled and verified{detail}"


def xai_live_allowance_status(now: dt.datetime | None = None) -> tuple[bool | None, str]:
    """Read the current SuperGrok window without treating static config as quota truth.

    ``None`` means no exact live allowance was reported, so the independently
    verified auth/budget gates may still decide availability. A reported stale,
    exhausted, limited, or unavailable window fails closed into the X UI ladder.
    """
    usage = read_json(MODEL_USAGE_PATH, {})
    limits = ((usage.get("codexbarLimits") or {}).get("xai") or {}) if isinstance(usage, dict) else {}
    if not isinstance(limits, dict) or not limits:
        return None, "live SuperGrok allowance is not reported"

    updated_at = str(limits.get("codexbarUpdatedAt") or "")
    if updated_at:
        try:
            observed = dt.datetime.fromisoformat(updated_at.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
            current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
            if current - observed > dt.timedelta(minutes=30):
                return False, "live SuperGrok allowance telemetry is stale"
        except ValueError:
            return False, "live SuperGrok allowance timestamp is invalid"

    status = str(limits.get("status") or "").strip().lower()
    if limits.get("available") is False or status in {"blocked", "error", "exhausted", "limited", "unavailable"}:
        return False, f"SuperGrok allowance is {status or 'unavailable'}"

    windows = limits.get("usageWindows") if isinstance(limits.get("usageWindows"), list) else []
    remaining = [
        float(row.get("remainingPercent"))
        for row in windows
        if isinstance(row, dict) and row.get("remainingPercent") is not None
    ]
    if remaining:
        minimum = min(remaining)
        if minimum <= 0:
            return False, "SuperGrok allowance is exhausted"
        return True, f"SuperGrok live allowance has {minimum:g}% remaining"
    if limits.get("available") is True:
        return True, "SuperGrok is live but its exact remaining allowance is unknown"
    return None, "live SuperGrok allowance is not reported"

def choose_model_route(args: argparse.Namespace, owner: str, needs_approval: bool) -> dict[str, Any]:
    caps = set(args.capability or [])
    task_type = args.task_type
    allowance_mode = codex_allowance_mode(args)
    codex_constrained = allowance_mode in {"conserve", "exhausted"}
    unsafe_privacy = args.privacy != "dashboard-safe"
    codex_only = (
        task_type in CODEX_ONLY_TASK_TYPES
        or task_type in INBOX_FRONTDOOR_TYPES
        or task_type in CONTROL_TOWER_TYPES
        or task_type in SORARE_TYPES
    )
    gemini_hint = task_type in GEMINI_FIRST_TASK_TYPES or bool(caps & GEMINI_FIRST_CAPABILITIES)
    glm_hint = task_type in GLM_FIRST_TASK_TYPES or bool(caps & GLM_FIRST_CAPABILITIES)
    xai_hint = task_type in XAI_FIRST_TASK_TYPES or bool(caps & XAI_FIRST_CAPABILITIES)
    openrouter_hint = task_type in OPENROUTER_FALLBACK_TASK_TYPES or bool(caps & OPENROUTER_FALLBACK_CAPABILITIES)
    gemini_first = bool(gemini_hint and not codex_only and not unsafe_privacy and not needs_approval)
    xai_available, xai_availability_reason = xai_verified_available()
    xai_first = bool(xai_hint and xai_available and not codex_only and not unsafe_privacy and not needs_approval)
    openrouter_fallback = bool(openrouter_hint and not codex_only and not unsafe_privacy and not needs_approval)

    requested_provider, requested_model, requested_reason = explicit_model_request(args)
    if requested_provider == "codex" and requested_model:
        policy_model = codex_model_for(args)
        requested_codex_model = requested_model.lower().removeprefix("openai/")
        if requested_codex_model in {"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"} and requested_codex_model != policy_model:
            requested_reason = (
                f"Requested {requested_codex_model} does not match the safety/complexity ladder for this task; "
                f"using {policy_model}. Sol is allowed only for earned hard/high-blast work and Luna is bounded Inbox-only."
            )
            requested_model = policy_model
    xai_requested_fallback = requested_provider == "xai" and not xai_available
    if xai_requested_fallback:
        requested_provider = "codex"
        requested_model = codex_model_for(args)
        requested_reason = f"{xai_availability_reason}; using the authenticated X/public-source fallback ladder under Codex coordination"
    if requested_provider:
        unavailable = explicit_route_unavailable(requested_provider, requested_model)
        cloud_ollama = requested_provider == "ollama" and requested_model.lower().endswith(":cloud")
        unsafe_specialist = (requested_provider in {"gemini", "xai", "openrouter"} or cloud_ollama) and (
            unsafe_privacy or needs_approval or codex_only
        )
        if unavailable or unsafe_specialist:
            reasons = []
            if unavailable:
                reasons.append(unavailable)
            if unsafe_specialist:
                reasons.append("requested specialist/fallback lane is unsafe for this privacy/task; using Codex execution lane")
            return explicit_route_payload(
                "codex",
                requested_model if requested_provider == "codex" and requested_model else PROVIDER_DEFAULT_MODELS["codex"],
                owner,
                args,
                allowance_mode,
                "; ".join(reasons),
            )
        payload = explicit_route_payload(requested_provider, requested_model, owner, args, allowance_mode, requested_reason)
        if xai_requested_fallback:
            payload.update({
                "role": "xai-unavailable-fallback",
                "fallbackFrom": "xai",
                "fallbackPath": "authenticated-x-ui",
                "fallbackLadder": ["authenticated-x-ui", "forwarded-x-links", "public-web-primary-sources"],
                "freshLaneRequired": False,
                "spendClass": "verified-fallback",
                "guardrails": [
                    "Use the dedicated agent-owned X browser, verify the signed-in session canary, search/read public posts only, and close temporary tabs.",
                    "Cap a normal request at eight searches and 200 unique public posts; disclose rate limits and partial coverage.",
                    "Corroborate material X claims with primary sources and never claim Grok was used when it was unavailable.",
                ],
            })
        return payload

    if glm_hint and not unsafe_privacy and not needs_approval and not codex_only:
        unavailable = explicit_route_unavailable("ollama", "glm-5.2:cloud")
        if not unavailable:
            return {
                "firstStop": "ollama",
                "provider": "ollama",
                "model": "glm-5.2:cloud",
                "auth": provider_auth_label("ollama", "glm-5.2:cloud"),
                "role": "glm-large-context-technical-reasoning",
                "owner": owner,
                "enforced": True,
                "freshLaneRequired": True,
                "verifyBeforeWork": True,
                "codexAllowanceMode": allowance_mode,
                "spendClass": "cloud-specialist",
                "privacy": "dashboard-safe",
                "reason": compact(
                    f"{task_type} benefits from GLM 5.2's large-context, tool-capable technical reasoning before owner integration."
                ),
                "guardrails": [
                    "Send sanitized technical context only; Ollama GLM 5.2 is a cloud model, not a private local lane.",
                    "Do not send secrets, OAuth payloads, raw emails, raw connector data, private account contents, wallet data, or customer/account data.",
                    "GLM may analyze, plan, or review; Codex on the owning host retains edits, permissions, execution, approvals, and final verification.",
                ],
            }

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
                "model": provider_budget("xai").get("apiModelUsed") or provider_budget("xai").get("lastModelUsed") or "grok-4.20-reasoning",
                "auth": PROVIDER_AUTH_LABELS.get("xai", "xai"),
                "role": role,
                "owner": owner,
                "enforced": True,
                "freshLaneRequired": True,
                "verifyBeforeWork": True,
                "codexAllowanceMode": allowance_mode,
                "spendClass": "codex-sparing" if codex_constrained else "normal",
                "privacy": "dashboard-safe",
                "reason": compact(f"{task_type} depends on public current-events, X-native, social sentiment, or market narrative context; {xai_availability_reason}; {budget_reason}."),
                "guardrails": [
                    "Send dashboard-safe public context or sanitized briefs only.",
                    "Do not send secrets, OAuth payloads, raw emails, raw connector data, private account contents, or customer/account data.",
                    "The selected owner still owns execution, approvals, repo edits, and final integration.",
                ],
            }
        gemini_first = True

    if xai_hint and not xai_first and not codex_only and not unsafe_privacy and not needs_approval:
        return {
            "firstStop": "codex",
            "provider": "codex",
            "model": codex_model_for(args),
            "role": "xai-unavailable-fallback",
            "owner": owner,
            "enforced": True,
            "freshLaneRequired": False,
            "verifyBeforeWork": True,
            "codexAllowanceMode": allowance_mode,
            "spendClass": "verified-fallback",
            "privacy": args.privacy,
            "fallbackFrom": "xai",
            "fallbackPath": "authenticated-x-ui",
            "fallbackLadder": ["authenticated-x-ui", "forwarded-x-links", "public-web-primary-sources"],
            "reason": compact(f"{xai_availability_reason}; use the dedicated authenticated X search lane first, then forwarded links and public-web primary sources, and disclose that Grok was not used."),
            "guardrails": [
                "Do not claim Grok or X-native verification when the route is unavailable.",
                "Use the dedicated agent-owned X browser, verify the session canary, collect public posts read-only, and close temporary tabs.",
                "Cap a normal request at eight searches and 200 unique public posts; disclose rate limits and partial coverage.",
                "Corroborate consequential X claims with primary sources and make source limitations visible.",
                "Do not send or store private connector data in route telemetry.",
            ],
        }

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
                "freshLaneRequired": True,
                "verifyBeforeWork": True,
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
            "model": codex_model_for(args),
            "role": "codex-execution",
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
    parser.add_argument(
        "--complexity",
        default="auto",
        choices=["auto", "bounded", "multi-step", "hard"],
        help="Bounded work may use Luna; multi-step/hard execution requires Terra unless Sol is explicitly earned.",
    )
    parser.add_argument(
        "--blast-radius",
        default="auto",
        choices=["auto", "low", "medium", "high"],
        help="Sol requires hard complexity plus high blast radius and a high/critical or explicit high-blast signal.",
    )
    parser.add_argument("--requester", default="joshex")
    parser.add_argument("--prefer", default="")
    parser.add_argument("--approval", default="none", choices=["none", "required", "approved", "rejected"])
    parser.add_argument("--codex-allowance", default="auto", choices=["auto", "normal", "conserve", "exhausted"])
    parser.add_argument("--requested-provider", default="", help="Explicit provider/lane Josh requested: codex, gemini, ollama, grok/xai, or openrouter.")
    parser.add_argument("--requested-model", default="", help="Explicit model Josh requested, e.g. gpt-5.5 or gemini-3.1-pro-preview.")
    parser.add_argument("--requested-reason", default="requested by Josh", help="Short reason to include in route disclosure.")
    parser.add_argument("--create-task", action="store_true")
    parser.add_argument("--brain-feed", action="store_true")
    parser.add_argument("--job", action="store_true")
    parser.add_argument("--queue-duration-ms", type=float, default=None)
    parser.add_argument("--memory-duration-ms", type=float, default=None)
    parser.add_argument("--tool-duration-ms", type=float, default=None)
    parser.add_argument("--model-duration-ms", type=float, default=None)
    parser.add_argument("--no-telemetry", action="store_true", help="Skip the route-decision telemetry append.")
    args = parser.parse_args()

    route_started = time.perf_counter()
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
    if not args.no_telemetry:
        routing_duration_ms = max(0, round((time.perf_counter() - route_started) * 1000))
        append_route_telemetry(args, result, routing_duration_ms)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
