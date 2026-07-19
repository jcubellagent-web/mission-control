#!/usr/bin/env python3
"""Josh 2.0 Inbox coordinator routing, private workers, and telemetry.

This module is intentionally conservative: shared telemetry gets route facts,
latency, host, fallback, and outcome only. Raw prompts and model output stay in
the host-local private worker directory and prompt files are removed as soon as
the worker starts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import html
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
#JAIMES: Inbox cards must use the host helper beside send_josh_reply.py so live Telegram sends keep their configured Bot API lane.
WORK_CARD_SCRIPT = WORKSPACE / "scripts" / "josh_work_card.py"
SEND_REPLY_SCRIPT = WORK_CARD_SCRIPT.with_name("send_josh_reply.py")
PRIVATE_DIR = Path(os.environ.get("JOSH_INBOX_COORDINATOR_PRIVATE_DIR", str(Path.home() / ".openclaw" / "private" / "inbox-coordinator")))
STATE_PATH = PRIVATE_DIR / "jobs.json"
LOCK_PATH = PRIVATE_DIR / "jobs.lock"
TELEMETRY_PATH = Path(os.environ.get("JOSH_INBOX_COORDINATOR_TELEMETRY", str(ROOT / "data" / "inbox-coordinator-telemetry.jsonl")))
CONTROL_TOWER_AGENT = "josh2"
DEFAULT_TIMEOUT_SECONDS = 900
MAX_RETRIES = 1
STALE_CARD_SECONDS = 10 * 60
DEDUPE_WINDOW_SECONDS = 10 * 60
DELIVERY_RECOVERY_WINDOW_SECONDS = DEDUPE_WINDOW_SECONDS
DELIVERY_PENDING_RETENTION_SECONDS = 24 * 60 * 60
DELIVERY_RECOVERY_BACKOFF_SECONDS = 5
MAX_AUTOMATIC_DELIVERY_RECOVERY_ATTEMPTS = 3
TERMINAL_JOB_STATUSES = frozenset({"done", "failed", "cancelled"})
JOB_ARTIFACT_FIELDS = ("promptPath", "resultPath")
JOB_AUDIT_FIELDS = (
    "jobId",
    "createdAt",
    "updatedAt",
    "startedAt",
    "completedAt",
    "finishedAt",
    "resultTakenAt",
    "resultInspectedAt",
    "status",
    "attempt",
    "maxRetries",
    "timeoutSeconds",
    "origin",
    "route",
    "actual",
    "latencyMs",
    "delivered",
    "lastError",
    "deliveryRecoveryAttempts",
    "deliveryRecoveryReferenceAt",
    "deliveryFailedAt",
    "deliveryRecoveredAt",
    "deliveryRecoveryAutomaticAttempts",
    "deliveryRecoveryLastAttemptAt",
    "previousStatus",
    "cancelledAt",
)
JAIMES_SSH_HOST = os.environ.get("JOSH_INBOX_JAIMES_SSH_HOST", "jaimes")
JAIMES_WORKSPACE = "/Users/jc_agent/.openclaw/workspace"

SECRET_WORDS = re.compile(
    r"(password|passwd|secret|token|api[_ -]?key|oauth|cookie|session|bearer|private key|seed phrase)",
    re.I,
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\b(?:sk|xai|gh[pousr])[-_][A-Za-z0-9_-]{8,}\b", re.I),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b", re.I),
    re.compile(r"(?im)\b(password|passwd|token|api[_ -]?key|secret)\s*[:=]\s*([^\s,;]{4,})"),
)
WORKER_OUTPUT_CONTRACT = """Return a concise structured result using exactly these plain-text sections in this order: Complete: Yes or No plus whether the objective was completed; What was done: 3-5 unique, source-supported bullets that state concrete findings, outcomes, or changes; Issues: bullets or n/a; Appropriate next steps: one evidence-based recommendation or next action; Approval needed: one approval bullet per issue when approval is genuinely required, otherwise n/a. For an assessment, review, or research request, surface the key findings and recommendation instead of merely saying the assessment or review finished. Generic process statements such as task complete, execution verified, result prepared, or summary delivered are not findings and must not be used to fill the bullets. Put every reported risk or limitation in Issues as well as the relevant finding. Use No action needed only when the findings explicitly support that conclusion and there are no issues, risks, limitations, approvals, or recommendations to act on. Do not include a Model line or claim a provider, model, host, worker, route, or latency; the verified delivery layer adds those facts. Never repeat or reveal passwords, tokens, API keys, cookies, OAuth payloads, or other secret values."""
FINAL_SECTION_LABELS = {
    "complete": "Complete:",
    "done": "What was done:",
    "issues": "Issues:",
    "next": "Appropriate next steps:",
    "approval": "Approval needed:",
}

EMPTY_FINAL_ITEMS = frozenset({"n/a", "na", "none"})
MISSING_FINDINGS_ISSUE = "Detailed findings were not captured well enough to verify completion."
RETRY_FINDINGS_NEXT_STEP = "Retry the task and require three to five concrete findings or outcomes plus an evidence-based recommendation."
STATUS_ONLY_FINAL_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"^(?:the )?(?:assessment|review|task|request|work|job)(?: (?:was|is))? (?:complete|completed|finished|done|closed)[.!]?$",
        r"^(?:completed|finished|reviewed|checked|processed|handled) (?:the )?(?:requested )?(?:task|request|work|assessment|review)[.!]?$",
        r"^(?:verified|confirmed) (?:the )?(?:(?:worker )?(?:runtime|execution|delivery|route|workflow)|worker)(?: (?:was|is))? (?:complete|completed|successful|verified)?[.!]?$",
        r"^(?:prepared|generated|delivered|sent|formatted) (?:the )?(?:final )?(?:result|response|summary|card)(?: for .+ delivery)?[.!]?$",
        r"^(?:agent )?work reached final review[.!]?$",
        r"^live card ordering (?:was )?preserved[.!]?$",
        r"^response formatting (?:was )?(?:recovered|verified)[.!]?$",
        r"^(?:acknowledged|routed)(?: the)? request(?: .+)?[.!]?$",
    )
)
CONCRETE_RESULT_SIGNAL = re.compile(
    r"\b(?:found|identified|confirmed|determined|discovered|revealed?|show(?:s|ed)?|"
    r"describ(?:e|es|ed)|indicat(?:e|es|ed)|support(?:s|ed)?|does(?: not)?|cannot|can't|"
    r"can|will|prevent(?:s|ed)?|allow(?:s|ed)?|requir(?:e|es|ed)|includ(?:e|es|ed)|"
    r"exclud(?:e|es|ed)|monitor(?:s|ed)?|trad(?:e|es|ed)|connect(?:s|ed)?|creat(?:e|es|ed)|"
    r"pos(?:e|es|ed)|risk|limitation|read[- ]only|us(?:e|es|ed)|recommend(?:s|ed|ation)?|"
    r"fix(?:es|ed)?|patch(?:es|ed)?|chang(?:e|es|ed)|updat(?:e|es|ed)|add(?:s|ed)?|"
    r"remov(?:e|es|ed)|implement(?:s|ed)?|configur(?:e|es|ed)|creat(?:e|es|ed)|"
    r"migrat(?:e|es|ed)|deploy(?:s|ed)?|pass(?:es|ed)?|fail(?:s|ed)?|block(?:s|ed)?|"
    r"resolv(?:e|es|ed)|reduc(?:e|es|ed)|increas(?:e|es|ed)|decreas(?:e|es|ed)|"
    r"compar(?:e|es|ed)|estimat(?:e|es|ed)|measur(?:e|es|ed)|validat(?:e|es|ed))\b",
    re.I,
)
CONCRETE_EVIDENCE_SIGNAL = re.compile(
    r"(?:https?://|(?:^|\s)/[A-Za-z0-9_.-]+/|\b[A-Za-z0-9_.-]+\.(?:py|js|ts|json|md|ya?ml)\b|\b\d+(?:\.\d+)+(?:\b|%))",
    re.I,
)
RISK_OR_LIMITATION_SIGNAL = re.compile(
    r"\b(?:risk|risks|risky|limitation|limitations|limited|cannot|can't|unable|unsupported|"
    r"not supported|do not|don't|avoid|blocked|blocker|warning|caution)\b",
    re.I,
)
ACTION_OR_RECOMMENDATION_SIGNAL = re.compile(
    r"\b(?:recommend(?:s|ed|ation)?|should|must|follow[- ]?up|next step|do not|don't|avoid|consider)\b",
    re.I,
)
NO_ACTION_SIGNAL = re.compile(r"^no (?:further )?actions? (?:(?:is|are) )?(?:needed|required)\b", re.I)


ROUTES: dict[str, dict[str, Any]] = {
    "luna": {
        "provider": "codex",
        "model": "gpt-5.6-luna",
        "tier": "codex-luna",
        "worker": "josh2-codex-luna",
        "host": "josh2",
        "role": "fast coordinator / quick execution",
        "executor": "local-codex",
    },
    "terra": {
        "provider": "codex",
        "model": "gpt-5.6-terra",
        "tier": "codex",
        "worker": "josh2-codex-terra",
        "host": "josh2",
        "role": "default trusted execution",
        "executor": "local-codex",
    },
    "sol": {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "tier": "codex-sol",
        "worker": "josh2-codex-sol",
        "host": "josh2",
        "role": "hard integration / escalation",
        "executor": "local-codex",
    },
    "gemini": {
        "provider": "gemini",
        "model": "gemini-3.5-flash",
        "tier": "fast",
        "worker": "jaimes-gemini-review",
        "host": "jaimes",
        "role": "low-cost summary/review",
        "executor": "remote-llm-router",
    },
    "gemini-pro": {
        "provider": "gemini",
        "model": "gemini-3.1-pro-preview",
        "tier": "reason",
        "worker": "jaimes-gemini-pro",
        "host": "jaimes",
        "role": "large-context review/reasoning",
        "executor": "remote-llm-router",
    },
    "jaimes": {
        "provider": "jaimes",
        "model": "jaimes-workhorse",
        "tier": "delegate",
        "worker": "jaimes-hermes-workhorse",
        "host": "jaimes",
        "role": "explicit delegated workhorse",
        "executor": "remote-hermes",
    },
    "glm": {
        "provider": "ollama",
        "model": "glm-5.2",
        "tier": "local",
        "worker": "josh2-ollama-glm",
        "host": "josh2",
        "role": "local/private draft or offline fallback",
        "executor": "local-ollama",
    },
    "ollama": {
        "provider": "ollama",
        "model": "local",
        "tier": "local",
        "worker": "josh2-ollama-local",
        "host": "josh2",
        "role": "local/private draft or offline fallback",
        "executor": "local-ollama",
    },
    "grok": {
        "provider": "xai",
        "model": "grok-4-fast-non-reasoning",
        "tier": "grok-fast",
        "worker": "jaimes-grok-public",
        "host": "jaimes",
        "role": "X/current-events specialist",
        "executor": "remote-llm-router-op",
    },
}

MODEL_ALIASES = {
    "gpt-5.6-luna": "luna",
    "luna": "luna",
    "gpt-5.6-terra": "terra",
    "terra": "terra",
    "codex": "terra",
    "gpt-5.6-sol": "sol",
    "sol": "sol",
    "gemini flash": "gemini",
    "gemini": "gemini",
    "gemini pro": "gemini-pro",
    "jaimes": "jaimes",
    "j.a.i.n": "jaimes",
    "jain": "jaimes",
    "glm-5.2": "glm",
    "glm 5.2": "glm",
    "glm": "glm",
    "ollama": "ollama",
    "grok": "grok",
    "xai": "grok",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_private_dir() -> None:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        PRIVATE_DIR.chmod(0o700)
    except Exception:
        pass


@contextmanager
def state_lock():
    """Serialize queue reads/writes across watcher, workers, cleanup, and recovery."""
    ensure_private_dir()
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextmanager
def existing_state_read_lock():
    """Take a shared lock for dry-run only when the lock already exists."""
    fd = -1
    try:
        fd = os.open(LOCK_PATH, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_SH)
    except OSError:
        if fd >= 0:
            os.close(fd)
        fd = -1
    try:
        yield
    finally:
        if fd >= 0:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def read_json(path: Path, fallback: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except Exception:
        return fallback


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
    path.chmod(0o600)


def load_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if args.prompt:
        return args.prompt
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def prompt_signature(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def contains_sensitive_terms(prompt: str) -> bool:
    return bool(SECRET_WORDS.search(prompt or ""))


def route_allowed_for_privacy(route_id: str, privacy: str) -> bool:
    if privacy == "dashboard-safe":
        return True
    return route_id in {"luna", "terra", "sol", "glm", "ollama"}


def run_check(cmd: list[str], timeout: int = 4) -> bool:
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def remote_check(command: str, timeout: int = 5) -> bool:
    return run_check(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=3",
            JAIMES_SSH_HOST,
            command,
        ],
        timeout=timeout,
    )


def detect_explicit_route(prompt: str) -> str:
    lower = " ".join((prompt or "").lower().split())
    for alias, route in sorted(MODEL_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        pattern = re.compile(
            rf"\b(?:use|run(?: this)? (?:with|on)|route(?: this)? (?:to|through)|ask|with|via|on|delegate to|send to)\s+{re.escape(alias)}\b"
        )
        for match in pattern.finditer(lower):
            prefix = lower[max(0, match.start() - 20):match.start()]
            if re.search(r"(?:do not|don't|dont|never|avoid|not to)\s*$", prefix):
                continue
            return route
    return ""


def classify_route(prompt: str, privacy: str) -> tuple[str, str]:
    lower = (prompt or "").lower()
    if privacy != "dashboard-safe" or contains_sensitive_terms(prompt):
        return "luna", "private/sensitive content stays on Josh 2.0 coordinator lane"
    if any(token in lower for token in ("current event", "latest news", "x/twitter", "social signal", "market narrative")):
        return "grok", "public current-events/social signal request"
    if any(token in lower for token in ("hard", "stabilize", "architecture", "migration", "integration", "debug", "root cause")):
        return "terra", "trusted execution/integration"
    if any(token in lower for token in ("fix", "patch", "change", "edit", "implement", "deploy", "repair", "build", "code")):
        return "terra", "trusted execution/integration"
    if any(token in lower for token in ("review", "summarize", "summary", "digest", "large context", "read this")):
        return "gemini", "dashboard-safe review/summarization"
    return "luna", "fast Inbox coordination"


def health(route_id: str, injected: dict[str, bool] | None = None) -> bool:
    if injected is not None and route_id in injected:
        return bool(injected[route_id])
    if route_id in {"luna", "terra", "sol"}:
        return (
            shutil.which("codex") is not None
            and (Path.home() / ".codex" / "config.toml").exists()
            and run_check(["codex", "--version"], timeout=3)
        )
    if route_id in {"gemini", "gemini-pro"}:
        return remote_check("test -x /opt/homebrew/bin/gemini && test -f ~/.openclaw/workspace/scripts/llm_router.py")
    if route_id == "jaimes":
        return remote_check("test -x ~/.local/bin/hermes")
    if route_id in {"ollama", "glm"}:
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1) as resp:
                if resp.status >= 500:
                    return False
                payload = json.loads(resp.read())
                names = {
                    str(item.get("name") or item.get("model") or "").lower()
                    for item in payload.get("models", [])
                    if isinstance(item, dict)
                }
                if route_id == "glm":
                    return any(name == "glm-5.2" or name.startswith("glm-5.2:") for name in names)
                return bool(names)
        except Exception:
            return False
    if route_id == "grok":
        return remote_check(
            "test -x /opt/homebrew/bin/op && "
            "test -x ~/.openclaw/workspace/scripts/op_agent_env.sh && "
            "/usr/bin/security find-generic-password -a \"$USER\" "
            "-s com.josh.agent-ecosystem.op-service-account.JC-Agents-Mac-mini >/dev/null 2>&1"
        )
    return False


def fallback_for(route_id: str, privacy: str, injected: dict[str, bool] | None = None) -> tuple[str, str]:
    candidates = ["luna", "terra", "gemini", "ollama"]
    if privacy != "dashboard-safe":
        candidates = ["luna", "terra", "ollama"]
    for candidate in candidates:
        if candidate != route_id and route_allowed_for_privacy(candidate, privacy) and health(candidate, injected):
            return candidate, f"{route_id} unhealthy; selected {candidate}"
    safe_default = "luna" if route_allowed_for_privacy("luna", privacy) else "ollama"
    return safe_default, f"{route_id} unavailable; safe fallback {safe_default} is not yet healthy"


def route_prompt(prompt: str, privacy: str = "dashboard-safe", injected_health: dict[str, bool] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    explicit = detect_explicit_route(prompt)
    if explicit:
        route_id = explicit
        reason = "explicit model request"
    else:
        route_id, reason = classify_route(prompt, privacy)

    requested_healthy = health(route_id, injected_health)
    policy_allowed = route_allowed_for_privacy(route_id, privacy)
    fallback = ""
    selected = route_id
    if explicit and not policy_allowed:
        selected, fallback = fallback_for(route_id, privacy, injected_health)
        reason = "explicit model request blocked by privacy policy"
        fallback = f"privacy policy blocked {route_id}; selected {selected}"
    elif explicit and not requested_healthy:
        selected, fallback = fallback_for(route_id, privacy, injected_health)
    elif not requested_healthy:
        selected, fallback = fallback_for(route_id, privacy, injected_health)

    cfg = ROUTES[selected]
    latency_ms = max(0, round((time.perf_counter() - started) * 1000))
    return {
        "ok": True,
        "routeId": selected,
        "requestedRouteId": route_id if explicit else "",
        "explicitRequest": bool(explicit),
        "requestedRouteHealthy": requested_healthy,
        "policyAllowed": policy_allowed,
        "provider": cfg["provider"],
        "model": cfg["model"],
        "tier": cfg["tier"],
        "worker": cfg["worker"],
        "host": cfg["host"],
        "role": cfg["role"],
        "executor": cfg["executor"],
        "routingReason": reason,
        "fallback": fallback,
        "privacy": privacy,
        "latencyMs": latency_ms,
        "executionVerified": False,
        "outcome": "planned",
    }


def append_telemetry(record: dict[str, Any]) -> None:
    safe = {
        "timestamp": utc_now(),
        "sourceAgent": CONTROL_TOWER_AGENT,
        "host": record.get("actualHost") or record.get("host") or socket.gethostname(),
        "worker": record.get("actualWorker") or record.get("worker"),
        "provider": record.get("actualProvider") or record.get("provider"),
        "model": record.get("actualModel") or record.get("model"),
        "routeId": record.get("routeId"),
        "requestedRouteId": record.get("requestedRouteId") or "",
        "explicitRequest": bool(record.get("explicitRequest")),
        "routingReason": record.get("routingReason"),
        "fallback": record.get("fallback") or "",
        "latencyMs": record.get("latencyMs"),
        "outcome": record.get("outcome"),
        "jobId": record.get("jobId") or "",
        "attempt": record.get("attempt"),
        "telemetryStage": record.get("telemetryStage") or "route",
        "executionVerified": bool(record.get("executionVerified")),
        "telemetryPolicy": "no raw prompts, model output, secrets, OAuth payloads, cookies, tokens, raw emails, or private account contents",
    }
    TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(safe, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(TELEMETRY_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def canonical_model_family(value: str) -> str:
    lowered = str(value or "").lower()
    if any(token in lowered for token in ("gemini", "google", "antigravity")):
        return "antigravity"
    if any(token in lowered for token in ("grok", "xai", "x.ai")):
        return "grok"
    if any(token in lowered for token in ("ollama", "llama", "qwen", "gemma", "glm")):
        return "ollama"
    return "codex"


def publish_control_tower(
    title: str,
    status: str,
    detail: str,
    *,
    job: dict[str, Any] | None = None,
    phase: str = "",
    route_verified: bool | None = None,
    brain_feed: bool = True,
    work_event: str = "",
) -> bool:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_publish.py"),
        "--agent",
        CONTROL_TOWER_AGENT,
        "--type",
        "status",
        "--status",
        status,
        "--title",
        title[:120],
        "--tool",
        "Josh 2.0 Inbox coordinator",
        "--detail",
        detail[:260],
        "--privacy",
        "dashboard-safe",
    ]
    if brain_feed:
        cmd.append("--brain-feed")
    if work_event:
        cmd += ["--work-event", work_event]
    snapshot = job or {}
    route = snapshot.get("route") if isinstance(snapshot.get("route"), dict) else {}
    actual = snapshot.get("actual") if isinstance(snapshot.get("actual"), dict) else {}
    work_id = str(snapshot.get("workId") or "")
    run_id = str(snapshot.get("ledgerRunId") or "")
    model_id = str(actual.get("actualModel") or route.get("model") or "")
    if work_id:
        cmd += ["--work-id", work_id]
    if run_id:
        cmd += ["--run-id", run_id]
    if phase:
        cmd += ["--phase", phase]
    if model_id:
        cmd += ["--model-family", canonical_model_family(model_id), "--model-id", model_id[:120]]
    if route_verified:
        cmd.append("--route-verified")
    elif route_verified is False:
        cmd.append("--route-unverified")
    if snapshot.get("originClaimHash"):
        cmd += ["--origin-claim-hash", str(snapshot["originClaimHash"])[:128]]
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=12,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def parse_utc(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def redact_secret_values(text: str) -> str:
    value = str(text or "")
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.groups >= 2:
            value = pattern.sub(lambda match: f"{match.group(1)}: [redacted]", value)
        else:
            value = pattern.sub("[redacted]", value)
    return value


def clean_final_item(value: str, limit: int = 700) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"</?pre>", "", text, flags=re.I)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", text)
    text = " ".join(redact_secret_values(text).split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def unique_final_items(values: list[str], *, limit: int = 5) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = clean_final_item(value)
        empty_candidate = item.lower().strip().rstrip(".")
        normalized = re.sub(r"[^a-z0-9]+", " ", item.lower()).strip()
        if not item or empty_candidate in EMPTY_FINAL_ITEMS or normalized in seen:
            continue
        seen.add(normalized)
        items.append(item)
        if len(items) >= limit:
            break
    return items


def is_status_only_final_item(value: str) -> bool:
    text = clean_final_item(value)
    return not text or any(pattern.fullmatch(text) for pattern in STATUS_ONLY_FINAL_PATTERNS)


def is_substantive_final_item(value: str) -> bool:
    text = clean_final_item(value)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'._/-]*", text)
    return len(text) >= 24 and len(words) >= 4 and not is_status_only_final_item(text)


def is_concrete_result_item(value: str) -> bool:
    text = clean_final_item(value)
    return is_substantive_final_item(text) and bool(
        CONCRETE_RESULT_SIGNAL.search(text) or CONCRETE_EVIDENCE_SIGNAL.search(text)
    )


def no_action_item(value: str) -> bool:
    return bool(NO_ACTION_SIGNAL.match(clean_final_item(value)))


def incomplete_summary_done_items(source_items: list[str], concrete_count: int) -> list[str]:
    """Preserve real details and fill shape only with transparent deficiency facts."""
    preserved = [item for item in source_items if is_substantive_final_item(item)][:4]
    deficiency_facts = [
        f"The supplied summary contained {len(source_items)} unique result bullet(s), including {concrete_count} concrete finding(s) or outcome(s).",
        "Completion requires three to five substantive source-provided bullets with concrete findings or outcomes.",
        "No missing findings were inferred or invented.",
    ]
    combined = unique_final_items([*preserved, *deficiency_facts], limit=5)
    if not any(item in deficiency_facts for item in combined):
        combined = unique_final_items([*preserved[:4], deficiency_facts[0]], limit=5)
    for fact in deficiency_facts:
        if len(combined) >= 3:
            break
        combined = unique_final_items([*combined, fact], limit=5)
    return combined


def parse_model_sections(output: str) -> dict[str, Any]:
    cleaned = html.unescape(str(output or "")).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"</?pre>", "", cleaned, flags=re.I)
    sections: dict[str, list[str]] = {key: [] for key in ("done", "issues", "next", "approval")}
    complete = False
    complete_declared = False
    current = ""
    loose: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = re.sub(r"[*_`]", "", line).strip().lower()
        if lower.startswith("model:"):
            continue
        if lower.startswith("complete:"):
            match = re.match(r"^complete:\s*(yes|no)\b", lower)
            complete = bool(match and match.group(1) == "yes")
            complete_declared = match is not None
            current = ""
            continue
        matched = False
        for key, label in FINAL_SECTION_LABELS.items():
            if key == "complete":
                continue
            if lower == label.lower() or lower.startswith(label.lower()):
                current = key
                remainder = line.split(":", 1)[1].strip() if ":" in line else ""
                item = clean_final_item(remainder)
                if item:
                    sections[key].append(item)
                matched = True
                break
        if matched:
            continue
        item = clean_final_item(line)
        if not item:
            continue
        if current:
            sections[current].append(item)
        else:
            loose.append(item)

    source_done = unique_final_items(sections["done"] or loose, limit=6)
    source_issues = unique_final_items(sections["issues"])
    source_next = unique_final_items(sections["next"])
    source_approval = unique_final_items(sections["approval"])
    substantive_done = [item for item in source_done if is_substantive_final_item(item)]
    concrete_done = [item for item in source_done if is_concrete_result_item(item)]
    quality_issues: list[str] = []

    if not complete_declared:
        quality_issues.append("Worker did not return a verifiable completion status.")
    if complete:
        if not 3 <= len(source_done) <= 5:
            quality_issues.append("A completion claim requires three to five unique result bullets.")
        if len(substantive_done) != len(source_done):
            quality_issues.append("Status or delivery-process text was used in place of substantive findings or outcomes.")
        if len(concrete_done) < 2:
            quality_issues.append("The completion claim did not include enough concrete findings or outcomes.")
        if not source_next:
            quality_issues.append("The completion claim did not include an evidence-based next step.")

        reported_text = " ".join([*source_done, *source_next, *source_approval])
        reported_risk = bool(RISK_OR_LIMITATION_SIGNAL.search(reported_text))
        if reported_risk and not source_issues:
            quality_issues.append("A reported risk or limitation was not reflected in Issues.")

        no_action_requested = any(no_action_item(item) for item in source_next)
        no_action_conflict = bool(
            source_issues
            or source_approval
            or reported_risk
            or ACTION_OR_RECOMMENDATION_SIGNAL.search(" ".join(source_done))
            or any(not no_action_item(item) for item in source_next)
        )
        if no_action_requested and no_action_conflict:
            quality_issues.append("The No action needed conclusion was not supported by the reported findings.")

    summary_sufficient = bool(complete and complete_declared and not quality_issues)
    if summary_sufficient:
        return {
            "complete": True,
            "completeDeclared": True,
            "summarySufficient": True,
            "summaryQualityIssues": [],
            "done": source_done,
            "issues": source_issues,
            "next": source_next,
            "approval": source_approval,
        }

    if complete or not complete_declared:
        issues = unique_final_items([MISSING_FINDINGS_ISSUE, *source_issues, *quality_issues])
        return {
            "complete": False,
            "completeDeclared": complete_declared,
            "summarySufficient": False,
            "summaryQualityIssues": quality_issues,
            "done": incomplete_summary_done_items(source_done, len(concrete_done)),
            "issues": issues,
            "next": [RETRY_FINDINGS_NEXT_STEP],
            "approval": source_approval,
        }

    # A worker may truthfully report Complete: No. Preserve its facts and issue
    # rather than inventing a successful result or silently changing its advice.
    return {
        "complete": False,
        "completeDeclared": True,
        "summarySufficient": False,
        "summaryQualityIssues": [],
        "done": source_done,
        "issues": source_issues,
        "next": source_next or ["Review the listed issue before retrying."],
        "approval": source_approval,
    }


def render_final_html(route: dict[str, Any], execution: dict[str, Any], output: str) -> str:
    #JAIMES: the delivery layer, not the model, owns the fixed final format and
    # inserts only verified runtime routing facts.
    sections = parse_model_sections(output)
    execution_verified = bool(execution.get("executionVerified"))
    model_verified = bool(execution.get("modelVerified"))
    if not execution_verified:
        sections["complete"] = False
        if "Worker execution was not verified." not in sections["issues"]:
            sections["issues"].append("Worker execution was not verified.")
    provider = clean_final_item(str(execution.get("actualProvider") or ""), limit=40)
    model = clean_final_item(str(execution.get("actualModel") or ""), limit=80)
    verified_model = f"{provider}/{model}" if model_verified and provider and model else "unverified"
    args = argparse.Namespace(
        model=verified_model,
        route=clean_final_item(str(route.get("routeId") or "unverified"), limit=80),
        why=clean_final_item(str(route.get("routingReason") or "verified Inbox routing"), limit=120),
        complete=bool(sections["complete"]),
        done=sections["done"],
        issue=sections["issues"],
        next=sections["next"],
        approval=sections["approval"],
    )
    return format_final(args)


def dedupe_key(prompt: str, origin: dict[str, str], sensitive: bool = False) -> str:
    chat_id = str(origin.get("chatId") or "")
    thread_id = str(origin.get("threadId") or "")
    message_id = str(origin.get("messageId") or "")
    run_id = str(origin.get("runId") or "")
    card_key = str(origin.get("cardKey") or "")
    if message_id:
        stable_origin = {"chatId": chat_id, "threadId": thread_id, "messageId": message_id}
    elif run_id:
        stable_origin = {"chatId": chat_id, "threadId": thread_id, "runId": run_id}
    else:
        stable_origin = {"chatId": chat_id, "threadId": thread_id, "cardKey": card_key}
    material = json.dumps(stable_origin, sort_keys=True, separators=(",", ":"))
    if sensitive:
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
    return hashlib.sha256(f"{material}\x1f{prompt_signature(prompt)}".encode("utf-8")).hexdigest()


def make_job(
    prompt: str,
    route: dict[str, Any],
    origin: dict[str, str],
    timeout: int,
    work_context: dict[str, str] | None = None,
) -> tuple[dict[str, Any], bool]:
    ensure_private_dir()
    sensitive = route.get("privacy") != "dashboard-safe" or contains_sensitive_terms(prompt)
    key = dedupe_key(prompt, origin, sensitive=sensitive)
    now = dt.datetime.now(dt.timezone.utc)
    with state_lock():
        state = read_json(STATE_PATH, {"jobs": {}})
        jobs = state.setdefault("jobs", {})
        for existing in jobs.values():
            if not isinstance(existing, dict) or existing.get("dedupeKey") != key:
                continue
            created = parse_utc(existing.get("createdAt"))
            recent = created is not None and (now - created).total_seconds() <= DEDUPE_WINDOW_SECONDS
            status = existing.get("status")
            if status in {"queued", "running"} or (recent and status == "done"):
                safe_context = work_context or {}
                changed = False
                if not existing.get("workId"):
                    existing["workId"] = str(safe_context.get("workId") or f"work-inbox-{existing.get('jobId') or key[:20]}")
                    changed = True
                if not existing.get("ledgerRunId"):
                    existing["ledgerRunId"] = str(safe_context.get("ledgerRunId") or f"run-inbox-{existing.get('jobId') or key[:20]}")
                    changed = True
                if not existing.get("originClaimHash"):
                    existing["originClaimHash"] = str(safe_context.get("originClaimHash") or hashlib.sha256(key.encode("utf-8")).hexdigest())
                    changed = True
                if "ledgerPrestarted" not in existing:
                    existing["ledgerPrestarted"] = bool(safe_context.get("workId"))
                    changed = True
                if changed:
                    save_json(STATE_PATH, state)
                return existing, True

        job_id = uuid.uuid4().hex[:20]
        prompt_path = PRIVATE_DIR / f"{job_id}.prompt"
        result_path = PRIVATE_DIR / f"{job_id}.result"
        if not sensitive:
            fd = os.open(prompt_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(prompt)
                handle.flush()
                os.fsync(handle.fileno())
        job = {
            "jobId": job_id,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
            "status": "queued",
            "attempt": 0,
            "maxRetries": 0 if sensitive else MAX_RETRIES,
            "timeoutSeconds": max(1, int(timeout)),
            "promptPath": "" if sensitive else str(prompt_path),
            "resultPath": str(result_path),
            "promptEphemeral": sensitive,
            "origin": origin,
            "route": {
                key: route.get(key)
                for key in (
                    "routeId", "provider", "model", "tier", "worker", "host",
                    "executor", "routingReason", "fallback", "privacy",
                )
            },
            "promptSignature": "" if sensitive else prompt_signature(prompt),
            "dedupeKey": key,
        }
        safe_context = work_context or {}
        job["workId"] = str(safe_context.get("workId") or f"work-inbox-{job_id}")
        job["ledgerRunId"] = str(safe_context.get("ledgerRunId") or f"run-inbox-{job_id}")
        job["originClaimHash"] = str(safe_context.get("originClaimHash") or hashlib.sha256(key.encode("utf-8")).hexdigest())
        job["ledgerPrestarted"] = bool(safe_context.get("workId"))
        jobs[job_id] = job
        save_json(STATE_PATH, state)
        return job, False


def spawn_worker(job_id: str, prompt: str | None = None) -> None:
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "worker", "--job-id", job_id],
        cwd=WORKSPACE,
        stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        text=True,
    )
    if prompt is not None and proc.stdin is not None:
        proc.stdin.write(prompt)
        proc.stdin.close()


LLM_EXECUTOR_CODE = r'''import json, sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".openclaw" / "workspace" / "scripts"))
import llm_router
cfg = json.loads(sys.argv[1])
timeout = int(sys.argv[2])
prompt = sys.stdin.read()
kind = cfg["executor"]
if kind == "local-codex":
    output = llm_router._ask_codex_cli(prompt, model=cfg["model"], timeout=timeout, tier=cfg["tier"])
elif kind == "local-ollama":
    model = cfg["model"]
    if model == "local":
        model = llm_router._resolve_ollama_model("local")
    output = llm_router._ask_ollama(prompt, model=model, timeout=timeout, tier=cfg["tier"])
    cfg["model"] = model
elif kind == "remote-llm-router":
    output = llm_router._ask_gemini(prompt, model=cfg["model"], timeout=timeout, tier=cfg["tier"])
elif kind == "remote-llm-router-op":
    output = llm_router._ask_xai(prompt, model=cfg["model"], timeout=timeout, tier=cfg["tier"])
else:
    raise RuntimeError(f"unsupported executor: {kind}")
print(json.dumps({"output": output, "provider": cfg["provider"], "model": cfg["model"], "modelVerified": True}))
'''


HERMES_EXECUTOR_CODE = r'''import json, os, subprocess, sys, tempfile
cfg = json.loads(sys.argv[1])
timeout = int(sys.argv[2])
prompt = sys.stdin.read()
fd, usage_path = tempfile.mkstemp(prefix="inbox-hermes-", suffix=".json")
os.close(fd)
try:
    proc = subprocess.run(
        ["/Users/jc_agent/.local/bin/hermes", "-z", prompt, "--usage-file", usage_path],
        capture_output=True, text=True, timeout=timeout, check=True,
    )
    try:
        usage = json.load(open(usage_path, encoding="utf-8"))
    except Exception:
        usage = {}
    model = str(usage.get("model") or usage.get("model_id") or "")
    provider = str(usage.get("provider") or "jaimes")
    print(json.dumps({"output": proc.stdout.strip(), "provider": provider, "model": model, "modelVerified": bool(model)}))
finally:
    try: os.unlink(usage_path)
    except OSError: pass
'''


def executor_command(route: dict[str, Any], timeout: int) -> tuple[list[str], str]:
    executor = str(route.get("executor") or "")
    cfg = json.dumps(route, separators=(",", ":"))
    if executor in {"local-codex", "local-ollama"}:
        return [sys.executable, "-c", LLM_EXECUTOR_CODE, cfg, str(timeout)], "josh2"

    runner = HERMES_EXECUTOR_CODE if executor == "remote-hermes" else LLM_EXECUTOR_CODE
    remote_python = ["/opt/homebrew/bin/python3", "-c", runner, cfg, str(timeout)]
    if executor == "remote-llm-router-op":
        remote_python = [
            f"{JAIMES_WORKSPACE}/scripts/op_agent_env.sh",
            f"{JAIMES_WORKSPACE}/config/agent-ecosystem.op.env",
            "--",
            *remote_python,
        ]
    remote_command = " ".join(shlex.quote(part) for part in remote_python)
    return [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        JAIMES_SSH_HOST, remote_command,
    ], "jaimes"


def execute_route(prompt: str, route: dict[str, Any], timeout: int) -> dict[str, Any]:
    cmd, actual_host = executor_command(route, timeout)
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=max(1, timeout),
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "executor failed").strip()
        raise RuntimeError(detail[-500:])
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("executor returned no result envelope")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("executor returned an invalid result envelope") from exc
    output = str(result.get("output") or "").strip()
    if not output:
        raise RuntimeError("executor returned empty model output")
    return {
        "output": output,
        "actualHost": actual_host,
        "actualWorker": route.get("worker"),
        "actualProvider": result.get("provider") or route.get("provider"),
        "actualModel": result.get("model") or "unverified",
        "modelVerified": bool(result.get("modelVerified")),
        "executionVerified": True,
    }


def submit_job(args: argparse.Namespace) -> dict[str, Any]:
    prompt = load_prompt(args)
    route: dict[str, Any] | None = None
    if getattr(args, "route_plan_json", ""):
        try:
            planned = json.loads(args.route_plan_json)
            route_id = str(planned.get("routeId") or "")
            cfg = ROUTES[route_id]
            if route_allowed_for_privacy(route_id, args.privacy):
                route = {
                    "ok": True,
                    "routeId": route_id,
                    "requestedRouteId": str(planned.get("requestedRouteId") or ""),
                    "explicitRequest": bool(planned.get("explicitRequest")),
                    "requestedRouteHealthy": bool(planned.get("requestedRouteHealthy")),
                    "policyAllowed": True,
                    "provider": cfg["provider"],
                    "model": cfg["model"],
                    "tier": cfg["tier"],
                    "worker": cfg["worker"],
                    "host": cfg["host"],
                    "role": cfg["role"],
                    "executor": cfg["executor"],
                    "routingReason": clean_final_item(str(planned.get("routingReason") or "verified Inbox routing"), limit=160),
                    "fallback": clean_final_item(str(planned.get("fallback") or ""), limit=160),
                    "privacy": args.privacy,
                    "latencyMs": planned.get("latencyMs"),
                    "executionVerified": False,
                    "outcome": "planned",
                }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            route = None
    if route is None:
        route = route_prompt(prompt, args.privacy)
    origin = {
        "runId": args.origin_run_id or "",
        "messageId": args.message_id or "",
        "cardKey": args.card_key or "",
        "chatId": args.chat_id or "",
        "threadId": args.thread_id or "",
    }
    if args.dry_run:
        return {
            "ok": True,
            "dryRun": True,
            "job": {
                "origin": origin,
                "promptSignature": prompt_signature(prompt),
                "status": "would-queue",
                "timeoutSeconds": args.timeout,
            },
            "route": route,
        }
    work_context = {
        "workId": str(getattr(args, "work_id", "") or ""),
        "ledgerRunId": str(getattr(args, "work_run_id", "") or ""),
        "originClaimHash": str(getattr(args, "origin_claim_hash", "") or ""),
    }
    job, deduplicated = make_job(prompt, route, origin, args.timeout, work_context)
    telemetry = {
        **route,
        "jobId": job["jobId"],
        "attempt": int(job.get("attempt") or 0),
        "telemetryStage": "queue",
        "executionVerified": False,
        "outcome": "deduplicated" if deduplicated else "queued",
    }
    append_telemetry(telemetry)
    if not deduplicated:
        spawn_worker(job["jobId"], prompt if job.get("promptEphemeral") else None)
    publish_control_tower(
        "Josh 2.0 Inbox worker queued",
        "active",
        f"{route['worker']} on {route['host']}; {route['routingReason']}",
        job=job,
        phase="active",
        route_verified=False,
        work_event="" if job.get("ledgerPrestarted") else "start",
    )
    return {
        "ok": True,
        "deduplicated": deduplicated,
        "job": {
            k: v
            for k, v in job.items()
            if k not in {"promptPath", "resultPath", "promptSignature", "dedupeKey"}
        },
        "route": route,
    }


def write_private_text(path: Path, value: str) -> None:
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def update_card_progress(snapshot: dict[str, Any], route: dict[str, Any], step: str, actual: dict[str, Any] | None = None) -> bool:
    origin = snapshot.get("origin") or {}
    card_key = str(origin.get("cardKey") or "")
    if not card_key or not WORK_CARD_SCRIPT.exists():
        return False
    facts = actual or {}
    verified = bool(facts.get("executionVerified"))
    model_label = (
        f"provider={facts.get('actualProvider')}; model={facts.get('actualModel')}; worker={facts.get('actualWorker')}; host={facts.get('actualHost')}"
        if verified
        else f"planned provider={route.get('provider')}; model={route.get('model')}; worker={route.get('worker')}; host={route.get('host')}"
    )
    cmd = [
        sys.executable, str(WORK_CARD_SCRIPT), "update",
        "--key", card_key,
        "--model", model_label,
        "--route", f"route={route.get('routeId')}; reason={route.get('routingReason')}; fallback={route.get('fallback') or 'none'}",
        "--now", step,
        "--done", step,
    ]
    if origin.get("chatId"):
        cmd.extend(["--chat-id", str(origin["chatId"])])
    if origin.get("threadId"):
        cmd.extend(["--thread-id", str(origin["threadId"])])
    try:
        proc = subprocess.run(cmd, cwd=WORKSPACE, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False)
        return proc.returncode == 0
    except Exception:
        return False


def deliver_result(job_id: str, snapshot: dict[str, Any], route: dict[str, Any], execution: dict[str, Any], output: str) -> bool:
    origin = snapshot.get("origin") or {}
    card_key = str(origin.get("cardKey") or "")
    if not card_key or not WORK_CARD_SCRIPT.exists():
        return False
    final_path = PRIVATE_DIR / f"{job_id}.final.html"
    write_private_text(final_path, render_final_html(route, execution, output))
    model_label = f"provider={execution.get('actualProvider') or route.get('provider')}; model={execution.get('actualModel') or 'unverified'}; worker={execution.get('actualWorker') or route.get('worker')}; host={execution.get('actualHost') or route.get('host')}"
    route_label = f"route={route.get('routeId')}; reason={route.get('routingReason')}; fallback={route.get('fallback') or 'none'}"
    sections = parse_model_sections(output)
    task_complete = bool(
        execution.get("executionVerified")
        and sections.get("complete")
        and sections.get("summarySufficient")
    )
    command = "done" if task_complete else "fail"
    cmd = [
        sys.executable,
        str(WORK_CARD_SCRIPT),
        command,
        "--key", card_key,
        "--model", model_label,
        "--route", route_label,
        "--done", "Worker execution verified|Final result delivered" if task_complete else "Worker stopped without a verified completion|Structured issue summary delivered",
        "--blocker", "None" if task_complete else "The objective was not verified complete",
        "--final-text-file", str(final_path),
    ]
    if origin.get("chatId"):
        cmd.extend(["--chat-id", str(origin["chatId"])])
    if origin.get("threadId"):
        cmd.extend(["--thread-id", str(origin["threadId"])])
    try:
        proc = subprocess.run(cmd, cwd=WORKSPACE, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
        return proc.returncode == 0
    finally:
        final_path.unlink(missing_ok=True)


def run_worker(job_id: str) -> dict[str, Any]:
    ensure_private_dir()
    lease_token = uuid.uuid4().hex
    with state_lock():
        state = read_json(STATE_PATH, {"jobs": {}})
        job = (state.get("jobs") or {}).get(job_id)
        if not isinstance(job, dict):
            return {"ok": False, "error": "unknown job"}
        if job.get("status") != "queued":
            return {"ok": True, "jobId": job_id, "outcome": "already-claimed"}
        job["status"] = "running"
        job["attempt"] = int(job.get("attempt") or 0) + 1
        job["updatedAt"] = utc_now()
        job["startedAt"] = job.get("startedAt") or utc_now()
        job["workerPid"] = os.getpid()
        job["leaseToken"] = lease_token
        save_json(STATE_PATH, state)
        snapshot = dict(job)

    route = snapshot.get("route") or {}
    prompt_raw = str(snapshot.get("promptPath") or "")
    prompt_path = Path(prompt_raw) if prompt_raw else None
    result_path = Path(str(snapshot.get("resultPath") or ""))
    started = time.perf_counter()
    execution = dict(snapshot.get("actual") or {})
    error_code = ""
    model_executed = False
    delivered = False
    output = ""
    try:
        update_card_progress(snapshot, route, "Asynchronous worker started")
        if result_path.exists() and execution.get("executionVerified"):
            output = result_path.read_text(encoding="utf-8")
        else:
            if snapshot.get("promptEphemeral"):
                prompt = sys.stdin.read()
                if not prompt:
                    raise RuntimeError("ephemeral prompt unavailable")
            elif prompt_path is not None:
                prompt = prompt_path.read_text(encoding="utf-8")
            else:
                raise RuntimeError("prompt unavailable")
            execution_prompt = f"{WORKER_OUTPUT_CONTRACT}\n\nUser request:\n{prompt}"
            execution = execute_route(execution_prompt, route, int(snapshot.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS))
            output = str(execution["output"])
            write_private_text(result_path, output)
            model_executed = True
            if prompt_path is not None:
                prompt_path.unlink(missing_ok=True)
        update_card_progress(snapshot, route, "Model execution verified; formatting final result", execution)
        delivered = deliver_result(job_id, snapshot, route, execution, output)
        if not delivered:
            raise RuntimeError("delivery failed")
        parsed_sections = parse_model_sections(output)
        task_complete = bool(
            execution.get("executionVerified")
            and parsed_sections.get("complete")
            and parsed_sections.get("summarySufficient")
        )
        outcome = "done" if task_complete else "failed"
        if not task_complete:
            error_code = "model_reported_incomplete"
    except Exception as exc:  # noqa: BLE001
        error_code = type(exc).__name__
        can_retry = (
            int(snapshot.get("attempt") or 0) <= int(snapshot.get("maxRetries") or 0)
            and (not snapshot.get("promptEphemeral") or result_path.exists())
        )
        outcome = "retry" if can_retry else "failed"

    if outcome == "failed" and not delivered:
        if execution.get("executionVerified") and output:
            delivered = deliver_result(job_id, snapshot, route, execution, output)
            if delivered:
                recovered_sections = parse_model_sections(output)
                outcome = "done" if (
                    recovered_sections.get("complete")
                    and recovered_sections.get("summarySufficient")
                ) else "failed"
        else:
            failure_execution = {
                "actualHost": route.get("host") or "unverified",
                "actualWorker": route.get("worker") or "unverified",
                "actualProvider": "unverified",
                "actualModel": "unverified",
                "modelVerified": False,
                "executionVerified": False,
            }
            delivered = deliver_result(
                job_id,
                snapshot,
                route,
                failure_execution,
                "I couldn't complete that request because the selected worker stopped before producing a verified result. Please retry once that route is healthy; no successful model execution was claimed.",
            )
            if not delivered:
                error_code = "failure_final_delivery_failed"

    latency_ms = max(0, round((time.perf_counter() - started) * 1000))
    with state_lock():
        state = read_json(STATE_PATH, {"jobs": {}})
        job = (state.get("jobs") or {}).get(job_id)
        if not isinstance(job, dict) or job.get("leaseToken") != lease_token:
            return {"ok": False, "jobId": job_id, "outcome": "lease-lost"}
        job["status"] = "queued" if outcome == "retry" else outcome
        job["updatedAt"] = utc_now()
        job["latencyMs"] = latency_ms
        job["delivered"] = delivered
        if execution.get("executionVerified") and result_path.exists() and not delivered:
            # Freeze the first delivery-failure timestamp. Automatic recovery is
            # bounded from this point and retries never refresh the window.
            job["deliveryFailedAt"] = job.get("deliveryFailedAt") or utc_now()
            job["deliveryRecoveryReferenceAt"] = job.get("deliveryRecoveryReferenceAt") or job["deliveryFailedAt"]
        elif delivered:
            job.pop("deliveryFailedAt", None)
        job.pop("workerPid", None)
        job.pop("leaseToken", None)
        if execution:
            job["actual"] = {
                key: execution.get(key)
                for key in ("actualHost", "actualWorker", "actualProvider", "actualModel", "modelVerified", "executionVerified")
            }
        if outcome == "done":
            job.pop("lastError", None)
        else:
            job["lastError"] = error_code or "worker_error"
        save_json(STATE_PATH, state)

    append_telemetry({
        **route,
        **execution,
        "jobId": job_id,
        "attempt": snapshot.get("attempt"),
        "latencyMs": latency_ms,
        "telemetryStage": "delivery" if execution.get("executionVerified") else "execution",
        "executionVerified": bool(execution.get("executionVerified")),
        "outcome": outcome,
    })
    if outcome == "retry":
        spawn_worker(job_id)
    publish_control_tower(
        "Josh 2.0 Inbox worker finished"
        if outcome == "done"
        else "Josh 2.0 Inbox worker retrying"
        if outcome == "retry"
        else "Josh 2.0 Inbox worker issue",
        "done" if outcome == "done" else "active" if outcome == "retry" else "error",
        f"{route.get('worker')} outcome={outcome}; executed={model_executed}; delivered={delivered}; latency={latency_ms}ms",
        job=job,
        phase="done" if outcome == "done" else "error" if outcome == "failed" else "active",
        route_verified=bool(execution.get("executionVerified")),
    )
    return {"ok": outcome in {"done", "retry"}, "jobId": job_id, "outcome": outcome, "latencyMs": latency_ms}


def _job_artifact_paths(job_id: str, job: dict[str, Any]) -> tuple[list[Path], int]:
    """Return only job-owned files directly inside the coordinator private dir."""
    safe_job_id = str(job_id or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", safe_job_id):
        return [], sum(bool(job.get(field)) for field in JOB_ARTIFACT_FIELDS)

    expected_names = {
        f"{safe_job_id}.prompt",
        f"{safe_job_id}.result",
        f"{safe_job_id}.final.html",
    }
    raw_paths = [str(job.get(field) or "").strip() for field in JOB_ARTIFACT_FIELDS]
    raw_paths.extend(str(PRIVATE_DIR / name) for name in sorted(expected_names))
    private_root = PRIVATE_DIR.resolve()
    paths: list[Path] = []
    seen: set[str] = set()
    unsafe = 0
    for raw in raw_paths:
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = PRIVATE_DIR / candidate
        try:
            parent = candidate.parent.resolve()
        except OSError:
            unsafe += 1
            continue
        if parent != private_root or candidate.name not in expected_names:
            unsafe += 1
            continue
        key = str(candidate.absolute())
        if key not in seen:
            paths.append(candidate)
            seen.add(key)
    return paths, unsafe


def _audit_tombstone(job_id: str, job: dict[str, Any], scrubbed_at: str) -> dict[str, Any]:
    tombstone = {key: job[key] for key in JOB_AUDIT_FIELDS if key in job}
    tombstone["jobId"] = str(job.get("jobId") or job_id)
    if str(job.get("status") or "") == "queued":
        # The legacy explicit include-queued override is a cancellation, not a
        # queued tombstone that recovery could accidentally try to execute.
        tombstone["previousStatus"] = "queued"
        tombstone["status"] = "cancelled"
        tombstone["cancelledAt"] = str(job.get("cancelledAt") or scrubbed_at)
        tombstone["lastError"] = "stale queued job cancelled by explicit cleanup"
    tombstone["auditTombstone"] = True
    tombstone["artifactsScrubbedAt"] = str(job.get("artifactsScrubbedAt") or scrubbed_at)
    return tombstone


def _has_recoverable_delivery_result(job_id: str, job: dict[str, Any], now: dt.datetime) -> bool:
    """Keep a verified, undelivered result for a finite explicit-retry grace."""
    if job.get("delivered") is True:
        return False
    actual = job.get("actual") or {}
    origin = job.get("origin") or {}
    if not isinstance(actual, dict) or not actual.get("executionVerified"):
        return False
    if not isinstance(origin, dict) or not str(origin.get("cardKey") or ""):
        return False
    reference = (
        parse_utc(job.get("deliveryRecoveryReferenceAt"))
        or parse_utc(job.get("deliveryFailedAt"))
        or parse_utc(job.get("finishedAt"))
        or parse_utc(job.get("updatedAt"))
        or parse_utc(job.get("createdAt"))
    )
    if reference is None:
        return False
    age_seconds = (now - reference).total_seconds()
    if not (-60 <= age_seconds <= DELIVERY_PENDING_RETENTION_SECONDS):
        return False
    safe_job_id = str(job_id or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", safe_job_id):
        return False
    configured = str(job.get("resultPath") or "").strip()
    if not configured:
        return False
    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        candidate = PRIVATE_DIR / candidate
    try:
        if candidate.parent.resolve() != PRIVATE_DIR.resolve():
            return False
        if candidate.name != f"{safe_job_id}.result" or candidate.is_symlink():
            return False
        return candidate.is_file() and candidate.stat().st_size > 0
    except OSError:
        return False


def _private_file_mode_changes(*, apply: bool) -> tuple[int, int, int]:
    """Return files needing hardening, verified changes, and failures."""
    needed = 0
    changed = 0
    failures = 0
    try:
        children = list(PRIVATE_DIR.iterdir())
    except OSError:
        return 0, 0, 0
    for path in children:
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if stat.S_IMODE(path.stat().st_mode) == 0o600:
                continue
            needed += 1
            if apply:
                path.chmod(0o600)
                if stat.S_IMODE(path.stat().st_mode) == 0o600:
                    changed += 1
                else:
                    failures += 1
        except OSError:
            if apply:
                failures += 1
    return needed, changed, failures


def _read_cleanup_state() -> tuple[dict[str, Any] | None, str]:
    """Read retention state without converting corruption into an empty queue."""
    try:
        raw = STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"jobs": {}}, ""
    except OSError:
        return None, "state-unreadable"
    try:
        state = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, "state-invalid-json"
    if not isinstance(state, dict) or not isinstance(state.get("jobs"), dict):
        return None, "state-invalid-jobs"
    if any(not isinstance(job, dict) for job in state["jobs"].values()):
        return None, "state-invalid-job"
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(job_id)) for job_id in state["jobs"]):
        return None, "state-invalid-job-id"
    return state, ""


def _private_dir_mode_changes(*, apply: bool) -> tuple[int, int, int]:
    """Return whether the private directory needs/is hardened to mode 0700."""
    try:
        current = stat.S_IMODE(PRIVATE_DIR.stat().st_mode)
    except FileNotFoundError:
        return 0, 0, 0
    except OSError:
        return 0, 0, 1 if apply else 0
    if current == 0o700:
        return 0, 0, 0
    if not apply:
        return 1, 0, 0
    try:
        PRIVATE_DIR.chmod(0o700)
        if stat.S_IMODE(PRIVATE_DIR.stat().st_mode) == 0o700:
            return 1, 1, 0
    except OSError:
        pass
    return 1, 0, 1


def _aged_orphan_artifacts(
    jobs: dict[str, Any],
    now: dt.datetime,
    max_age_seconds: int,
) -> tuple[list[Path], int, int]:
    """Find old job-shaped files that are not owned by any persisted row."""
    expected_names = {
        name
        for job_id in jobs
        for name in (
            f"{job_id}.prompt",
            f"{job_id}.result",
            f"{job_id}.final.html",
        )
    }
    artifact_name = re.compile(r"^[A-Za-z0-9_-]{1,128}\.(?:prompt|result|final\.html)$")
    try:
        children = list(PRIVATE_DIR.iterdir())
    except FileNotFoundError:
        return [], 0, 0
    except OSError:
        return [], 0, 1
    orphans: list[Path] = []
    unsafe = 0
    inspection_failures = 0
    for path in children:
        if path.name in expected_names or not artifact_name.fullmatch(path.name):
            continue
        try:
            metadata = path.lstat()
        except OSError:
            inspection_failures += 1
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            unsafe += 1
            continue
        modified = dt.datetime.fromtimestamp(metadata.st_mtime, tz=dt.timezone.utc)
        if (now - modified).total_seconds() > max_age_seconds:
            orphans.append(path)
    return orphans, unsafe, inspection_failures


def cleanup(max_age_seconds: int, include_queued: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Scrub aged payload files while retaining privacy-safe audit tombstones.

    Queued work is cancelled and scrubbed only under the legacy explicit
    ``include_queued`` override; running work is never eligible. The
    scheduled/default path is intentionally terminal-only.
    """
    result = {
        "ok": True,
        "dryRun": bool(dry_run),
        "scannedJobs": 0,
        "eligibleJobs": 0,
        "scrubbedJobs": 0,
        "wouldScrubJobs": 0,
        "removedArtifacts": 0,
        "wouldRemoveArtifacts": 0,
        "hardenedFiles": 0,
        "wouldHardenFiles": 0,
        "hardenedDirectories": 0,
        "wouldHardenDirectories": 0,
        "unsafeArtifactPaths": 0,
        "retainedTombstones": 0,
        "preservedActiveJobs": 0,
        "preservedDeliveryPendingJobs": 0,
        "cancelledQueuedJobs": 0,
        "artifactInspectionFailures": 0,
        "artifactRemovalFailures": 0,
        "invalidTimestampJobs": 0,
        "unsafeOrphanArtifacts": 0,
        "removedOrphanArtifacts": 0,
        "wouldRemoveOrphanArtifacts": 0,
        "permissionFailures": 0,
        "stateWriteFailures": 0,
        "errors": [],
    }
    try:
        max_age_seconds = int(max_age_seconds)
    except (TypeError, ValueError):
        result["ok"] = False
        result["errors"].append("invalid-max-age")
        return result
    if max_age_seconds < 0:
        result["ok"] = False
        result["errors"].append("negative-max-age")
        return result

    lock_context = existing_state_read_lock() if dry_run else state_lock()
    with lock_context:
        state, state_error = _read_cleanup_state()
        if state is None:
            result["ok"] = False
            result["errors"].append(state_error or "state-invalid")
            needed, _changed, _failures = _private_file_mode_changes(apply=False)
            result["wouldHardenFiles"] = needed
            dir_needed, _dir_changed, _dir_failures = _private_dir_mode_changes(apply=False)
            result["wouldHardenDirectories"] = dir_needed
            if not dry_run:
                _needed, changed, failures = _private_file_mode_changes(apply=True)
                _dir_needed, dir_changed, dir_failures = _private_dir_mode_changes(apply=True)
                result["hardenedFiles"] = changed
                result["hardenedDirectories"] = dir_changed
                result["permissionFailures"] = failures + dir_failures
                if failures or dir_failures:
                    result["errors"].append("permission-hardening-failed")
            return result
        jobs = state["jobs"]
        now = dt.datetime.now(dt.timezone.utc)
        scrubbed_at = utc_now()
        eligible_statuses = set(TERMINAL_JOB_STATUSES)
        if include_queued:
            eligible_statuses.add("queued")
        for job_id, job in list(jobs.items()):
            if not isinstance(job, dict):
                continue
            result["scannedJobs"] += 1
            updated_dt = (
                parse_utc(job.get("updatedAt"))
                or parse_utc(job.get("completedAt"))
                or parse_utc(job.get("createdAt"))
            )
            if updated_dt is None:
                result["invalidTimestampJobs"] += 1
                if "invalid-job-timestamp" not in result["errors"]:
                    result["errors"].append("invalid-job-timestamp")
                continue
            stale = (now - updated_dt).total_seconds() > max_age_seconds
            if not stale or str(job.get("status") or "") not in eligible_statuses:
                continue

            if _has_recoverable_delivery_result(str(job_id), job, now):
                # A completed model result awaiting the existing-card edit is
                # still live work, even when its worker status is terminal.
                result["preservedDeliveryPendingJobs"] += 1
                continue

            delivery_claim_started = parse_utc(job.get("deliveryRecoveryStartedAt"))
            delivery_claim_age = (
                (now - delivery_claim_started).total_seconds()
                if delivery_claim_started is not None
                else None
            )
            if (
                job.get("deliveryRecoveryToken")
                and delivery_claim_age is not None
                and -5 <= delivery_claim_age <= DELIVERY_RECOVERY_WINDOW_SECONDS
            ):
                # Delivery recovery performs the Telegram edit outside the
                # state lock. Keep its verified result and claim intact until
                # the bounded claim window closes.
                result["preservedActiveJobs"] += 1
                continue

            result["eligibleJobs"] += 1
            artifact_paths, unsafe = _job_artifact_paths(str(job_id), job)
            result["unsafeArtifactPaths"] += unsafe
            if unsafe:
                if "unsafe-artifact-path" not in result["errors"]:
                    result["errors"].append("unsafe-artifact-path")
                continue
            existing_artifacts = []
            inspection_failures = 0
            for path in artifact_paths:
                try:
                    if path.exists() or path.is_symlink():
                        existing_artifacts.append(path)
                except OSError:
                    inspection_failures += 1
            result["artifactInspectionFailures"] += inspection_failures
            if inspection_failures:
                if "artifact-inspection-failed" not in result["errors"]:
                    result["errors"].append("artifact-inspection-failed")
                continue
            tombstone = _audit_tombstone(str(job_id), job, scrubbed_at)
            needs_scrub = tombstone != job or bool(existing_artifacts)
            if not needs_scrub:
                result["retainedTombstones"] += 1
                continue
            result["wouldScrubJobs"] += 1
            result["wouldRemoveArtifacts"] += len(existing_artifacts)
            if dry_run:
                continue
            removed = 0
            removal_failures = 0
            for path in existing_artifacts:
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    removal_failures += 1
            result["removedArtifacts"] += removed
            result["artifactRemovalFailures"] += removal_failures
            if removal_failures:
                if "artifact-removal-failed" not in result["errors"]:
                    result["errors"].append("artifact-removal-failed")
                # Keep the complete row so cleanup can retry the remaining
                # owned artifact instead of claiming a fully scrubbed record.
                continue
            jobs[job_id] = tombstone
            result["scrubbedJobs"] += 1
            if tombstone.get("previousStatus") == "queued":
                result["cancelledQueuedJobs"] += 1
            result["retainedTombstones"] += 1

        orphan_artifacts, unsafe_orphans, orphan_inspection_failures = _aged_orphan_artifacts(
            jobs,
            now,
            max_age_seconds,
        )
        result["unsafeOrphanArtifacts"] = unsafe_orphans
        result["artifactInspectionFailures"] += orphan_inspection_failures
        result["wouldRemoveOrphanArtifacts"] = len(orphan_artifacts)
        result["wouldRemoveArtifacts"] += len(orphan_artifacts)
        if unsafe_orphans and "unsafe-orphan-artifact" not in result["errors"]:
            result["errors"].append("unsafe-orphan-artifact")
        if orphan_inspection_failures and "artifact-inspection-failed" not in result["errors"]:
            result["errors"].append("artifact-inspection-failed")
        if not dry_run:
            for path in orphan_artifacts:
                try:
                    path.unlink(missing_ok=True)
                    result["removedArtifacts"] += 1
                    result["removedOrphanArtifacts"] += 1
                except OSError:
                    result["artifactRemovalFailures"] += 1
            if result["artifactRemovalFailures"] and "artifact-removal-failed" not in result["errors"]:
                result["errors"].append("artifact-removal-failed")

        needed, _changed, _failures = _private_file_mode_changes(apply=False)
        result["wouldHardenFiles"] = needed
        dir_needed, _dir_changed, _dir_failures = _private_dir_mode_changes(apply=False)
        result["wouldHardenDirectories"] = dir_needed
        if not dry_run:
            try:
                save_json(STATE_PATH, state)
            except Exception:
                result["stateWriteFailures"] += 1
                if "state-write-failed" not in result["errors"]:
                    result["errors"].append("state-write-failed")
            _needed, changed, failures = _private_file_mode_changes(apply=True)
            _dir_needed, dir_changed, dir_failures = _private_dir_mode_changes(apply=True)
            result["hardenedFiles"] = changed
            result["hardenedDirectories"] = dir_changed
            result["permissionFailures"] = failures + dir_failures
            if (failures or dir_failures) and "permission-hardening-failed" not in result["errors"]:
                result["errors"].append("permission-hardening-failed")
    result["ok"] = not result["errors"]
    return result


