#!/usr/bin/env python3
"""Launch or preview a verified fresh model lane for Josh 2.0 / JAIMES.

This enforces the ecosystem rule that a model switch is a controlled handoff to a
fresh lane, not mutation of the current conversation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROUTE = ROOT / "scripts" / "agent_route.py"
JAIMES_SSH_HOST = os.environ.get("MODEL_LANE_JAIMES_HOST", "jaimes")
JAIMES_REPO = "~/.openclaw/workspace/mission-control"


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def compact(text: str, limit: int = 240) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def run_json(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or proc.stdout.strip() or f"command failed: {cmd}")
    return json.loads(proc.stdout)


def route_for(args: argparse.Namespace) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(AGENT_ROUTE),
        "--task-type",
        args.task_type,
        "--title",
        args.title,
        "--objective",
        args.objective,
        "--privacy",
        args.privacy,
        "--requester",
        args.requester,
        "--codex-allowance",
        args.codex_allowance,
    ]
    for cap in args.capability or []:
        cmd += ["--capability", cap]
    if args.requested_provider:
        cmd += ["--requested-provider", args.requested_provider]
    if args.requested_model:
        cmd += ["--requested-model", args.requested_model]
    if args.requested_reason:
        cmd += ["--requested-reason", args.requested_reason]
    return run_json(cmd)


def checkpoint_text(args: argparse.Namespace, route: dict[str, Any]) -> str:
    model_route = route.get("modelRoute") or {}
    return "\n".join(
        [
            "MODEL LANE CHECKPOINT",
            f"Objective: {args.objective}",
            f"Task type: {args.task_type}",
            f"Privacy: {args.privacy}",
            f"Owner: {route.get('agent')}",
            f"Requested provider: {args.requested_provider or 'auto'}",
            f"Requested model: {args.requested_model or 'auto'}",
            f"Selected provider: {model_route.get('provider')}",
            f"Selected model: {model_route.get('model')}",
            f"Auth: {model_route.get('auth') or model_route.get('provider')}",
            f"Reason: {model_route.get('reason')}",
            "Rule: start fresh, verify Active Model/Auth first, then work.",
        ]
    )


def build_prompt(args: argparse.Namespace, route: dict[str, Any]) -> str:
    header = f"""You are working in a fresh model lane for Josh.

The launcher owns runtime-model verification. Do not infer, debate, or restate
your model identity; complete the task itself. If provider selection or
authentication fails, the fail-closed launcher will reject the run.

{checkpoint_text(args, route)}

