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
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from objective_quality import current_request_text as objective_current_request_text  # type: ignore  # noqa: E402
RUNTIME_PROBE_SCRIPT = ROOT / "scripts" / "ecosystem_runtime_probe.py"
#JAIMES: Inbox cards must use the host helper beside send_josh_reply.py so live Telegram sends keep their configured Bot API lane.
WORK_CARD_SCRIPT = WORKSPACE / "scripts" / "josh_work_card.py"
SEND_REPLY_SCRIPT = WORK_CARD_SCRIPT.with_name("send_josh_reply.py")
TELEGRAM_GATEWAY_SCRIPT = ROOT / "scripts" / "josh_telegram_fast_ack.py"
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
WORKER_OUTPUT_CONTRACT = """Return a concise structured result using exactly these plain-text sections in this order: Complete: Yes or No plus whether the objective was completed; What was done: 3-5 unique, source-supported bullets that state concrete findings, outcomes, or changes; Issues: bullets or n/a; Appropriate next steps: one evidence-based recommendation or next action; Approval needed: one approval bullet per issue when approval is genuinely required, otherwise n/a. For an assessment, review, or research request, surface the key findings and recommendation instead of merely saying the assessment or review finished. Generic process statements such as task complete, execution verified, result prepared, or summary delivered are not findings and must not be used to fill the bullets. Put every reported risk or limitation in Issues as well as the relevant finding. Use No action needed only when the findings explicitly support that conclusion and there are no issues, risks, limitations, approvals, or recommendations to act on. The coordinator has already selected and launched the current route: do not launch another provider/model, use SSH to test another host, or infer a fallback. This five-section schema is the private worker-to-delivery handoff, not the complete user-facing Telegram contract. Do not include a Model line or claim a provider, model, authentication method, host, worker, route, fallback, or latency in this worker handoff; the verified delivery layer is required to prepend the user-facing final with Model, Route, and Why. When auditing Telegram response behavior, do not treat that required delivery header as a formatter defect or as a violation of this worker-only rule. Never repeat or reveal passwords, tokens, API keys, cookies, OAuth payloads, or other secret values."""
QUICK_WORKER_OUTPUT_CONTRACT = """Return a concise structured result using exactly these plain-text sections in this order: Complete: Yes or No; What was done: 1-3 brief bullets containing the direct answer or conversational result; Issues: bullets or n/a; Appropriate next steps: one brief next action or n/a; Approval needed: a bullet only when approval is genuinely required, otherwise n/a. Do not pad a quick answer with invented findings. Do not include a Model line or claim a provider, model, host, worker, route, or latency; the verified delivery layer adds those facts. Never repeat or reveal passwords, tokens, API keys, cookies, OAuth payloads, or other secret values."""
TELEGRAM_HEALTH_EVIDENCE_UNAVAILABLE_RESULT = """Complete: No — current Telegram health could not be verified.
What was done:
- The coordinator requested the host-native read-only runtime probe.
- The probe did not return a parseable allowlisted service snapshot.
- No sandbox-local failure was treated as evidence of a service outage.
Issues:
- Current gateway and fast-ack health remain unverified.
Appropriate next steps:
- Retry after the host-native read-only runtime probe is available.
Approval needed:
- n/a"""
FINAL_SECTION_LABELS = {
    "complete": "Complete:",
    "done": "What was done:",
    "issues": "Issues:",
    "next": "Appropriate next steps:",
    "approval": "Approval needed:",
}

EMPTY_FINAL_ITEMS = frozenset({"n/a", "na", "none"})
MISSING_FINDINGS_ISSUE = "The reported evidence was not sufficient to verify the result as complete."
RETRY_FINDINGS_NEXT_STEP = "Retry after collecting the missing evidence or findings."
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
WORKER_META_COMPLIANCE_RE = re.compile(
    r"^(?:followed|used|kept|omitted|included|formatted|structured)\b.{0,180}\b"
    r"(?:requested\s+(?:section|format)|section\s+order|plain[- ]text\s+format|"
    r"prohibited\s+model\s+line|response\s+(?:concise|structured)|"
    r"final\s+summary\s+template|approval\s+status)\b",
    re.I,
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
    r"compar(?:e|es|ed)|estimat(?:e|es|ed)|measur(?:e|es|ed)|validat(?:e|es|ed)|"
    r"select(?:s|ed)?|reserv(?:e|es|ed)|occur(?:s|red)?|"
    r"rout(?:e|es|ed|ing)|authenticat(?:e|es|ed|ion)|fallback|quota|allowance)\b",
    re.I,
)
CONCRETE_EVIDENCE_SIGNAL = re.compile(
    r"(?:https?://|(?:^|\s)/[A-Za-z0-9_.-]+/|\b[A-Za-z0-9_.-]+\.(?:py|js|ts|json|md|ya?ml)\b|"
    r"\b\d+(?:\.\d+)?\s*(?:%|ms|seconds?|minutes?|hours?|bytes?|kb|mb|gb|tests?|checks?|cases?|errors?|messages?)\b|"
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}\b)",
    re.I,
)
OPERATIONAL_RESULT_SIGNAL = re.compile(
    r"(?:\b(?:gateway|service|daemon|watcher|process|socket|connection|endpoint|api|launchd|"
    r"runtime|bot|helper|logs?|files?|source|entry|entries|inventory|messages?|delivery)\b.{0,100}"
    r"\b(?:running|listening|connected|reachable|responding|registered|loaded|active|healthy|"
    r"stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified|"
    r"last\s+modified|has\s+no|have\s+no|there\s+(?:is|are)\s+no|port\s+\d{2,5})\b|"
    r"\b(?:running|listening|connected|reachable|responding|registered|loaded|active|healthy|"
    r"stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified|"
    r"last\s+modified|has\s+no|have\s+no|there\s+(?:is|are)\s+no|port\s+\d{2,5})\b.{0,100}"
    r"\b(?:gateway|service|daemon|watcher|process|socket|connection|endpoint|api|launchd|"
    r"runtime|bot|helper|logs?|files?|source|entry|entries|inventory|messages?|delivery)\b)",
    re.I,
)
OPERATIONAL_STATUS_FILLER_SIGNAL = re.compile(
    r"\b(?:gateway|service|daemon|watcher|process|runtime|bot|helper|delivery)\s+"
    r"(?:(?:health|status|operational|connectivity|delivery)\s+){0,2}"
    r"(?:assessment|review|report|request|task|work)\s+(?:is\s+|was\s+|remains\s+)?"
    r"(?:active|running|connected|complete|completed|done|last\s+modified)\b",
    re.I,
)
OPERATIONAL_RISK_SIGNAL = re.compile(
    r"(?:\b(?:gateway|service|daemon|watcher|process|socket|connection|endpoint|api|launchd|"
    r"runtime|bot|helper|logs?|files?|source|entry|entries|inventory|messages?|delivery)\b.{0,100}"
    r"\b(?:not\s+(?:running|listening|connected|reachable|responding|registered|loaded|active|healthy)|"
    r"stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified|"
    r"has\s+no|have\s+no|there\s+(?:is|are)\s+no)\b|"
    r"\b(?:not\s+(?:running|listening|connected|reachable|responding|registered|loaded|active|healthy)|"
    r"stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified|"
    r"has\s+no|have\s+no|there\s+(?:is|are)\s+no)\b.{0,100}"
    r"\b(?:gateway|service|daemon|watcher|process|socket|connection|endpoint|api|launchd|"
    r"runtime|bot|helper|logs?|files?|source|entry|entries|inventory|messages?|delivery)\b)",
    re.I,
)
NEGATED_OPERATIONAL_RISK_SIGNAL = re.compile(
    r"\b(?:no|not|without)\s+(?:\w+\s+){0,2}"
    r"(?:stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified)\b",
    re.I,
)
POSITIVE_OPERATIONAL_ABSENCE_SIGNAL = re.compile(
    r"\b(?:(?:has|have)\s+no|there\s+(?:is|are)\s+no)\s+"
    r"(?:remaining\s+)?(?:service\s+)?(?:issues?|failures?|errors?|problems?|risks?|blockers?)\b",
    re.I,
)
RISK_OR_LIMITATION_SIGNAL = re.compile(
    r"\b(?:risk|risks|risky|limitation|limitations|limited|cannot|can't|unable|unsupported|"
    r"not supported|could\s+not|does\s+not|did\s+not|do not|don't|avoid|"
    r"blocked|blocker|failed|failure|"
    r"warning|caution)\b",
    re.I,
)
ACTION_OR_RECOMMENDATION_SIGNAL = re.compile(
    r"\b(?:recommend(?:s|ed|ation)?|should|must|follow[- ]?up|next step|do not|don't|avoid|consider)\b",
    re.I,
)
NO_ACTION_SIGNAL = re.compile(r"^no (?:further )?actions? (?:(?:is|are) )?(?:needed|required)\b", re.I)
MODEL_ROUTING_AUDIT_SIGNAL = re.compile(
    r"(?:\b(?:model|provider|specialist)\b.{0,140}\b(?:route|routing|fallback|authentication|auth)\b|"
    r"\b(?:route|routing|fallback|authentication|auth)\b.{0,140}\b(?:model|provider|specialist)\b)",
    re.I | re.S,
)


