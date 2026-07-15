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
PRIVATE_DIR = Path(os.environ.get("JOSH_INBOX_COORDINATOR_PRIVATE_DIR", str(Path.home() / ".openclaw" / "private" / "inbox-coordinator")))
STATE_PATH = PRIVATE_DIR / "jobs.json"
LOCK_PATH = PRIVATE_DIR / "jobs.lock"
TELEMETRY_PATH = Path(os.environ.get("JOSH_INBOX_COORDINATOR_TELEMETRY", str(ROOT / "data" / "inbox-coordinator-telemetry.jsonl")))
CONTROL_TOWER_AGENT = "josh2"
DEFAULT_TIMEOUT_SECONDS = 900
MAX_RETRIES = 1
STALE_CARD_SECONDS = 10 * 60
DEDUPE_WINDOW_SECONDS = 10 * 60
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
WORKER_OUTPUT_CONTRACT = """Return a concise structured result using exactly these plain-text sections in this order: Complete: Yes or No plus whether the objective was completed; What was done: 3-5 tight bullets; Issues: bullets or n/a; Appropriate next steps: one useful next action or No action needed.; Approval needed: one approval bullet per issue when approval is genuinely required, otherwise n/a. Do not include a Model line or claim a provider, model, host, worker, route, or latency; the verified delivery layer adds those facts. Never repeat or reveal passwords, tokens, API keys, cookies, OAuth payloads, or other secret values."""
FINAL_SECTION_LABELS = {
    "complete": "Complete:",
    "done": "What was done:",
    "issues": "Issues:",
    "next": "Appropriate next steps:",
    "approval": "Approval needed:",
}


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


def publish_control_tower(title: str, status: str, detail: str) -> None:
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
        "--brain-feed",
    ]
    try:
        subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


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
            complete = not bool(re.search(r"\b(no|false|incomplete|blocked)\b", lower))
            complete_declared = True
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

    if not sections["done"]:
        sections["done"] = loose[:5] or ["Completed the requested Inbox worker task."]
    while len(sections["done"]) < 3:
        additions = [
            "Verified the worker execution completed.",
            "Prepared the result for deterministic Telegram delivery.",
        ]
        candidate = additions[len(sections["done"]) - 1]
        if candidate not in sections["done"]:
            sections["done"].append(candidate)
        else:
            break
    sections["done"] = sections["done"][:5]
    sections["issues"] = [item for item in sections["issues"] if item.lower() not in {"n/a", "na", "none"}][:5]
    sections["next"] = [item for item in sections["next"] if item.lower() not in {"n/a", "na", "none"}][:5]
    sections["approval"] = [item for item in sections["approval"] if item.lower() not in {"n/a", "na", "none"}][:5]
    if not complete_declared:
        sections["issues"].append("Worker did not return a verifiable completion status.")
    if not sections["next"]:
        sections["next"] = ["No action needed."] if complete and not sections["issues"] else ["Review the listed issue before retrying."]
    return {"complete": complete, "completeDeclared": complete_declared, **sections}


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


def make_job(prompt: str, route: dict[str, Any], origin: dict[str, str], timeout: int) -> tuple[dict[str, Any], bool]:
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
    job, deduplicated = make_job(prompt, route, origin, args.timeout)
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
    publish_control_tower("Josh 2.0 Inbox worker queued", "active", f"{route['worker']} on {route['host']}; {route['routingReason']}")
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
    task_complete = bool(execution.get("executionVerified") and sections.get("complete"))
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
        task_complete = bool(execution.get("executionVerified") and parse_model_sections(output).get("complete"))
        outcome = "done" if task_complete else "failed"
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
                outcome = "done"
        else:
            failure_execution = {
                "actualHost": route.get("host") or "unverified",
                "actualWorker": route.get("worker") or "unverified",
                "actualProvider": "unverified",
                "actualModel": "unverified",
                "modelVerified": False,
                "executionVerified": False,
            }
            deliver_result(
                job_id,
                snapshot,
                route,
                failure_execution,
                "I couldn't complete that request because the selected worker stopped before producing a verified result. Please retry once that route is healthy; no successful model execution was claimed.",
            )

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
        "Josh 2.0 Inbox worker finished" if outcome == "done" else "Josh 2.0 Inbox worker issue",
        "done" if outcome == "done" else "error",
        f"{route.get('worker')} outcome={outcome}; executed={model_executed}; delivered={delivered}; latency={latency_ms}ms",
    )
    return {"ok": outcome in {"done", "retry"}, "jobId": job_id, "outcome": outcome, "latencyMs": latency_ms}


