#!/usr/bin/env python3
"""Launch or preview a verified fresh model lane for Josh 2.0 / JAIMES.

This enforces the ecosystem rule that a model switch is a controlled handoff to a
fresh lane, not mutation of the current conversation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROUTE = ROOT / "scripts" / "agent_route.py"
AGENT_PUBLISH = ROOT / "scripts" / "agent_publish.py"
MODEL_LANE_RECEIPT = ROOT / "scripts" / "model_lane_receipt.py"
METRICS_PREFIX = "MODEL_LANE_METRICS:"
JAIMES_SSH_HOST = os.environ.get("MODEL_LANE_JAIMES_HOST", "jaimes")
JAIMES_REPO = "~/.openclaw/workspace/mission-control"
CONTROL_TOWER_HOST = os.environ.get("MODEL_LANE_CONTROL_TOWER_HOST", "josh2.0@josh2")
CONTROL_TOWER_REPO = "/Users/josh2.0/.openclaw/workspace/mission-control"
LANE_HEARTBEAT_SECONDS = 30
RESULT_ROOTS = (Path("/private/tmp"), Path("/tmp"))
SOL_MODEL = "gpt-5.6-sol"
SOL_REVIEW_TRIGGERS = {
    "hard-high-blast",
    "terra-retry-exhausted",
    "security-critical-review",
    "conflicting-high-stakes-evidence",
    "explicit-josh-request",
}
SOL_CONTEXT_STRING_FIELDS = {
    "trigger",
    "objective",
    "question",
    "requestedOutput",
    "parentWorkId",
    "parentRunId",
}
SOL_CONTEXT_LIST_FIELDS = {
    "constraints",
    "authoritativeFiles",
    "evidence",
    "attemptedApproaches",
}
SOL_CONTEXT_FORBIDDEN_KEYS = {
    "password",
    "secret",
    "token",
    "cookie",
    "oauth",
    "credential",
    "privatekey",
    "seedphrase",
    "rawemail",
    "rawconnectorcontent",
}

PROVIDER_MODEL_FAMILIES = {
    "codex": "codex",
    "gemini": "antigravity",
    "ollama": "ollama",
    "xai": "grok",
}

PROVIDER_ALIASES = {
    "grok": "xai",
    "x": "xai",
    "google": "gemini",
    "antigravity": "gemini",
}

PROVIDER_AUTH_LABELS = {
    "codex": "OpenAI Codex OAuth/subscription",
    "gemini": "Antigravity-authenticated Gemini subscription",
    "ollama": "Ollama runtime",
    "xai": "SuperGrok CLI OAuth/subscription",
    "openrouter": "OpenRouter metered API",
}


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def compact(text: str, limit: int = 240) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def normalized_model(value: str) -> str:
    model = str(value or "").strip().lower()
    for prefix in ("openai-codex/", "openai/", "codex/"):
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _forbidden_context_key(value: Any) -> str:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in SOL_CONTEXT_FORBIDDEN_KEYS:
                return str(key)
            nested = _forbidden_context_key(child)
            if nested:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _forbidden_context_key(child)
            if nested:
                return nested
    return ""


def validate_sol_context_packet(args: argparse.Namespace, route: dict[str, Any]) -> dict[str, Any] | None:
    """Require a complete, parent-bound, dashboard-safe packet for every Sol lane."""
    selected = route.get("modelRoute") or {}
    if normalized_model(selected.get("model") or getattr(args, "requested_model", "")) != SOL_MODEL:
        return None
    if str(getattr(args, "privacy", "") or "") != "dashboard-safe":
        raise SystemExit("A Sol review lane accepts only explicitly sanitized dashboard-safe context.")
    raw_path = str(getattr(args, "context_packet", "") or "").strip()
    if not raw_path:
        raise SystemExit("A Sol review lane requires --context-packet with the complete parent handoff.")
    path = Path(raw_path).expanduser().resolve()
    if not any(path == root or root in path.parents for root in RESULT_ROOTS):
        raise SystemExit("--context-packet must be under /private/tmp or /tmp")
    try:
        packet = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Sol context packet is unreadable or invalid JSON: {compact(str(exc), 200)}") from exc
    if not isinstance(packet, dict):
        raise SystemExit("Sol context packet must be a JSON object.")
    for field in sorted(SOL_CONTEXT_STRING_FIELDS):
        if not str(packet.get(field) or "").strip():
            raise SystemExit(f"Sol context packet requires non-empty {field}.")
    for field in sorted(SOL_CONTEXT_LIST_FIELDS):
        values = packet.get(field)
        if not isinstance(values, list) or not values or not all(str(value).strip() for value in values):
            raise SystemExit(f"Sol context packet requires a non-empty string list for {field}.")
    if packet["parentWorkId"] != str(getattr(args, "controller_work_id", "") or "") or packet[
        "parentRunId"
    ] != str(getattr(args, "controller_run_id", "") or ""):
        raise SystemExit("Sol context packet parentWorkId/parentRunId must match the controlling task exactly.")
    forbidden = _forbidden_context_key(packet)
    if forbidden:
        raise SystemExit(f"Sol context packet contains forbidden private-data key: {forbidden}.")

    trigger = str(packet["trigger"]).strip()
    if trigger not in SOL_REVIEW_TRIGGERS:
        raise SystemExit(f"Unsupported Sol escalation trigger: {trigger}.")
    complexity = str(getattr(args, "complexity", "auto") or "auto")
    blast_radius = str(getattr(args, "blast_radius", "auto") or "auto")
    priority = str(getattr(args, "priority", "normal") or "normal")
    capabilities = {str(value).strip().lower() for value in (getattr(args, "capability", []) or [])}
    if trigger == "hard-high-blast" and not (
        complexity == "hard"
        and blast_radius == "high"
        and (priority in {"high", "critical"} or capabilities & {"high-blast-radius", "incident-command", "security-critical", "production-migration"})
    ):
        raise SystemExit("hard-high-blast Sol review requires hard complexity, high blast radius, and a high/critical signal.")
    if trigger == "terra-retry-exhausted" and not (
        complexity == "hard" and len(packet["attemptedApproaches"]) >= 2
    ):
        raise SystemExit("terra-retry-exhausted requires hard complexity and two materially different attempted approaches.")
    if trigger == "security-critical-review" and not (
        blast_radius == "high" and "security-critical" in capabilities
    ):
        raise SystemExit("security-critical-review requires high blast radius and the security-critical capability.")
    if trigger == "conflicting-high-stakes-evidence" and not (
        blast_radius == "high" and len(packet["evidence"]) >= 2
    ):
        raise SystemExit("conflicting-high-stakes-evidence requires high blast radius and at least two evidence items.")
    if trigger == "explicit-josh-request" and "explicit-josh-sol-request" not in capabilities:
        raise SystemExit("explicit-josh-request requires the explicit-josh-sol-request capability marker.")
    return packet


def substantive_output(text: str) -> str:
    """Strip launcher identity lines and return only provider task output."""
    kept = []
    for line in str(text or "").splitlines():
        lowered = line.strip().lower()
        if lowered.startswith("active model/auth:") or lowered.startswith("route reason:"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def persist_result(args: argparse.Namespace | None, output: str) -> None:
    """Persist controller-private output for recovery from a lost caller stream."""
    if args is None or not str(getattr(args, "result_file", "") or "").strip():
        return
    target = Path(str(args.result_file)).expanduser().resolve()
    if not any(target == root or root in target.parents for root in RESULT_ROOTS):
        raise RuntimeError("--result-file must be under /private/tmp or /tmp")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_text(output.rstrip() + "\n", encoding="utf-8")
    target.chmod(0o600)
    print(f"Model Lane Result File: {target}", file=sys.stderr)


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


def execution_agent(args: argparse.Namespace, route: dict[str, Any]) -> str:
    """Return the agent that actually executes a visible model lane.

    Specialist providers launched from another host are executed on JAIMES. The
    parent remains linked through the work-store worker contract, but Live Work
    Board and Brain Atlas must attribute the active worker to its real host.
    """
    provider = str((route.get("modelRoute") or {}).get("provider") or "").strip()
    if Path.home().name == "jc_agent":
        return "jaimes"
    if (
        args.transport == "auto"
        and provider in {"gemini", "ollama", "xai"}
    ):
        return "jaimes"
    return str(args.requester or route.get("agent") or "joshex").strip()


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
    owner = execution_agent(args, route)
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
        "--priority",
        str(getattr(args, "priority", "normal") or "normal"),
        "--complexity",
        str(getattr(args, "complexity", "auto") or "auto"),
        "--blast-radius",
        str(getattr(args, "blast_radius", "auto") or "auto"),
    ]
    for cap in args.capability or []:
        cmd += ["--capability", cap]
    if args.requested_provider:
        cmd += ["--requested-provider", args.requested_provider]
    if args.requested_model:
        cmd += ["--requested-model", args.requested_model]
    if args.requested_reason:
        cmd += ["--requested-reason", args.requested_reason]
    return preserve_parent_owned_route(args, run_json(cmd))


def preserve_parent_owned_route(args: argparse.Namespace, route: dict[str, Any]) -> dict[str, Any]:
    """Keep the controller-verified route exact on the remote execution host.

    Josh 2.0 owns fresh quota telemetry and verifies JAIMES runtime/model
    availability before forwarding. The parent-owned child must not replace
    that route from an older host-local allowance cache.
    """
    if args.lane_visibility != "parent-owned":
        return route
    provider = PROVIDER_ALIASES.get(
        str(args.requested_provider or "").strip().lower(),
        str(args.requested_provider or "").strip().lower(),
    )
    model = str(args.requested_model or "").strip()
    if not provider or not model:
        raise SystemExit("A parent-owned model lane requires an exact provider and model.")
    for prefix in (f"{provider}/", "xai/" if provider == "xai" else ""):
        if prefix and model.lower().startswith(prefix):
            model = model[len(prefix):]
            break
    preserved = dict(route)
    selected = {
        "firstStop": "grok" if provider == "xai" else provider,
        "provider": provider,
        "model": model,
        "auth": PROVIDER_AUTH_LABELS.get(provider, provider),
        "role": "parent-verified-specialist",
        "owner": str(args.requester or route.get("agent")),
        "enforced": True,
        "freshLaneRequired": True,
        "verifyBeforeWork": True,
        "reason": str(args.requested_reason or "parent-verified specialist route"),
    }
    preserved["modelRoute"] = selected
    return preserved


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
    packet = getattr(args, "sol_context_packet_data", None)
    packet_text = ""
    if isinstance(packet, dict):
        packet_text = """