def expected_result_path(job_id: str, job: dict[str, Any]) -> Path | None:
    """Return only the coordinator-owned result path for this job.

    State is private, but recovery still must not turn a corrupted state row into
    an arbitrary local-file read.  New and legacy coordinator jobs both use this
    exact filename in ``PRIVATE_DIR``.
    """
    configured = str(job.get("resultPath") or "")
    if not configured:
        return None
    expected = PRIVATE_DIR / f"{job_id}.result"
    configured_absolute = Path(os.path.abspath(os.path.normpath(configured)))
    expected_absolute = Path(os.path.abspath(os.path.normpath(str(expected))))
    if configured_absolute != expected_absolute:
        return None
    return expected_absolute


def verified_saved_result(job_id: str, job: dict[str, Any]) -> tuple[str | None, str]:
    """Read a saved result only when execution and path ownership are verified."""
    actual = job.get("actual") or {}
    if not isinstance(actual, dict) or not actual.get("executionVerified"):
        return None, "execution-unverified"
    result_path = expected_result_path(job_id, job)
    if result_path is None:
        return None, "result-path-invalid"
    fd = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(result_path, flags)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            return None, "result-file-unsafe"
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            output = handle.read()
    except Exception:
        return None, "result-file-unavailable"
    finally:
        if fd >= 0:
            os.close(fd)
    if not output.strip():
        return None, "result-file-empty"
    return output, ""


