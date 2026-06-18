#!/usr/bin/env python3
"""Launch or preview a verified fresh model lane for Josh 2.0 / JAIMES.

This enforces the ecosystem rule that a model switch is a controlled handoff to a
fresh lane, not mutation of the current conversation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROUTE = ROOT / "scripts" / "agent_route.py"


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
    model_route = route.get("modelRoute") or {}
    header = f"""You are starting a fresh verified model lane for Josh.

First visible lines MUST be:
Active Model/Auth: {model_route.get('model') or model_route.get('provider')} ({model_route.get('auth') or model_route.get('provider')})
Route reason: {model_route.get('reason')}

Do not claim a different model. If this lane did not start on that model, stop and say verification failed.

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

    if args.transport == "openclaw":
        openclaw_model = model if "/" in model else f"openai/{model}" if provider == "codex" else model
        session_key = f"agent:{route.get('agent')}:lane-{utc_stamp()}"
        return [
            "openclaw",
            "agent",
            "--agent",
            str(route.get("agent") or "jaimes"),
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

    hermes_provider = {
        "codex": "openai-codex",
        "gemini": "google-gemini-cli",
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
        "-q",
        prompt,
    ]


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
            "commandPreview": " ".join(shlex.quote(part) for part in cmd[:8]) + (" …" if len(cmd) > 8 else ""),
            "verification": (route.get("modelRoute") or {}).get("verification") or {"required": True},
        },
    }
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return 0

    proc = subprocess.run(cmd, cwd=ROOT, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