SOL REVIEW CONTRACT:
- You are a bounded, read-only reviewer. Do not edit files, invoke subagents, or take side effects.
- Treat the structured parent packet below as the complete handoff. Do not infer missing private context.
- Return sections named Assessment, Evidence, Recommendation, Risks, and Required Follow-up.
- The Terra parent remains task owner, executor, integrator, and final verifier.

STRUCTURED PARENT CONTEXT:
""" + json.dumps(packet, indent=2, sort_keys=True)
    header = f"""You are working in a fresh model lane for Josh.

The launcher owns runtime-model verification. Do not infer, debate, or restate
your model identity; complete the task itself. If provider selection or
authentication fails, the fail-closed launcher will reject the run.

{checkpoint_text(args, route)}

TASK:
{args.prompt or args.objective}
{packet_text}
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
            "--controller-work-id", args.controller_work_id,
            "--controller-run-id", args.controller_run_id,
            "--lane-id", args.lane_id,
            "--route-decision-id", str((route.get("routeTelemetry") or {}).get("routeDecisionId") or getattr(args, "route_decision_id", "")),
            "--request-signature", str((route.get("routeTelemetry") or {}).get("requestSignature") or getattr(args, "request_signature", "")),
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
        effort = "high" if normalized_model(model) == SOL_MODEL else "medium"
        return [
            "codex", "exec", "--ignore-user-config", "--sandbox", "read-only",
            "--ephemeral", "--disable", "multi_agent", "-c",
            f'model_reasoning_effort="{effort}"', "-m", model or "gpt-5.5", prompt,
        ]

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

    if provider == "ollama":
        return [
            sys.executable, str(ROOT / "scripts" / "ollama_local_pass.py"),
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


def record_execution_receipt(
    args: argparse.Namespace,
    route: dict[str, Any],
    *,
    return_code: int,
    duration_ms: int,
    stdout: str,
    metrics: dict[str, Any],
) -> str:
    model_route = route.get("modelRoute") or {}
    receipt_id = "mlr-" + hashlib.sha256(
        f"{args.lane_id}\x1f{model_route.get('provider')}\x1f{model_route.get('model')}\x1f{time.time_ns()}".encode()
    ).hexdigest()[:20]
    input_tokens = int(metrics.get("inputTokens") or 0)
    output_tokens = int(metrics.get("outputTokens") or 0)
    prompt_chars = len(build_prompt(args, route))
    payload = {
        "routeDecisionId": args.route_decision_id or (route.get("routeTelemetry") or {}).get("routeDecisionId"),
        "requestSignature": args.request_signature or (route.get("routeTelemetry") or {}).get("requestSignature"),
        "laneWorkId": args.lane_id,
        "laneRunId": f"{args.lane_id}-run" if args.lane_id else "",
        "controllerWorkId": args.controller_work_id,
        "controllerRunId": args.controller_run_id,
        "owner": args.requester,
        "taskType": args.task_type,
        "privacy": args.privacy,
        "provider": model_route.get("provider"),
        "model": model_route.get("model"),
        "role": model_route.get("role"),
        "outcome": "success" if return_code == 0 else "error",
        "exitCode": return_code,
        "durationMs": duration_ms,
        "providerDurationMs": round(int(metrics.get("providerDurationNs") or 0) / 1_000_000),
        "inputTokens": input_tokens or None,
        "outputTokens": output_tokens or None,
        "totalTokens": input_tokens + output_tokens if input_tokens or output_tokens else None,
        "promptCharacters": prompt_chars,
        "estimatedPromptTokens": round(prompt_chars / 4),
        "outputCharacters": len(stdout),
        "tokenCountsActual": bool(input_tokens or output_tokens),
        "canary": any(word in f"{args.task_type} {args.title}".lower() for word in ("canary", "routing-test", "smoke-test")),
        "fallbackFrom": model_route.get("fallbackFrom"),
        "integrationDisposition": "pending",
        "integrationReasonCode": "awaiting-controller-review",
        "telemetryPolicy": "metadata only; no raw prompt or output",
    }
    proc = subprocess.run(
        [sys.executable, str(MODEL_LANE_RECEIPT), "append", "--receipt-id", receipt_id, "--payload", json.dumps(payload, separators=(",", ":"))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"Model-lane receipt warning: {compact(proc.stderr or proc.stdout, 300)}", file=sys.stderr)
    else:
        print(f"Model Lane Receipt: {receipt_id}", file=sys.stderr)
    return receipt_id


def execute_verified(cmd: list[str], route: dict[str, Any]) -> int:
    args = route.get("_executionArgs")
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    duration_ms = max(0, round((time.perf_counter() - started) * 1000))
    metrics: dict[str, Any] = {}
    stderr_lines: list[str] = []
    for line in proc.stderr.splitlines():
        if line.startswith(METRICS_PREFIX):
            try:
                metrics = json.loads(line.removeprefix(METRICS_PREFIX))
            except json.JSONDecodeError:
                pass
        else:
            stderr_lines.append(line)
    clean_stderr = "\n".join(stderr_lines)
    combined = f"{proc.stdout}\n{proc.stderr}".lower()
    fallback_markers = ("switching to fallback", "primary auth failed", "you need to be signed in")
    if proc.returncode != 0:
        if proc.stdout:
            print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
        if clean_stderr:
            print(clean_stderr, file=sys.stderr)
        if args is not None and not (cmd and cmd[0] == "ssh"):
            record_execution_receipt(args, route, return_code=proc.returncode, duration_ms=duration_ms, stdout=proc.stdout, metrics=metrics)
        return proc.returncode
    if any(marker in combined for marker in fallback_markers):
        print("Verified model lane failed: provider fallback or authentication failure detected.", file=sys.stderr)
        if args is not None and not (cmd and cmd[0] == "ssh"):
            record_execution_receipt(args, route, return_code=3, duration_ms=duration_ms, stdout=proc.stdout, metrics=metrics)
        return 3
    model_route = route.get("modelRoute") or {}
    if cmd and cmd[0] == "ssh":
        expected_model = str(model_route.get("model") or "").strip().lower()
        active_lines = [
            line.strip().lower()
            for line in f"{proc.stdout}\n{proc.stderr}".splitlines()
            if line.strip().lower().startswith("active model/auth:")
        ]
        if not expected_model or not active_lines or not any(expected_model in line for line in active_lines):
            print(
                f"Verified model lane failed: child did not report expected model {expected_model or '<missing>'}.",
                file=sys.stderr,
            )
            return 3
    else:
        print(
            f"Active Model/Auth: {model_route.get('model') or model_route.get('provider')} "
            f"({model_route.get('auth') or model_route.get('provider')})"
        )
        print(f"Route reason: {model_route.get('reason')}")
    output = substantive_output(proc.stdout)
    if not output:
        print("Verified model lane failed: provider returned no substantive output.", file=sys.stderr)
        if args is not None and not (cmd and cmd[0] == "ssh"):
            record_execution_receipt(args, route, return_code=5, duration_ms=duration_ms, stdout=proc.stdout, metrics=metrics)
        return 5
    try:
        persist_result(args, output)
    except Exception as exc:
        print(f"Verified model lane failed: result persistence error: {compact(str(exc), 300)}", file=sys.stderr)
        if args is not None and not (cmd and cmd[0] == "ssh"):
            record_execution_receipt(args, route, return_code=6, duration_ms=duration_ms, stdout=proc.stdout, metrics=metrics)
        return 6
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    if clean_stderr:
        print(clean_stderr, file=sys.stderr)
    if args is not None and not (cmd and cmd[0] == "ssh"):
        record_execution_receipt(args, route, return_code=0, duration_ms=duration_ms, stdout=proc.stdout, metrics=metrics)
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
    route["_executionArgs"] = args
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
        fallback_route["_executionArgs"] = args
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
    parser.add_argument("--priority", default="normal", choices=["low", "normal", "high", "critical"])
    parser.add_argument("--complexity", default="auto", choices=["auto", "bounded", "multi-step", "hard"])
    parser.add_argument("--blast-radius", default="auto", choices=["auto", "low", "medium", "high"])
    parser.add_argument(
        "--context-packet",
        default="",
        help="Dashboard-safe JSON handoff required for every gpt-5.6-sol review lane.",
    )
    parser.add_argument("--transport", default="auto", choices=["auto", "hermes", "codex", "openclaw"])
    parser.add_argument("--controller-work-id", default="", help="Exact controlling Live Work id for this separate lane.")
    parser.add_argument("--controller-run-id", default="", help="Exact controlling Live Work run id for this separate lane.")
    parser.add_argument("--lane-id", default="", help="Optional idempotent lane id; generated when omitted.")
    parser.add_argument("--route-decision-id", default="", help="Route-decision receipt joined to this execution.")
    parser.add_argument("--request-signature", default="", help="Privacy-safe request signature from the router.")
    parser.add_argument(
        "--result-file",
        default="",
        help="Optional controller-private recovery file under /private/tmp or /tmp.",
    )
    parser.add_argument(
        "--lane-visibility",
        default="required",
        choices=["required", "parent-owned", "diagnostic"],
        help="Real lanes require a parent; remote child transport and bounded diagnostics opt out explicitly.",
    )
    parser.add_argument("--execute", action="store_true", help="Actually launch the fresh lane. Default prints the verified command plan only.")
    args = parser.parse_args()

    route = route_for(args)
    args.sol_context_packet_data = validate_sol_context_packet(args, route)
    if not args.route_decision_id:
        args.route_decision_id = str((route.get("routeTelemetry") or {}).get("routeDecisionId") or "")
    if not args.request_signature:
        args.request_signature = str((route.get("routeTelemetry") or {}).get("requestSignature") or "")
    if args.execute and not args.lane_id:
        args.lane_id = lane_work_id(args)
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