def cleanup(max_age_seconds: int, include_queued: bool = False) -> dict[str, Any]:
    with state_lock():
        state = read_json(STATE_PATH, {"jobs": {}})
        jobs = state.get("jobs") if isinstance(state, dict) else {}
        if not isinstance(jobs, dict):
            return {"ok": True, "removed": 0}
        now = dt.datetime.now(dt.timezone.utc)
        removed = 0
        for job_id, job in list(jobs.items()):
            updated_dt = parse_utc((job or {}).get("updatedAt")) or now
            stale = (now - updated_dt).total_seconds() > max_age_seconds
            removable_statuses = {"done", "failed", "queued"} if include_queued else {"done", "failed"}
            if stale and str((job or {}).get("status") or "") in removable_statuses:
                for field in ("promptPath", "resultPath"):
                    try:
                        Path(str(job.get(field) or "")).unlink(missing_ok=True)
                    except Exception:
                        pass
                jobs.pop(job_id, None)
                removed += 1
        save_json(STATE_PATH, state)
    return {"ok": True, "removed": removed}


def recover() -> dict[str, Any]:
    to_spawn: list[str] = []
    left_running = 0
    with state_lock():
        state = read_json(STATE_PATH, {"jobs": {}})
        jobs = state.get("jobs") if isinstance(state, dict) else {}
        if isinstance(jobs, dict):
            for job_id, job in jobs.items():
                if not isinstance(job, dict):
                    continue
                status = job.get("status")
                if status == "running":
                    pid = int(job.get("workerPid") or 0)
                    alive = False
                    if pid > 0:
                        try:
                            os.kill(pid, 0)
                            alive = True
                        except OSError:
                            alive = False
                    if alive:
                        left_running += 1
                        continue
                    if int(job.get("attempt") or 0) >= int(job.get("maxRetries") or 0) + 1:
                        job["status"] = "failed"
                        job["updatedAt"] = utc_now()
                        job["lastError"] = "dead worker exhausted its retry budget"
                        job.pop("workerPid", None)
                        job.pop("leaseToken", None)
                        continue
                    job["status"] = "queued"
                    job.pop("workerPid", None)
                    job.pop("leaseToken", None)
                if job.get("status") == "queued":
                    if int(job.get("attempt") or 0) >= int(job.get("maxRetries") or 0) + 1:
                        job["status"] = "failed"
                        job["updatedAt"] = utc_now()
                        job["lastError"] = "queued recovery exhausted its retry budget"
                        continue
                    job["updatedAt"] = utc_now()
                    to_spawn.append(job_id)
            save_json(STATE_PATH, state)
    for job_id in to_spawn:
        spawn_worker(job_id)
    return {"ok": True, "recovered": len(to_spawn), "leftRunning": left_running}


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in job.items()
        if key not in {
            "promptPath", "resultPath", "promptSignature", "dedupeKey",
            "lastError", "leaseToken", "workerPid",
        }
    }


def job_status(job_id: str) -> dict[str, Any]:
    with state_lock():
        state = read_json(STATE_PATH, {"jobs": {}})
        job = (state.get("jobs") or {}).get(job_id)
        if not isinstance(job, dict):
            return {"ok": False, "error": "unknown job"}
        return {"ok": True, "job": public_job(job), "resultReady": Path(str(job.get("resultPath") or "")).exists()}


def take_result(job_id: str) -> dict[str, Any]:
    with state_lock():
        state = read_json(STATE_PATH, {"jobs": {}})
        job = (state.get("jobs") or {}).get(job_id)
        if not isinstance(job, dict):
            return {"ok": False, "error": "unknown job"}
        if job.get("status") != "done":
            return {"ok": False, "error": "result not ready", "status": job.get("status")}
        result_path = Path(str(job.get("resultPath") or ""))
        try:
            output = result_path.read_text(encoding="utf-8")
        except Exception:
            return {"ok": False, "error": "result file unavailable"}
        result_path.unlink(missing_ok=True)
        job["resultTakenAt"] = utc_now()
        job["updatedAt"] = utc_now()
        save_json(STATE_PATH, state)
        return {"ok": True, "job": public_job(job), "output": output}


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
    lines = [
        f"Model: {args.model} | Route: {args.route} | Why: {args.why}",
        "",
        f"Complete: {complete}",
        "",
        "What was done:",
        *[f"- {item}" for item in args.done],
        "",
        "Issues:",
        *([f"- {item}" for item in args.issue] if args.issue else ["- n/a"]),
        "",
        "Appropriate next steps:",
        *([f"- {item}" for item in args.next] if args.next else ["- No action needed."]),
        "",
        "Approval needed:",
        *([f"- {item}" for item in args.approval] if args.approval else ["- n/a"]),
    ]
    wrapped: list[str] = []
    for line in lines:
        if not line:
            wrapped.append("")
            continue
        subsequent = "  " if line.startswith("- ") else "   "
        wrapped.extend(fixed_width_lines(line, width=38, subsequent_indent=subsequent))
    return f"<pre>{html.escape(chr(10).join(wrapped))}</pre>"


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
    cleanup_p.add_argument("--include-queued", action="store_true")

    sub.add_parser("recover")

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
        print(json.dumps(cleanup(args.max_age_seconds, args.include_queued), indent=2, sort_keys=True))
        return 0
    if args.command == "recover":
        print(json.dumps(recover(), indent=2, sort_keys=True))
        return 0
    if args.command == "format-final":
        print(format_final(args))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
