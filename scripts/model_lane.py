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
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROUTE = ROOT / "scripts" / "agent_route.py"
AGENT_PUBLISH = ROOT / "scripts" / "agent_publish.py"
JAIMES_SSH_HOST = os.environ.get("MODEL_LANE_JAIMES_HOST", "jaimes")
JAIMES_REPO = "~/.openclaw/workspace/mission-control"
CONTROL_TOWER_HOST = os.environ.get("MODEL_LANE_CONTROL_TOWER_HOST", "josh2")
CONTROL_TOWER_REPO = "/Users/josh2.0/.openclaw/workspace/mission-control"
LANE_HEARTBEAT_SECONDS = 30

PROVIDER_MODEL_FAMILIES = {
    "codex": "codex",
    "gemini": "antigravity",
    "ollama": "ollama",
    "xai": "grok",
}


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def compact(text: str, limit: int = 240) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def lane_work_id(args: argparse.Namespace) -> str:
    explicit = str(getattr(args, "lane_id", "") or "").strip()
    if explicit:
        return explicit
    return f"model-lane-{utc_stamp().lower()}-{uuid.uuid4().hex[:10]}"


def validate_lane_visibility(args: argparse.Namespace) -> None:
    mode = str(getattr(args, "lane_visibility", "required") or "required")
    if mode != "required":
        return
    if not str(getattr(args, "controller_work_id", "") or "").strip() or not str(
        getattr(args, "controller_run_id", "") or ""
    ).strip():
        raise SystemExit(
            "Executing a model lane requires --controller-work-id and --controller-run-id so "
            "Control Tower can render the separate worker without inferring its parent."
        )


def model_route_identity(route: dict[str, Any]) -> tuple[str, str]:
    selected = route.get("modelRoute") or {}
    provider = str(selected.get("provider") or "").strip().lower()
    model = str(selected.get("model") or "").strip()
    family = PROVIDER_MODEL_FAMILIES.get(provider, provider)
    if not family or not model:
        raise SystemExit("Verified model-lane visibility requires an exact provider and model.")
    return family, model


def lane_publish_command(
    args: argparse.Namespace,
    route: dict[str, Any],
    *,
    work_id: str,
    run_id: str,
    work_event: str,
    status: str,
    phase: str,
    detail: str,
) -> list[str]:
    family, model = model_route_identity(route)
    owner = str(route.get("agent") or args.requester).strip()
    return [
        sys.executable,
        str(AGENT_PUBLISH),
        "--agent", owner,
        "--title", compact(f"Model lane: {args.title}", 180),
        "--status", status,
        "--phase", phase,
        "--tool", "model_lane.py",
        "--detail", compact(detail, 460),
        "--origin", "model-lane",
        "--work-id", work_id,
        "--run-id", run_id,
        "--work-event", work_event,
        "--model-family", family,
        "--model-id", model,
        "--route-verified",
        "--execution-role", "worker",
        "--controller-work-id", str(args.controller_work_id),
        "--controller-run-id", str(args.controller_run_id),
        "--lease-seconds", str(max(LANE_HEARTBEAT_SECONDS * 3, 120)),
    ]


def run_lane_publish(command: list[str]) -> None:
    if Path.home().name == "jc_agent":
        canonical = ["python3", "scripts/agent_publish.py", *command[2:]]
        command = [
            "ssh",
            CONTROL_TOWER_HOST,
            f"cd {shlex.quote(CONTROL_TOWER_REPO)} && {shlex.join(canonical)}",
        ]
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip() or "model-lane visibility publish failed"
        )


class LaneVisibility:
    """Publish one exact nested worker lifecycle for a separately executing lane."""

    def __init__(self, args: argparse.Namespace, route: dict[str, Any]) -> None:
        self.args = args
        self.route = route
        self.work_id = lane_work_id(args)
        self.run_id = f"{self.work_id}-run"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.heartbeat_error = ""

    def _publish(self, *, work_event: str, status: str, phase: str, detail: str) -> None:
        with self._lock:
            route = self.route
            run_lane_publish(lane_publish_command(
                self.args,
                route,
                work_id=self.work_id,
                run_id=self.run_id,
                work_event=work_event,
                status=status,
                phase=phase,
                detail=detail,
            ))

    def start(self) -> None:
        family, model = model_route_identity(self.route)
        self._publish(
            work_event="start",
            status="active",
            phase="working",
            detail=f"Separate verified {family} lane is executing with {model}.",
        )
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(LANE_HEARTBEAT_SECONDS):
            try:
                self._publish(
                    work_event="heartbeat",
                    status="active",
                    phase="working",
                    detail="Separate model lane is still executing.",
                )
            except Exception as exc:  # terminal cleanup still gets a final attempt
                self.heartbeat_error = compact(str(exc), 300)

    def update_route(self, route: dict[str, Any]) -> None:
        with self._lock:
            self.route = route
        family, model = model_route_identity(route)
        self._publish(
            work_event="update",
            status="active",
            phase="routed",
            detail=f"Disclosed fallback lane is now executing with {family}/{model}.",
        )

    def finish(self, result: int) -> bool:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        try:
            self._publish(
                work_event="terminal",
                status="done" if result == 0 else "error",
                phase="delivered" if result == 0 else "failed",
                detail=(
                    "Separate model lane completed and returned to its controller."
                    if result == 0
                    else f"Separate model lane stopped with exit code {result}."
                ),
            )
            if self.heartbeat_error:
                print(
                    f"Model-lane heartbeat warning: {self.heartbeat_error}",
                    file=sys.stderr,
                )
                return False
            return True
        except Exception as exc:
            print(f"Model-lane visibility cleanup failed: {compact(str(exc), 300)}", file=sys.stderr)
            return False


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
            "--lane-visibility", "parent-owned",
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
    on_route_change: Callable[[dict[str, Any]], None] | None = None,
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
        if on_route_change:
            on_route_change(fallback_route)
        result = execute_verified(command_for(args, fallback_route), fallback_route)
        if result == 0:
            return 0
        primary_label = f"{provider}/{model}"
    return result


def execute_with_lane_visibility(
    args: argparse.Namespace,
    command: list[str],
    route: dict[str, Any],
) -> int:
    if args.lane_visibility != "required":
        return execute_with_disclosed_fallbacks(args, command, route)
    visibility = LaneVisibility(args, route)
    try:
        visibility.start()
    except Exception as exc:
        visibility.finish(1)
        raise SystemExit(f"Model lane did not start because visibility could not be proven: {compact(str(exc), 300)}") from exc
    result = 1
    try:
        result = execute_with_disclosed_fallbacks(
            args,
            command,
            route,
            on_route_change=visibility.update_route,
        )
        return result if visibility.finish(result) else (result or 4)
    except BaseException:
        visibility.finish(result)
        raise


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
    parser.add_argument("--controller-work-id", default="", help="Exact controlling Live Work id for this separate lane.")
    parser.add_argument("--controller-run-id", default="", help="Exact controlling Live Work run id for this separate lane.")
    parser.add_argument("--lane-id", default="", help="Optional idempotent lane id; generated when omitted.")
    parser.add_argument(
        "--lane-visibility",
        default="required",
        choices=["required", "parent-owned", "diagnostic"],
        help="Real lanes require a parent; remote child transport and bounded diagnostics opt out explicitly.",
    )
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

    validate_lane_visibility(args)
    return execute_with_lane_visibility(args, cmd, route)


if __name__ == "__main__":
    raise SystemExit(main())