TASK:
{args.prompt or args.objective}
"""
    return header


def command_for(args: argparse.Namespace, route: dict[str, Any]) -> list[str]:
    model_route = route.get("modelRoute") or {}
    provider = str(model_route.get("provider") or "codex")
    model = str(model_route.get("model") or "")
    prompt = build_prompt(args, route)
    source = f"model-lane-{utc_stamp()}"

    #JAIMES: Codex-app and Josh 2.0 specialist passes execute on the authenticated
    # headless JAIMES host; the current GPT session remains coordinator/integrator.
    if args.transport == "auto" and provider in {"gemini", "ollama", "xai"} and Path.home().name != "jc_agent":
        remote = [
            "python3", "scripts/model_lane.py",
            "--task-type", args.task_type,
            "--title", args.title,
            "--objective", args.objective,
            "--prompt", args.prompt or args.objective,
            "--privacy", args.privacy,
            "--requester", args.requester,
            "--requested-provider", provider,
            "--requested-model", model,
            "--requested-reason", str(model_route.get("reason") or "usage-aware specialist route"),
            "--codex-allowance", str(model_route.get("codexAllowanceMode") or args.codex_allowance),
            "--transport", "hermes",
            "--execute",
        ]
        for cap in args.capability or []:
            remote += ["--capability", cap]
        return ["ssh", JAIMES_SSH_HOST, f"cd {JAIMES_REPO} && {shlex.join(remote)}"]

    if args.transport == "openclaw":
        #JAIMES: OpenCLAW model overrides require provider-qualified ids and the configured main agent id.
        provider_prefix = {
            "codex": "openai",
            "gemini": "google-gemini-cli",
            "ollama": "ollama",
            "xai": "xai",
            "openrouter": "openrouter",
        }.get(provider, provider)
        openclaw_model = model if "/" in model else f"{provider_prefix}/{model}"
        route_agent = str(route.get("agent") or "main")
        agent_id = "main" if route_agent in {"josh", "josh2", "jaimes"} else route_agent
        session_key = f"agent:{agent_id}:lane-{utc_stamp()}"
        return [
            "openclaw",
            "agent",
            "--agent",
            agent_id,
            "--session-key",
            session_key,
            "--model",
            openclaw_model,
            "--message",
            prompt,
            "--json",
        ]

    if args.transport == "codex" or (args.transport == "auto" and provider == "codex"):
        return ["codex", "exec", "-m", model or "gpt-5.5", prompt]

    if provider == "gemini":
        return [
            sys.executable, str(ROOT / "scripts" / "antigravity_pass.py"),
            "--model", model, "--prompt", prompt,
        ]

    if provider == "xai":
        return [
            "grok", "-m", model, "-p", prompt,
            "--output-format", "plain", "--no-subagents", "--permission-mode", "plan",
        ]

    if provider == "ollama" and model.lower().endswith(":cloud"):
        return [
            sys.executable, str(ROOT / "scripts" / "ollama_cloud_pass.py"),
            "--model", model, "--prompt", prompt,
        ]

    hermes_provider = {
        "codex": "openai-codex",
        "gemini": "antigravity",
        "ollama": "ollama-local",
        "xai": "xai",
        "openrouter": "openrouter",
    }.get(provider, provider)
    return [
        "hermes",
        "chat",
        "--provider",
        hermes_provider,
        "-m",
        model,
        "--source",
        source,
        "-Q",
        "-q",
        prompt,
    ]


def command_preview(cmd: list[str]) -> str:
    if cmd and cmd[0] == "ssh":
        return f"ssh {cmd[1]} <verified specialist lane; prompt redacted>"
    visible: list[str] = []
    redact_next = False
    for part in cmd:
        if redact_next:
            visible.append("<prompt redacted>")
            redact_next = False
            continue
        visible.append(part)
        if part in {"-p", "-q", "--message", "--prompt"}:
            redact_next = True
    return " ".join(shlex.quote(part) for part in visible)


def execute_verified(cmd: list[str], route: dict[str, Any]) -> int:
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    combined = f"{proc.stdout}\n{proc.stderr}".lower()
    fallback_markers = ("switching to fallback", "primary auth failed", "you need to be signed in")
    if proc.returncode != 0:
        if proc.stdout:
            print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
        if proc.stderr:
            print(proc.stderr, file=sys.stderr, end="" if proc.stderr.endswith("\n") else "\n")
        return proc.returncode
    if any(marker in combined for marker in fallback_markers):
        print("Verified model lane failed: provider fallback or authentication failure detected.", file=sys.stderr)
        return 3
    if not (cmd and cmd[0] == "ssh"):
        model_route = route.get("modelRoute") or {}
        print(
            f"Active Model/Auth: {model_route.get('model') or model_route.get('provider')} "
            f"({model_route.get('auth') or model_route.get('provider')})"
        )
        print(f"Route reason: {model_route.get('reason')}")
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="" if proc.stderr.endswith("\n") else "\n")
    return 0


def execute_with_disclosed_fallbacks(
    args: argparse.Namespace,
    primary_cmd: list[str],
    route: dict[str, Any],
) -> int:
    """Run a selected lane, then only policy-declared dashboard-safe fallbacks.

    Explicit model requests stay fail-closed. Automatic specialist routes may
    continue through the router-provided ladder, but every provider/model
    switch is printed before execution so a fallback can never be mistaken for
    the selected model.
    """
    result = execute_verified(primary_cmd, route)
    model_route = route.get("modelRoute") or {}
    fallbacks = model_route.get("fallbackRoutes") or []
    if (
        result == 0
        or args.requested_provider
        or args.requested_model
        or args.privacy != "dashboard-safe"
        or not isinstance(fallbacks, list)
    ):
        return result

    primary_label = f"{model_route.get('provider')}/{model_route.get('model')}"
    for candidate in fallbacks:
        if not isinstance(candidate, dict):
            continue
        provider = str(candidate.get("provider") or "").strip()
        model = str(candidate.get("model") or "").strip()
        if not provider or not model:
            continue
        print(
            f"Fallback disclosure: {primary_label} failed verification; trying {provider}/{model}.",
            file=sys.stderr,
        )
        fallback_route = dict(route)
        fallback_model_route = dict(model_route)
        fallback_model_route.update(candidate)
        fallback_model_route.update({
            "firstStop": provider,
            "provider": provider,
            "model": model,
            "fallbackFrom": primary_label,
            "freshLaneRequired": True,
            "verifyBeforeWork": True,
        })
        fallback_route["modelRoute"] = fallback_model_route
        result = execute_verified(command_for(args, fallback_route), fallback_route)
        if result == 0:
            return 0
        primary_label = f"{provider}/{model}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or launch a verified fresh model lane.")
    parser.add_argument("--task-type", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--privacy", default="dashboard-safe")
    parser.add_argument("--requester", default="josh")
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--requested-provider", default="")
    parser.add_argument("--requested-model", default="")
    parser.add_argument("--requested-reason", default="requested by Josh")
    parser.add_argument("--codex-allowance", default="auto", choices=["auto", "normal", "conserve", "exhausted"])
    parser.add_argument("--transport", default="auto", choices=["auto", "hermes", "codex", "openclaw"])
    parser.add_argument("--execute", action="store_true", help="Actually launch the fresh lane. Default prints the verified command plan only.")
    args = parser.parse_args()

    route = route_for(args)
    cmd = command_for(args, route)
    plan = {
        "route": route,
        "freshLane": {
            "required": True,
            "checkpoint": checkpoint_text(args, route),
            "transport": args.transport,
            "commandPreview": command_preview(cmd),
            "verification": (route.get("modelRoute") or {}).get("verification") or {"required": True},
        },
    }
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return 0

    return execute_with_disclosed_fallbacks(args, cmd, route)


if __name__ == "__main__":
    raise SystemExit(main())