class RouteExecutionError(RuntimeError):
    """Fail-closed execution error carrying only dashboard-safe route facts."""

    def __init__(self, message: str, route: dict[str, Any], attempts: list[str]):
        super().__init__(message)
        self.route = dict(route)
        self.attempts = list(attempts)
UNRESOLVED_EXECUTION_FAILURE_SIGNAL = re.compile(
    r"(?:\bno\s+(?:verified\s+)?(?:specialist|provider|model|route)\b.{0,80}"
    r"\b(?:completed|executed|ran|returned)\b|"
    r"\b(?:exact\s+)?(?:model|provider|specialist|authentication|auth)\b.{0,100}"
    r"\b(?:was not|were not|could not be|failed to be)\s+(?:executed|verified|initialized|run)\b|"
    r"\b(?:all|every|both|neither|none\s+of\s+the)\b.{0,80}"
    r"\b(?:specialists?|providers?|models?|routes?|fallbacks?)\b.{0,100}"
    r"\b(?:failed|unverified|could not be confirmed|failed to initialize)\b|"
    r"\b(?:final|required|selected)\s+(?:specialist|provider|model|route|fallback)\b.{0,100}"
    r"\b(?:failed|unverified|could not be confirmed|failed to initialize)\b)",
    re.I | re.S,
)
TELEGRAM_HEALTH_REQUEST = re.compile(
    r"(?:\btelegram\b.{0,80}\b(?:health|healthy|status|running|connectivity|delivery)\b|"
    r"\b(?:health|healthy|status|running|connectivity|delivery)\b.{0,80}\btelegram\b)",
    re.I | re.S,
)
TELEGRAM_RESPONSE_AUDIT_REQUEST = re.compile(
    r"(?:\b(?:audit|assess|check|evaluate|inspect|review|verify)\b.{0,100}"
    r"\btelegram\b.{0,100}\b(?:response|reply|behavior|behaviour|contract|format|lifecycle)\b|"
    r"\btelegram\b.{0,100}\b(?:response|reply|behavior|behaviour|contract|format|lifecycle)\b"
    r".{0,100}\b(?:audit|assess|check|evaluate|inspect|review|verify)\b)",
    re.I | re.S,
)
READ_ONLY_REQUEST = re.compile(
    r"\b(?:read[- ]only|make\s+no\s+(?:changes?|edits?|modifications?)|"
    r"no\s+(?:changes?|edits?|modifications?)|(?:do\s+not|don't)\s+"
    r"(?:make\s+(?:any\s+)?)?(?:changes?|edits?|modifications?|repairs?|restarts?)|"
    r"without\s+(?:making\s+(?:any\s+)?)?(?:changes?|edits?|modifications?))\b",
    re.I,
)
MUTATION_SIGNAL = re.compile(
    r"\b(?:fix|patch|changes?|edit|implement|deploy|repair|restart|recover|build|code)\b",
    re.I,
)
NEGATED_MUTATION_SIGNAL = re.compile(
    r"\b(?:make\s+no|no|do\s+not|don't|never|without)\s+(?:\w+\s+){0,2}"
    r"(?:changes?|edits?|modifications?|fixes?|patches?|deployments?|repairs?|restarts?)\b",
    re.I,
)


ROUTES: dict[str, dict[str, Any]] = {
    "luna": {
        "provider": "codex",
        "model": "gpt-5.6-luna",
        "auth": "OpenAI Codex OAuth/subscription",
        "tier": "codex-luna",
        "worker": "josh2-codex-luna",
        "host": "josh2",
        "role": "fast coordinator / quick execution",
        "executor": "local-codex",
    },
    "terra": {
        "provider": "codex",
        "model": "gpt-5.6-terra",
        "auth": "OpenAI Codex OAuth/subscription",
        "tier": "codex",
        "worker": "josh2-codex-terra",
        "host": "josh2",
        "role": "default trusted execution",
        "executor": "local-codex",
    },
    "sol": {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "auth": "OpenAI Codex OAuth/subscription",
        "tier": "codex-sol",
        "worker": "josh2-codex-sol",
        "host": "josh2",
        "role": "hard integration / escalation",
        "executor": "local-codex",
    },
    "gpt-5.5": {
        "provider": "codex",
        "model": "gpt-5.5",
        "auth": "OpenAI Codex OAuth/subscription",
        "tier": "codex-stable",
        "worker": "josh2-codex-gpt-5-5",
        "host": "josh2",
        "role": "stable compatibility execution",
        "executor": "local-codex",
    },
    "gpt-5.4-mini": {
        "provider": "codex",
        "model": "gpt-5.4-mini",
        "auth": "OpenAI Codex OAuth/subscription",
        "tier": "codex-mini",
        "worker": "josh2-codex-gpt-5-4-mini",
        "host": "josh2",
        "role": "economy bounded execution",
        "executor": "local-codex",
    },
    "gemini": {
        "provider": "gemini",
        "model": "gemini-3.6-flash-medium",
        "auth": "Antigravity session",
        "tier": "fast",
        "worker": "jaimes-gemini-review",
        "host": "jaimes",
        "role": "low-cost summary/review",
        "executor": "remote-antigravity",
    },
    "gemini-pro": {
        "provider": "gemini",
        "model": "gemini-3.1-pro-high",
        "auth": "Antigravity session",
        "tier": "reason",
        "worker": "jaimes-gemini-pro",
        "host": "jaimes",
        "role": "large-context review/reasoning",
        "executor": "remote-antigravity",
    },
    "jaimes": {
        "provider": "jaimes",
        "model": "jaimes-workhorse",
        "auth": "Hermes runtime authentication",
        "tier": "delegate",
        "worker": "jaimes-hermes-workhorse",
        "host": "jaimes",
        "role": "explicit delegated workhorse",
        "executor": "remote-hermes",
    },
    "glm": {
        "provider": "ollama",
        "model": "glm-5.2:cloud",
        "auth": "Ollama Cloud",
        "tier": "reason",
        "worker": "jaimes-ollama-glm",
        "host": "jaimes",
        "role": "sanitized large-context technical reasoning",
        "executor": "remote-ollama",
    },
    "ollama": {
        "provider": "ollama",
        "model": "local",
        "auth": "Local Ollama runtime",
        "tier": "local",
        "worker": "josh2-ollama-local",
        "host": "josh2",
        "role": "local/private draft or offline fallback",
        "executor": "local-ollama",
    },
    "grok": {
        "provider": "xai",
        "model": "grok-4.5",
        "auth": "Grok CLI authentication",
        "tier": "grok-fast",
        "worker": "jaimes-grok-public",
        "host": "jaimes",
        "role": "X/current-events specialist",
        "executor": "remote-grok-cli",
    },
}