def delivery_recovery_reference(job: dict[str, Any]) -> dt.datetime | None:
    """Return the immutable timestamp that bounds automatic redelivery."""
    return (
        parse_utc(job.get("deliveryRecoveryReferenceAt"))
        or parse_utc(job.get("deliveryFailedAt"))
        or parse_utc(job.get("finishedAt"))
        or parse_utc(job.get("updatedAt"))
        or parse_utc(job.get("createdAt"))
    )


def delivery_recovery_is_fresh(job: dict[str, Any], now: dt.datetime) -> bool:
    reference = delivery_recovery_reference(job)
    if reference is None:
        return False
    age_seconds = (now - reference).total_seconds()
    # A small negative age tolerates host clock skew without allowing an
    # unbounded future timestamp to keep automatic recovery eligible forever.
    return -60 <= age_seconds <= DELIVERY_RECOVERY_WINDOW_SECONDS


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def recover(job_id: str = "") -> dict[str, Any]:
    """Recover interrupted workers and retry delivery of verified saved results.

    Delivery recovery is intentionally separate from worker recovery.  Once a
    verified result exists, this function claims only a delivery attempt and
    calls ``deliver_result`` with the original card key; it never queues a model
    worker for that job.  The claim prevents concurrent recovery processes from
    posting the same final result twice.
    """
    to_spawn: list[str] = []
    delivery_claims: list[tuple[str, str, dict[str, Any], str]] = []
    left_running = 0
    delivery_in_progress = 0
    delivery_not_recoverable = 0
    now = dt.datetime.now(dt.timezone.utc)
    requested_job_id = str(job_id or "").strip()
    delivery_deferred_historical = 0
    delivery_deferred_backoff = 0
    delivery_attempts_exhausted = 0
    requested_job_found = False
    with state_lock():
        state = read_json(STATE_PATH, {"jobs": {}})
        jobs = state.get("jobs") if isinstance(state, dict) else {}
        if isinstance(jobs, dict):
            for job_id, job in jobs.items():
                if not isinstance(job, dict):
                    continue
                if requested_job_id and job_id != requested_job_id:
                    continue
                if requested_job_id:
                    requested_job_found = True
                status = job.get("status")
                saved_output, _saved_error = verified_saved_result(job_id, job)
                delivery_pending = bool(saved_output and not job.get("delivered"))
                if delivery_pending and not job.get("deliveryRecoveryReferenceAt"):
                    # Freeze the pre-recovery state timestamp before any status
                    # normalization below can update ``updatedAt``. This keeps
                    # historical rows historical on every future scan.
                    reference = delivery_recovery_reference(job)
                    if reference is not None:
                        job["deliveryRecoveryReferenceAt"] = reference.isoformat().replace("+00:00", "Z")
                automatic_delivery_allowed = delivery_recovery_is_fresh(job, now)
                if status == "running":
                    pid = int(job.get("workerPid") or 0)
                    if process_is_alive(pid):
                        left_running += 1
                        continue
                    # A dead worker with a verified result needs delivery only.
                    # Never send it back through the model worker queue.
                    if delivery_pending:
                        job["status"] = "failed"
                        job["updatedAt"] = utc_now()
                        job["lastError"] = "worker stopped after saving a verified result"
                        job.pop("workerPid", None)
                        job.pop("leaseToken", None)
                    elif int(job.get("attempt") or 0) >= int(job.get("maxRetries") or 0) + 1:
                        job["status"] = "failed"
                        job["updatedAt"] = utc_now()
                        job["lastError"] = "dead worker exhausted its retry budget"
                        job.pop("workerPid", None)
                        job.pop("leaseToken", None)
                    else:
                        job["status"] = "queued"
                        job.pop("workerPid", None)
                        job.pop("leaseToken", None)
                if job.get("status") == "queued":
                    # The prior worker may have saved output immediately before
                    # dying.  Deliver that output rather than re-running it.
                    if delivery_pending:
                        job["status"] = "failed"
                        job["updatedAt"] = utc_now()
                        job["lastError"] = "verified result awaiting delivery recovery"
                    elif int(job.get("attempt") or 0) >= int(job.get("maxRetries") or 0) + 1:
                        job["status"] = "failed"
                        job["updatedAt"] = utc_now()
                        job["lastError"] = "queued recovery exhausted its retry budget"
                    else:
                        job["updatedAt"] = utc_now()
                        to_spawn.append(job_id)

                if job.get("status") != "failed" or job.get("delivered"):
                    continue
                saved_output, _saved_error = verified_saved_result(job_id, job)
                if not saved_output:
                    continue
                origin = job.get("origin") or {}
                if not isinstance(origin, dict) or not str(origin.get("cardKey") or ""):
                    # Delivery recovery may update only the existing live card.
                    # A missing key is never replaced with a new Telegram send.
                    delivery_not_recoverable += 1
                    continue

                claim_token = str(job.get("deliveryRecoveryToken") or "")
                claim_pid = int(job.get("deliveryRecoveryPid") or 0)
                claim_started = parse_utc(job.get("deliveryRecoveryStartedAt"))
                claim_fresh = bool(
                    claim_started
                    and (now - claim_started).total_seconds() <= 2 * 60
                )
                if claim_token and claim_fresh and process_is_alive(claim_pid):
                    delivery_in_progress += 1
                    continue
                job.pop("deliveryRecoveryToken", None)
                job.pop("deliveryRecoveryPid", None)
                job.pop("deliveryRecoveryStartedAt", None)

                if not requested_job_id:
                    if not automatic_delivery_allowed:
                        # Old terminal rows remain auditable but inert. An
                        # operator must select the exact job id to retry their
                        # Telegram delivery; a routine scan never resurrects it.
                        delivery_deferred_historical += 1
                        continue
                    automatic_attempts = int(job.get("deliveryRecoveryAutomaticAttempts") or 0)
                    if automatic_attempts >= MAX_AUTOMATIC_DELIVERY_RECOVERY_ATTEMPTS:
                        delivery_attempts_exhausted += 1
                        continue
                    last_attempt = parse_utc(job.get("deliveryRecoveryLastAttemptAt"))
                    if last_attempt is not None and (now - last_attempt).total_seconds() < DELIVERY_RECOVERY_BACKOFF_SECONDS:
                        delivery_deferred_backoff += 1
                        continue

                claim_token = uuid.uuid4().hex
                attempt_started = utc_now()
                job["deliveryRecoveryToken"] = claim_token
                job["deliveryRecoveryPid"] = os.getpid()
                job["deliveryRecoveryStartedAt"] = attempt_started
                job["deliveryRecoveryLastAttemptAt"] = attempt_started
                job["deliveryRecoveryAttempts"] = int(job.get("deliveryRecoveryAttempts") or 0) + 1
                if not requested_job_id:
                    job["deliveryRecoveryAutomaticAttempts"] = int(job.get("deliveryRecoveryAutomaticAttempts") or 0) + 1
                job["updatedAt"] = attempt_started
                delivery_claims.append((job_id, claim_token, dict(job), saved_output))
            save_json(STATE_PATH, state)

    for job_id in to_spawn:
        spawn_worker(job_id)

    delivery_recovered = 0
    delivery_retry_failed = 0
    for job_id, claim_token, snapshot, output in delivery_claims:
        route = snapshot.get("route") or {}
        execution = snapshot.get("actual") or {}
        delivered = False
        try:
            delivered = deliver_result(job_id, snapshot, route, execution, output)
        except Exception:
            delivered = False
        recovered_sections = parse_model_sections(output)
        complete = bool(
            recovered_sections.get("complete")
            and recovered_sections.get("summarySufficient")
        )
        with state_lock():
            state = read_json(STATE_PATH, {"jobs": {}})
            job = (state.get("jobs") or {}).get(job_id)
            if not isinstance(job, dict) or job.get("deliveryRecoveryToken") != claim_token:
                delivery_retry_failed += 1
                continue
            job.pop("deliveryRecoveryToken", None)
            job.pop("deliveryRecoveryPid", None)
            job.pop("deliveryRecoveryStartedAt", None)
            job["updatedAt"] = utc_now()
            if delivered:
                job["delivered"] = True
                job["deliveryRecoveredAt"] = utc_now()
                job["status"] = "done" if complete else "failed"
                if complete:
                    job.pop("lastError", None)
                else:
                    job["lastError"] = "model_reported_incomplete"
                delivery_recovered += 1
            else:
                job["status"] = "failed"
                job["delivered"] = False
                job["lastError"] = "delivery_recovery_failed"
                delivery_retry_failed += 1
            save_json(STATE_PATH, state)
        append_telemetry({
            **route,
            **execution,
            "jobId": job_id,
            "attempt": snapshot.get("attempt"),
            "telemetryStage": "delivery-recovery",
            "executionVerified": True,
            "outcome": "delivered" if delivered else "delivery-failed",
        })
        publish_control_tower(
            "Josh 2.0 Inbox result delivery recovered" if delivered else "Josh 2.0 Inbox result delivery retry failed",
            "done" if delivered else "error",
            f"{route.get('worker')} reused the verified saved result and existing Inbox card",
            job=job,
            phase="done" if delivered and complete else "error",
            route_verified=True,
        )

    return {
        "ok": delivery_retry_failed == 0,
        "recovered": len(to_spawn),
        "leftRunning": left_running,
        "deliveryRecovered": delivery_recovered,
        "deliveryRetryFailed": delivery_retry_failed,
        "deliveryInProgress": delivery_in_progress,
        "deliveryNotRecoverable": delivery_not_recoverable,
        "deliveryDeferredHistorical": delivery_deferred_historical,
        "deliveryDeferredBackoff": delivery_deferred_backoff,
        "deliveryAttemptsExhausted": delivery_attempts_exhausted,
        "requestedJobId": requested_job_id,
        "requestedJobFound": requested_job_found if requested_job_id else None,
    }


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in job.items()
        if key not in {
            "promptPath", "resultPath", "promptSignature", "dedupeKey",
            "lastError", "leaseToken", "workerPid", "deliveryRecoveryToken",
            "deliveryRecoveryPid",
        }
    }


