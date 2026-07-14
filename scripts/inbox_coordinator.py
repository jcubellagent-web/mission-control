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
import hashlib
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
PRIVATE_DIR = Path(os.environ.get("JOSH_INBOX_COORDINATOR_PRIVATE_DIR", str(Path.home() / ".openclaw" / "private" / "inbox-coordinator")))
STATE_PATH = PRIVATE_DIR / "jobs.json"
TELEMETRY_PATH = Path(os.environ.get("JOSH_INBOX_COORDINATOR_TELEMETRY", str(ROOT / "data" / "inbox-coordinator-telemetry.jsonl")))
CONTROL_TOWER_AGENT = "josh2"
DEFAULT_TIMEOUT_SECONDS = 900
MAX_RETRIES = 1
STALE_CARD_SECONDS = 10 * 60

SECRET_WORDS = re.compile(
    r"(password|passwd|secret|token|api[_ -]?key|oauth|cookie|session|bearer|private key|seed phrase)",
    re.I,
)


ROUTES: dict[str, dict[str, Any]] = {
    "luna": {
        "provider": "codex",
        "model": "gpt-5.6-luna",
        "tier": "codex-luna",
        "worker": "josh2-codex-luna",
        "host": "josh2",
        "role": "fast coordinator / quick execution",
    },
    "terra": {
        "provider": "codex",
        "model": "gpt-5.6-terra",
        "tier": "codex",
        "worker": "josh2-codex-terra",
        "host": "josh2",
        "role": "default trusted execution",
    },
    "sol": {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "tier": "codex-sol",
        "worker": "josh2-codex-sol",
        "host": "josh2",
        "role": "hard integration / escalation",
    },
    "gemini": {
        "provider": "gemini",
        "model": "gemini-3.5-flash",
        "tier": "fast",
        "worker": "jaimes-gemini-review",
        "host": "jaimes",
        "role": "low-cost summary/review",
    },
    "gemini-pro": {
        "provider": "gemini",
        "model": "gemini-3.1-pro-preview",
        "tier": "reason",
        "worker": "jaimes-gemini-pro",
        "host": "jaimes",
        "role": "large-context review/reasoning",
    },
    "jaimes": {
        "provider": "jaimes",
        "model": "jaimes-workhorse",
        "tier": "delegate",
        "worker": "jaimes-hermes-workhorse",
        "host": "jaimes",
        "role": "explicit delegated workhorse",
    },
    "glm": {
        "provider": "ollama",
        "model": "glm-5.2",
        "tier": "local",
        "worker": "josh2-ollama-glm",
        "host": "josh2",
        "role": "local/private draft or offline fallback",
    },
    "ollama": {
        "provider": "ollama",
        "model": "local",
        "tier": "local",
        "worker": "josh2-ollama-local",
        "host": "josh2",
        "role": "local/private draft or offline fallback",
    },
    "grok": {
        "provider": "xai",
        "model": "grok-4-fast-non-reasoning",
        "tier": "grok-fast",
        "worker": "jaimes-grok-public",
        "host": "jaimes",
        "role": "X/current-events specialist",
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


def read_json(path: Path, fallback: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except Exception:
        return fallback


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


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


def detect_explicit_route(prompt: str) -> str:
    lower = " ".join((prompt or "").lower().split())
    for alias, route in sorted(MODEL_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b(use|run|route|ask|with|via|on|delegate to|send to)?\s*{re.escape(alias)}\b", lower):
            return route
    return ""


def classify_route(prompt: str, privacy: str) -> tuple[str, str]:
    lower = (prompt or "").lower()
    if privacy != "dashboard-safe" or contains_sensitive_terms(prompt):
        return "luna", "private/sensitive content stays on Josh 2.0 coordinator lane"
    if any(token in lower for token in ("current event", "latest news", "x/twitter", "social signal", "market narrative")):
        return "grok", "public current-events/social signal request"
    if any(token in lower for token in ("review", "summarize", "summary", "digest", "large context", "read this")):
        return "gemini", "dashboard-safe review/summarization"
    if any(token in lower for token in ("hard", "stabilize", "architecture", "migration", "integration", "debug", "root cause")):
        return "terra", "trusted execution/integration"
    return "luna", "fast Inbox coordination"


def health(route_id: str, injected: dict[str, bool] | None = None) -> bool:
    if injected is not None and route_id in injected:
        return bool(injected[route_id])
    if route_id in {"luna", "terra", "sol"}:
        return shutil.which("codex") is not None and (Path.home() / ".codex" / "config.toml").exists()
    if route_id in {"gemini", "gemini-pro"}:
        return (Path.home() / "bin" / "gemini").exists() or shutil.which("gemini") is not None
    if route_id == "jaimes":
        return shutil.which("ssh") is not None
    if route_id in {"ollama", "glm"}:
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1) as resp:
                return resp.status < 500
        except Exception:
            return False
    if route_id == "grok":
        return bool(os.environ.get("XAI_API_KEY") or os.environ.get("LLM_ROUTER_XAI_KEY_REF") or shutil.which("op"))
    return False


def fallback_for(route_id: str, privacy: str, injected: dict[str, bool] | None = None) -> tuple[str, str]:
    candidates = ["luna", "terra", "gemini", "ollama"]
    if privacy != "dashboard-safe":
        candidates = ["luna", "terra", "ollama"]
    for candidate in candidates:
        if candidate != route_id and health(candidate, injected):
            return candidate, f"{route_id} unhealthy; selected {candidate}"
    return route_id, f"{route_id} unhealthy and no healthier fallback found"


def route_prompt(prompt: str, privacy: str = "dashboard-safe", injected_health: dict[str, bool] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    explicit = detect_explicit_route(prompt)
    if explicit:
        route_id = explicit
        reason = "explicit model request"
    else:
        route_id, reason = classify_route(prompt, privacy)

    requested_healthy = health(route_id, injected_health)
    fallback = ""
    selected = route_id
    if explicit and not requested_healthy:
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
        "provider": cfg["provider"],
        "model": cfg["model"],
        "tier": cfg["tier"],
        "worker": cfg["worker"],
        "host": cfg["host"],
        "role": cfg["role"],
        "routingReason": reason,
        "fallback": fallback,
        "privacy": privacy,
        "latencyMs": latency_ms,
        "outcome": "routed",
    }


def append_telemetry(record: dict[str, Any]) -> None:
    safe = {
        "timestamp": utc_now(),
        "sourceAgent": CONTROL_TOWER_AGENT,
        "host": record.get("host") or socket.gethostname(),
        "worker": record.get("worker"),
        "provider": record.get("provider"),
        "model": record.get("model"),
        "routeId": record.get("routeId"),
        "requestedRouteId": record.get("requestedRouteId") or "",
        "explicitRequest": bool(record.get("explicitRequest")),
        "routingReason": record.get("routingReason"),
        "fallback": record.get("fallback") or "",
        "latencyMs": record.get("latencyMs"),
        "outcome": record.get("outcome"),
        "jobId": record.get("jobId") or "",
        "promptSignature": record.get("promptSignature") or "",
        "attempt": record.get("attempt"),
        "telemetryPolicy": "no raw prompts, model output, secrets, OAuth payloads, cookies, tokens, raw emails, or private account contents",
    }
    TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TELEMETRY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe, sort_keys=True) + "\n")


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
        subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12, check=False)
    except Exception:
        pass