FALLBACK_LADDERS: dict[str, tuple[str, ...]] = {
    "gemini": ("glm", "terra"),
    "gemini-pro": ("glm", "terra"),
    "glm": ("gemini-pro", "terra"),
    "grok": ("gemini", "terra"),
    "jaimes": ("glm", "terra"),
    "luna": ("terra",),
    "terra": ("sol", "luna"),
    "sol": ("terra", "luna"),
    "gpt-5.5": ("terra", "luna"),
    "gpt-5.4-mini": ("luna", "terra"),
    "ollama": ("luna", "terra"),
}

#JAIMES: topic prompts may request any configured specialist family; normalize punctuation before routing.
MODEL_ALIASES = {
    "gpt-5.6-luna": "luna",
    "luna": "luna",
    "gpt-5.6-terra": "terra",
    "terra": "terra",
    "codex": "terra",
    "gpt-5.6-sol": "sol",
    "sol": "sol",
    "gpt-5.5": "gpt-5.5",
    "gpt-5.4-mini": "gpt-5.4-mini",
    "antigravity gemini 3.5 flash": "gemini",
    "gemini 3.5 flash": "gemini",
    "gemini flash": "gemini",
    "gemini": "gemini",
    "antigravity gemini 3.1 pro": "gemini-pro",
    "gemini 3.1 pro preview": "gemini-pro",
    "gemini 3.1 pro": "gemini-pro",
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
    #JAIMES: GLM 5.2 is reached through Ollama Cloud, so it never receives private/raw context.
    return route_id in {"luna", "terra", "sol", "gpt-5.5", "gpt-5.4-mini", "ollama"}


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
        flexible_alias = re.escape(alias).replace(r"\ ", r"[\s._-]+").replace(r"\-", r"[\s_-]*")
        pattern = re.compile(
            rf"\b(?:use|run(?: this)? (?:with|on)|route(?: this)? (?:to|through)|ask|with|via|on|delegate to|send to|spawn(?: a)? sub[- ]?agent (?:with|on|using)|launch(?: a)? sub[- ]?agent (?:with|on|using))\s+(?:a\s+)?{flexible_alias}\b"
        )
        for match in pattern.finditer(lower):
            prefix = lower[max(0, match.start() - 20):match.start()]
            if re.search(r"(?:do not|don't|dont|never|avoid|not to)\s*$", prefix):
                continue
            return route
    return ""


def classify_route(prompt: str, privacy: str) -> tuple[str, str]:
    # Output-format instructions are not task intent. Reuse the same canonical
    # request extractor that drives Telegram objective text so a request for a
    # model/auth field in the final summary cannot hijack the selected route.
    core_request = objective_current_request_text(prompt) or str(prompt or "")
    lower = core_request.lower()
    if privacy != "dashboard-safe" or contains_sensitive_terms(prompt):
        return "luna", "private/sensitive content stays on Josh 2.0 coordinator lane"
    if any(token in lower for token in ("current event", "latest news", "x/twitter", "social signal", "market narrative")):
        return "grok", "public current-events/social signal request"
    mutation_text = NEGATED_MUTATION_SIGNAL.sub("", lower)
    #JAIMES: prefer GLM Cloud for sanitized, read-only technical depth; Codex retains execution.
    glm_reasoning = any(
        token in lower
        for token in (
            "large-context technical",
            "architecture analysis",
            "multi-file planning",
            "structured code review",
            "parallel technical reasoning",
            "technical second opinion",
        )
    )
    glm_mutation = re.search(
        r"\b(?:fix|patch|edit|implement|deploy|repair|restart|recover|build)\b",
        mutation_text,
    )
    model_routing_audit = bool(
        MODEL_ROUTING_AUDIT_SIGNAL.search(lower)
        and re.search(r"\b(?:assess|audit|check|evaluate|inspect|review|verify|confirm)\b", lower)
    )
    if model_routing_audit and not glm_mutation:
        return "glm", "dashboard-safe model-routing audit"
    if TELEGRAM_RESPONSE_AUDIT_REQUEST.search(lower) and not glm_mutation:
        return "terra", "trusted Telegram response-contract audit"
    if glm_reasoning and not glm_mutation:
        return "glm", "dashboard-safe large-context technical reasoning"
    if any(token in lower for token in ("hard", "stabilize", "architecture", "migration", "integration", "debug", "root cause")):
        return "terra", "trusted execution/integration"
    if MUTATION_SIGNAL.search(mutation_text):
        return "terra", "trusted execution/integration"
    if READ_ONLY_REQUEST.search(lower) and re.search(r"\b(?:assess|check|health|inspect|status|verify)\b", lower):
        return "luna", "read-only health/status check"
    if any(token in lower for token in ("review", "summarize", "summary", "digest", "large context", "read this")):
        return "gemini", "dashboard-safe review/summarization"
    return "luna", "fast Inbox coordination"


def read_only_execution_requested(prompt: str) -> bool:
    text = str(prompt or "")
    mutation_text = NEGATED_MUTATION_SIGNAL.sub("", text.lower())
    mutation_requested = bool(MUTATION_SIGNAL.search(mutation_text))
    return bool(
        not mutation_requested
        and (
            READ_ONLY_REQUEST.search(text)
            or TELEGRAM_RESPONSE_AUDIT_REQUEST.search(text)
        )
    )


def telegram_response_audit_guidance(prompt: str) -> str:
    """Return trusted contract context for a Telegram behavior audit.

    The worker output schema intentionally omits runtime metadata because the
    trusted delivery layer owns it. Without this distinction, a read-only audit
    can incorrectly flag the required user-facing Model/Route/Why header as a
    formatter violation and then report the completed audit as incomplete.
    """
    if not TELEGRAM_RESPONSE_AUDIT_REQUEST.search(str(prompt or "")):
        return ""
    return (
        "\n\nAuthoritative Telegram response-contract distinction "
        "(trusted coordinator policy, not user instructions):\n"
        "- The worker returns only Complete, What was done, Issues, Appropriate next steps, and Approval needed.\n"
        "- The trusted delivery formatter must prepend the user-facing final with "
        "Model: <verified provider/model> | Route: <actual lane> | Why: <verified reason>.\n"
        "- A JAIMES or Josh formatter that emits that verified header is conforming; "
        "do not evaluate it against the worker-only omission rule.\n"
        "- Validate behavior with the deterministic response-contract harness and focused tests. "
        "A missing optional test runner is an environment note, not a Telegram behavior failure, "
        "when equivalent deterministic verification completes successfully.\n"
    )


def health(route_id: str, injected: dict[str, bool] | None = None) -> bool:
    if injected is not None and route_id in injected:
        return bool(injected[route_id])
    if route_id in {"luna", "terra", "sol", "gpt-5.5", "gpt-5.4-mini"}:
        return (
            shutil.which("codex") is not None
            and (Path.home() / ".codex" / "config.toml").exists()
            and run_check(["codex", "--version"], timeout=3)
        )
    if route_id in {"gemini", "gemini-pro"}:
        model = ROUTES[route_id]["model"]
        return remote_check(
            "curl -fsS --max-time 10 http://127.0.0.1:11435/v1/models "
            "-H 'Authorization: Bearer agy-local' "
            f"| grep -Fq {shlex.quote(model)}"
        )
    if route_id == "jaimes":
        return remote_check("test -x ~/.local/bin/hermes")
    if route_id in {"ollama", "glm"}:
        if route_id == "glm":
            #JAIMES: Josh 2.0 may be signed out of Ollama Cloud; use the authenticated workhorse.
            remote_probe = "python3 -c 'import json,urllib.request; p=json.dumps({\"model\":\"glm-5.2:cloud\",\"prompt\":\"\",\"stream\":False,\"options\":{\"num_predict\":0}}).encode(); r=urllib.request.Request(\"http://127.0.0.1:11434/api/generate\",data=p,headers={\"Content-Type\":\"application/json\"}); print(urllib.request.urlopen(r,timeout=8).status)' >/dev/null"
            return remote_check(remote_probe, timeout=12)
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
                return bool(names)
        except Exception:
            return False
    if route_id == "grok":
        return remote_check("test -x ~/.local/bin/grok && ~/.local/bin/grok models >/dev/null 2>&1")
    return False


def fallback_for(route_id: str, privacy: str, injected: dict[str, bool] | None = None) -> tuple[str, str]:
    candidates = FALLBACK_LADDERS.get(route_id, ("luna", "terra"))
    for candidate in candidates:
        if candidate != route_id and route_allowed_for_privacy(candidate, privacy) and health(candidate, injected):
            return candidate, f"{route_id} unhealthy; selected {candidate}"
    safe_default = "luna" if route_allowed_for_privacy("luna", privacy) else "ollama"
    return safe_default, f"{route_id} unavailable; safe fallback {safe_default} is not yet healthy"


def route_prompt(prompt: str, privacy: str = "dashboard-safe", injected_health: dict[str, bool] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    core_request = objective_current_request_text(prompt) or str(prompt or "")
    explicit = detect_explicit_route(core_request)
    if explicit:
        route_id = explicit
        reason = "explicit model request"
    else:
        route_id, reason = classify_route(prompt, privacy)

    requested_healthy = health(route_id, injected_health)
    policy_allowed = route_allowed_for_privacy(route_id, privacy)
    fallback = ""
    selected = route_id
    outcome = "planned"
    preflight_error = ""
    if explicit and not policy_allowed:
        # Explicit model requests are fail-closed. Never replace the named
        # provider with a different model when privacy policy rejects it.
        reason = "explicit model request blocked by privacy policy"
        fallback = ""
        outcome = "blocked"
        preflight_error = "privacy-policy"
    elif explicit and not requested_healthy:
        reason = "explicit model request unavailable; automatic fallback disabled"
        fallback = ""
        outcome = "blocked"
        preflight_error = "route-unhealthy"
    elif not requested_healthy:
        selected, fallback = fallback_for(route_id, privacy, injected_health)

    cfg = ROUTES[selected]
    preflight_verified = bool(
        policy_allowed
        and (requested_healthy if explicit else health(selected, injected_health))
    )
    latency_ms = max(0, round((time.perf_counter() - started) * 1000))
    return {
        "ok": not bool(explicit and preflight_error),
        "routeId": selected,
        "policyRouteId": route_id,
        "requestedRouteId": route_id if explicit else "",
        "explicitRequest": bool(explicit),
        "requestedRouteHealthy": requested_healthy,
        "policyAllowed": policy_allowed,
        "preflightVerified": preflight_verified,
        "preflightError": preflight_error,
        "provider": cfg["provider"],
        "model": cfg["model"],
        "auth": cfg["auth"],
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
        "outcome": outcome,
    }


def append_telemetry(record: dict[str, Any]) -> None:
    safe = {
        "timestamp": utc_now(),
        "sourceAgent": CONTROL_TOWER_AGENT,
        "host": record.get("actualHost") or record.get("host") or socket.gethostname(),
        "worker": record.get("actualWorker") or record.get("worker"),
        "provider": record.get("actualProvider") or record.get("provider"),
        "model": record.get("actualModel") or record.get("model"),
        "auth": record.get("actualAuth") if record.get("authVerified") else "unverified",
        "authVerified": bool(record.get("authVerified")),
        "routeId": record.get("routeId"),
        "policyRouteId": record.get("policyRouteId") or record.get("routeId"),
        "requestedRouteId": record.get("requestedRouteId") or "",
        "explicitRequest": bool(record.get("explicitRequest")),
        "routingReason": record.get("routingReason"),
        "fallback": record.get("fallback") or "",
        "attemptedRoutes": list(record.get("attemptedRoutes") or []),
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


def is_worker_meta_compliance_item(value: str) -> bool:
    """Reject formatter obedience as a user-facing result."""
    return bool(WORKER_META_COMPLIANCE_RE.search(clean_final_item(value)))


def is_substantive_final_item(value: str) -> bool:
    text = clean_final_item(value)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'._/-]*", text)
    return (
        len(text) >= 24
        and len(words) >= 4
        and not is_status_only_final_item(text)
        and not is_worker_meta_compliance_item(text)
    )


def is_concrete_result_item(value: str) -> bool:
    text = clean_final_item(value)
    if OPERATIONAL_STATUS_FILLER_SIGNAL.search(text):
        return False
    return is_substantive_final_item(text) and bool(
        CONCRETE_RESULT_SIGNAL.search(text)
        or CONCRETE_EVIDENCE_SIGNAL.search(text)
        or OPERATIONAL_RESULT_SIGNAL.search(text)
    )


def has_operational_risk(value: str) -> bool:
    text = NEGATED_OPERATIONAL_RISK_SIGNAL.sub("", clean_final_item(value))
    text = POSITIVE_OPERATIONAL_ABSENCE_SIGNAL.sub("", text)
    return bool(OPERATIONAL_RISK_SIGNAL.search(text))


def no_action_item(value: str) -> bool:
    return bool(NO_ACTION_SIGNAL.match(clean_final_item(value)))


def incomplete_summary_done_items(source_items: list[str], concrete_count: int) -> list[str]:
    """Preserve reported details without exposing validator bookkeeping."""
    _ = concrete_count
    preserved = [item for item in source_items if is_substantive_final_item(item)][:5]
    if len(preserved) >= 3:
        return preserved
    user_facing_limitations = [
        "The worker response did not provide enough source-supported findings to establish the requested result.",
        "No unreported findings were inferred or presented as facts.",
        "The result remains unverified until the missing evidence is collected.",
    ]
    combined = unique_final_items([*preserved, *user_facing_limitations], limit=5)
    return combined if len(combined) >= 3 else user_facing_limitations


#JAIMES: Keep Codex sandboxed; runtime-health answers use only allowlisted host-native --no-write evidence and fail closed when it is absent.
def telegram_health_host_context(prompt: str) -> dict[str, Any] | None:
    """Collect a bounded host-native snapshot for Telegram health requests.

    The Codex worker stays sandboxed. Only an allowlist of dashboard-safe probe
    fields crosses into its prompt, so sandbox EPERM results cannot masquerade
    as the service-owning host's current state.
    """
    prompt_text = str(prompt or "")
    mutation_text = NEGATED_MUTATION_SIGNAL.sub("", prompt_text.lower())
    if not TELEGRAM_HEALTH_REQUEST.search(prompt_text) or MUTATION_SIGNAL.search(mutation_text):
        return None
    unavailable = {
        "available": False,
        "instruction": "Host-native Telegram health evidence was unavailable; do not infer service health or claim the assessment complete.",
    }
    try:
        proc = subprocess.run(
            [sys.executable, str(RUNTIME_PROBE_SCRIPT), "--no-write"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        payload = json.loads(proc.stdout or "")
    except (OSError, subprocess.SubprocessError, TypeError, ValueError, json.JSONDecodeError):
        return unavailable
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        return unavailable
    checked_at = payload.get("checkedAt")
    checked_time = parse_utc(checked_at) if isinstance(checked_at, str) else None
    current_time = parse_utc(utc_now())
    if checked_time is None or current_time is None:
        return unavailable
    age_seconds = (current_time - checked_time).total_seconds()
    if age_seconds > 300 or age_seconds < -60:
        return unavailable
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        return unavailable
    allowed_checks: dict[str, dict[str, Any]] = {}
    required_checks = (
        "gateway",
        "telegramFastAck",
        "telegramWorkCardHelper",
        "telegramInboxClaimHelper",
        "sourceFreshness",
    )
    for key in required_checks:
        row = checks.get(key)
        if not isinstance(row, dict) or not isinstance(row.get("ok"), bool):
            return unavailable
        safe_row: dict[str, Any] = {"ok": row["ok"]}
        detail_value = row.get("detail")
        detail = clean_final_item(detail_value, limit=120) if isinstance(detail_value, str) else ""
        if detail:
            safe_row["detail"] = detail
        latency = row.get("latencyMs")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool) and 0 <= latency <= 60_000:
            safe_row["latencyMs"] = round(float(latency), 1)
        allowed_checks[key] = safe_row
    return {
        "available": True,
        "checkedAt": checked_at,
        "ok": all(row["ok"] for row in allowed_checks.values()),
        "checks": allowed_checks,
        "scope": "Josh 2.0 host services only; this does not itself prove end-to-end Telegram delivery.",
    }


def render_telegram_health_result(host_context: dict[str, Any]) -> str:
    """Render host-health findings deterministically from the allowlisted snapshot."""
    checks = host_context["checks"]

    def finding(label: str, key: str) -> str:
        row = checks[key]
        state = "passed" if row["ok"] else "failed"
        detail = clean_final_item(row.get("detail", ""), limit=120)
        suffix = f": {detail}" if detail else "."
        return f"The {label} check {state}{suffix}"

    support_keys = ("telegramWorkCardHelper", "telegramInboxClaimHelper", "sourceFreshness")
    support_passed = sum(1 for key in support_keys if checks[key]["ok"])
    done = [
        finding("gateway", "gateway"),
        finding("Telegram Fast Ack service", "telegramFastAck"),
        f"The work-card helper, Inbox claim helper, and source-freshness checks passed {support_passed} of 3 checks.",
    ]
    failed = [
        finding(label, key)
        for label, key in (
            ("gateway", "gateway"),
            ("Telegram Fast Ack service", "telegramFastAck"),
            ("work-card helper", "telegramWorkCardHelper"),
            ("Inbox claim helper", "telegramInboxClaimHelper"),
            ("source freshness", "sourceFreshness"),
        )
        if not checks[key]["ok"]
    ]
    issues = [*failed, "This host-service snapshot does not verify end-to-end Telegram message delivery."]
    next_step = (
        "Use one human-origin Telegram message only if end-to-end delivery confirmation is required."
        if not failed
        else "Review the failed host checks before relying on Telegram delivery."
    )
    return "\n".join([
        "Complete: Yes — the current Josh 2.0 host-service assessment completed.",
        "What was done:",
        *(f"- {item}" for item in done),
        "Issues:",
        *(f"- {item}" for item in issues),
        "Appropriate next steps:",
        f"- {next_step}",
        "Approval needed:",
        "- n/a",
    ])


def enforce_host_evidence_gate(output: str, host_context: dict[str, Any] | None) -> str:
    if host_context is not None and host_context.get("available") is False:
        return TELEGRAM_HEALTH_EVIDENCE_UNAVAILABLE_RESULT
    if host_context is not None and host_context.get("available") is True:
        return render_telegram_health_result(host_context)
    return str(output or "")


def route_requires_successful_execution(route: dict[str, Any] | None) -> bool:
    """Return whether this objective specifically requires a live route proof."""
    if not isinstance(route, dict):
        return False
    return str(route.get("routingReason") or "") == "dashboard-safe model-routing audit"


def parse_model_sections(
    output: str,
    delivery_tier: int = 3,
    *,
    require_successful_execution: bool = False,
) -> dict[str, Any]:
    cleaned = html.unescape(str(output or "")).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"</?pre>", "", cleaned, flags=re.I)
    sections: dict[str, list[str]] = {key: [] for key in ("done", "issues", "next", "approval")}
    complete = False
    complete_declared = False
    quick_result = int(delivery_tier or 3) in {1, 2}
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
            normalized_line = clean_final_item(line)
            match = re.match(
                r"^complete:\s*(yes|no)\b(?:\s*[-—:,]?\s*(.*))?$",
                normalized_line,
                flags=re.I,
            )
            complete = bool(match and match.group(1).lower() == "yes")
            complete_declared = match is not None
            if quick_result and match and match.group(2):
                detail = clean_final_item(match.group(2))
                if detail:
                    sections["done" if complete else "issues"].append(detail)
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

    source_done = [
        item
        for item in unique_final_items(sections["done"] or loose, limit=8)
        if not is_worker_meta_compliance_item(item)
    ][:6]
    source_issues = unique_final_items(sections["issues"])
    source_next = unique_final_items(sections["next"])
    source_approval = unique_final_items(sections["approval"])
    substantive_done = [item for item in source_done if is_substantive_final_item(item)]
    concrete_done = [item for item in source_done if is_concrete_result_item(item)]
    quality_issues: list[str] = []

    if not complete_declared:
        quality_issues.append("Worker did not return a verifiable completion status.")
    if complete:
        if quick_result:
            if not 1 <= len(source_done) <= 3:
                quality_issues.append("A quick-answer completion requires one to three concise result bullets.")
            elif any(is_status_only_final_item(item) for item in source_done):
                quality_issues.append("A quick-answer completion must contain the direct conversational result.")
        else:
            if not 3 <= len(source_done) <= 5:
                quality_issues.append("A completion claim requires three to five unique result bullets.")
            if len(substantive_done) != len(source_done):
                quality_issues.append("Status or delivery-process text was used in place of substantive findings or outcomes.")
            if len(concrete_done) < 2:
                quality_issues.append("The completion claim did not include enough concrete findings or outcomes.")
        if not source_next and not quick_result:
            quality_issues.append("The completion claim did not include an evidence-based next step.")

        reported_text = " ".join([*source_done, *source_next, *source_approval])
        execution_truth_text = " ".join([*source_done, *source_issues, *source_next])
        if (
            require_successful_execution
            and UNRESOLVED_EXECUTION_FAILURE_SIGNAL.search(execution_truth_text)
        ):
            quality_issues.append(
                "The completion claim contradicted an unresolved model or route execution failure."
            )
        reported_risk = bool(
            RISK_OR_LIMITATION_SIGNAL.search(reported_text)
            or has_operational_risk(reported_text)
        )
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
        issues = unique_final_items([*source_issues, MISSING_FINDINGS_ISSUE])
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


def render_final_html(
    route: dict[str, Any],
    execution: dict[str, Any],
    output: str,
    delivery_tier: int = 3,
) -> str:
    #JAIMES: the delivery layer, not the model, owns the fixed final format and
    # inserts only verified runtime routing facts.
    sections = parse_model_sections(
        output,
        delivery_tier=delivery_tier,
        require_successful_execution=route_requires_successful_execution(route),
    )
    execution_verified = bool(execution.get("executionVerified"))
    model_verified = bool(execution.get("modelVerified"))
    if not execution_verified:
        sections["complete"] = False
        if "Worker execution was not verified." not in sections["issues"]:
            sections["issues"].append("Worker execution was not verified.")
    provider = clean_final_item(str(execution.get("actualProvider") or ""), limit=40)
    model = clean_final_item(str(execution.get("actualModel") or ""), limit=80)
    verified_model = f"{provider}/{model}" if model_verified and provider and model else "unverified"
    verified_auth = (
        clean_final_item(str(execution.get("actualAuth") or ""), limit=80)
        if execution_verified and bool(execution.get("authVerified"))
        else "unverified"
    ) or "unverified"
    verified_fallback = clean_final_item(str(route.get("fallback") or ""), limit=220) or "none"
    verified_reason = clean_final_item(
        str(route.get("routingReason") or "verified Inbox routing"),
        limit=120,
    )
    args = argparse.Namespace(
        model=verified_model,
        route=clean_final_item(str(route.get("routeId") or "unverified"), limit=80),
        why=f"{verified_reason}; auth={verified_auth}; fallback={verified_fallback}",
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
                if "deliveryTier" not in existing:
                    existing["deliveryTier"] = int(safe_context.get("deliveryTier") or 3)
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
                    "routeId", "policyRouteId", "provider", "model", "auth", "tier", "worker", "host",
                    "executor", "routingReason", "fallback", "privacy", "requestedRouteId",
                    "explicitRequest", "requestedRouteHealthy", "policyAllowed",
                    "preflightVerified", "preflightError", "outcome",
                    "attemptedRoutes",
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
        job["deliveryTier"] = int(safe_context.get("deliveryTier") or 3)
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


LLM_EXECUTOR_CODE = r'''import json, os, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".openclaw" / "workspace" / "scripts"))
import llm_router
cfg = json.loads(sys.argv[1])
timeout = int(sys.argv[2])
prompt = sys.stdin.read()
kind = cfg["executor"]

def ask_codex_read_only():
    if llm_router._codex_disabled():
        raise RuntimeError("Codex routing disabled by LLM_ROUTER_DISABLE_CODEX")
    if not llm_router._codex_host_allowed():
        raise RuntimeError("Codex local routing is not allowed on this host")
    if not llm_router._codex_available():
        raise RuntimeError("Codex CLI/config not available")
    started = time.monotonic()
    workdir = llm_router._codex_workdir()
    fd, output_path_str = tempfile.mkstemp(prefix="inbox_codex_read_only_", suffix=".txt")
    os.close(fd)
    output_path = Path(output_path_str)
    cmd = [
        "codex", "exec", "-m", cfg["model"], "-C", str(workdir),
        "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral",
        "--color", "never", "-o", str(output_path), prompt,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=max(timeout, 120), check=True,
        )
        text = output_path.read_text().strip() if output_path.exists() else ""
        if not text:
            text = (proc.stdout or proc.stderr or "").strip()
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"Codex CLI failed: {detail[-500:]}") from exc
    finally:
        output_path.unlink(missing_ok=True)
    if not text:
        raise RuntimeError("Codex CLI returned no text")
    llm_router._record_usage(
        "codex", cfg["model"], len(prompt), len(text),
        (time.monotonic() - started) * 1000, cfg["tier"],
    )
    return text

if kind == "local-codex":
    if cfg.get("readOnly"):
        output = ask_codex_read_only()
    else:
        output = llm_router._ask_codex_cli(prompt, model=cfg["model"], timeout=timeout, tier=cfg["tier"])
elif kind == "local-ollama":
    model = cfg["model"]
    if model == "local":
        model = llm_router._resolve_ollama_model("local")
    output = llm_router._ask_ollama(prompt, model=model, timeout=timeout, tier=cfg["tier"])
    cfg["model"] = model
elif kind == "remote-llm-router":
    output = llm_router._ask_gemini(prompt, model=cfg["model"], tier=cfg["tier"])
elif kind == "remote-llm-router-op":
    output = llm_router._ask_xai(prompt, model=cfg["model"], timeout=timeout, tier=cfg["tier"])
else:
    raise RuntimeError(f"unsupported executor: {kind}")
envelope = {"output": output, "provider": cfg["provider"], "model": cfg["model"], "modelVerified": True}
if kind == "local-codex":
    # A successful Codex CLI response verifies the configured authenticated
    # subscription lane; publish that observed fact instead of auth=unverified.
    envelope.update({"actualAuth": "OpenAI Codex OAuth/subscription", "authVerified": True})
elif kind == "local-ollama":
    envelope.update({"actualAuth": "Local Ollama runtime", "authVerified": True})
print(json.dumps(envelope))
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
    model = str(usage.get("model") or usage.get("model_id") or cfg.get("model") or "")
    provider = str(usage.get("provider") or cfg.get("provider") or "jaimes")
    print(json.dumps({"output": proc.stdout.strip(), "provider": provider, "model": model, "modelVerified": bool(model)}))
finally:
    try: os.unlink(usage_path)
    except OSError: pass
'''


ANTIGRAVITY_EXECUTOR_CODE = r'''import json, os, sys, urllib.error, urllib.request
cfg = json.loads(sys.argv[1])
timeout = int(sys.argv[2])
prompt = sys.stdin.read()
model = str(cfg["model"])
base_url = os.environ.get("ANTIGRAVITY_BASE_URL", "http://127.0.0.1:11435/v1").rstrip("/")
token = os.environ.get("ANTIGRAVITY_LOCAL_TOKEN", "agy-local")
payload = json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": prompt}],
    "stream": False,
}).encode("utf-8")
request = urllib.request.Request(
    f"{base_url}/chat/completions", data=payload,
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read())
except urllib.error.HTTPError as exc:
    raise RuntimeError(f"Antigravity Gemini request failed with HTTP {exc.code}") from exc
actual_model = str(result.get("model") or "").strip()
if actual_model and actual_model != model:
    raise RuntimeError(f"Antigravity returned unexpected model {actual_model}")
choices = result.get("choices") or []
message = (choices[0].get("message") or {}) if choices and isinstance(choices[0], dict) else {}
content = message.get("content")
if isinstance(content, list):
    output = "\n".join(
        str(item.get("text") or "") for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ).strip()
else:
    output = str(content or "").strip()
if not output:
    raise RuntimeError("Antigravity returned empty output")
print(json.dumps({
    "output": output, "provider": "gemini", "model": model,
    "modelVerified": True, "actualAuth": "Antigravity session", "authVerified": True,
}))
'''


OLLAMA_EXECUTOR_CODE = r'''import json, sys, urllib.error, urllib.request
cfg = json.loads(sys.argv[1])
timeout = int(sys.argv[2])
prompt = sys.stdin.read()
payload = json.dumps({
    "model": str(cfg["model"]), "prompt": prompt, "stream": False, "think": False,
}).encode("utf-8")
request = urllib.request.Request(
    "http://127.0.0.1:11434/api/generate", data=payload,
    headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read())
except urllib.error.HTTPError as exc:
    if exc.code == 401:
        raise RuntimeError("Ollama Cloud authentication failed") from exc
    raise
output = str(result.get("response") or "").strip()
if not output:
    raise RuntimeError("Ollama returned empty output")
print(json.dumps({
    "output": output, "provider": "ollama", "model": cfg["model"],
    "modelVerified": True, "actualAuth": "Ollama Cloud", "authVerified": True,
}))
'''


GROK_EXECUTOR_CODE = r'''import json, subprocess, sys
cfg = json.loads(sys.argv[1])
timeout = int(sys.argv[2])
prompt = sys.stdin.read()
proc = subprocess.run(
    [
        "/Users/jc_agent/.local/bin/grok",
        "-p", prompt,
        "--output-format", "json",
        "--no-subagents",
        "--max-turns", "2",
        "--verbatim",
        "--no-plan",
        "--tools", "",
        "--model", str(cfg.get("model") or "grok-4.5"),
    ],
    capture_output=True, text=True, timeout=timeout, check=True,
)
payload = json.loads(proc.stdout)
output = str(payload.get("text") or "").strip()
if not output:
    raise RuntimeError("Grok CLI returned empty output")
print(json.dumps({
    "output": output, "provider": "xai", "model": cfg.get("model") or "grok-4.5",
    "modelVerified": True, "actualAuth": "Grok CLI authentication", "authVerified": True,
}))
'''


def executor_command(route: dict[str, Any], timeout: int) -> tuple[list[str], str]:
    executor = str(route.get("executor") or "")
    cfg = json.dumps(route, separators=(",", ":"))
    if executor in {"local-codex", "local-ollama"}:
        return [sys.executable, "-c", LLM_EXECUTOR_CODE, cfg, str(timeout)], "josh2"

    if executor == "remote-grok-cli":
        runner = GROK_EXECUTOR_CODE
    elif executor == "remote-antigravity":
        runner = ANTIGRAVITY_EXECUTOR_CODE
    elif executor == "remote-ollama":
        runner = OLLAMA_EXECUTOR_CODE
    elif executor == "remote-hermes":
        runner = HERMES_EXECUTOR_CODE
    else:
        runner = LLM_EXECUTOR_CODE
    remote_python = ["/opt/homebrew/bin/python3", "-c", runner, cfg, str(timeout)]
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
    actual_auth = clean_final_item(str(result.get("actualAuth") or ""), limit=80)
    auth_verified = bool(result.get("authVerified") is True and actual_auth)
    return {
        "output": output,
        "actualHost": actual_host,
        "actualWorker": route.get("worker"),
        "actualProvider": result.get("provider") or route.get("provider"),
        "actualModel": result.get("model") or "unverified",
        "actualAuth": actual_auth,
        "authVerified": auth_verified,
        "modelVerified": bool(result.get("modelVerified")),
        "executionVerified": True,
    }


def execution_fallback_route(
    route: dict[str, Any],
    attempted: set[str],
    injected_health: dict[str, bool] | None = None,
    candidate_order: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Return the next healthy, privacy-safe runtime route with disclosure."""
    route_id = str(route.get("routeId") or "")
    privacy = str(route.get("privacy") or "dashboard-safe")
    candidates = candidate_order or FALLBACK_LADDERS.get(route_id, ("luna", "terra"))
    for candidate in candidates:
        if (
            candidate in attempted
            or not route_allowed_for_privacy(candidate, privacy)
            or not health(candidate, injected_health)
        ):
            continue
        current_cfg = ROUTES.get(route_id, {})
        candidate_cfg = ROUTES[candidate]
        disclosure = (
            f"{route_id} ({current_cfg.get('provider', 'unverified')}/"
            f"{current_cfg.get('model', 'unverified')}) execution failed; selected "
            f"{candidate} ({candidate_cfg['provider']}/{candidate_cfg['model']})"
        )
        previous = clean_final_item(str(route.get("fallback") or ""), limit=220)
        fallback = "; ".join(value for value in (previous, disclosure) if value)
        return {
            **route,
            "routeId": candidate,
            "provider": candidate_cfg["provider"],
            "model": candidate_cfg["model"],
            "auth": candidate_cfg["auth"],
            "tier": candidate_cfg["tier"],
            "worker": candidate_cfg["worker"],
            "host": candidate_cfg["host"],
            "role": candidate_cfg["role"],
            "executor": candidate_cfg["executor"],
            "fallback": fallback[:440],
            "executionVerified": False,
            "outcome": "fallback-planned",
        }
    return None


def execute_route_with_fallback(
    prompt: str,
    route: dict[str, Any],
    timeout: int,
    injected_health: dict[str, bool] | None = None,
    on_fallback: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute the planned route, then fresh disclosed fallbacks if it fails."""
    current = dict(route)
    attempted: set[str] = set()
    attempted_order: list[str] = []
    original_route_id = str(current.get("policyRouteId") or current.get("routeId") or "")
    fallback_candidates = FALLBACK_LADDERS.get(original_route_id, ("luna", "terra"))
    if current.get("explicitRequest") and not current.get("preflightVerified"):
        raise RouteExecutionError(
            "Explicit route preflight failed; automatic fallback is disabled",
            current,
            attempted_order,
        )
    while True:
        route_id = str(current.get("routeId") or "unverified")
        attempted.add(route_id)
        attempted_order.append(route_id)
        try:
            execution = execute_route(prompt, current, timeout)
            current["attemptedRoutes"] = list(attempted_order)
            return current, execution
        except Exception as exc:  # noqa: BLE001 - only safe route IDs leave this boundary
            current["attemptedRoutes"] = list(attempted_order)
            if current.get("explicitRequest"):
                raise RouteExecutionError(
                    f"Explicit route {route_id} failed; automatic fallback is disabled",
                    current,
                    attempted_order,
                ) from exc
            next_route = execution_fallback_route(
                current,
                attempted,
                injected_health,
                candidate_order=fallback_candidates,
            )
            if next_route is None:
                final_failure = (
                    f"{route_id} ({current.get('provider', 'unverified')}/"
                    f"{current.get('model', 'unverified')}) execution failed; "
                    "no eligible fallback remained"
                )
                previous = clean_final_item(str(current.get("fallback") or ""), limit=440)
                current["fallback"] = "; ".join(
                    value for value in (previous, final_failure) if value
                )[:660]
                raise RouteExecutionError(
                    "No verified execution route completed; attempted "
                    + ", ".join(attempted_order),
                    current,
                    attempted_order,
                ) from exc
            next_route["attemptedRoutes"] = list(attempted_order)
            if on_fallback is None or not on_fallback(dict(next_route)):
                raise RouteExecutionError(
                    "Fallback route could not be disclosed before execution",
                    current,
                    attempted_order,
                ) from exc
            current = next_route


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
                    "policyRouteId": str(planned.get("policyRouteId") or route_id),
                    "requestedRouteId": str(planned.get("requestedRouteId") or ""),
                    "explicitRequest": bool(planned.get("explicitRequest")),
                    "requestedRouteHealthy": bool(planned.get("requestedRouteHealthy")),
                    "policyAllowed": bool(planned.get("policyAllowed", True)),
                    "preflightVerified": bool(planned.get("preflightVerified")),
                    "preflightError": clean_final_item(str(planned.get("preflightError") or ""), limit=40),
                    "provider": cfg["provider"],
                    "model": cfg["model"],
                    "auth": cfg["auth"],
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
                    "outcome": clean_final_item(str(planned.get("outcome") or "planned"), limit=40),
                    "attemptedRoutes": [],
                }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            route = None
    if route is None:
        route = route_prompt(prompt, args.privacy)
    if route.get("explicitRequest") and not route.get("preflightVerified"):
        return {
            "ok": False,
            "status": "explicit-route-preflight-failed",
            "error": str(route.get("preflightError") or "route-unhealthy"),
            "route": route,
        }
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
        "deliveryTier": int(getattr(args, "delivery_tier", 3) or 3),
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


def update_card_progress(snapshot: dict[str, Any], progress_code: str) -> bool:
    origin = snapshot.get("origin") or {}
    run_id = str(origin.get("runId") or "")
    if not run_id or progress_code not in {"worker_started", "fallback_selected", "verifying"} or not TELEGRAM_GATEWAY_SCRIPT.exists():
        return False
    cmd = [
        sys.executable,
        str(TELEGRAM_GATEWAY_SCRIPT),
        "--progress-event-json-stdin",
    ]
    payload = json.dumps(
        {"runId": run_id, "progressCode": progress_code},
        separators=(",", ":"),
    )
    retryable = {
        "run-card-not-ready",
        "progress-origin-not-coordinator-owned",
        "progress-origin-mismatch",
        "worker-not-running",
    }
    for attempt in range(6):
        try:
            proc = subprocess.run(
                cmd,
                cwd=WORKSPACE,
                input=payload,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            try:
                receipt = json.loads(proc.stdout or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                receipt = {}
            if (
                proc.returncode == 0
                and isinstance(receipt, dict)
                and receipt.get("ok") is True
                and str(receipt.get("status") or "").startswith("progress-recorded")
            ):
                return True
            status = str(receipt.get("status") or "") if isinstance(receipt, dict) else ""
            if status not in retryable or attempt == 5:
                return False
            time.sleep(min(0.1 * (2**attempt), 0.8))
        except Exception:
            return False
    return False


def checkpoint_worker_route(job_id: str, lease_token: str, effective_route: dict[str, Any]) -> bool:
    """Persist a disclosed fallback route before its execution begins."""
    with state_lock():
        state = read_json(STATE_PATH, {"jobs": {}})
        job = (state.get("jobs") or {}).get(job_id)
        if (
            not isinstance(job, dict)
            or job.get("status") != "running"
            or not lease_token
            or job.get("leaseToken") != lease_token
        ):
            return False
        job["route"] = {
            key: effective_route.get(key)
            for key in (
                "routeId", "policyRouteId", "provider", "model", "auth", "tier", "worker", "host",
                "executor", "routingReason", "fallback", "privacy", "requestedRouteId",
                "explicitRequest", "requestedRouteHealthy", "policyAllowed",
                "preflightVerified", "preflightError", "outcome",
                "attemptedRoutes",
            )
        }
        job["updatedAt"] = utc_now()
        save_json(STATE_PATH, state)
        return True


def checkpoint_worker_execution(
    job_id: str,
    lease_token: str,
    execution: dict[str, Any],
    effective_route: dict[str, Any] | None = None,
) -> bool:
    """Expose only verified route facts to the trusted progress gateway."""
    allowed = (
        "actualHost", "actualWorker", "actualProvider", "actualModel",
        "actualAuth", "authVerified", "modelVerified", "executionVerified",
    )
    with state_lock():
        state = read_json(STATE_PATH, {"jobs": {}})
        job = (state.get("jobs") or {}).get(job_id)
        if (
            not isinstance(job, dict)
            or job.get("status") != "running"
            or not lease_token
            or job.get("leaseToken") != lease_token
        ):
            return False
        job["actual"] = {key: execution.get(key) for key in allowed}
        if effective_route is not None:
            job["route"] = {
                key: effective_route.get(key)
                for key in (
                    "routeId", "policyRouteId", "provider", "model", "auth", "tier", "worker", "host",
                    "executor", "routingReason", "fallback", "privacy", "requestedRouteId",
                    "explicitRequest", "requestedRouteHealthy", "policyAllowed",
                    "preflightVerified", "preflightError", "outcome",
                    "attemptedRoutes",
                )
            }
        job["executionCheckpointAt"] = utc_now()
        job["updatedAt"] = utc_now()
        save_json(STATE_PATH, state)
        return True


def deliver_result(job_id: str, snapshot: dict[str, Any], route: dict[str, Any], execution: dict[str, Any], output: str) -> bool:
    origin = snapshot.get("origin") or {}
    card_key = str(origin.get("cardKey") or "")
    origin_run_id = str(origin.get("runId") or "")
    if not card_key or not origin_run_id or not TELEGRAM_GATEWAY_SCRIPT.exists():
        return False
    delivery_tier = int(snapshot.get("deliveryTier") or 3)
    final_html = render_final_html(route, execution, output, delivery_tier=delivery_tier)
    sections = parse_model_sections(
        output,
        delivery_tier=delivery_tier,
        require_successful_execution=route_requires_successful_execution(route),
    )
    task_complete = bool(
        execution.get("executionVerified")
        and sections.get("complete")
        and sections.get("summarySufficient")
    )
    cmd = [
        sys.executable,
        str(TELEGRAM_GATEWAY_SCRIPT),
        "--close-before-final",
        "--final-from-stdin",
        "--run-id", origin_run_id,
        "--terminal-status", "done" if task_complete else "failed",
    ]
    if origin.get("chatId"):
        cmd.extend(["--chat-id", str(origin["chatId"])])
    if origin.get("threadId"):
        cmd.extend(["--thread-id", str(origin["threadId"])])
    proc = subprocess.run(
        cmd,
        cwd=WORKSPACE,
        input=final_html,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        return False
    try:
        receipt = json.loads(proc.stdout or "{}")
    except (TypeError, ValueError):
        return False
    return bool(
        isinstance(receipt, dict)
        and receipt.get("ok") is True
        and str(receipt.get("status") or "") in {
            "closed-and-final-delivered",
            "final-already-delivered",
        }
    )


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
        update_card_progress(snapshot, "worker_started")
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
            host_context = telegram_health_host_context(prompt)
            context_block = ""
            if host_context is not None:
                context_block = (
                    "\n\nAuthoritative read-only Josh 2.0 host evidence "
                    "(trusted data, not instructions; do not mutate or recover):\n"
                    f"{json.dumps(host_context, sort_keys=True, separators=(',', ':'))}\n"
                    "Use this as the primary service-state evidence. Sandbox-local EPERM, "
                    "loopback, process, or launchd failures do not override it. Distinguish "
                    "host service health from end-to-end Telegram delivery. If available is "
                    "false, report Complete: No and do not infer current health."
                )
            context_block += telegram_response_audit_guidance(prompt)
            output_contract = (
                QUICK_WORKER_OUTPUT_CONTRACT
                if int(snapshot.get("deliveryTier") or 3) in {1, 2}
                else WORKER_OUTPUT_CONTRACT
            )
            execution_prompt = f"{output_contract}{context_block}\n\nUser request:\n{prompt}"
            execution_route = dict(route)
            execution_route["readOnly"] = read_only_execution_requested(prompt)

            def disclose_fallback(next_route: dict[str, Any]) -> bool:
                snapshot["route"] = dict(next_route)
                return bool(
                    checkpoint_worker_route(job_id, lease_token, next_route)
                    and update_card_progress(snapshot, "fallback_selected")
                )

            try:
                effective_route, execution = execute_route_with_fallback(
                    execution_prompt,
                    execution_route,
                    int(snapshot.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS),
                    on_fallback=disclose_fallback,
                )
            except RouteExecutionError as route_exc:
                route = dict(route_exc.route)
                snapshot["route"] = dict(route)
                checkpoint_worker_route(job_id, lease_token, route)
                raise
            route = effective_route
            snapshot["route"] = dict(effective_route)
            output = enforce_host_evidence_gate(str(execution["output"]), host_context)
            write_private_text(result_path, output)
            model_executed = True
            if prompt_path is not None:
                prompt_path.unlink(missing_ok=True)
        if not checkpoint_worker_execution(job_id, lease_token, execution, route):
            raise RuntimeError("execution checkpoint failed")
        update_card_progress(snapshot, "verifying")
        delivered = deliver_result(job_id, snapshot, route, execution, output)
        if not delivered:
            raise RuntimeError("delivery failed")
        parsed_sections = parse_model_sections(
            output,
            delivery_tier=int(snapshot.get("deliveryTier") or 3),
            require_successful_execution=route_requires_successful_execution(route),
        )
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
                recovered_sections = parse_model_sections(
                    output,
                    delivery_tier=int(snapshot.get("deliveryTier") or 3),
                    require_successful_execution=route_requires_successful_execution(route),
                )
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
                "actualAuth": "",
                "authVerified": False,
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
                for key in (
                    "actualHost", "actualWorker", "actualProvider", "actualModel",
                    "actualAuth", "authVerified", "modelVerified", "executionVerified",
                )
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
        recovered_sections = parse_model_sections(
            output,
            delivery_tier=int(job.get("deliveryTier") or 3),
            require_successful_execution=route_requires_successful_execution(route),
        )
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
    submit_p.add_argument("--delivery-tier", type=int, choices=(1, 2, 3), default=3)
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