def job_status(job_id: str) -> dict[str, Any]:
    with state_lock():
        state = read_json(STATE_PATH, {"jobs": {}})
        job = (state.get("jobs") or {}).get(job_id)
        if not isinstance(job, dict):
            return {"ok": False, "error": "unknown job"}
        result_path = str(job.get("resultPath") or "").strip()
        return {
            "ok": True,
            "job": public_job(job),
            "resultReady": bool(result_path and Path(result_path).is_file()),
        }


def take_result(job_id: str) -> dict[str, Any]:
    with state_lock():
        state = read_json(STATE_PATH, {"jobs": {}})
        job = (state.get("jobs") or {}).get(job_id)
        if not isinstance(job, dict):
            return {"ok": False, "error": "unknown job"}
        if job.get("status") not in {"done", "failed"}:
            return {"ok": False, "error": "result not ready", "status": job.get("status")}
        output, saved_error = verified_saved_result(job_id, job)
        if output is None:
            return {"ok": False, "error": saved_error or "result file unavailable"}
        delivery_pending = not bool(job.get("delivered"))
        origin = job.get("origin") or {}
        recovery_eligible = bool(
            delivery_pending
            and isinstance(origin, dict)
            and str(origin.get("cardKey") or "")
        )
        result_path = expected_result_path(job_id, job)
        if delivery_pending:
            # Inspection must not consume the only artifact a safe delivery
            # retry can use. Repeated take-result calls therefore return the
            # same verified output while delivery remains pending.
            if not job.get("deliveryRecoveryReferenceAt"):
                reference = delivery_recovery_reference(job)
                if reference is not None:
                    job["deliveryRecoveryReferenceAt"] = reference.isoformat().replace("+00:00", "Z")
            job["resultInspectedAt"] = job.get("resultInspectedAt") or utc_now()
        else:
            if result_path is not None:
                result_path.unlink(missing_ok=True)
            job["resultTakenAt"] = job.get("resultTakenAt") or utc_now()
        job["updatedAt"] = utc_now()
        save_json(STATE_PATH, state)
        return {
            "ok": True,
            "job": public_job(job),
            "output": output,
            "deliveryPending": delivery_pending,
            "deliveryRecoveryEligible": recovery_eligible,
            "resultRetained": delivery_pending,
        }