def make_job(prompt: str, route: dict[str, Any], origin: dict[str, str], timeout: int) -> dict[str, Any]:
    ensure_private_dir()
    job_id = hashlib.sha256(f"{time.time()}:{prompt_signature(prompt)}".encode("utf-8")).hexdigest()[:20]
    prompt_path = PRIVATE_DIR / f"{job_id}.prompt"
    result_path = PRIVATE_DIR / f"{job_id}.result"
    prompt_path.write_text(prompt, encoding="utf-8")
    prompt_path.chmod(0o600)
    job = {
        "jobId": job_id,
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "status": "queued",
        "attempt": 0,
        "maxRetries": MAX_RETRIES,
        "timeoutSeconds": timeout,
        "promptPath": str(prompt_path),
        "resultPath": str(result_path),
        "origin": origin,
        "route": {key: route.get(key) for key in ("routeId", "provider", "model", "tier", "worker", "host", "routingReason", "fallback")},
        "promptSignature": prompt_signature(prompt),
    }
    state = read_json(STATE_PATH, {"jobs": {}})
    state.setdefault("jobs", {})[job_id] = job
    save_json(STATE_PATH, state)
    return job


def submit_job(args: argparse.Namespace) -> dict[str, Any]:
    prompt = load_prompt(args)
    route = route_prompt(prompt, args.privacy)
    origin = {
        "runId": args.origin_run_id or "",
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
    job = make_job(prompt, route, origin, args.timeout)
    telemetry = {**route, "jobId": job["jobId"], "promptSignature": job["promptSignature"], "attempt": 0, "outcome": "queued"}
    append_telemetry(telemetry)
    if not args.dry_run:
        cmd = [sys.executable, str(Path(__file__).resolve()), "worker", "--job-id", job["jobId"]]
        subprocess.Popen(cmd, cwd=WORKSPACE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    publish_control_tower("Josh 2.0 Inbox worker queued", "active", f"{route['worker']} on {route['host']}; {route['routingReason']}")
    return {"ok": True, "job": {k: v for k, v in job.items() if k not in {"promptPath", "resultPath"}}, "route": route}


def run_worker(job_id: str) -> dict[str, Any]:
    ensure_private_dir()
    state = read_json(STATE_PATH, {"jobs": {}})
    job = (state.get("jobs") or {}).get(job_id)
    if not isinstance(job, dict):
        return {"ok": False, "error": "unknown job"}
    route = job.get("route") or {}
    prompt_path = Path(job.get("promptPath") or "")
    result_path = Path(job.get("resultPath") or "")
    started = time.perf_counter()
    job["status"] = "running"
    job["attempt"] = int(job.get("attempt") or 0) + 1
    job["updatedAt"] = utc_now()
    save_json(STATE_PATH, state)
    try:
        prompt = prompt_path.read_text(encoding="utf-8")
        try:
            prompt_path.unlink(missing_ok=True)
        except Exception:
            pass
        sys.path.insert(0, str(WORKSPACE / "scripts"))
        from llm_router import ask  # type: ignore

        output = ask(prompt, tier=str(route.get("tier") or "codex-luna"), timeout=int(job.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS))
        result_path.write_text(output, encoding="utf-8")
        result_path.chmod(0o600)
        job["status"] = "done"
        outcome = "done"
    except Exception as exc:  # noqa: BLE001
        job["lastError"] = str(exc)[-500:]
        if int(job.get("attempt") or 0) <= int(job.get("maxRetries") or 0):
            job["status"] = "queued"
            outcome = "retry"
        else:
            job["status"] = "failed"
            outcome = "failed"
    job["updatedAt"] = utc_now()
    latency_ms = max(0, round((time.perf_counter() - started) * 1000))
    save_json(STATE_PATH, state)
    append_telemetry({
        **route,
        "jobId": job_id,
        "promptSignature": job.get("promptSignature"),
        "attempt": job.get("attempt"),
        "latencyMs": latency_ms,
        "outcome": outcome,
    })
    if outcome == "retry":
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "worker", "--job-id", job_id], cwd=WORKSPACE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    publish_control_tower("Josh 2.0 Inbox worker finished" if outcome == "done" else "Josh 2.0 Inbox worker issue", "done" if outcome == "done" else "error", f"{route.get('worker')} outcome={outcome}; latency={latency_ms}ms")
    return {"ok": outcome in {"done", "retry"}, "jobId": job_id, "outcome": outcome, "latencyMs": latency_ms}


def cleanup(max_age_seconds: int, include_queued: bool = False) -> dict[str, Any]:
    state = read_json(STATE_PATH, {"jobs": {}})
    jobs = state.get("jobs") if isinstance(state, dict) else {}
    if not isinstance(jobs, dict):
        return {"ok": True, "removed": 0}
    now = dt.datetime.now(dt.timezone.utc)
    removed = 0
    for job_id, job in list(jobs.items()):
        updated = str((job or {}).get("updatedAt") or "")
        try:
            updated_dt = dt.datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except Exception:
            updated_dt = now
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
    state = read_json(STATE_PATH, {"jobs": {}})
    jobs = state.get("jobs") if isinstance(state, dict) else {}
    recovered = 0
    if isinstance(jobs, dict):
        for job_id, job in jobs.items():
            if isinstance(job, dict) and job.get("status") in {"queued", "running"}:
                job["status"] = "queued"
                job["updatedAt"] = utc_now()
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "worker", "--job-id", job_id], cwd=WORKSPACE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                recovered += 1
        save_json(STATE_PATH, state)
    return {"ok": True, "recovered": recovered}


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
    return f"<pre>{html.escape(chr(10).join(lines))}</pre>"


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
    submit_p.add_argument("--card-key", default="")
    submit_p.add_argument("--chat-id", default="")
    submit_p.add_argument("--thread-id", default="")
    submit_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    submit_p.add_argument("--dry-run", action="store_true")

    worker_p = sub.add_parser("worker")
    worker_p.add_argument("--job-id", required=True)

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
        result["promptSignature"] = prompt_signature(prompt)
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