def display_width(value: str) -> int:
    total = 0
    text = str(value or "")
    for index, char in enumerate(text):
        if char == "\u200d" or unicodedata.combining(char) or unicodedata.category(char) in {"Cf", "Mn", "Me"}:
            continue
        if index + 1 < len(text) and text[index + 1] == "\ufe0f":
            total += 2
        else:
            total += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return total


def fixed_width_lines(value: str, *, width: int = 38, subsequent_indent: str = "   ") -> list[str]:
    pending = str(value or "")
    lines: list[str] = []
    first = True
    while pending:
        candidate = pending if first else f"{subsequent_indent}{pending.lstrip()}"
        if display_width(candidate) <= width:
            lines.append(candidate.rstrip())
            break
        used = 0
        cut = 0
        for index, char in enumerate(candidate):
            next_char = candidate[index + 1] if index + 1 < len(candidate) else ""
            char_width = 0 if char == "\u200d" or unicodedata.combining(char) or unicodedata.category(char) in {"Cf", "Mn", "Me"} else (2 if next_char == "\ufe0f" or unicodedata.east_asian_width(char) in {"W", "F"} else 1)
            if used + char_width > width:
                break
            used += char_width
            cut = index + 1
        whitespace = candidate.rfind(" ", len(subsequent_indent) if not first else 0, cut)
        if whitespace > (len(subsequent_indent) if not first else 0):
            cut = whitespace
        lines.append(candidate[:cut].rstrip())
        pending = candidate[cut:].lstrip()
        first = False
    return lines or [""]


def format_final(args: argparse.Namespace) -> str:
    complete = "Yes" if args.complete else "No"
    status = "COMPLETE" if args.complete else "NEEDS ATTENTION"

    def bullets(items: list[str], fallback: str) -> list[str]:
        values = items or [fallback]
        return [f"• {html.escape(str(item))}" for item in values]

    # Use only Bot API-supported HTML tags here. The live card uses native
    # Rich Messages; the result remains a proportional, readable message even
    # on clients that do not yet render the richer block vocabulary.
    lines = [
        f"<b>JOSH 2.0 · {status}</b>",
        f"<code>{html.escape(f'Model: {args.model} | Route: {args.route} | Why: {args.why}')}</code>",
        "",
        f"<blockquote><b>Complete:</b> {complete}</blockquote>",
        "",
        "<b>What was done:</b>",
        *bullets(args.done, "Detailed findings were not captured."),
        "",
        "<b>Issues:</b>",
        *bullets(args.issue, "None"),
        "",
        "<b>Appropriate next steps:</b>",
        *bullets(args.next, "No action needed."),
        "",
        "<b>Approval needed:</b>",
        *bullets(args.approval, "None"),
    ]
    return "\n".join(lines)


def parse_health(raw: str) -> dict[str, bool] | None:
    if not raw:
        return None
    return {key: bool(value) for key, value in json.loads(raw).items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Josh 2.0 Inbox coordinator.")
    sub = parser.add_subparsers(dest="command", required=True)

    route_p = sub.add_parser("route")
    route_p.add_argument("--prompt")
    route_p.add_argument("--prompt-file")
    route_p.add_argument("--privacy", default="dashboard-safe")
    route_p.add_argument("--health-json", default="")
    route_p.add_argument("--telemetry", action="store_true")

    submit_p = sub.add_parser("submit")
    submit_p.add_argument("--prompt")
    submit_p.add_argument("--prompt-file")
    submit_p.add_argument("--privacy", default="dashboard-safe")
    submit_p.add_argument("--origin-run-id", default="")
    submit_p.add_argument("--message-id", default="")
    submit_p.add_argument("--card-key", default="")
    submit_p.add_argument("--chat-id", default="")
    submit_p.add_argument("--thread-id", default="")
    submit_p.add_argument("--route-plan-json", default="")
    submit_p.add_argument("--work-id", default="")
    submit_p.add_argument("--work-run-id", default="")
    submit_p.add_argument("--origin-claim-hash", default="")
    submit_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    submit_p.add_argument("--dry-run", action="store_true")

    worker_p = sub.add_parser("worker")
    worker_p.add_argument("--job-id", required=True)

    status_p = sub.add_parser("status")
    status_p.add_argument("--job-id", required=True)

    result_p = sub.add_parser("take-result")
    result_p.add_argument("--job-id", required=True)

    cleanup_p = sub.add_parser("cleanup")
    cleanup_p.add_argument("--max-age-seconds", type=int, default=24 * 60 * 60)
    cleanup_p.add_argument(
        "--include-queued",
        action="store_true",
        help="also cancel and scrub queued jobs older than the retention window",
    )
    cleanup_p.add_argument("--dry-run", action="store_true")

    recover_p = sub.add_parser("recover")
    recover_p.add_argument("--job-id", default="")

    final_p = sub.add_parser("format-final")
    final_p.add_argument("--model", required=True)
    final_p.add_argument("--route", required=True)
    final_p.add_argument("--why", required=True)
    final_p.add_argument("--complete", action="store_true")
    final_p.add_argument("--done", action="append", default=[])
    final_p.add_argument("--issue", action="append", default=[])
    final_p.add_argument("--next", action="append", default=[])
    final_p.add_argument("--approval", action="append", default=[])

    args = parser.parse_args()
    if args.command == "route":
        prompt = load_prompt(args)
        result = route_prompt(prompt, args.privacy, parse_health(args.health_json))
        if args.telemetry:
            append_telemetry(result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "submit":
        print(json.dumps(submit_job(args), indent=2, sort_keys=True))
        return 0
    if args.command == "worker":
        print(json.dumps(run_worker(args.job_id), indent=2, sort_keys=True))
        return 0
    if args.command == "status":
        print(json.dumps(job_status(args.job_id), indent=2, sort_keys=True))
        return 0
    if args.command == "take-result":
        print(json.dumps(take_result(args.job_id), indent=2, sort_keys=True))
        return 0
    if args.command == "cleanup":
        cleanup_result = cleanup(args.max_age_seconds, args.include_queued, args.dry_run)
        print(json.dumps(cleanup_result, indent=2, sort_keys=True))
        return 0 if cleanup_result.get("ok") else 1
    if args.command == "recover":
        print(json.dumps(recover(args.job_id), indent=2, sort_keys=True))
        return 0
    if args.command == "format-final":
        print(format_final(args))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
