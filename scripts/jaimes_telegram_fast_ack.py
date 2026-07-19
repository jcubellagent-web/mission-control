#!/usr/bin/env python3
"""Send an immediate JAIMES Telegram acknowledgement for new direct-chat turns."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import html
import json
import os
import re
import secrets
import shlex
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any, Callable

from jaimes_completion_evidence import write_completion_evidence


HOME = Path.home()
WORKSPACE = HOME / ".openclaw" / "workspace"
SESSIONS_PATH = HOME / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
HERMES_SESSIONS_PATH = HOME / ".hermes" / "sessions" / "sessions.json"
SESSION_DIR = SESSIONS_PATH.parent
HERMES_SESSION_DIR = HERMES_SESSIONS_PATH.parent
HERMES_STATE_DB = HOME / ".hermes" / "state.db"
STATE_PATH = HOME / ".openclaw" / "telegram" / "jaimes_fast_ack_state.json"
JAIMES_WORK_CARD_STATE_PATH = WORKSPACE / "memory" / "jaimes_work_cards.json"
HANDOFF_DIR = HOME / ".openclaw" / "telegram" / "jaimes_handoff_receipts"
DIRECT_SESSION_KEYS = (
    "agent:main:telegram:dm:6218150306",
    "agent:main:telegram:direct:6218150306",
)
CONTROL_CENTER_CHAT_ID = "-1003589561528"
JAIMES_CONTROL_CENTER_TOPICS = {"17", "19", "20", "56"}
JAIMES_DIRECT_MENTION_TOPICS = {"1"}
JAIMES_MENTION_RE = re.compile(r"(?:^|[\s,.:;!?()\[\]{}])@jaimes(?=$|[\s,.:;!?()\[\]{}])", re.I)
TELEGRAM_GROUP_TOPIC_RE = re.compile(r"telegram:group:(-?\d+):(?:topic:)?(\d+)")
DEFAULT_MODEL = "openai-codex/gpt-5.6-sol"
DEFAULT_ROUTE = "JAIMES Telegram -> Hermes task"
STALE_BOOTSTRAP_SECONDS = 120
HANDOFF_RECEIPT_TTL_SECONDS = 90
HANDOFF_LEASE_ARRIVAL_GRACE_SECONDS = 15
BOT_IDENTITY_CHECK_SECONDS = 5 * 60
EXPECTED_BOT_USERNAME = os.environ.get("JAIMES_TELEGRAM_BOT_USERNAME", "Jaimes_claw_bot")
HEARTBEAT_SECONDS = 20
MAX_ACTIVE_CARD_SECONDS = 45 * 60
WORK_CARD_API_TIMEOUT_SECONDS = 8
WORK_CARD_PARENT_TIMEOUT_SECONDS = 12
SURFACE_RETRY_BASE_SECONDS = 5
SURFACE_RETRY_MAX_SECONDS = 60
SURFACE_RETRY_MAX_RECORDS = 100
CONTROL_TOWER_SSH_HOST = os.environ.get("CONTROL_TOWER_SSH_HOST", "josh2.0@josh2")
CONTROL_TOWER_REMOTE_ROOT = os.environ.get(
    "CONTROL_TOWER_REMOTE_ROOT",
    "/Users/josh2.0/.openclaw/workspace/mission-control",
)
CONTROL_TOWER_REMOTE_PYTHON = os.environ.get(
    "CONTROL_TOWER_REMOTE_PYTHON",
    "/opt/homebrew/bin/python3",
)
APPROVAL_ACTIONS_PATH = WORKSPACE / "memory" / "telegram_approval_actions.json"
X_INTELLIGENCE_QUEUE = WORKSPACE / "memory" / "x_intelligence_intake_queue.jsonl"
X_STATUS_URL_RE = re.compile(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_]{1,15}/status/\d+", re.I)
TELEGRAM_META_PATTERN = re.compile(r"Conversation info.*?```\s*\n\nSender .*?```\s*\n\n", re.S)

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
canonical_scripts = str(WORKSPACE / "mission-control" / "scripts")
if canonical_scripts not in sys.path:
    # Keep the executing script's siblings authoritative. Appending the
    # canonical fallback also makes isolated regression copies truly isolated.
    sys.path.append(canonical_scripts)

try:
    import jaimes_work_card as work_card  # type: ignore
except Exception:  # noqa: BLE001
    work_card = None

try:
    from agent_skill_router import select_skill, write_selection  # type: ignore
except Exception:  # noqa: BLE001
    select_skill = None
    write_selection = None

try:
    from telegram_channel_registry import owner_accepts, topics_for_owner  # type: ignore
    JAIMES_CONTROL_CENTER_TOPICS = topics_for_owner("jaimes", CONTROL_CENTER_CHAT_ID) or JAIMES_CONTROL_CENTER_TOPICS
except Exception:  # noqa: BLE001
    owner_accepts = lambda owner, chat_id, thread_id, direct=False: (  # type: ignore
        owner == "jaimes"
        and (direct or (str(chat_id) == CONTROL_CENTER_CHAT_ID and str(thread_id) in JAIMES_CONTROL_CENTER_TOPICS))
    )

try:
    from objective_quality import objective_is_near_copy, semantic_reinterpretation  # type: ignore
except Exception:  # noqa: BLE001
    # Fail closed so an import problem cannot expose a prompt echo as an
    # apparent agent interpretation on Telegram or Control Tower.
    objective_is_near_copy = lambda _prompt, _objective: True
    semantic_reinterpretation = lambda _prompt: ""

try:
    from telegram_ux_helpers import (  # type: ignore
        approve_all_step as ux_approve_all_step,
        button_label as ux_button_label,
        final_action_steps as ux_final_action_steps,
        steps_are_all_applicable as ux_steps_are_all_applicable,
    )
except Exception:  # noqa: BLE001
    ux_approve_all_step = None
    ux_button_label = None
    ux_final_action_steps = None
    ux_steps_are_all_applicable = None


def parse_telegram_target_from_key(key: str) -> dict[str, Any]:
    match = TELEGRAM_GROUP_TOPIC_RE.search(key or "")
    if match:
        return {
            "telegram_chat_id": match.group(1),
            "telegram_thread_id": match.group(2),
            "telegram_session_key": key,
        }
    if "telegram:direct:" in key or "telegram:dm:" in key:
        return {"telegram_chat_id": "6218150306", "telegram_session_key": key}
    return {"telegram_session_key": key}


def telegram_target(meta: dict[str, Any] | None = None) -> Any:
    if meta and meta.get("telegram_chat_id"):
        chat_id = str(meta.get("telegram_chat_id"))
        return int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
    return work_card.telegram_target() if work_card is not None else ""


def apply_telegram_target(payload: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    if not meta:
        return payload
    chat_id = meta.get("telegram_chat_id")
    if chat_id:
        payload["chat_id"] = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    thread_id = meta.get("telegram_thread_id")
    if thread_id:
        payload["message_thread_id"] = int(thread_id) if str(thread_id).isdigit() else thread_id
    return payload


def work_card_target_args(meta: dict[str, Any] | None) -> list[str]:
    """Persist the originating Telegram target and task identity."""
    if not meta:
        return []
    chat_id = meta.get("telegram_chat_id") or meta.get("chat_id")
    thread_id = meta.get("telegram_thread_id") or meta.get("thread_id")
    args: list[str] = []
    if chat_id not in {None, ""}:
        args += ["--chat-id", str(chat_id)]
    if thread_id not in {None, ""}:
        args += ["--thread-id", str(thread_id)]
    work_id = meta.get("work_id")
    run_id = meta.get("ledger_run_id") or meta.get("work_run_id")
    task_started_at = meta.get("task_started_at") or meta.get("started_at")
    if work_id not in {None, ""}:
        args += ["--work-id", str(work_id)]
    if run_id not in {None, ""}:
        args += ["--run-id", str(run_id)]
    if task_started_at not in {None, ""}:
        args += ["--task-started-at", str(task_started_at)]
    return args


def work_card_surface_receipt(key: str) -> dict[str, Any]:
    """Recover the durable card checkpoint after a child delivery failure.

    Managed group work-card sends checkpoint ambiguous Telegram responses
    before the child exits nonzero. The watcher must carry that state upward;
    treating it as a clean failure could create a duplicate surface even though
    Telegram may already have accepted JAIMES's first request.
    """
    if work_card is None:
        return {}
    try:
        state = work_card.load_state()
    except Exception:  # noqa: BLE001
        return {}
    cards = state.get("cards") if isinstance(state, dict) else None
    record = cards.get(key) if isinstance(cards, dict) else None
    if not isinstance(record, dict):
        return {}
    return {
        "header_message_id": _handoff_id(record.get("header_message_id")),
        "message_id": _handoff_id(record.get("message_id")),
        "surface_indeterminate": any(
            record.get(field) == "indeterminate"
            for field in ("header_delivery_status", "live_delivery_status")
        ),
    }


def send_initial_ack(text: str, timeout: int = 15, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    if work_card is None:
        return {"ok": False, "error": "jaimes_work_card unavailable"}
    payload = {
        "chat_id": telegram_target(meta),
        "text": text,
        "disable_notification": True,
    }
    apply_telegram_target(payload, meta)
    return work_card.api_call("sendMessage", payload, timeout=timeout)


def send_chat_action(action: str = "typing", meta: dict[str, Any] | None = None) -> None:
    if os.environ.get("JAIMES_TELEGRAM_TYPING_ACTIONS", "").lower() not in {"1", "true", "yes"}:
        return
    if work_card is None:
        return
    payload = apply_telegram_target({"chat_id": telegram_target(meta), "action": action}, meta)
    work_card.api_call("sendChatAction", payload, timeout=6)


def sanitize_error_text(value: Any, limit: int = 320) -> str:
    """Return bounded diagnostics with Telegram URLs and credentials removed."""
    error = str(value or "Telegram API call failed")
    error = re.sub(
        r"https://api\.telegram\.org/bot[^/\s]+",
        "https://api.telegram.org/bot<redacted>",
        error,
    )
    error = re.sub(
        r'(?i)("(?:[a-z0-9_]*(?:token|secret|password|api_key|access_token|cookie)|authorization)"\s*:\s*)"[^"]*"',
        r'\1"<redacted>"',
        error,
    )
    error = re.sub(
        r"(?i)('(?:[a-z0-9_]*(?:token|secret|password|api_key|access_token|cookie)|authorization)'\s*:\s*)'[^']*'",
        r"\1'<redacted>'",
        error,
    )
    error = re.sub(
        r"(?i)\b([a-z0-9_]*(?:token|secret|password|api_key|access_token|cookie)|authorization)\b\s*[=:]\s*(?:bearer\s+)?[^\s,;}\]]+",
        r"\1=<redacted>",
        error,
    )
    error = re.sub(
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{4,}\b",
        "Bearer <redacted>",
        error,
    )
    error = re.sub(
        r"(?i)\b(?:sk|xai|gh[pousr])[-_][A-Za-z0-9_-]{4,}\b",
        "<redacted>",
        error,
    )
    return error[: max(0, int(limit))]


def delivery_operation_id(method: str, delivery_key: str) -> str:
    return hashlib.sha256(
        f"{method}|{delivery_key or 'unscoped'}".encode("utf-8")
    ).hexdigest()[:24]


def refresh_delivery_error_state(
    state: dict[str, Any], unresolved: dict[str, Any]
) -> None:
    ordered = sorted(
        (item for item in unresolved.items() if isinstance(item[1], dict)),
        key=lambda item: str(item[1].get("at") or ""),
    )[-50:]
    state["unresolved_telegram_deliveries"] = dict(ordered)
    if ordered:
        state["last_telegram_delivery_error"] = ordered[-1][1]
    else:
        state.pop("last_telegram_delivery_error", None)


def resolve_delivery_incident(
    state: dict[str, Any], method: str, delivery_key: str
) -> None:
    """Retire a proven no-effect incident without inventing an API success."""
    unresolved = state.get("unresolved_telegram_deliveries")
    if not isinstance(unresolved, dict):
        return
    unresolved.pop(delivery_operation_id(method, delivery_key), None)
    refresh_delivery_error_state(state, unresolved)


def record_api_result(state: dict[str, Any], method: str, result: dict[str, Any]) -> None:
    """Keep short, secret-free Telegram API evidence in watcher state."""
    row = {"at": utc_now(), "method": method, "ok": bool(result.get("ok"))}
    delivery_key = str(result.get("delivery_key") or "").strip()
    operation_id = delivery_operation_id(method, delivery_key)
    if method in {"sendMessage", "editMessageText"}:
        row["operation"] = operation_id
    if not row["ok"]:
        row["error"] = sanitize_error_text(
            result.get("description") or result.get("error")
        )
    history = list(state.get("telegram_api_results") or [])
    history.append(row)
    state["telegram_api_results"] = history[-40:]
    if row["ok"]:
        state["last_telegram_api_success"] = row
        state.pop("last_telegram_api_error", None)
    else:
        state["last_telegram_api_error"] = row
    if method in {"sendMessage", "editMessageText"}:
        unresolved = state.get("unresolved_telegram_deliveries")
        if not isinstance(unresolved, dict):
            unresolved = {}
            previous = state.get("last_telegram_delivery_error")
            if isinstance(previous, dict):
                previous_method = str(previous.get("method") or "")
                previous_id = str(
                    previous.get("operation")
                    or delivery_operation_id(previous_method, "")
                )
                unresolved[previous_id] = previous
        if row["ok"]:
            unresolved.pop(operation_id, None)
            legacy_id = delivery_operation_id(method, "")
            for candidate_id, candidate in list(unresolved.items()):
                if not isinstance(candidate, dict):
                    continue
                if (
                    str(candidate.get("method") or "") == method
                    and (
                        not candidate.get("operation")
                        or str(candidate_id) == legacy_id
                    )
                ):
                    unresolved.pop(candidate_id, None)
        else:
            unresolved[operation_id] = row
        refresh_delivery_error_state(state, unresolved)


def verify_bot_identity(state: dict[str, Any]) -> bool:
    identity = state.get("telegram_identity") if isinstance(state.get("telegram_identity"), dict) else {}
    checked_at = parse_utc(identity.get("checked_at"))
    if checked_at and (dt.datetime.now(dt.timezone.utc) - checked_at).total_seconds() < BOT_IDENTITY_CHECK_SECONDS:
        return identity.get("ok") is True
    if work_card is None:
        state["telegram_identity"] = {"checked_at": utc_now(), "ok": False, "username": ""}
        return False
    result = work_card.api_call("getMe", {}, timeout=6)
    username = str((result.get("result") or {}).get("username") or "")
    ok = bool(result.get("ok") and username.casefold() == EXPECTED_BOT_USERNAME.casefold())
    record_api_result(state, "getMe", {
        "ok": ok,
        "error": "Telegram bot identity mismatch" if result.get("ok") and not ok else result.get("error") or "",
    })
    state["telegram_identity"] = {
        "checked_at": utc_now(),
        "ok": ok,
        "username": username if ok else "",
    }
    return ok


def set_eyes_reaction(platform_message_id: str, state: dict[str, Any], meta: dict[str, Any] | None = None) -> bool:
    if work_card is None or not platform_message_id:
        return False
    payload = apply_telegram_target({
        "chat_id": telegram_target(meta),
        "message_id": int(platform_message_id),
        "reaction": [{"type": "emoji", "emoji": "👀"}],
        "is_big": False,
    }, meta)
    result = work_card.api_call("setMessageReaction", payload, timeout=4)
    record_api_result(state, "setMessageReaction", result)
    return bool(result.get("ok"))


def send_message_draft(draft_id: int, text: str = "", meta: dict[str, Any] | None = None) -> None:
    """Optionally update Telegram draft text.

    Disabled by default. The custom draft lane has rendered badly in Telegram
    and can expose streaming/internal-looking text as overlapping UI. Keep the
    visible chat clean; use the editable work card instead.
    """
    if os.environ.get("JAIMES_TELEGRAM_DRAFTS", "").lower() not in {"1", "true", "yes"}:
        return
    if work_card is None:
        return
    safe = clean_prompt(text).replace("\n", " · ")[:280]
    payload = apply_telegram_target({"chat_id": telegram_target(meta), "draft_id": draft_id, "text": safe}, meta)
    work_card.api_call("sendMessageDraft", payload, timeout=6)


def edit_message(
    message_id: str,
    text: str,
    timeout: int = 15,
    meta: dict[str, Any] | None = None,
    parse_mode: str | None = None,
) -> dict[str, Any]:
    if work_card is None or not message_id:
        return {"ok": False, "error": "missing editable acknowledgement or work-card helper"}
    payload = {
        "chat_id": telegram_target(meta),
        "message_id": int(message_id) if str(message_id).isdigit() else message_id,
        "text": text,
        "disable_notification": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    apply_telegram_target(payload, meta)
    return work_card.api_call("editMessageText", payload, timeout=timeout)


def send_buttons_message(text: str, buttons: list, timeout: int = 15, meta: dict[str, Any] | None = None) -> str:
    if work_card is None:
        return ""
    payload = {
        "chat_id": telegram_target(meta),
        "text": text,
        "reply_markup": {"inline_keyboard": buttons},
        "disable_notification": True,
    }
    apply_telegram_target(payload, meta)
    result = work_card.api_call("sendMessage", payload, timeout=timeout)
    return str(result.get("result", {}).get("message_id") or "") if result.get("ok") else ""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Multiple watcher ticks can overlap briefly during launchd reloads. A
    # shared `.tmp` name let one process replace another process's temp file,
    # crashing the approval-button sender before it reached Telegram.
    tmp = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        # Replacing with the private temporary inode normally supplies this
        # mode already. The explicit chmod also repairs a legacy state file on
        # the next successful daemon write.
        os.chmod(path, 0o600)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _handoff_id(value: Any, *, allow_negative: bool = False) -> str:
    text = str(value or "").strip()
    pattern = r"-?\d+" if allow_negative else r"\d+"
    if not re.fullmatch(pattern, text):
        return ""
    if not allow_negative and int(text) <= 0:
        return ""
    return text


def positive_message_id(value: Any) -> str:
    """Return a confirmed Telegram message id or an empty string."""
    text = str(value or "").strip()
    return text if text.isdigit() and int(text) > 0 else ""


def registerable_ack_result(result: dict[str, Any]) -> bool:
    """Only a concrete, objective-bound surface may become active state."""
    if not result.get("ok"):
        return False
    if str(result.get("status") or "").strip().lower() == "awaiting-objective-interpretation":
        return False
    if result.get("requires_objective_interpretation"):
        return False
    return bool(
        str(result.get("key") or "").strip()
        and str(result.get("objective") or "").strip()
        and str(result.get("route") or "").strip()
        and positive_message_id(result.get("ack_message_id"))
    )


def handoff_identity(chat_id: Any, thread_id: Any, message_id: Any) -> tuple[str, str, str]:
    chat = _handoff_id(chat_id, allow_negative=True)
    thread = _handoff_id(thread_id)
    message = _handoff_id(message_id)
    if not chat or not thread or not message:
        raise ValueError("handoff origin ids must be numeric")
    return chat, thread, message


def handoff_paths(chat_id: Any, thread_id: Any, message_id: Any) -> tuple[Path, Path]:
    chat, thread, message = handoff_identity(chat_id, thread_id, message_id)
    digest = hashlib.sha256(f"{chat}:{thread}:{message}".encode("utf-8")).hexdigest()
    return HANDOFF_DIR / f"{digest}.json", HANDOFF_DIR / f"{digest}.lock"


@contextlib.contextmanager
def handoff_lock(chat_id: Any, thread_id: Any, message_id: Any):
    record_path, lock_path = handoff_paths(chat_id, thread_id, message_id)
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(HANDOFF_DIR, 0o700)
    except OSError:
        pass
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield record_path


def write_handoff_record(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def parse_utc(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def handoff_record_matches(record: dict[str, Any], chat_id: Any, thread_id: Any, message_id: Any) -> bool:
    try:
        chat, thread, message = handoff_identity(chat_id, thread_id, message_id)
    except ValueError:
        return False
    return (
        str(record.get("chat_id") or "") == chat
        and str(record.get("thread_id") or "") == thread
        and str(record.get("inbound_message_id") or "") == message
    )


def handoff_record_fresh(record: dict[str, Any]) -> bool:
    expires = parse_utc(record.get("expires_at"))
    return bool(expires and expires > dt.datetime.now(dt.timezone.utc))


def handoff_claim_matches(
    record: dict[str, Any],
    chat_id: Any,
    thread_id: Any,
    message_id: Any,
    claim_token: str,
) -> bool:
    """Return true only while this exact worker still owns the handoff claim."""
    return bool(
        claim_token
        and isinstance(record, dict)
        and record.get("status") in {"claimed", "indeterminate"}
        and secrets.compare_digest(str(record.get("claim_token") or ""), claim_token)
        and handoff_record_matches(record, chat_id, thread_id, message_id)
    )


def public_indeterminate_handoff_receipt(record: dict[str, Any]) -> dict[str, Any]:
    """Return a privacy-safe ownership receipt without exposing the claim token."""
    return {
        "ok": True,
        "handled": True,
        "schema_version": int(record.get("schema_version") or 1),
        "status": "indeterminate",
        "ownership_state": "claimed_in_flight",
        "agent": "jaimes",
        "chat_id": str(record.get("chat_id") or ""),
        "thread_id": str(record.get("thread_id") or ""),
        "inbound_message_id": str(record.get("inbound_message_id") or ""),
        "indeterminate_at": str(record.get("indeterminate_at") or ""),
        "expires_at": str(record.get("expires_at") or ""),
    }


def handoff_event_state(meta: dict[str, Any], event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return `process`, `wait`, or `consume` for an exact Inbox handoff row."""
    chat_id = meta.get("telegram_chat_id")
    thread_id = meta.get("telegram_thread_id")
    message_id = event.get("platform_message_id") or ((meta.get("origin") or {}).get("message_id"))
    try:
        with handoff_lock(chat_id, thread_id, message_id) as record_path:
            record = load_json(record_path, {})
            if not isinstance(record, dict) or not handoff_record_matches(record, chat_id, thread_id, message_id):
                age = event_age_seconds(str(event.get("ts") or ""))
                if age is not None and age > HANDOFF_LEASE_ARRIVAL_GRACE_SECONDS:
                    expired = {
                        "schema_version": 1,
                        "status": "cancelled",
                        "agent": "jaimes",
                        "chat_id": str(chat_id or ""),
                        "thread_id": str(thread_id or ""),
                        "inbound_message_id": str(message_id or ""),
                        "cancelled_at": utc_now(),
                        "expires_at": utc_now(),
                        "reason": "handoff_lease_never_arrived_josh_fallback_owned",
                    }
                    write_handoff_record(record_path, expired)
                    return "consume", expired
                return "wait", {}
            status = str(record.get("status") or "")
            if status in {"accepted", "failed", "cancelled"}:
                # A crash after the durable terminal receipt but before the
                # watcher cursor save must consume this row without resending.
                return "consume", record
            if status == "waiting" and handoff_record_fresh(record):
                return "process", record
            if status in {"waiting", "claimed", "indeterminate"} and not handoff_record_fresh(record):
                expired = dict(record)
                if status == "waiting" or record.get("ownership_state") == "claimed_no_effect":
                    expired.update({
                        "status": "cancelled",
                        "cancelled_at": utc_now(),
                        "reason": "handoff_expired_before_surface",
                    })
                else:
                    expired.update({
                        "status": "failed",
                        "failed_at": utc_now(),
                        "reason": "handoff_surface_owner_expired",
                    })
                write_handoff_record(record_path, expired)
                return "consume", expired
            return "wait", record
    except ValueError:
        return "wait", {}


def recover_accepted_handoff_card(
    state: dict[str, Any],
    event: dict[str, Any],
    meta: dict[str, Any],
    record: dict[str, Any],
) -> None:
    """Restore minimal private tracking after accepted-receipt/cursor split-brain."""
    if record.get("status") != "accepted":
        return
    run_id = str(event.get("run_id") or "")
    message_id = str(event.get("platform_message_id") or record.get("inbound_message_id") or "")
    if not run_id or not message_id:
        return
    cards = state.setdefault("active_cards", {})
    if run_id in cards:
        return
    key = f"jaimes-fast-ack-{meta.get('telegram_chat_id') or 'telegram'}-{message_id}"
    work_id, ledger_run_id, origin_claim_hash = telegram_work_identity(key, run_id)
    cards[run_id] = {
        "key": key,
        "work_id": work_id,
        "ledger_run_id": ledger_run_id,
        "origin_claim_hash": origin_claim_hash,
        "objective": objective_from_prompt(str(event.get("prompt") or "")),
        "model": str(meta.get("model") or DEFAULT_MODEL),
        "route": "Recovered from durable JAIMES Inbox acceptance",
        "header_message_id": str(record.get("header_message_id") or ""),
        "ack_message_id": str(record.get("live_message_id") or ""),
        "telegram_chat_id": str(meta.get("telegram_chat_id") or ""),
        "telegram_thread_id": str(meta.get("telegram_thread_id") or ""),
        "session_id": str(event.get("session_id") or meta.get("sessionId") or ""),
        "task_started_at": str(event.get("ts") or record.get("accepted_at") or utc_now()),
        "started_at": str(record.get("accepted_at") or utc_now()),
        "last_progress_at": str(record.get("accepted_at") or utc_now()),
        "last_card_update_at": str(record.get("accepted_at") or utc_now()),
        "status": "active",
        "retention": "persistent-edit-only",
        "recovered_from_handoff_receipt": True,
    }


def await_handoff(chat_id: Any, thread_id: Any, message_id: Any, timeout: float) -> tuple[int, dict[str, Any]]:
    """Wait for exact JAIMES surface acceptance without carrying prompt data."""
    try:
        chat, thread, message = handoff_identity(chat_id, thread_id, message_id)
    except ValueError as exc:
        return 2, {"ok": False, "status": "invalid-origin", "error": str(exc)}
    wait_seconds = min(12.0, max(0.5, float(timeout)))
    deadline = time.monotonic() + wait_seconds
    lease_expiry = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=wait_seconds)
    with handoff_lock(chat, thread, message) as record_path:
        existing = load_json(record_path, {})
        if not (
            isinstance(existing, dict)
            and existing.get("status") in {"waiting", "claimed", "indeterminate", "accepted"}
            and handoff_record_matches(existing, chat, thread, message)
            and handoff_record_fresh(existing)
        ):
            write_handoff_record(record_path, {
                "schema_version": 1,
                "status": "waiting",
                "agent": "jaimes",
                "chat_id": chat,
                "thread_id": thread,
                "inbound_message_id": message,
                "created_at": utc_now(),
                "expires_at": lease_expiry.isoformat().replace("+00:00", "Z"),
            })

    while True:
        with handoff_lock(chat, thread, message) as record_path:
            record = load_json(record_path, {})
            accepted = bool(
                isinstance(record, dict)
                and record.get("status") == "accepted"
                and handoff_record_matches(record, chat, thread, message)
                and handoff_record_fresh(record)
                and record.get("reaction_ok") is True
                and _handoff_id(record.get("header_message_id"))
                and _handoff_id(record.get("live_message_id"))
            )
            if accepted:
                return 0, {"ok": True, **record}
            indeterminate = bool(
                isinstance(record, dict)
                and record.get("status") == "indeterminate"
                and record.get("ownership_state") == "claimed_in_flight"
                and handoff_record_matches(record, chat, thread, message)
                and handoff_record_fresh(record)
                and str(record.get("claim_token") or "")
            )
            if indeterminate:
                return 0, public_indeterminate_handoff_receipt(record)
            if isinstance(record, dict) and record.get("status") in {"failed", "cancelled"}:
                return 2, {"ok": False, **record}
            if time.monotonic() >= deadline:
                if (
                    isinstance(record, dict)
                    and record.get("status") == "claimed"
                    and handoff_record_matches(record, chat, thread, message)
                ):
                    if record.get("ownership_state") == "claimed_no_effect":
                        record.update({
                            "status": "cancelled",
                            "cancelled_at": utc_now(),
                            "reason": "handoff_timeout_before_surface",
                        })
                        write_handoff_record(record_path, record)
                        return 2, {
                            "ok": False,
                            "status": "timeout",
                            "agent": "jaimes",
                            "chat_id": chat,
                            "thread_id": thread,
                            "inbound_message_id": message,
                        }
                    if record.get("ownership_state") == "surface_inflight":
                        durable = work_card_surface_receipt(str(record.get("card_key") or ""))
                        has_surface_evidence = bool(
                            durable.get("surface_indeterminate")
                            or durable.get("header_message_id")
                            or durable.get("message_id")
                        )
                        if not has_surface_evidence:
                            record.update({
                                "status": "cancelled",
                                "cancelled_at": utc_now(),
                                "reason": "handoff_timeout_without_durable_surface_evidence",
                            })
                            write_handoff_record(record_path, record)
                            return 2, {
                                "ok": False,
                                "status": "timeout",
                                "agent": "jaimes",
                                "chat_id": chat,
                                "thread_id": thread,
                                "inbound_message_id": message,
                            }
                    now = dt.datetime.now(dt.timezone.utc)
                    record.update({
                        "status": "indeterminate",
                        "ownership_state": "claimed_in_flight",
                        "indeterminate_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        "expires_at": (now + dt.timedelta(seconds=HANDOFF_RECEIPT_TTL_SECONDS)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    })
                    write_handoff_record(record_path, record)
                    return 0, public_indeterminate_handoff_receipt(record)
                if isinstance(record, dict) and record.get("status") == "waiting":
                    record.update({"status": "cancelled", "cancelled_at": utc_now(), "reason": "handoff_timeout"})
                    write_handoff_record(record_path, record)
                return 2, {
                    "ok": False,
                    "status": "timeout",
                    "agent": "jaimes",
                    "chat_id": chat,
                    "thread_id": thread,
                    "inbound_message_id": message,
                }
        time.sleep(0.1)


def queue_forwarded_x_intelligence(event: dict[str, Any], meta: dict[str, Any]) -> int:
    """Queue public X status URLs without opening, scraping, or mutating X."""
    urls = list(dict.fromkeys(X_STATUS_URL_RE.findall(str(event.get("prompt") or ""))))
    if not urls:
        return 0
    X_INTELLIGENCE_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    queued = 0
    with X_INTELLIGENCE_QUEUE.open("a", encoding="utf-8") as handle:
        for url in urls:
            fingerprint = hashlib.sha256(f"{event.get('session_id')}:{event.get('ts')}:{url}".encode()).hexdigest()[:20]
            handle.write(json.dumps({
                "fingerprint": fingerprint,
                "url": url,
                "received_at": utc_now(),
                "session_id": event.get("session_id"),
                "source_timestamp": event.get("ts"),
                "telegram_chat_id": meta.get("telegram_chat_id"),
                "telegram_thread_id": meta.get("telegram_thread_id"),
                "status": "pending_public_verification",
                "policy": {"logged_in_x_scraping": False, "xai_enabled": False, "account_mutation": False},
            }, sort_keys=True) + "\n")
            queued += 1
    return queued


def local_time_label() -> str:
    return dt.datetime.now().astimezone().strftime("%H:%M:%S %Z")


def session_metadata() -> dict[str, Any]:
    # Hermes state.db is authoritative for current gateway sessions. The
    # legacy OpenCLAW/Hermes JSON stores can lag after /new or a model change.
    db_session = active_hermes_session_metadata()
    if db_session:
        return db_session
    candidates: list[tuple[str, dict[str, Any]]] = []
    session_stores = [load_json(SESSIONS_PATH, {}), load_json(HERMES_SESSIONS_PATH, {})]
    for sessions in session_stores:
        if not isinstance(sessions, dict):
            continue
        for key, value in sessions.items():
            target = parse_telegram_target_from_key(str(key))
            chat_id = str(target.get("telegram_chat_id") or "")
            thread_id = str(target.get("telegram_thread_id") or "")
            is_direct = str(key) in DIRECT_SESSION_KEYS
            is_authorized_group_topic = chat_id == CONTROL_CENTER_CHAT_ID and bool(thread_id)
            if not is_direct and not is_authorized_group_topic:
                continue
            normalized = normalize_session_metadata(value, assume_telegram=True)
            if not normalized:
                continue
            normalized.update(target)
            normalized["session_key"] = str(key)
            candidates.append((str(value.get("updatedAt") or value.get("updated_at") or ""), normalized))
    if not candidates:
        return {}
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def active_hermes_sessions_metadata() -> list[dict[str, Any]]:
    """Return every active Telegram session owned by JAIMES.

    #JAIMES: Telegram rollover can keep routing a fresh prompt into an older
    # owned session, so the live-card watcher must scan all owned sessions
    # instead of trusting only the most recently started session.
    """
    if not HERMES_STATE_DB.exists():
        return []
    try:
        with sqlite3.connect(HERMES_STATE_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, model, model_config, session_key, chat_id, thread_id,
                       started_at, origin_json
                  FROM sessions
                 WHERE source = 'telegram'
                   AND ended_at IS NULL
                   AND (chat_id = '6218150306' OR chat_id = ?)
              ORDER BY started_at DESC
                """,
                (CONTROL_CENTER_CHAT_ID,),
            ).fetchall()
    except (OSError, sqlite3.Error):
        return []
    sessions: list[dict[str, Any]] = []
    for row in rows:
        try:
            model_config = json.loads(str(row["model_config"] or "{}"))
        except (TypeError, ValueError):
            model_config = {}
        runtime = model_config.get("gateway_runtime") if isinstance(model_config, dict) else {}
        provider = str((runtime or {}).get("provider") or "openai-codex")
        model = str(row["model"] or DEFAULT_MODEL.split("/", 1)[-1])
        session_key = str(row["session_key"] or "")
        target = parse_telegram_target_from_key(session_key)
        target.update({
            "sessionId": str(row["id"]),
            "channel": "telegram",
            "model": f"{provider}/{model}",
            "provider": provider,
            "runtime_model": model,
            "session_key": session_key,
            "telegram_chat_id": str(row["chat_id"] or target.get("telegram_chat_id") or ""),
        })
        if row["thread_id"] is not None:
            target["telegram_thread_id"] = str(row["thread_id"])
        try:
            target["origin"] = json.loads(str(row["origin_json"] or "{}"))
        except (TypeError, ValueError):
            target["origin"] = {}
        sessions.append(target)
    return sessions


def active_hermes_session_metadata() -> dict[str, Any]:
    sessions = active_hermes_sessions_metadata()
    return sessions[0] if sessions else {}


def normalize_session_metadata(value: Any, assume_telegram: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    session_id = value.get("sessionId") or value.get("session_id")
    channel = value.get("channel") or value.get("platform") or value.get("origin", {}).get("platform")
    if assume_telegram and not channel:
        channel = "telegram"
    if not session_id or channel != "telegram":
        return {}
    normalized = dict(value)
    normalized["sessionId"] = session_id
    normalized["channel"] = "telegram"
    provider = str(value.get("provider") or "").strip()
    model = str(value.get("model") or DEFAULT_MODEL).strip()
    normalized["model"] = f"{provider}/{model}" if provider and "/" not in model else model
    return normalized


def runtime_route(model: str) -> tuple[str, str]:
    lower = model.lower()
    if "gpt-5.6-sol" in lower:
        return "JAIMES verified execution", "heavy workhorse reasoning"
    if "gpt-5.6-terra" in lower:
        return "JAIMES balanced execution", "balanced speed and depth"
    if "gpt-5.6-luna" in lower:
        return "JAIMES lightweight execution", "bounded low-complexity work"
    if "gemini" in lower:
        return "JAIMES review helper", "safe synthesis or review"
    if "grok" in lower:
        return "JAIMES current-events specialist", "X-native or current context"
    return "JAIMES execution", "active Hermes session"


def recent_prompt_events(session_id: str) -> list[dict[str, str]]:
    path = first_existing_session_path(session_id)
    if not path.exists():
        return []
    events: list[dict[str, str]] = []
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 256_000))
            raw = handle.read().decode("utf-8", errors="ignore")
    except Exception:
        return []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except Exception:
            continue
        if item.get("type") != "prompt.submitted":
            continue
        ts = str(item.get("ts") or "")
        data = item.get("data") or {}
        if ts:
            events.append({
                "session_id": session_id,
                "ts": ts,
                "run_id": str(item.get("runId") or ""),
                "seq": str(item.get("seq") or ""),
                "prompt": str(data.get("prompt") or ""),
            })
    return events


def recent_prompt_events_from_state_db(session_id: str, after_message_id: int) -> list[dict[str, str]]:
    """Read new direct prompts from Hermes' canonical session store.

    Current Hermes Telegram sessions persist in ``state.db``, not trajectory
    files. The old reader remains for compatibility, but this is the live path.
    """
    if not HERMES_STATE_DB.exists():
        return []
    query = """
        SELECT id, timestamp, content, platform_message_id
        FROM messages
        WHERE session_id = ? AND role = 'user' AND id > ?
          AND TRIM(COALESCE(content, '')) != ''
        ORDER BY id ASC
    """
    con = sqlite3.connect(f"file:{HERMES_STATE_DB}?mode=ro", uri=True, timeout=2)
    try:
        rows = con.execute(query, (session_id, after_message_id)).fetchall()
    finally:
        con.close()
    events: list[dict[str, str]] = []
    for message_id, timestamp, prompt, platform_message_id in rows:
        events.append({
            "session_id": session_id,
            "ts": dt.datetime.fromtimestamp(float(timestamp), dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "run_id": f"telegram-message-{message_id}",
            "seq": str(message_id),
            "prompt": str(prompt or ""),
            "platform_message_id": str(platform_message_id or ""),
            "db_message_id": str(message_id),
        })
    return events


def final_assistant_record_after(session_id: str, user_message_id: int) -> dict[str, Any]:
    """Return the delivered final record for exactly one user turn."""
    if not HERMES_STATE_DB.exists():
        return {}
    con = sqlite3.connect(f"file:{HERMES_STATE_DB}?mode=ro", uri=True, timeout=2)
    try:
        next_user = con.execute(
            "SELECT MIN(id) FROM messages WHERE session_id = ? AND role = 'user' AND id > ?",
            (session_id, user_message_id),
        ).fetchone()
        upper_id = int(next_user[0]) if next_user and next_user[0] else 2**63 - 1
        row = con.execute(
            """
            SELECT id, content, platform_message_id, timestamp FROM messages
             WHERE session_id = ? AND role = 'assistant'
               AND id > ? AND id < ? AND TRIM(COALESCE(content, '')) != ''
             ORDER BY id DESC LIMIT 1
            """,
            (session_id, user_message_id, upper_id),
        ).fetchone()
        if not row:
            return {}
        return {
            "id": int(row[0]),
            "content": str(row[1] or ""),
            "platform_message_id": str(row[2] or ""),
            "recorded_at": dt.datetime.fromtimestamp(
                float(row[3]), dt.timezone.utc
            ).isoformat().replace("+00:00", "Z") if row[3] is not None else "",
        }
    finally:
        con.close()


def final_assistant_message_after(session_id: str, user_message_id: int) -> str:
    """Compatibility wrapper for callers that only need final content."""
    return str(final_assistant_record_after(session_id, user_message_id).get("content") or "")


FINAL_SECTION_ALIASES = {
    "what was done": "done",
    "tldr": "done",
    "tl;dr": "done",
    "objective complete": "done",
    "issues": "issues",
    "challenges": "issues",
    "blockers": "issues",
    "appropriate next steps": "next",
    "next steps": "next",
    "next": "next",
    "approval needed": "approval",
    "mitigation steps for approval": "approval",
}


def clean_final_item(value: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    # Output is rendered inside a preformatted block, where underscores are
    # literal identifier characters rather than Markdown emphasis. Preserve
    # names such as ``jaimes_live_card.py`` while still removing markup that
    # would create visual noise in the canonical summary.
    text = re.sub(r"[`*#]", "", text)
    text = re.sub(r"^[\s>\-•]+", "", text)
    text = re.sub(r"^\d+[.)]\s*", "", text)
    text = " ".join(text.split()).strip(" :-")
    return text[:260]


FINAL_STATUS_ONLY_RE = re.compile(
    r"(?i)^(?:the\s+)?(?:assessment|analysis|review|task|request|work|worker execution|"
    r"runtime outcome|result|summary|final review|live[- ]work lifecycle)\s+"
    r"(?:is\s+|was\s+)?(?:complete|completed|done|finished|verified|prepared|closed)\.?$"
)
FINAL_RESULT_SIGNAL_RE = re.compile(
    r"(?i)\b(?:confirmed|found|identified|determined|changed|fixed|added|removed|"
    r"implemented|differ(?:s|ed|ent)?|caus(?:e|es|ed)|repair(?:s|ed)?|"
    r"(?:en|dis)abl(?:e|es|ed|ing)|reconcil(?:e|es|ed)|retir(?:e|es|ed)|"
    r"replac(?:e|es|ed)|rerout(?:e|es|ed)|mov(?:e|es|ed)|prevent(?:s|ed)?|"
    r"preserv(?:e|es|ed)|verified|completed|executed|ran|processed|measured|"
    r"recorded|delivered|returned|produced|passed|failed|"
    r"cannot|can't|could not|does not|"
    r"unsupported|risk|recommend(?:ed|ation)?|should|avoid|blocked|requires?|"
    r"increased|decreased|matched|differs?|supports?)\b"
)
FINAL_NUMERIC_RESULT_RE = re.compile(
    r"(?i)(?:\b\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\b|"
    r"\b\d+(?:\.\d+)?\s*%\b|"
    r"\b(?:failures?|errors?|tasks?|outputs?|hashes?|rounds?|latency)\s*[:=]?\s*\d)",
)
FINAL_BENCHMARK_FRACTION_RE = re.compile(r"\b\d+\s*/\s*\d+\b")
FINAL_ZERO_FAILURE_RE = re.compile(
    r"(?i)(?:\b(?:failures?|errors?)\s*[:=]?\s*0\b|\b0\s+(?:failures?|errors?)\b)"
)
FINAL_RISK_RE = re.compile(
    r"(?i)\b(?:risk|cannot|can't|could not|does not|unsupported|unsafe|avoid|"
    r"do not|blocked|failure|failed|limitation|credential|permission)\b"
)
FINAL_RECOMMENDATION_RE = re.compile(
    r"(?i)\b(?:recommend(?:ed|ation)?|next|should|use\b|avoid|do not|retry|"
    r"follow[- ]?up|proceed|keep|remove|add|enable|disable|review)\b"
)
FINAL_NO_ACTION_RE = re.compile(
    r"(?i)\b(?:no action needed|no further action|nothing else (?:is )?needed)\b"
)
FINAL_EVIDENCE_FIELD_RE = re.compile(
    r"(?i)\b(workId|runId|observedAt|mode)\s*[:=]\s*([^|\n]+)"
)
FINAL_SESSION_HISTORY_RE = re.compile(r"(?i)\bsession[- ]history\b")
FINAL_HISTORICAL_OBJECTIVE_RE = re.compile(
    r"(?i)\b(?:historical|history|retrospective|archive|archived|previous|prior|"
    r"earlier|past|last\s+(?:run|week|month))\b"
)


def parse_final_timestamp(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def final_evidence_fields(text: str) -> dict[str, str]:
    """Read the private provenance line that the formatter removes."""
    fields: dict[str, str] = {}
    for raw in str(text or "").splitlines():
        if not re.match(r"(?i)^\s*Evidence\s*:", raw):
            continue
        for name, value in FINAL_EVIDENCE_FIELD_RE.findall(raw):
            fields[name.lower()] = clean_final_item(value)
    return fields


def final_source_uses_session_history(text: str) -> bool:
    header = re.search(
        r"(?im)^Model:\s*[^|\n]+\s*\|\s*Route:\s*([^|\n]+)",
        str(text or ""),
    )
    return bool(header and FINAL_SESSION_HISTORY_RE.search(header.group(1)))


def final_evidence_problems(
    text: str,
    *,
    objective: str,
    work_id: str = "",
    run_id: str = "",
    task_started_at: str = "",
    response_recorded_at: str = "",
) -> list[str]:
    """Fail closed when a current task is answered from unbound old history."""
    task_time = parse_final_timestamp(task_started_at)
    response_time = parse_final_timestamp(response_recorded_at)
    if task_time and response_time and response_time < task_time:
        return ["The response record predates the current Telegram task."]

    if FINAL_HISTORICAL_OBJECTIVE_RE.search(str(objective or "")):
        return []

    fields = final_evidence_fields(text)
    problems: list[str] = []
    expected = {
        "workid": str(work_id or ""),
        "runid": str(run_id or ""),
    }
    for name, value in expected.items():
        observed = str(fields.get(name) or "")
        if observed and value and observed != value:
            problems.append(f"The evidence {name} does not match the current task.")

    observed_at = parse_final_timestamp(fields.get("observedat", ""))
    if task_time and observed_at and observed_at < task_time:
        problems.append("The cited evidence predates the current Telegram task.")

    historical_mode = str(fields.get("mode") or "").lower() in {
        "historical", "history", "session-history", "session history",
    }
    session_history = historical_mode or final_source_uses_session_history(text)
    has_current_context = bool(work_id or run_id or task_started_at)
    if session_history and has_current_context:
        if work_id and fields.get("workid") != work_id:
            problems.append("Session-history evidence is not bound to the current work record.")
        if run_id and fields.get("runid") != run_id:
            problems.append("Session-history evidence is not bound to the current run.")
        if task_time and (not observed_at or observed_at < task_time):
            problems.append("Session-history evidence was not observed during the current task.")
    return list(dict.fromkeys(problems))


def stale_evidence_sections(problems: list[str]) -> tuple[bool, dict[str, list[str]]]:
    """Replace stale claims with a truthful current-run retry result."""
    return False, {
        "done": [
            "Held the response before treating historical findings as current results.",
            "Preserved the current task identity for a focused evidence retry.",
            "No unbound session-history claim was accepted as current evidence.",
        ],
        "issues": unique_final_items(problems)[:5],
        "next": ["Retry using evidence produced and identified for the current task and run."],
        "approval": [],
    }


def split_final_items(value: str) -> list[str]:
    """Extract source statements without manufacturing result bullets."""
    cleaned = clean_final_item(value)
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\s*[|•]\s*", cleaned)
    return [item for item in (clean_final_item(part) for part in parts) if item]


def unique_final_items(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = clean_final_item(item)
        key = re.sub(r"\W+", " ", cleaned.lower()).strip()
        if cleaned and key and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def substantive_final_item(value: str) -> bool:
    cleaned = clean_final_item(value)
    if len(cleaned.split()) < 4 or FINAL_STATUS_ONLY_RE.fullmatch(cleaned):
        return False
    lowered = cleaned.lower()
    return not any(marker in lowered for marker in (
        "verified the runtime outcome",
        "prepared the result for telegram delivery",
        "closed the live-work lifecycle",
        "closed the live work lifecycle",
        "agent work reached final review",
        "response formatting was recovered",
        "live card ordering was preserved",
        "checked the request and identified the unresolved issue",
    ))


def truthful_incomplete_sections(
    sections: dict[str, list[str]],
    issue: str,
) -> tuple[bool, dict[str, list[str]]]:
    preserved = [item for item in unique_final_items(sections["done"]) if substantive_final_item(item)]
    for statement in (
        "The agent response did not include enough concrete findings or outcomes.",
        "Available details were preserved without inventing missing facts.",
        "A focused retry is required to produce a useful final summary.",
    ):
        if len(preserved) >= 3:
            break
        preserved.append(statement)
    sections["done"] = unique_final_items(preserved)[:5]
    sections["issues"] = unique_final_items([*sections["issues"], issue])[:5]
    sections["next"] = [
        "Retry with evidence, concrete findings, and a supported recommendation."
    ]
    sections["approval"] = []
    return False, sections


def parse_final_sections(text: str) -> tuple[bool, dict[str, list[str]]]:
    sections: dict[str, list[str]] = {"done": [], "issues": [], "next": [], "approval": []}
    current = "done"
    explicit_complete: bool | None = None
    for raw in str(text or "").splitlines():
        line = clean_final_item(raw)
        if not line:
            continue
        complete_match = re.match(r"(?i)^complete\s*:\s*(yes|no)\b(?:\s*[-—:]\s*(.*))?$", line)
        if complete_match:
            explicit_complete = complete_match.group(1).lower() == "yes"
            if complete_match.group(2):
                sections["done" if explicit_complete else "issues"].extend(
                    split_final_items(complete_match.group(2))
                )
            continue
        normalized = re.sub(r"[^a-z; ]", "", line.lower()).strip()
        if normalized in FINAL_SECTION_ALIASES:
            current = FINAL_SECTION_ALIASES[normalized]
            continue
        if re.match(r"(?i)^(?:model|route|objective|status|evidence)\s*:", line):
            continue
        if line.lower() not in {"n/a", "na", "none", "not applicable"}:
            sections[current].extend(split_final_items(line))

    sections = {key: unique_final_items(values) for key, values in sections.items()}

    if explicit_complete is None:
        failure_text = " ".join(sections["issues"] + sections["done"]).lower()
        explicit_complete = not any(marker in failure_text for marker in (
            "couldn't", "could not", "failed", "blocked", "unavailable", "not complete", "needs attention",
        ))
    source_text = html.unescape(str(text or ""))
    substantive = [item for item in sections["done"] if substantive_final_item(item)]
    result_bearing = [
        item for item in substantive
        if FINAL_RESULT_SIGNAL_RE.search(item) or FINAL_NUMERIC_RESULT_RE.search(item)
    ]
    benchmark_success = bool(
        explicit_complete
        and FINAL_BENCHMARK_FRACTION_RE.search(source_text)
        and FINAL_ZERO_FAILURE_RE.search(source_text)
    )
    risk_items = [
        item for item in substantive
        if FINAL_RISK_RE.search(item) and not FINAL_ZERO_FAILURE_RE.search(item)
    ]
    if risk_items and not sections["issues"]:
        # Copying a source statement into Issues surfaces the limitation
        # without inferring a fact that the agent did not provide.
        sections["issues"] = risk_items[:5]

    recommendation_items = [item for item in substantive if FINAL_RECOMMENDATION_RE.search(item)]
    if not sections["next"] and recommendation_items:
        sections["next"] = recommendation_items[:3]
    no_action_match = FINAL_NO_ACTION_RE.search(source_text)
    if not sections["next"] and no_action_match:
        sections["next"] = [no_action_match.group(0).rstrip(".") + "."]
    if benchmark_success and not sections["next"]:
        sections["next"] = ["No action needed."]

    no_action = any(FINAL_NO_ACTION_RE.search(item) for item in sections["next"])
    recommendation_or_risk = bool(recommendation_items or risk_items or sections["issues"])
    quality_problems: list[str] = []
    if explicit_complete:
        if len(substantive) < 3 and not benchmark_success:
            quality_problems.append("fewer than three substantive source-provided findings")
        if len(result_bearing) < 2 and not benchmark_success:
            quality_problems.append("fewer than two concrete findings or outcomes")
        if not sections["next"]:
            quality_problems.append("no supported recommendation or next step")
        if no_action and recommendation_or_risk:
            quality_problems.append("No action needed conflicts with the reported recommendation or risk")
    if quality_problems:
        return truthful_incomplete_sections(
            sections,
            "Detailed findings were not captured well enough for a reliable completion: "
            + "; ".join(quality_problems) + ".",
        )

    if not explicit_complete:
        return truthful_incomplete_sections(
            sections,
            "The source response did not establish a complete, reliable outcome.",
        )
    sections["done"] = substantive[:5]
    sections["issues"] = sections["issues"][:5]
    sections["next"] = sections["next"][:5]
    sections["approval"] = sections["approval"][:5]
    return True, sections


def structured_final_text(
    text: str,
    *,
    objective: str,
    model: str,
    route: str,
    why: str = "",
    work_id: str = "",
    run_id: str = "",
    task_started_at: str = "",
    response_recorded_at: str = "",
) -> str:
    """Normalize a native Hermes final to the canonical fixed-width contract."""
    complete, sections = parse_final_sections(text)
    if complete and (
        not clean_final_item(model)
        or not clean_final_item(route)
        or "unverified" in f"{model} {route}".lower()
    ):
        complete, sections = truthful_incomplete_sections(
            sections,
            "The runtime model or route was not verified, so completion cannot be claimed reliably.",
        )
    evidence_problems = final_evidence_problems(
        text,
        objective=objective,
        work_id=work_id,
        run_id=run_id,
        task_started_at=task_started_at,
        response_recorded_at=response_recorded_at,
    )
    if evidence_problems:
        complete, sections = stale_evidence_sections(evidence_problems)

    def wrap(value: str, *, indent: str = "") -> list[str]:
        return textwrap.wrap(
            value,
            width=38,
            subsequent_indent=indent,
            break_long_words=True,
            break_on_hyphens=False,
        ) or [""]

    def bullets(items: list[str], fallback: str) -> list[str]:
        chosen = [clean_final_item(item) for item in items if clean_final_item(item)][:5] or [fallback]
        rows: list[str] = []
        for item in chosen:
            rows.extend(wrap(f"- {item}", indent="  "))
        return rows

    model_label = clean_final_item(model) or "unverified"
    route_label = clean_final_item(route) or "unverified"
    why_label = clean_final_item(why) or "verified JAIMES execution"
    route_match = re.fullmatch(
        r"(.+?)\s*\|\s*Why:\s*(.+)",
        route_label,
        flags=re.I,
    )
    if route_match:
        route_label = clean_final_item(route_match.group(1)) or "unverified"
        if not clean_final_item(why):
            why_label = clean_final_item(route_match.group(2)) or why_label
    objective_label = clean_final_item(objective) or "Complete the current Telegram task"
    next_items = sections["next"]
    approval_items = sections["approval"]
    lines = [
        *wrap(
            f"Model: {model_label} | Route: {route_label} | Why: {why_label}",
            indent="   ",
        ),
        "",
        *wrap(f"Complete: {'Yes' if complete else 'No'} - {objective_label}", indent="   "),
        "",
        "What was done:",
        *bullets(sections["done"], "Completed the request."),
        "",
        "Issues:",
        *bullets(sections["issues"], "n/a"),
        "",
        "Appropriate next steps:",
        *bullets(next_items, "Review the issue and choose the next safe step." if not complete else "No action needed."),
        "",
        "Approval needed:",
        *bullets(approval_items, "n/a"),
    ]
    return f"<pre>{html.escape(chr(10).join(lines))}</pre>"


def final_contract_is_canonical(value: str) -> bool:
    plain = html.unescape(re.sub(r"^<pre>|</pre>$", "", str(value or "").strip(), flags=re.I))
    labels = ["Complete:", "What was done:", "Issues:", "Appropriate next steps:", "Approval needed:"]
    positions = [plain.find(label) for label in labels]
    complete_at = plain.find("Complete:")
    header = " ".join(
        line.strip()
        for line in plain[:complete_at].splitlines()
        if line.strip()
    )
    header_ok = bool(re.fullmatch(
        r"Model:\s*[^|]+?\s*\|\s*Route:\s*[^|]+?\s*\|\s*Why:\s*[^|]+",
        header,
        flags=re.I,
    ))
    return (
        header_ok
        and all(position >= 0 for position in positions)
        and positions == sorted(positions)
        and bool(re.search(r"(?m)^Complete: (?:Yes|No)\b", plain))
    )


def latest_direct_message_id(session_id: str) -> int:
    if not HERMES_STATE_DB.exists():
        return 0
    con = sqlite3.connect(f"file:{HERMES_STATE_DB}?mode=ro", uri=True, timeout=2)
    try:
        row = con.execute(
            "SELECT COALESCE(MAX(id), 0) FROM messages WHERE session_id = ? AND role = 'user'",
            (session_id,),
        ).fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        con.close()


def bootstrap_direct_message_cursor(session_id: str) -> int:
    """Keep the newest fresh user turn eligible on watcher/session bootstrap.

    Image-only Telegram turns can be the first row in a newly created Hermes
    session. Initializing the cursor to MAX(id) silently consumed that turn
    before the acknowledgement loop saw it. Historical rows remain skipped,
    while one fresh newest row is replayed through the normal ack path.
    """
    if not HERMES_STATE_DB.exists():
        return 0
    con = sqlite3.connect(f"file:{HERMES_STATE_DB}?mode=ro", uri=True, timeout=2)
    try:
        rows = con.execute(
            """
            SELECT id, timestamp
              FROM messages
             WHERE session_id = ? AND role = 'user'
             ORDER BY id DESC
             LIMIT 2
            """,
            (session_id,),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return 0
    message_id, timestamp = int(rows[0][0]), float(rows[0][1])
    age = max(0.0, time.time() - timestamp)
    if age > STALE_BOOTSTRAP_SECONDS:
        return message_id
    # Include the preceding user row when available so a compaction marker and
    # its replayed prompt are classified together on a newly observed session.
    return max(0, int(rows[1][0]) - 1) if len(rows) > 1 else max(0, message_id - 1)


def first_existing_session_path(session_id: str) -> Path:
    candidates: list[Path] = []
    for base in (SESSION_DIR, HERMES_SESSION_DIR):
        candidates.append(base / f"{session_id}.trajectory.jsonl")
        candidates.extend(
            sorted(
                base.glob(f"{session_id}-topic-*.trajectory.jsonl"),
                key=lambda path: path.stat().st_mtime if path.exists() else 0,
                reverse=True,
            )
        )
    for path in candidates:
        if path.exists():
            return path
    return SESSION_DIR / f"{session_id}.trajectory.jsonl"


def friendly_tool_name(name: str) -> str:
    raw = (name or "").split(".")[-1].replace("_", " ").strip().lower()
    labels = {
        "exec command": "local check",
        "apply patch": "file edit",
        "parallel": "parallel checks",
        "tool search tool": "tool lookup",
    }
    return labels.get(raw, raw or "task step")


def short_progress_text(value: str, limit: int = 58) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def summarize_tool_progress(name: str, arguments: dict[str, Any] | None, completed: bool) -> str:
    """Turn a Hermes tool event into a categorized operator-facing activity."""
    args = arguments or {}
    raw = str(name or "tool").split(".")[-1]
    path = str(args.get("path") or args.get("file_path") or "")
    filename = Path(path).name if path else ""
    category = "Tool"
    if raw == "skill_view":
        category, detail = "Skill", f"{args.get('name') or 'relevant workflow'} — workflow guidance"
    elif raw == "read_file":
        detail = f"read_file — inspecting {filename or 'the target file'}"
    elif raw == "search_files":
        pattern = short_progress_text(str(args.get("pattern") or "target logic"), limit=38)
        detail = f"search_files — tracing {pattern}"
    elif raw in {"patch", "write_file"}:
        category, detail = "Action", f"{raw} — updating {filename or 'the implementation'}"
    elif raw == "todo":
        category, detail = "Action", "todo — updating the task checklist"
    elif raw == "terminal":
        command = str(args.get("command") or "")
        lower = command.lower()
        if "unittest" in lower or "pytest" in lower or "py_compile" in lower:
            category, detail = "Verification", "terminal — running focused regression checks"
        elif "launchctl" in lower:
            category, detail = "Action", "launchctl — reloading and checking the Telegram watcher"
        elif "jaimes_live_card.py" in lower:
            category, detail = "Action", "live-card helper — refreshing this work card"
        elif "jaimes_bf_push.sh" in lower:
            category, detail = "Action", "Brain Feed — publishing the current phase"
        elif "git " in lower or lower.strip().startswith("git"):
            category, detail = "Action", "git — validating and syncing the shared source"
        elif "ssh " in lower or "scp " in lower:
            detail = "remote shell — checking or syncing the canonical host"
        else:
            category, detail = "Action", "terminal — running a bounded system operation"
    elif raw in {"web_search", "web_extract", "x_search"}:
        subject = short_progress_text(str(args.get("query") or (args.get("urls") or ["the source"])[0]), limit=48)
        detail = f"{raw} — researching {subject}"
    elif raw in {"memory", "honcho_search", "session_search"}:
        detail = f"{raw} — checking durable context"
    elif raw.startswith("browser_"):
        detail = f"{raw} — inspecting the live page"
    else:
        detail = f"{friendly_tool_name(raw)} — executing the current step"
    if completed:
        completed_label = {
            "Skill": "Skill applied",
            "Tool": "Tool result",
            "Action": "Action completed",
            "Verification": "Verification passed",
        }[category]
        return f"{completed_label}: {detail}"
    return f"{category}: {detail}"


def recent_progress_events(session_id: str) -> list[dict[str, str]]:
    """Read live tool progress from Hermes state.db and bind it to its user turn."""
    if not HERMES_STATE_DB.exists():
        return []
    con = sqlite3.connect(f"file:{HERMES_STATE_DB}?mode=ro", uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT id, role, tool_call_id, tool_calls, tool_name, content
              FROM messages
             WHERE session_id = ?
             ORDER BY id DESC LIMIT 500
            """,
            (session_id,),
        ).fetchall()[::-1]
    except Exception:
        con.close()
        return []
    finally:
        try:
            con.close()
        except Exception:
            pass
    events: list[dict[str, str]] = []
    current_user_id = 0
    call_args: dict[str, tuple[str, dict[str, Any]]] = {}
    for row in rows:
        role = str(row["role"] or "")
        if role == "user":
            current_user_id = int(row["id"])
            call_args = {}
            continue
        if not current_user_id:
            continue
        run_id = f"telegram-message-{current_user_id}"
        if role == "assistant" and row["tool_calls"]:
            try:
                calls = json.loads(row["tool_calls"])
            except Exception:
                calls = []
            for index, call in enumerate(calls if isinstance(calls, list) else []):
                fn = call.get("function") or {}
                name = str(fn.get("name") or call.get("name") or "tool")
                raw_args = fn.get("arguments") or call.get("arguments") or {}
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    args = {}
                call_id = str(call.get("id") or call.get("call_id") or f"{row['id']}:{index}")
                call_args[call_id] = (name, args)
                events.append({
                    "event_id": f"db:{row['id']}:{call_id}:tool.call",
                    "run_id": run_id,
                    "type": "tool.call",
                    "summary": summarize_tool_progress(name, args, False),
                    "final_text": "",
                })
        elif role == "tool":
            call_id = str(row["tool_call_id"] or "")
            name, args = call_args.get(call_id, (str(row["tool_name"] or "tool"), {}))
            events.append({
                "event_id": f"db:{row['id']}:{call_id}:tool.result",
                "run_id": run_id,
                "type": "tool.result",
                "summary": summarize_tool_progress(name, args, True),
                "final_text": "",
            })
    return events


def hermes_session_lineage(session_id: str) -> set[str]:
    """Return the current Hermes session plus its compression ancestors."""
    lineage = {str(session_id)} if session_id else set()
    if not session_id or not HERMES_STATE_DB.exists():
        return lineage
    try:
        con = sqlite3.connect(f"file:{HERMES_STATE_DB}?mode=ro", uri=True, timeout=2)
        try:
            columns = {str(row[1]) for row in con.execute("PRAGMA table_info(sessions)")}
            if "parent_session_id" not in columns:
                return lineage
            current = str(session_id)
            for _ in range(8):
                row = con.execute(
                    "SELECT parent_session_id FROM sessions WHERE id = ?",
                    (current,),
                ).fetchone()
                parent = str(row[0] or "") if row else ""
                if not parent or parent in lineage:
                    break
                lineage.add(parent)
                current = parent
        finally:
            con.close()
    except Exception:
        return lineage
    return lineage


def mitigation_steps_from_text(text: str) -> list[str]:
    if ux_final_action_steps is not None:
        return ux_final_action_steps(text)[1]
    if not text:
        return []
    match = re.search(r"(?im)^\s*(?:🔐\s*)?(?:\*\*)?(?:Approval needed|Mitigation steps for approval):?(?:\*\*)?\s*$", text)
    if not match:
        return []
    body = text[match.end():]
    steps: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"(?i)^\*{0,2}(complete|what was done|tldr|issues|appropriate next steps|approval options|objective|status|next|model|control tower|context|sources?|references?)\b", line):
            break
        line = re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", line).strip()
        line = clean_approval_step(line)
        if not line or line.lower() in {"n/a", "na", "none", "not applicable"}:
            continue
        steps.append(line)
        if len(steps) >= 5:
            break
    return steps


def clean_approval_step(step: str) -> str:
    text = " ".join((step or "").split())
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[-*•\s]+", "", text).strip()
    text = text.strip("*_ ")
    text = re.sub(r"^\*{1,2}|\*{1,2}$", "", text).strip()
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return text


def actionable_approval_step(step: str) -> bool:
    normalized = " ".join(clean_approval_step(step).strip().lower().split())
    if normalized in {"", "n/a", "na", "none", "not applicable", "no action needed"}:
        return False
    if re.match(r"^(context|complete|what was done|issues|appropriate next steps|approval needed|approval options|objective|status|next|model|route|using|sources?|references?)\b", normalized):
        return False
    if re.match(r"^https?://", normalized):
        return False
    if re.match(r"^context:\s*\d+%$", normalized):
        return False
    if re.match(r"^(say|send|reply)\s+[\"'`]", normalized) or re.search(r"\bif you want\b", normalized):
        return False
    return True


def approval_button_label(step: str) -> str:
    label = clean_approval_step(step)
    label = re.sub(r"(?i)^(optional:\s*)", "", label).strip()
    label = re.sub(r"(?i)^(approve|approval to|approval for)\s+", "", label).strip()
    label = label.rstrip(".")
    label = label[:38] + ("..." if len(label) > 38 else "")
    return f"Approve: {label or 'next action'}"


def approval_callback(objective: str, step: str, index: int) -> str:
    digest = hashlib.sha1(f"jaimes|{objective}|{step}|{index}".encode("utf-8")).hexdigest()[:10]
    return f"approve:jaimes:{digest}:{index}"


def save_approval_actions(actions: dict[str, Any]) -> None:
    existing = load_json(APPROVAL_ACTIONS_PATH, {})
    if not isinstance(existing, dict):
        existing = {}
    existing.update(actions)
    save_json(APPROVAL_ACTIONS_PATH, existing)


def send_approval_options(objective: str, final_text: str, dry_run: bool = False, meta: dict[str, Any] | None = None) -> str:
    mode = "approval"
    steps: list[str]
    if ux_final_action_steps is not None:
        mode, steps = ux_final_action_steps(final_text)
    else:
        steps = [step for step in mitigation_steps_from_text(final_text) if actionable_approval_step(step)]
    steps = [step for step in steps if actionable_approval_step(step)]
    if not steps:
        return ""
    actions: dict[str, Any] = {}
    buttons = []
    numeric_mode = str((meta or {}).get("telegram_thread_id") or "") == "17"
    if not numeric_mode and ux_steps_are_all_applicable is not None and ux_steps_are_all_applicable(mode, steps, final_text):
        all_step = ux_approve_all_step(steps) if ux_approve_all_step is not None else "Run all listed steps"
        callback = approval_callback(objective, all_step, 0)
        actions[callback] = {
            "agent": "jaimes",
            "objective": objective,
            "step": all_step,
            "created_at": utc_now(),
        }
        buttons.append([{"text": "Approve all", "callback_data": callback}])
    prefix = "Approve" if mode == "approval" else "Next"
    for index, step in enumerate(steps[:4], start=1):
        callback = approval_callback(objective, step, index)
        actions[callback] = {
            "agent": "jaimes",
            "objective": objective,
            "step": step,
            "created_at": utc_now(),
        }
        if numeric_mode:
            button = {"text": str(index), "callback_data": callback}
        elif ux_button_label is not None:
            button = {"text": ux_button_label(step, prefix=prefix, limit=46), "callback_data": callback}
        else:
            button = {"text": approval_button_label(step), "callback_data": callback}
        if numeric_mode:
            buttons.append(button)
        else:
            buttons.append([button])
    if numeric_mode:
        buttons = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    else:
        buttons.append([{"text": "Hold / no action", "callback_data": "next:hold"}])
    if dry_run:
        return json.dumps({"mode": mode, "numeric_mode": numeric_mode, "buttons": buttons}, sort_keys=True)
    save_approval_actions(actions)
    title = "\u2060" if numeric_mode else ("Approval options:" if mode == "approval" else "Next step options:")
    if numeric_mode:
        # model.completed can reach the watcher a fraction before the Telegram
        # adapter posts the final card. Hold the separate grid briefly so the
        # visible order is always: final card, then its selection buttons.
        time.sleep(float(os.environ.get("JAIMES_TELEGRAM_BUTTON_DELAY_SECONDS", "2.5")))
    return send_buttons_message(title, buttons, meta=meta)


def clean_prompt(prompt: str) -> str:
    text = TELEGRAM_META_PATTERN.sub("", prompt or "").strip()
    return text or "Handle latest Telegram task"


def is_button_prompt(prompt: str) -> bool:
    """Return true for Telegram callback turns materialized by Hermes."""
    return bool(re.match(r"^\s*\[J\]\s*Selected option:", prompt or "", re.I))


def should_start_visible_card(prompt: str, meta: dict[str, Any] | None, cards_flag: str) -> bool:
    """Button approvals always get a card, even when generic fast cards are off."""
    if not (meta or {}).get("telegram_chat_id"):
        return False
    return is_button_prompt(prompt) or cards_flag not in {"0", "false", "no"}


def objective_from_prompt(prompt: str) -> str:
    text = clean_prompt(prompt)
    lowered = text.lower().strip()
    if lowered.startswith("/overview"):
        return "Run JAIMES overview"
    if lowered.startswith("/steer"):
        rest = text[len("/steer"):].strip()
        return rest or "Handle steering request"
    if lowered.startswith("/status"):
        return "Report JAIMES status"
    if lowered.startswith("/models"):
        return "Show active model routing"
    if lowered.startswith("/daily"):
        return "Run JAIMES daily overview"
    if lowered.startswith("/nwq"):
        return "Show new work queue"
    return summarize_objective(text)


OBJECTIVE_RULES = [
    (("what's happening to jaimes", "what is happening to jaimes", "jaimes status", "unresponsive"), "Check JAIMES status"),
    (("telegram ux", "telegram interface", "telegram formatting", "telegram button", "work card format", "live card"), "Tune JAIMES Telegram UX"),
    (("crypto", "wallet", "portfolio", "profit target", "trade card", "trading autonomy", "autonomous trading", "autotrading", "trading", "trades", "recent trades", "memecoin", "robinhood-chain", "rh crypto"), "Tune JAIMES crypto action mode"),
    (("mission control", "control tower", "brain feed", "dashboard", "kiosk"), "Check Control Tower state"),
    (("sorare", "lineup", "game week", "gw", "pre-lock", "mission"), "Review Sorare lineup state"),
    (("fantasy baseball", "espn", "roster", "lineup", "matchup", "waiver"), "Sync fantasy baseball roster"),
    (("health", "status", "gateway", "hermes", "telegram"), "Run JAIMES health check"),
    (("update", "upgrade", "install", "latest"), "Update JAIMES stack"),
    (("breaking", "latest news", "x.com", "twitter", "current events"), "Review current-event signal"),
    (("summarize", "summary", "digest", "overview", "explain", "analyze"), "Summarize and review"),
]

LEADING_REQUEST_RE = re.compile(
    r"^(please\s+)?(can you|could you|would you|may you|make sure|check|review|look at|help me|i want you to)\s+",
    re.I,
)


def summarize_objective(text: str) -> str:
    clean = " ".join((text or "").split())

    #JAIMES: objective cards must describe the current request, not a quoted
    # objective/final-card example pasted below it. Structured card rows are
    # evidence for the task, never candidate task instructions.
    embedded_card_row = re.compile(
        r"^(?:[🎯🤖📊⏱️✅⚠️➡️🔐]\s*)?"
        r"(?:objective|model|steps?|eta|complete|what was done|issues|"
        r"appropriate next steps|approval needed|status|progress)\s*(?::|$)",
        re.I,
    )
    eligible_lines: list[str] = []
    skip_objective_value = False
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"(?:🎯\s*)?objective\s*", line, re.I):
            skip_objective_value = True
            continue
        if skip_objective_value:
            skip_objective_value = False
            continue
        if embedded_card_row.match(line) or line.startswith(("```", "- ")):
            continue
        eligible_lines.append(line)
    parts = [
        p.strip(" ,.-")
        for p in re.split(r"(?<=[.!?])\s+|\n+", "\n".join(eligible_lines))
        if p.strip()
    ]
    request_markers = ("please", "can you", "could you", "would you", "why ", "did you", "fix ", "make ", "change ", "add ", "remove ", "check ", "find ", "build ", "run ", "verify ")
    candidates = [
        p for p in parts
        if not embedded_card_row.match(p)
        and any(marker in p.lower() for marker in request_markers)
    ]
    intent = candidates[-1] if candidates else clean
    intent = re.sub(r"^(?:okay|ok|perfect|great|thanks|thank you|much better)[,! .-]*", "", intent, flags=re.I)
    intent_lower = intent.lower()
    request_context = " ".join(eligible_lines).lower()

    if "objective" in request_context and any(
        marker in request_context for marker in ("copy", "quote", "similar", "own words", "interpret", "paraphrase")
    ):
        return "Make agent task objectives reflect interpreted intent"

    if "old objective" in request_context and any(
        marker in request_context for marker in ("current task", "correct objective", "mapping", "mapped")
    ):
        return "Fix current-task objective mapping"
    if "button" in intent_lower and ("approval" in intent_lower or "steps" in intent_lower):
        return "Check the unexpected approval button"
    if "card" in intent_lower and "summar" in intent_lower and "objective" in intent_lower:
        return "Make objective cards summarize task intent"
    if "final" in intent_lower and "summar" in intent_lower and any(
        marker in intent_lower for marker in ("code block", "format")
    ):
        return "Format final summaries as code blocks"
    if "alert" in intent_lower and any(word in intent_lower for word in ("hard to read", "format", "section")):
        return "Reformat alerts into clear sections"
    if "market cap" in intent_lower and any(word in intent_lower for word in ("bought", "buy", "sold", "sell")):
        return "Investigate matching trade market-cap labels"

    for markers, summary in OBJECTIVE_RULES:
        if any(marker in intent_lower for marker in markers):
            return summary
    verification = re.match(
        r"^(?:please\s+)?(?:test|validate|verify|confirm|check|make sure)\s+(.+)$",
        intent,
        re.I,
    )
    if verification:
        target = verification.group(1).strip(" .?!")
        return f"Confirm {target} meets the intended requirements"[:80]
    intent = LEADING_REQUEST_RE.sub("", intent).strip(" .?!")
    intent = re.sub(r"^please\s+(?:actually\s+)?", "", intent, flags=re.I)
    words = intent.split()
    if len(words) > 12:
        intent = " ".join(words[:12])
    return intent[:80] or "Handle Telegram task"


def classify_privacy(prompt: str) -> str:
    text = clean_prompt(prompt).lower()
    private_markers = {
        "password", "cookie", "oauth", "token", "keychain", "gmail", "email",
        "calendar", "account", "login", "sorare", "browser", "chrome",
        "bank", "stripe", "payment", "private", "personal account",
    }
    return "sensitive-account" if any(marker in text for marker in private_markers) else "dashboard-safe"


def classify_task_type(prompt: str) -> str:
    text = clean_prompt(prompt).lower()
    if any(marker in text for marker in ("keychain", "cookie.codex", "codex cookie", "alert on your screen")):
        return "macos-keychain-alert"
    if any(marker in text for marker in ("breaking", "latest news", "x.com", "twitter", "market narrative", "sentiment", "current events")):
        return "current-events"
    if any(marker in text for marker in ("summarize", "summary", "digest", "overview", "readability", "review", "explain", "analyze")):
        return "summary"
    if any(marker in text for marker in ("fix", "patch", "update", "install", "upgrade", "test", "build", "repo", "code", "script")):
        return "repo-patch"
    if any(marker in text for marker in ("health", "status", "mission control", "brain feed", "sync")):
        return "summary"
    return "connected-account-triage" if classify_privacy(prompt) != "dashboard-safe" else "summary"


def display_model_route(route_result: dict[str, Any], fallback_model: str) -> tuple[str, str]:
    model_route = route_result.get("modelRoute") if isinstance(route_result, dict) else {}
    if not isinstance(model_route, dict):
        return fallback_model, DEFAULT_ROUTE
    first_stop = str(model_route.get("firstStop") or "codex")
    model = str(model_route.get("model") or model_route.get("provider") or fallback_model)
    model_lower = model.lower()
    agent = str(model_route.get("owner") or route_result.get("agent") or "jaimes")
    if "gemini" in model_lower and "pro" in model_lower:
        friendly_model = "Gemini Pro"
    elif "gemini" in model_lower:
        friendly_model = "Gemini Flash"
    elif "grok" in model_lower or first_stop == "xai":
        friendly_model = "Grok"
    elif first_stop == "openrouter":
        friendly_model = "OpenRouter"
    else:
        friendly_model = "Codex"
    if first_stop == "gemini":
        why = "safe summary/review"
    elif first_stop == "xai":
        why = "public current-events"
    elif first_stop == "openrouter":
        why = "fallback check"
    else:
        friendly_model = "Codex"
        why = "execution/private fit"
    return f"{friendly_model} - {why}", f"auto: {agent} -> {first_stop}"


def auto_route_for_prompt(prompt: str, fallback_model: str) -> dict[str, str]:
    task_type = classify_task_type(prompt)
    privacy = classify_privacy(prompt)
    cmd = [
        "python3",
        "mission-control/scripts/agent_route.py",
        "--task-type",
        task_type,
        "--title",
        "JAIMES Telegram task",
        "--objective",
        objective_from_prompt(prompt),
        "--privacy",
        privacy,
        "--requester",
        "jaimes",
        "--prefer",
        "jaimes",
    ]
    if task_type in {"summary", "digest", "daily-digest"}:
        cmd += ["--capability", "gemini-review"]
    try:
        result = run_cmd(cmd, timeout=12)
        if result.get("ok") and result.get("stdout"):
            route_result = json.loads(str(result["stdout"]))
            model_line, route_line = display_model_route(route_result, fallback_model)
            return {"model": model_line, "route": route_line, "task_type": task_type, "privacy": privacy}
    except Exception:
        pass
    return {
        "model": fallback_model,
        "route": f"{DEFAULT_ROUTE}; auto route unavailable, using local Codex fallback",
        "task_type": task_type,
        "privacy": privacy,
    }


def skill_for_prompt(prompt: str) -> dict[str, str]:
    if select_skill is None:
        return {"id": "", "label": "", "reason": ""}
    try:
        selection = select_skill(prompt, "jaimes")
        if write_selection is not None:
            write_selection(selection, clean_prompt(prompt))
        return {
            "id": str(selection.get("id") or ""),
            "label": str(selection.get("label") or ""),
            "reason": str(selection.get("reason") or ""),
        }
    except Exception:
        return {"id": "", "label": "", "reason": ""}


def run_cmd(
    cmd: list[str],
    timeout: int = 20,
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str | int | bool]:
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update(extra_env)
    try:
        proc = subprocess.run(
            cmd,
            cwd=WORKSPACE,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": 124, "stdout": "", "stderr": f"command timed out after {timeout}s"}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": sanitize_error_text(proc.stderr.strip(), limit=1200)
        if proc.stderr.strip()
        else "",
    }


def run_work_card_cmd(cmd: list[str]) -> dict[str, str | int | bool]:
    """Run one bounded Telegram card edit without duplicate Brain Feed I/O.

    The watcher publishes the same lifecycle phase itself after the Telegram
    receipt is known. Keeping the helper surface-only prevents its remote Brain
    Feed SSH call from exceeding the fast-ack parent deadline.
    """
    bounded = list(cmd)
    if "--timeout" not in bounded:
        bounded.extend(["--timeout", str(WORK_CARD_API_TIMEOUT_SECONDS)])
    if "--no-brain-feed" not in bounded:
        bounded.append("--no-brain-feed")
    return run_cmd(
        bounded,
        timeout=WORK_CARD_PARENT_TIMEOUT_SECONDS,
        extra_env={"ALLOW_NO_BRAIN_FEED": "1"},
    )


def telegram_work_identity(key: str, run_id: str) -> tuple[str, str, str]:
    stable = f"{key}|{run_id}".encode("utf-8")
    digest = hashlib.sha256(stable).hexdigest()
    return (
        f"work-telegram-{digest[:24]}",
        f"run-telegram-{digest[24:48]}",
        hashlib.sha256(b"telegram-origin|" + stable).hexdigest(),
    )


def canonical_model_family(value: str) -> str:
    lowered = str(value or "").lower()
    if any(token in lowered for token in ("gemini", "google", "antigravity")):
        return "antigravity"
    if any(token in lowered for token in ("grok", "xai", "x.ai")):
        return "grok"
    if any(token in lowered for token in ("ollama", "llama", "qwen", "gemma", "glm")):
        return "ollama"
    return "codex"


def publish_jaimes(
    title: str,
    status: str,
    detail: str,
    *,
    work_id: str = "",
    run_id: str = "",
    phase: str = "",
    model_id: str = "",
    route_verified: bool | None = None,
    origin_claim_hash: str = "",
    brain_feed: bool = True,
    work_event: str = "",
) -> bool:
    publish_args = [
        "--agent",
        "jaimes",
        "--type",
        "status",
        "--status",
        status,
        "--title",
        title,
        "--tool",
        "JAIMES Telegram",
        "--detail",
        detail[:260],
        "--privacy",
        "dashboard-safe",
    ]
    if brain_feed:
        publish_args.append("--brain-feed")
    if work_event:
        publish_args += ["--work-event", work_event]
    if work_id:
        publish_args += ["--work-id", work_id]
    if run_id:
        publish_args += ["--run-id", run_id]
    if phase:
        publish_args += ["--phase", phase]
    if model_id:
        publish_args += ["--model-family", canonical_model_family(model_id), "--model-id", model_id[:120]]
    if route_verified:
        publish_args.append("--route-verified")
    elif route_verified is False:
        publish_args.append("--route-unverified")
    if origin_claim_hash:
        publish_args += ["--origin-claim-hash", origin_claim_hash]
    # JAIMES runs on a different host. The local checkout is not the
    # operational ledger: submit the identity-bearing event to Josh 2.0 so
    # Control Tower, SSE, and FinOps observe one canonical transaction.
    remote_command = "cd {} && {}".format(
        shlex.quote(CONTROL_TOWER_REMOTE_ROOT),
        shlex.join([
            CONTROL_TOWER_REMOTE_PYTHON,
            f"{CONTROL_TOWER_REMOTE_ROOT}/scripts/agent_publish.py",
            *publish_args,
        ]),
    )
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=4",
                CONTROL_TOWER_SSH_HOST,
                remote_command,
            ],
            cwd=WORKSPACE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=12,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def event_age_seconds(ts: str) -> float | None:
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        event_time = dt.datetime.fromisoformat(ts)
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - event_time).total_seconds()
    except Exception:
        return None


def send_ack(
    event: dict[str, str],
    model: str,
    state: dict[str, Any],
    dry_run: bool = False,
    meta: dict[str, Any] | None = None,
    reaction_already_done: bool = False,
    surface_attempt_callback: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    task_identity = event.get("platform_message_id") or event.get("db_message_id") or event["ts"].replace(":", "").replace(".", "-")
    task_started_at = str(event.get("ts") or utc_now())
    key = f"jaimes-fast-ack-{(meta or {}).get('telegram_chat_id') or 'telegram'}-{task_identity}"
    work_id, work_run_id, origin_claim_hash = telegram_work_identity(
        key,
        str(event.get("run_id") or task_identity or "run"),
    )
    if key in set(state.get("processed_task_keys") or []):
        #JAIMES: a replayed state-db row with the same stable task key must not
        # create a second acknowledgement or work-card lifecycle.
        return {
            "ok": True,
            "duplicate_suppressed": True,
            "header_message_id": "",
            "ack_message_id": "",
            "key": key,
            "model": model or DEFAULT_MODEL,
            "route": "",
            "skill": {},
            "objective": objective_from_prompt(event.get("prompt", "")),
            "reaction_ok": False,
            "button_triggered": False,
            "run_id": "",
            "last_card_update_at": utc_now(),
        }
    prompt = event.get("prompt", "")
    objective = objective_from_prompt(prompt)
    inbound_message_id = event.get("platform_message_id") or str(((meta or {}).get("origin") or {}).get("message_id") or "")
    handoff_topic = bool(
        str((meta or {}).get("telegram_chat_id") or "") == CONTROL_CENTER_CHAT_ID
        and str((meta or {}).get("telegram_thread_id") or "") in JAIMES_DIRECT_MENTION_TOPICS
    )
    reaction_ok = bool(reaction_already_done)
    if not dry_run and not reaction_already_done:
        reaction_ok = set_eyes_reaction(inbound_message_id, state, meta=meta)
    if handoff_topic and not dry_run and not reaction_ok:
        return {
            "ok": False,
            "handoff_terminal_failure": True,
            "error": "eyes_reaction_failed",
            "header_message_id": "",
            "ack_message_id": "",
            "key": key,
            "model": model or DEFAULT_MODEL,
            "route": "",
            "skill": {},
            "objective": objective,
            "reaction_ok": False,
            "button_triggered": is_button_prompt(prompt),
            "run_id": event.get("run_id") or "",
            "last_card_update_at": utc_now(),
        }

    if objective_is_near_copy(prompt, objective):
        objective = semantic_reinterpretation(prompt)
    if not objective or objective_is_near_copy(prompt, objective):
        # #JAIMES: keep only the immediate reaction when deterministic intake
        # cannot produce a genuine interpretation; the main agent must decide
        # the objective before Telegram or Control Tower receives one.
        if not dry_run:
            send_chat_action(meta=meta)
        return {
            "ok": True,
            "status": "awaiting-objective-interpretation",
            "header_message_id": "",
            "ack_message_id": "",
            "key": key,
            "model": model or DEFAULT_MODEL,
            "route": "",
            "skill": {},
            "objective": "",
            "requires_objective_interpretation": True,
            "reaction_ok": reaction_ok,
            "button_triggered": is_button_prompt(prompt),
            "run_id": event.get("run_id") or "",
            "last_card_update_at": utc_now(),
        }

    skill = skill_for_prompt(prompt)
    # A router recommendation is not a model switch. Keep the visible model
    # sourced from the active Hermes session until a new lane actually starts.
    display_model = model or DEFAULT_MODEL
    active_lane, active_reason = runtime_route(display_model)
    display_route = f"{active_lane} | Why: {active_reason}"
    if skill.get("label"):
        display_route = f"{display_route}; runbook={skill['id']}"
    cards_flag = os.environ.get("JAIMES_TELEGRAM_LIVE_CARDS", "").lower()
    start_visible_card = should_start_visible_card(prompt, meta, cards_flag)

    if handoff_topic and not dry_run and start_visible_card and surface_attempt_callback:
        # The durable owner records card-attempt intent immediately before the
        # child is spawned. A crash during routing still remains no-effect and
        # can safely fall back to Josh.
        if not surface_attempt_callback():
            return {
                "ok": False,
                "handoff_terminal_failure": True,
                "error": "handoff_claim_cancelled_before_surface",
                "reaction_ok": reaction_ok,
                "header_message_id": "",
                "ack_message_id": "",
                "key": key,
                "objective": objective,
                "run_id": event.get("run_id") or "",
                "last_card_update_at": utc_now(),
            }

    #JAIMES: send the first stable surface once. The previous placeholder ->
    # objective -> live-card edit chain forced Telegram to remove/redraw the same
    # bubble several times in two seconds, which looked like cards disappearing.
    header_message_id = "dry-run-header" if dry_run else ""
    ack_message_id = "dry-run-message" if dry_run else ""
    surface_indeterminate = False
    if not dry_run and start_visible_card:
        card_command = [
            "python3",
            "mission-control/scripts/jaimes_work_card.py",
            "start",
            "--key",
            key,
            "--title",
            objective,
            "--model",
            display_model,
            "--route",
            display_route,
            "--now",
            "Objective, model route, and runbook confirmed",
            "--done",
            f"Received Telegram task|Objective determined: {objective}|Model selected: {display_model}|Skill selected: {skill.get('label') or 'none'}",
            "--next",
            "Work automatically; show buttons only for final approval steps if needed",
            "--work-id",
            work_id,
            "--run-id",
            work_run_id,
            "--task-started-at",
            task_started_at,
        ]
        managed_group_topic = bool(
            str((meta or {}).get("telegram_chat_id") or "") == CONTROL_CENTER_CHAT_ID
            and str((meta or {}).get("telegram_thread_id") or "")
        )
        if handoff_topic:
            # Topic 1's immutable header and live card are new surfaces for the
            # current inbound message. Never adopt the prior poll receipt.
            card_command.extend(["--separate-message", "--timeout", "4"])
        elif managed_group_topic:
            # Every group task owns a fresh origin-scoped card. Reusing the
            # prior task's pending acknowledgement would overwrite its visible
            # history and bind the new task to the wrong Telegram message.
            card_command.append("--separate-message")
        # Every managed surface uses the same bounded helper. Topic 17 used to
        # run a 15-second Bot API child under a 6-second parent; Telegram could
        # accept the card just before the parent killed receipt persistence.
        card_result = run_work_card_cmd(card_command + work_card_target_args(meta))
        card_receipt: dict[str, Any] = {}
        if card_result.get("stdout"):
            try:
                parsed_receipt = json.loads(str(card_result["stdout"]))
                if isinstance(parsed_receipt, dict):
                    card_receipt = parsed_receipt
            except (TypeError, ValueError):
                card_receipt = {}
        durable_receipt = work_card_surface_receipt(key)
        ack_message_id = str(
            card_receipt.get("message_id")
            or (card_receipt.get("result") or {}).get("message_id")
            or durable_receipt.get("message_id")
            or ""
        )
        header_message_id = str(
            card_receipt.get("header_message_id")
            or (card_receipt.get("result") or {}).get("header_message_id")
            or durable_receipt.get("header_message_id")
            or ""
        )
        if handoff_topic:
            ack_message_id = _handoff_id(ack_message_id)
            header_message_id = _handoff_id(header_message_id)
        surface_indeterminate = bool(durable_receipt.get("surface_indeterminate"))
        confirmed_card_receipt = bool(
            ack_message_id
            and not surface_indeterminate
            and (not handoff_topic or header_message_id)
        )
        record_api_result(state, "sendMessage", {
            "ok": confirmed_card_receipt,
            "error": card_result.get("stderr") or card_result.get("error") or "",
            "delivery_key": key,
        })
    elif not dry_run:
        ack_result = send_initial_ack(
            f"🤖 {display_model}\n\n👀 Objective\n{objective}",
            meta=meta,
        )
        ack_result["delivery_key"] = key
        record_api_result(state, "sendMessage", ack_result)
        ack_message_id = str(ack_result.get("result", {}).get("message_id") or "") if ack_result.get("ok") else ""

    if not dry_run and not ack_message_id:
        # Do not silently mark this event deduplicated when Telegram did not
        # confirm the durable acknowledgement or live card.
        record_api_result(state, "sendMessage", {
            "ok": False,
            "error": "No message_id returned by initial Telegram surface",
            "delivery_key": key,
        })
    surface_ok = bool(
        ack_message_id
        and not surface_indeterminate
        and (not handoff_topic or header_message_id)
    )
    if not dry_run:
        send_chat_action(meta=meta)
        if surface_ok:
            publish_jaimes(
                objective,
                "active",
                f"Objective confirmed; {display_model}; skill={skill.get('label') or 'none'}",
                work_id=work_id,
                run_id=work_run_id,
                phase="active",
                model_id=display_model,
                route_verified=True,
                origin_claim_hash=origin_claim_hash,
                work_event="start",
            )

    return {
        "ok": bool(dry_run or (surface_ok and (reaction_ok or not handoff_topic))),
        "header_message_id": header_message_id,
        "ack_message_id": ack_message_id,
        "key": key,
        "model": display_model,
        "route": display_route,
        "skill": skill,
        "objective": objective,
        "work_id": work_id,
        "ledger_run_id": work_run_id,
        "origin_claim_hash": origin_claim_hash,
        "reaction_ok": reaction_ok,
        "button_triggered": is_button_prompt(prompt),
        "run_id": event.get("run_id") or "",
        "task_started_at": task_started_at,
        "last_card_update_at": utc_now(),
        "telegram_chat_id": (meta or {}).get("telegram_chat_id"),
        "telegram_thread_id": (meta or {}).get("telegram_thread_id"),
        "retention": "persistent-edit-only",
        "surface_indeterminate": bool(
            not dry_run and surface_indeterminate
        ) if start_visible_card else False,
        "error": sanitize_error_text(
            card_result.get("stderr") or card_result.get("error") or "",
            limit=240,
        )
        if start_visible_card and not dry_run and not surface_ok else "",
    }


def complete_cards_from_final_responses(state: dict[str, Any], session_id: str, dry_run: bool = False) -> int:
    """Normalize the delivered native final, then align the live card to 100%."""
    completed = 0
    for run_id, card in (state.get("active_cards") or {}).items():
        if not isinstance(card, dict) or card.get("status") == "done":
            continue
        match = re.fullmatch(r"telegram-message-(\d+)", str(run_id))
        if not match:
            continue
        final_record = final_assistant_record_after(session_id, int(match.group(1)))
        if not final_record:
            continue
        native_final_id = str(final_record.get("platform_message_id") or "")
        if not native_final_id:
            card["final_contract_status"] = "waiting_for_telegram_delivery_id"
            continue
        key = str(card.get("key") or "")
        if not key:
            continue
        formatted_final = structured_final_text(
            str(final_record.get("content") or ""),
            objective=str(card.get("objective") or "JAIMES Telegram task"),
            model=str(card.get("model") or DEFAULT_MODEL),
            route=str(card.get("route") or DEFAULT_ROUTE),
            work_id=str(card.get("work_id") or ""),
            run_id=str(card.get("ledger_run_id") or ""),
            task_started_at=str(card.get("task_started_at") or card.get("started_at") or ""),
            response_recorded_at=str(final_record.get("recorded_at") or ""),
        )
        evidence_problems = final_evidence_problems(
            str(final_record.get("content") or ""),
            objective=str(card.get("objective") or "JAIMES Telegram task"),
            work_id=str(card.get("work_id") or ""),
            run_id=str(card.get("ledger_run_id") or ""),
            task_started_at=str(card.get("task_started_at") or card.get("started_at") or ""),
            response_recorded_at=str(final_record.get("recorded_at") or ""),
        )
        card["final_evidence_status"] = "stale" if evidence_problems else "current"
        card["final_evidence_work_id"] = str(card.get("work_id") or "")
        card["final_evidence_run_id"] = str(card.get("ledger_run_id") or "")
        card["final_evidence_task_started_at"] = str(
            card.get("task_started_at") or card.get("started_at") or ""
        )
        card["final_response_recorded_at"] = str(final_record.get("recorded_at") or "")
        if not final_contract_is_canonical(formatted_final):
            card["final_contract_status"] = "formatter_error"
            continue
        if dry_run:
            edit_result = {"ok": True, "dry_run": True}
        else:
            edit_result = edit_message(
                native_final_id,
                formatted_final,
                meta=card,
                parse_mode="HTML",
            )
        not_modified = "message is not modified" in str(
            edit_result.get("description") or edit_result.get("error") or ""
        ).lower()
        if not dry_run:
            record_api_result(state, "editMessageText", {
                **edit_result,
                "ok": bool(edit_result.get("ok") or not_modified),
                "delivery_key": f"{key}:final",
            })
        if not edit_result.get("ok") and not not_modified:
            card["final_contract_status"] = "retry_same_message"
            card["final_contract_attempts"] = int(card.get("final_contract_attempts") or 0) + 1
            card["native_final_message_id"] = native_final_id
            continue
        cmd = [
            "python3", "mission-control/scripts/jaimes_work_card.py", "done",
            "--key", key,
            "--title", str(card.get("objective") or "JAIMES Telegram task"),
            "--model", str(card.get("model") or DEFAULT_MODEL),
            "--route", str(card.get("route") or DEFAULT_ROUTE),
            "--done", "Final response prepared and task closed",
            "--blocker", "None",
            "--no-final-summary",
        ] + work_card_target_args(card)
        result = {"ok": True, "dry_run": True} if dry_run else run_work_card_cmd(cmd)
        if not dry_run:
            record_api_result(state, "editMessageText", {
                "ok": bool(result.get("ok")),
                "error": result.get("stderr") or result.get("error") or "",
                "delivery_key": key,
            })
        if result.get("ok"):
            card["status"] = "done"
            card["ended_at"] = utc_now()
            card["last_card_update_at"] = card["ended_at"]
            card["native_final_message_id"] = native_final_id
            card["final_db_message_id"] = final_record.get("id")
            card["final_contract_status"] = "canonical"
            card["final_contract_attempts"] = int(card.get("final_contract_attempts") or 0) + 1
            if not dry_run:
                publish_jaimes(
                    str(card.get("objective") or "JAIMES Telegram task"),
                    "done",
                    "Verified final response delivered in JAIMES Telegram.",
                    work_id=str(card.get("work_id") or ""),
                    run_id=str(card.get("ledger_run_id") or ""),
                    phase="done",
                    model_id=str(card.get("model") or DEFAULT_MODEL),
                    route_verified=True,
                    origin_claim_hash=str(card.get("origin_claim_hash") or ""),
                )
            completed += 1
    return completed


def reconcile_adapter_confirmed_deliveries(
    state: dict[str, Any],
    *,
    dry_run: bool = False,
) -> int:
    """Close watcher state only after the adapter's successful-send receipt."""
    snapshot = load_json(JAIMES_WORK_CARD_STATE_PATH, {})
    work_cards = snapshot.get("cards") if isinstance(snapshot, dict) else {}
    if not isinstance(work_cards, dict):
        return 0
    confirmed = 0
    for card in (state.get("active_cards") or {}).values():
        if not isinstance(card, dict) or card.get("status") == "done":
            continue
        record = work_cards.get(str(card.get("key") or ""))
        if not isinstance(record, dict) or record.get("status") != "done":
            continue
        final_message_id = str(record.get("final_message_id") or "")
        if not final_message_id:
            continue
        identity_pairs = (
            ("work_id", "work_id"),
            ("ledger_run_id", "run_id"),
            ("task_started_at", "task_started_at"),
        )
        if any(
            str(card.get(card_field) or "")
            and str(record.get(record_field) or "") != str(card.get(card_field) or "")
            for card_field, record_field in identity_pairs
        ):
            continue
        work_log = " ".join(str(item) for item in (record.get("work_log") or record.get("done") or []))
        if "Final summary delivered" not in work_log:
            continue
        ended_at = str(record.get("updated_at") or utc_now())
        card["status"] = "done"
        card["ended_at"] = ended_at
        card["last_card_update_at"] = ended_at
        card["final_contract_status"] = "canonical"
        card["native_final_message_id"] = final_message_id
        card["final_message_id"] = final_message_id
        card["final_delivery_verified_by"] = "hermes-adapter-success"
        card["final_delivery_confirmed_at"] = ended_at
        card_key = str(card.get("key") or "")
        if card_key:
            resolve_delivery_incident(state, "editMessageText", card_key)
            resolve_delivery_incident(state, "editMessageText", f"{card_key}:final")
        if not dry_run:
            publish_jaimes(
                str(card.get("objective") or "JAIMES Telegram task"),
                "done",
                "Canonical final response confirmed delivered in JAIMES Telegram.",
                work_id=str(card.get("work_id") or ""),
                run_id=str(card.get("ledger_run_id") or ""),
                phase="done",
                model_id=str(card.get("model") or DEFAULT_MODEL),
                route_verified=True,
                origin_claim_hash=str(card.get("origin_claim_hash") or ""),
            )
        confirmed += 1
    return confirmed


def update_active_cards(state: dict[str, Any], session_id: str, dry_run: bool = False) -> list[dict[str, Any]]:
    # Groups retain opt-in live cards. Direct-chat cards are always maintained:
    # the direct acknowledgement promise includes a single editable work card.
    cards_flag = os.environ.get("JAIMES_TELEGRAM_LIVE_CARDS", "").lower()
    active = state.get("active_cards") or {}
    has_direct_card = any(
        isinstance(card, dict) and not card.get("telegram_thread_id")
        for card in active.values()
    )
    if cards_flag in {"0", "false", "no"} or (cards_flag not in {"1", "true", "yes"} and not has_direct_card):
        state["processed_progress_events"] = sorted(set(state.get("processed_progress_events") or []))[-300:]
        return []
    processed = set(state.get("processed_progress_events") or [])
    approval_sent = set(state.get("approval_buttons_sent") or [])
    updates: list[dict[str, Any]] = []
    pending_by_run: dict[str, dict[str, Any]] = {}
    lineage = hermes_session_lineage(session_id)
    lineage_cards = [
        (run_id, card)
        for run_id, card in active.items()
        if isinstance(card, dict)
        and card.get("status") != "done"
        and str(card.get("session_id") or "") in lineage
    ]
    lineage_cards.sort(
        key=lambda item: parse_utc(
            item[1].get("last_progress_at") or item[1].get("started_at")
        ) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    )
    for source_event in recent_progress_events(session_id):
        event = dict(source_event)
        event_id = event["event_id"]
        if event_id in processed:
            continue
        card = active.get(event["run_id"])
        if not card and lineage_cards:
            # Hermes compression creates a child session and new internal user
            # row for the same turn. Keep those child tool events bound to the
            # one origin-scoped Telegram card until a genuine new prompt owns a
            # new run/card.
            continued_run_id, card = lineage_cards[-1]
            event["run_id"] = continued_run_id
            event["continued_session_id"] = session_id
        if not card or card.get("status") == "done":
            processed.add(event_id)
            continue
        if card.get("session_id") and str(card.get("session_id")) not in lineage:
            processed.add(event_id)
            continue
        # Coalesce a burst of tool/model events into one visible edit. Replaying
        # every micro-event after rollover can time out the work-card helper and
        # makes Telegram look noisy rather than live.
        previous = pending_by_run.get(event["run_id"])
        if previous:
            processed.add(previous["event_id"])
        pending_by_run[event["run_id"]] = event
    for event in pending_by_run.values():
        event_id = event["event_id"]
        card = active.get(event["run_id"])
        if not card:
            continue
        objective = str(card.get("objective") or "JAIMES Telegram task")
        key = str(card.get("key") or "")
        if not key:
            processed.add(event_id)
            continue
        if event["type"] == "model.completed":
            cmd = [
                "python3",
                "mission-control/scripts/jaimes_work_card.py",
                "update",
                "--key",
                key,
                "--title",
                objective,
                "--model",
                str(card.get("model") or DEFAULT_MODEL),
                "--route",
                str(card.get("route") or DEFAULT_ROUTE),
                "--now",
                "Final response prepared; awaiting Telegram delivery",
            ] + work_card_target_args(card)
            result = {"ok": True, "dry_run": True} if dry_run else run_work_card_cmd(cmd)
            if not dry_run:
                record_api_result(state, "editMessageText", {
                    "ok": bool(result.get("ok")),
                    "error": result.get("stderr") or result.get("error") or "",
                    "delivery_key": key,
                })
            if not dry_run and result.get("ok"):
                publish_jaimes(
                    objective,
                    "active",
                    "Final response prepared; awaiting confirmed Telegram delivery.",
                    work_id=str(card.get("work_id") or ""),
                    run_id=str(card.get("ledger_run_id") or ""),
                    phase="delivery",
                    model_id=str(card.get("model") or DEFAULT_MODEL),
                    route_verified=True,
                    origin_claim_hash=str(card.get("origin_claim_hash") or ""),
                )
            if result.get("ok"):
                processed.add(event_id)
                card["status"] = "active"
                if event.get("continued_session_id") and str(card.get("session_id") or "") != session_id:
                    card.setdefault("continued_from_session_ids", []).append(str(card.get("session_id") or ""))
                    card["continued_from_session_ids"] = [
                        value for value in dict.fromkeys(card["continued_from_session_ids"]) if value
                    ][-8:]
                    card["session_id"] = session_id
                card["current_summary"] = "Final response prepared; awaiting Telegram delivery"
                card["model_completed_at"] = utc_now()
                card["last_card_update_at"] = utc_now()
                card["last_progress_at"] = card["last_card_update_at"]
        else:
            if not dry_run:
                send_chat_action(meta=card)
            cmd = [
                "python3",
                "mission-control/scripts/jaimes_work_card.py",
                "update",
                "--key",
                key,
                "--title",
                objective,
                "--model",
                str(card.get("model") or DEFAULT_MODEL),
                "--route",
                str(card.get("route") or DEFAULT_ROUTE),
                "--now",
                event["summary"],
            ]
            if event["type"] == "tool.result":
                cmd += ["--done", event["summary"]]
            cmd += work_card_target_args(card)
            result = {"ok": True, "dry_run": True} if dry_run else run_work_card_cmd(cmd)
            if not dry_run:
                record_api_result(state, "editMessageText", {
                    "ok": bool(result.get("ok")),
                    "error": result.get("stderr") or result.get("error") or "",
                    "delivery_key": key,
                })
            if not dry_run and result.get("ok"):
                publish_jaimes(
                    objective,
                    "active",
                    event["summary"],
                    work_id=str(card.get("work_id") or ""),
                    run_id=str(card.get("ledger_run_id") or ""),
                    phase="active",
                    model_id=str(card.get("model") or DEFAULT_MODEL),
                    route_verified=True,
                    origin_claim_hash=str(card.get("origin_claim_hash") or ""),
                )
            if result.get("ok"):
                processed.add(event_id)
                card["status"] = "active"
                if event.get("continued_session_id") and str(card.get("session_id") or "") != session_id:
                    card.setdefault("continued_from_session_ids", []).append(str(card.get("session_id") or ""))
                    card["continued_from_session_ids"] = [
                        value for value in dict.fromkeys(card["continued_from_session_ids"]) if value
                    ][-8:]
                    card["session_id"] = session_id
                card["current_summary"] = event["summary"]
                card["last_card_update_at"] = utc_now()
                card["last_progress_at"] = card["last_card_update_at"]
        updates.append({"event": event_id, "result": result})
    now = dt.datetime.now(dt.timezone.utc)
    for run_id, card in active.items():
        if not isinstance(card, dict) or card.get("status") == "done":
            continue
        last_raw = str(card.get("last_card_update_at") or "")
        try:
            last = dt.datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
        except Exception:
            last = now
        objective = str(card.get("objective") or "JAIMES Telegram task")
        key = str(card.get("key") or "")
        if not key:
            continue
        progress_raw = str(card.get("last_progress_at") or card.get("started_at") or last_raw or "")
        try:
            last_progress = dt.datetime.fromisoformat(progress_raw.replace("Z", "+00:00"))
        except Exception:
            last_progress = last
        if (now - last_progress).total_seconds() > MAX_ACTIVE_CARD_SECONDS:
            summary = "No recent model or tool progress; JAIMES is back on standby."
            cmd = [
                "python3",
                "mission-control/scripts/jaimes_work_card.py",
                "done",
                "--key",
                key,
                "--title",
                objective,
                "--model",
                str(card.get("model") or DEFAULT_MODEL),
                "--route",
                str(card.get("route") or DEFAULT_ROUTE),
                "--done",
                summary,
                "--blocker",
                "None",
                "--no-final-summary",
            ] + work_card_target_args(card)
            result = {"ok": True, "dry_run": True} if dry_run else run_work_card_cmd(cmd)
            if not dry_run:
                record_api_result(state, "editMessageText", {
                    "ok": bool(result.get("ok")),
                    "error": result.get("stderr") or result.get("error") or "",
                    "delivery_key": key,
                })
            if not dry_run and result.get("ok"):
                publish_jaimes(
                    objective,
                    "cancelled",
                    summary,
                    work_id=str(card.get("work_id") or ""),
                    run_id=str(card.get("ledger_run_id") or ""),
                    phase="cancelled",
                    model_id=str(card.get("model") or DEFAULT_MODEL),
                    origin_claim_hash=str(card.get("origin_claim_hash") or ""),
                )
            if result.get("ok"):
                card["status"] = "done"
                card["ended_at"] = utc_now()
                card["last_card_update_at"] = card["ended_at"]
                updates.append({"event": f"expired:{run_id}:{card['ended_at']}", "result": result})
            else:
                card["heartbeat_checked_at"] = utc_now()
                updates.append({"event": f"expiry-edit-failed:{run_id}:{card['heartbeat_checked_at']}", "result": result})
            continue
        heartbeat_raw = str(card.get("heartbeat_checked_at") or "")
        try:
            heartbeat_checked = dt.datetime.fromisoformat(heartbeat_raw.replace("Z", "+00:00"))
        except Exception:
            heartbeat_checked = last
        if (now - max(last, heartbeat_checked)).total_seconds() < HEARTBEAT_SECONDS:
            continue
        # Refresh the same card without changing its concrete phase or adding a
        # Completed row. This makes long tool/model calls visibly alive while
        # keeping the card's substantive progress ledger clean.
        current_summary = str(
            card.get("current_summary") or "Work remains active on the verified JAIMES route."
        )
        heartbeat_summary = current_summary
        cmd = [
            "python3",
            "mission-control/scripts/jaimes_work_card.py",
            "update",
            "--key",
            key,
            "--title",
            objective,
            "--model",
            str(card.get("model") or DEFAULT_MODEL),
            "--route",
            str(card.get("route") or DEFAULT_ROUTE),
            "--now",
            heartbeat_summary,
        ] + work_card_target_args(card)
        result = {"ok": True, "dry_run": True} if dry_run else run_work_card_cmd(cmd)
        heartbeat_at = utc_now()
        card["heartbeat_checked_at"] = heartbeat_at
        if not dry_run:
            record_api_result(state, "editMessageText", {
                "ok": bool(result.get("ok")),
                "error": result.get("stderr") or result.get("error") or "",
                "delivery_key": key,
            })
        if result.get("ok"):
            card["last_card_update_at"] = heartbeat_at
        updates.append({"event": f"heartbeat:{run_id}:{heartbeat_at}", "result": result})
        if not dry_run and result.get("ok"):
            publish_jaimes(
                objective,
                "active",
                current_summary,
                work_id=str(card.get("work_id") or ""),
                run_id=str(card.get("ledger_run_id") or ""),
                phase="heartbeat",
                model_id=str(card.get("model") or DEFAULT_MODEL),
                route_verified=True,
                origin_claim_hash=str(card.get("origin_claim_hash") or ""),
                brain_feed=False,
                work_event="heartbeat",
            )
    state["processed_progress_events"] = sorted(processed)[-300:]
    state["approval_buttons_sent"] = sorted(approval_sent)[-200:]
    return updates


def retire_noncurrent_active_cards(state: dict[str, Any], current_run_id: str) -> int:
    """Silently retire every historical card except the current user turn."""
    retired = 0
    ended_at = utc_now()
    for run_id, card in (state.get("active_cards") or {}).items():
        if not isinstance(card, dict) or card.get("status") == "done" or run_id == current_run_id:
            continue
        card["status"] = "done"
        card["ended_at"] = ended_at
        card["retired_reason"] = "superseded-by-newer-user-turn"
        card_key = str(card.get("key") or "")
        if card_key:
            resolve_delivery_incident(state, "editMessageText", card_key)
            resolve_delivery_incident(state, "editMessageText", f"{card_key}:final")
        retired += 1
    return retired


def retire_for_genuine_events(state: dict[str, Any], events: list[dict[str, Any]]) -> int:
    """Retire cards only when an actual ingested Telegram user turn exists."""
    if not events:
        return 0
    return retire_noncurrent_active_cards(state, str(events[-1]["run_id"]))


def internal_replay_prompt(prompt: str) -> bool:
    lowered = (prompt or "").lstrip().lower()
    return lowered.startswith((
        "[context compaction",
        "[async delegation",
        "[your active task list was preserved",
    ))


def direct_jaimes_mention(prompt: str) -> bool:
    return bool(JAIMES_MENTION_RE.search(clean_prompt(prompt)))


def contextual_followup_prompt(prompt: str) -> bool:
    """Recognize short turns that clearly ask about the current result."""
    text = " ".join(clean_prompt(prompt).lower().split()).strip()
    if not text or len(text.split()) > 12 or "http://" in text or "https://" in text:
        return False
    return bool(re.fullmatch(
        r"(?:\?{1,4}|and\??|results?\??|findings?\??|status\??|any update\??|"
        r"(?:so\s+)?what did (?:you|it) find(?: out)?\??|what happened\??|"
        r"what (?:are|were) the (?:findings|results|next steps)\??|"
        r"(?:can you\s+)?summari[sz]e (?:that|it)\??|tell me more\??)",
        text,
        flags=re.I,
    ))


def attach_contextual_followup(
    state: dict[str, Any],
    event: dict[str, Any],
    meta: dict[str, Any],
) -> bool:
    """Keep an explicit result follow-up on the current card and run."""
    if not contextual_followup_prompt(str(event.get("prompt") or "")):
        return False
    card = recent_active_card_for_meta(state, meta, max_age_seconds=float(MAX_ACTIVE_CARD_SECONDS))
    if not card:
        return False
    active = state.setdefault("active_cards", {})
    previous_run_id = next((run_id for run_id, value in active.items() if value is card), "")
    current_run_id = str(event.get("run_id") or "")
    if not current_run_id:
        return False
    if previous_run_id and previous_run_id != current_run_id:
        active.pop(previous_run_id, None)
        active[current_run_id] = card
        card.setdefault("continued_from_run_ids", []).append(previous_run_id)
        card["continued_from_run_ids"] = list(dict.fromkeys(card["continued_from_run_ids"]))[-20:]
    card.setdefault("followup_message_ids", []).append(str(event.get("db_message_id") or ""))
    card["followup_message_ids"] = [value for value in card["followup_message_ids"] if value][-20:]
    card["run_id"] = current_run_id
    card["last_followup_at"] = utc_now()
    return True


def session_has_compaction_marker(session_id: str) -> bool:
    """Detect a compression continuation even when no marker row was copied.

    Hermes rotates to a child session during compaction. Depending on the
    provider/rotation path, the child can begin with copied history but no
    synthetic ``[context compaction ...]`` user row. The parent link and its
    ``end_reason='compression'`` are the durable signal in that case.
    """
    if not HERMES_STATE_DB.exists():
        return False
    con = sqlite3.connect(f"file:{HERMES_STATE_DB}?mode=ro", uri=True, timeout=2)
    try:
        row = con.execute(
            """
            SELECT 1 FROM messages
             WHERE session_id = ? AND role = 'user'
               AND LOWER(LTRIM(COALESCE(content, ''))) LIKE '[context compaction%'
             LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if row:
            return True
        session_columns = {
            str(column[1])
            for column in con.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if not {"id", "parent_session_id", "end_reason"}.issubset(session_columns):
            # Older Hermes state databases and small test fixtures predate the
            # compaction-edge columns. The explicit marker above remains valid;
            # absence of the newer schema is not itself evidence of compaction.
            return False
        #JAIMES: a compression child may carry replayed user turns without a
        # marker. Treat the parent compression edge as equivalent so poll_once
        # keeps extending the existing card instead of creating a new pair.
        row = con.execute(
            """
            SELECT 1
              FROM sessions AS child
              JOIN sessions AS parent ON parent.id = child.parent_session_id
             WHERE child.id = ? AND parent.end_reason = 'compression'
             LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        return bool(row)
    finally:
        con.close()


def replayed_prompt_from_other_session(event: dict[str, Any]) -> bool:
    """Suppress exact historical prompts copied into a compaction session."""
    prompt = str(event.get("prompt") or "").strip()
    session_id = str(event.get("session_id") or "")
    message_id = int(event.get("db_message_id") or 0)
    if not prompt or not session_id or not message_id or not HERMES_STATE_DB.exists():
        return False
    con = sqlite3.connect(f"file:{HERMES_STATE_DB}?mode=ro", uri=True, timeout=2)
    try:
        row = con.execute(
            """
            SELECT 1 FROM messages
             WHERE role = 'user' AND session_id != ? AND id < ?
               AND TRIM(COALESCE(content, '')) = ?
             LIMIT 1
            """,
            (session_id, message_id, prompt),
        ).fetchone()
        return bool(row)
    finally:
        con.close()


def media_only_prompt(prompt: str) -> bool:
    """Return True for attachment-only continuation rows with no user request."""
    lines = [line.strip() for line in str(prompt or "").splitlines() if line.strip()]
    meaningful = []
    for line in lines:
        lower = line.lower()
        if re.fullmatch(r"\[j(?:\|\d+)?\]", lower):
            continue
        if lower.startswith(("[image attached at:", "[file attached at:", "[audio attached at:")):
            continue
        if lower in {"[screenshot]", "[image]", "[attachment]"}:
            continue
        meaningful.append(line)
    return bool(lines) and not meaningful


def recent_active_card_for_meta(state: dict[str, Any], meta: dict[str, Any], max_age_seconds: float = 90.0) -> dict[str, Any] | None:
    """Resolve the current turn's card for multipart attachment continuation."""
    now = dt.datetime.now(dt.timezone.utc)
    candidates = []
    for card in (state.get("active_cards") or {}).values():
        if not isinstance(card, dict) or card.get("status") != "active":
            continue
        if str(card.get("telegram_chat_id") or "") != str(meta.get("telegram_chat_id") or ""):
            continue
        if str(card.get("telegram_thread_id") or "") != str(meta.get("telegram_thread_id") or ""):
            continue
        raw_started = str(card.get("started_at") or "")
        try:
            started = dt.datetime.fromisoformat(raw_started.replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            started = None
        if started and (now - started).total_seconds() <= max_age_seconds:
            candidates.append(card)
    return max(candidates, key=lambda card: str(card.get("started_at") or "")) if candidates else None


def inbox_handoff_topic(meta: dict[str, Any] | None) -> bool:
    return bool(
        str((meta or {}).get("telegram_chat_id") or "") == CONTROL_CENTER_CHAT_ID
        and str((meta or {}).get("telegram_thread_id") or "") in JAIMES_DIRECT_MENTION_TOPICS
    )


def process_ack_event(
    event: dict[str, Any],
    *,
    model: str,
    state: dict[str, Any],
    dry_run: bool,
    meta: dict[str, Any],
    reaction_already_done: bool = False,
) -> dict[str, Any]:
    """Fence Topic 1 ownership behind an exact, privacy-safe acceptance lease.

    The file lock protects only state transitions. Telegram and subprocess
    work can take longer than Josh's handoff wait, so it must never run while
    the lock is held. A random claim token makes the post-send commit
    conditional: if the waiter cancels first, a late sender cannot overwrite
    that cancellation with an accepted receipt.
    """
    if dry_run or not inbox_handoff_topic(meta):
        return send_ack(
            event,
            model=model,
            state=state,
            dry_run=dry_run,
            meta=meta,
            reaction_already_done=reaction_already_done,
        )

    chat_id = meta.get("telegram_chat_id")
    thread_id = meta.get("telegram_thread_id")
    message_id = event.get("platform_message_id") or ((meta.get("origin") or {}).get("message_id"))
    claim_token = secrets.token_urlsafe(24)
    try:
        with handoff_lock(chat_id, thread_id, message_id) as record_path:
            record = load_json(record_path, {})
            if not (
                isinstance(record, dict)
                and record.get("status") == "waiting"
                and handoff_record_matches(record, chat_id, thread_id, message_id)
                and handoff_record_fresh(record)
            ):
                return {
                    "ok": False,
                    "handoff_terminal_failure": True,
                    "error": "handoff_lease_unavailable",
                    "reaction_ok": False,
                    "header_message_id": "",
                    "ack_message_id": "",
                }
            claimed = {
                **record,
                "status": "claimed",
                "ownership_state": "claimed_no_effect",
                "claim_token": claim_token,
                "claimed_at": utc_now(),
                # The waiting lease is sized for Josh's handoff wait, not for
                # the claimed sender's bounded Telegram work. Extend it while
                # atomically taking ownership so the valid token holder cannot
                # self-cancel before the waiter records `indeterminate`.
                "expires_at": (
                    dt.datetime.now(dt.timezone.utc)
                    + dt.timedelta(seconds=HANDOFF_RECEIPT_TTL_SECONDS)
                ).isoformat().replace("+00:00", "Z"),
            }
            write_handoff_record(record_path, claimed)

        # The eyes reaction is idempotent and is the first permitted Telegram
        # effect. Keep the claim in `claimed_no_effect` until that call proves
        # an effect, so a pre-request crash remains safe for Josh fallback.
        reaction_ok = set_eyes_reaction(str(message_id or ""), state, meta=meta)
        if not reaction_ok:
            with handoff_lock(chat_id, thread_id, message_id) as record_path:
                current = load_json(record_path, {})
                if handoff_claim_matches(current, chat_id, thread_id, message_id, claim_token):
                    current.update({
                        "status": "failed",
                        "failed_at": utc_now(),
                        "reason": "eyes_reaction_failed",
                    })
                    write_handoff_record(record_path, current)
            return {
                "ok": False,
                "handoff_terminal_failure": True,
                "error": "eyes_reaction_failed",
                "reaction_ok": False,
                "header_message_id": "",
                "ack_message_id": "",
            }

        def mark_surface_attempt() -> bool:
            # This callback runs after routing and immediately before the
            # work-card child. It is the durable intent checkpoint that closes
            # the eyes-only crash window without holding the lock over I/O.
            with handoff_lock(chat_id, thread_id, message_id) as record_path:
                claimed = load_json(record_path, {})
                claim_active = handoff_claim_matches(
                    claimed, chat_id, thread_id, message_id, claim_token
                ) and handoff_record_fresh(claimed)
                if not claim_active:
                    return False
                claimed["ownership_state"] = "surface_inflight"
                claimed["reaction_ok"] = True
                claimed["card_key"] = (
                    f"jaimes-fast-ack-{chat_id or 'telegram'}-{message_id}"
                )
                claimed["surface_attempt_started_at"] = utc_now()
                write_handoff_record(record_path, claimed)
                return True

        # Telegram/network/subprocess work is intentionally outside the file
        # lock so the waiter can atomically cancel at its deadline.
        result = send_ack(
            event,
            model=model,
            state=state,
            dry_run=False,
            meta=meta,
            reaction_already_done=True,
            surface_attempt_callback=mark_surface_attempt,
        )

        with handoff_lock(chat_id, thread_id, message_id) as record_path:
            current = load_json(record_path, {})
            claim_active = handoff_claim_matches(
                current, chat_id, thread_id, message_id, claim_token
            ) and handoff_record_fresh(current)
            if not claim_active:
                result = dict(result)
                result.update({
                    "ok": False,
                    "handoff_terminal_failure": True,
                    "error": "handoff_claim_cancelled",
                })
                return result

            if result.get("ok"):
                accepted_at = dt.datetime.now(dt.timezone.utc)
                receipt = {
                    "schema_version": 1,
                    "status": "accepted",
                    "agent": "jaimes",
                    "chat_id": str(chat_id),
                    "thread_id": str(thread_id),
                    "inbound_message_id": str(message_id),
                    "reaction_ok": True,
                    "header_message_id": str(result.get("header_message_id") or ""),
                    "live_message_id": str(result.get("ack_message_id") or ""),
                    "accepted_at": accepted_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "expires_at": (accepted_at + dt.timedelta(seconds=HANDOFF_RECEIPT_TTL_SECONDS)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                }
                write_handoff_record(record_path, receipt)
                result["handoff_receipt"] = receipt
                return result

            if result.get("surface_indeterminate"):
                indeterminate_at = dt.datetime.now(dt.timezone.utc)
                receipt = {
                    **current,
                    "status": "indeterminate",
                    "ownership_state": "claimed_in_flight",
                    "indeterminate_at": indeterminate_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "expires_at": (indeterminate_at + dt.timedelta(seconds=HANDOFF_RECEIPT_TTL_SECONDS)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "reaction_ok": bool(result.get("reaction_ok")),
                    "header_message_id": str(result.get("header_message_id") or ""),
                    "live_message_id": str(result.get("ack_message_id") or ""),
                    "reason": "telegram_surface_delivery_indeterminate",
                }
                write_handoff_record(record_path, receipt)
                result["handoff_indeterminate"] = True
                result["handoff_receipt"] = public_indeterminate_handoff_receipt(receipt)
                return result

            failure = {
                **current,
                "status": "failed",
                "failed_at": utc_now(),
                "reason": str(result.get("error") or "jaimes_surface_acceptance_failed")[:120],
            }
            write_handoff_record(record_path, failure)
            result["handoff_terminal_failure"] = True
            return result
    except ValueError:
        return {
            "ok": False,
            "handoff_terminal_failure": True,
            "error": "invalid_handoff_origin",
            "reaction_ok": False,
            "header_message_id": "",
            "ack_message_id": "",
        }


def poll_once(dry_run: bool = False) -> dict[str, Any]:
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    if not dry_run and not verify_bot_identity(state):
        state["last_checked_at"] = utc_now()
        state["status"] = "telegram-identity-error"
        state["last_error_at"] = utc_now()
        state["last_error"] = "JAIMES Telegram bot identity verification failed"
        save_json(STATE_PATH, state)
        return {"ok": False, "status": state["status"]}
    acked = set(state.get("acked_prompt_events") or [])
    metas = active_hermes_sessions_metadata()
    if not metas:
        fallback = session_metadata()
        metas = [fallback] if fallback else []
    if not metas:
        state["last_checked_at"] = utc_now()
        state["direct_session_id"] = ""
        state["last_result"] = {"ok": False, "status": "no-direct-session"}
        state["status"] = "no-direct-session"
        if not dry_run:
            save_json(STATE_PATH, state)
        return {"ok": False, "status": "no-direct-session"}

    state.setdefault("active_cards", {})
    surface_retries = state.get("surface_retry_events")
    if not isinstance(surface_retries, dict):
        surface_retries = {}
    state["surface_retry_events"] = surface_retries
    poll_now = dt.datetime.now(dt.timezone.utc)

    def advance_event_cursor(event: dict[str, Any]) -> None:
        """Consume one DB row only after it is handled or safely quarantined."""
        event_session_id = str(event.get("session_id") or "")
        event_db_id = int(event.get("db_message_id") or 0)
        if not event_session_id or event_db_id <= 0:
            return
        event_cursor_key = f"direct_db_cursor:{event_session_id}"
        state[event_cursor_key] = max(int(state.get(event_cursor_key) or 0), event_db_id)

    def retire_surface_retry(event_id: str) -> None:
        """Drop a known no-effect retry and its exact delivery incident."""
        record = surface_retries.pop(event_id, None)
        if not isinstance(record, dict):
            return
        delivery_key = str(record.get("delivery_key") or "")
        if delivery_key:
            resolve_delivery_incident(state, "sendMessage", delivery_key)

    candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    session_ids: list[str] = []
    # Scan every owned live Telegram session. A rollover can leave the gateway
    # writing new prompts into an older session for several minutes.
    for session_meta in metas:
        sid = str(session_meta.get("sessionId") or "")
        if not sid:
            continue
        session_ids.append(sid)
        cursor_key = f"direct_db_cursor:{sid}"
        if cursor_key not in state:
            state[cursor_key] = bootstrap_direct_message_cursor(sid)
            state["direct_db_cursor_initialized_at"] = utc_now()
        batch = recent_prompt_events_from_state_db(sid, int(state.get(cursor_key) or 0))
        replay_times = [
            float(dt.datetime.fromisoformat(e["ts"].replace("Z", "+00:00")).timestamp())
            for e in batch if internal_replay_prompt(e.get("prompt") or "")
        ]
        compaction_session = session_has_compaction_marker(sid)
        for event in batch:
            event_id = f"{event['session_id']}:{event['ts']}"
            event_db_id = int(event.get("db_message_id") or 0)
            retry_record = surface_retries.get(event_id)
            retry_pending = isinstance(retry_record, dict)
            if retry_pending:
                retry_after = parse_utc(retry_record.get("next_retry_at"))
                if retry_after and poll_now < retry_after:
                    # Keep the database cursor behind this row while the
                    # persisted bounded backoff is active. The daemon polls at
                    # 1.5 seconds, so retrying every tick would hammer Telegram.
                    continue
            if (
                str(session_meta.get("telegram_chat_id") or "") == CONTROL_CENTER_CHAT_ID
                and not owner_accepts(
                    "jaimes",
                    session_meta.get("telegram_chat_id"),
                    session_meta.get("telegram_thread_id"),
                    direct=False,
                )
                and not direct_jaimes_mention(event.get("prompt") or "")
            ):
                # Any Josh-owned or newly authorized topic remains Josh-owned
                # unless JAIMES is directly tagged.
                acked.add(event_id)
                state[cursor_key] = max(int(state.get(cursor_key) or 0), event_db_id)
                continue
            if (
                inbox_handoff_topic(session_meta)
                and direct_jaimes_mention(event.get("prompt") or "")
                and not dry_run
            ):
                handoff_decision, handoff_record = handoff_event_state(session_meta, event)
                if handoff_decision == "wait":
                    # Do not advance until Josh creates a waiting lease, or
                    # while another token owner is still resolving it.
                    break
                if handoff_decision == "consume":
                    event_id = f"{event['session_id']}:{event['ts']}"
                    acked.add(event_id)
                    state[cursor_key] = max(int(state.get(cursor_key) or 0), int(event.get("db_message_id") or 0))
                    recover_accepted_handoff_card(state, event, session_meta, handoff_record)
                    continue
            age = event_age_seconds(event["ts"])
            event_ts = dt.datetime.fromisoformat(event["ts"].replace("Z", "+00:00")).timestamp()
            replay_adjacent = any(abs(event_ts - marker_ts) <= 2.0 for marker_ts in replay_times)
            replay_duplicate = compaction_session and replayed_prompt_from_other_session(event)
            if (
                internal_replay_prompt(event.get("prompt") or "")
                or replay_adjacent
                or replay_duplicate
                or (
                    age is not None
                    and age > STALE_BOOTSTRAP_SECONDS
                    and not retry_pending
                )
            ):
                acked.add(event_id)
                state[cursor_key] = max(int(state.get(cursor_key) or 0), event_db_id)
                retire_surface_retry(event_id)
                continue
            if event_id not in acked:
                candidates.append((event_ts, event, session_meta))

    # Preserve the existing anti-replay rule: if multiple genuine turns arrive
    # during one catch-up pass, only the newest creates visible Telegram UX.
    candidates.sort(key=lambda item: item[0])
    if candidates:
        _, newest_event, newest_meta = candidates[-1]
        attached_followup = attach_contextual_followup(state, newest_event, newest_meta)
        attached_card = (
            recent_active_card_for_meta(state, newest_meta)
            if not attached_followup and media_only_prompt(newest_event.get("prompt") or "")
            else None
        )
        if attached_followup:
            attached_id = f"{newest_event['session_id']}:{newest_event['ts']}"
            acked.add(attached_id)
            advance_event_cursor(newest_event)
            retire_surface_retry(attached_id)
            state["contextual_followups_attached"] = int(state.get("contextual_followups_attached") or 0) + 1
            candidates.pop()
        elif attached_card:
            attached_id = f"{newest_event['session_id']}:{newest_event['ts']}"
            acked.add(attached_id)
            advance_event_cursor(newest_event)
            retire_surface_retry(attached_id)
            attached_card.setdefault("attachment_message_ids", []).append(str(newest_event.get("db_message_id") or ""))
            state["multipart_rows_attached"] = int(state.get("multipart_rows_attached") or 0) + 1
            candidates.pop()
    selected = candidates[-1:] if candidates else []
    if selected:
        selected_event = selected[0][1]
        selected_session = str(selected_event.get("session_id") or "")
        selected_db_id = int(selected_event.get("db_message_id") or 0)
        for retry_event_id, retry_record in list(surface_retries.items()):
            if not isinstance(retry_record, dict):
                continue
            if (
                str(retry_record.get("session_id") or "") == selected_session
                and 0 < int(retry_record.get("db_message_id") or 0) < selected_db_id
            ):
                # The existing anti-replay policy chooses the newest user turn.
                # A prior definitive no-effect failure is now superseded, so
                # retire its retry and exact health incident rather than leave
                # an unreachable permanent error behind the advanced cursor.
                acked.add(retry_event_id)
                retire_surface_retry(retry_event_id)
    for _, stale_event, _ in candidates[:-1]:
        stale_event_id = f"{stale_event['session_id']}:{stale_event['ts']}"
        acked.add(stale_event_id)
        advance_event_cursor(stale_event)
        retire_surface_retry(stale_event_id)

    sent: list[dict[str, Any]] = []
    selected_meta = selected[0][2] if selected else metas[0]
    selected_session_id = str(selected_meta.get("sessionId") or "")
    selected_model = str(selected_meta.get("model") or DEFAULT_MODEL)
    events = [selected[0][1]] if selected else []
    if events:
        state["silently_retired_cards"] = int(state.get("silently_retired_cards") or 0) + retire_for_genuine_events(state, events)
    for event in events:
        event_id = f"{event['session_id']}:{event['ts']}"
        retry_record = surface_retries.get(event_id)
        reaction_already_done = bool(
            isinstance(retry_record, dict) and retry_record.get("reaction_ok")
        )
        queued_x = (
            0
            if dry_run or isinstance(retry_record, dict)
            else queue_forwarded_x_intelligence(event, selected_meta)
        )
        result = process_ack_event(
            event,
            model=selected_model,
            state=state,
            dry_run=dry_run,
            meta=selected_meta,
            reaction_already_done=reaction_already_done,
        )
        if queued_x:
            result["x_intelligence_queued"] = queued_x
        if result.get("ok"):
            acked.add(event_id)
            advance_event_cursor(event)
            retire_surface_retry(event_id)
            if registerable_ack_result(result):
                state.setdefault("processed_task_keys", []).append(str(result.get("key") or ""))
                state["processed_task_keys"] = sorted({k for k in state["processed_task_keys"] if k})[-300:]
            if result.get("run_id") and registerable_ack_result(result):
                state["active_cards"][result["run_id"]] = {
                    "key": result.get("key"),
                    "work_id": result.get("work_id"),
                    "ledger_run_id": result.get("ledger_run_id"),
                    "origin_claim_hash": result.get("origin_claim_hash"),
                    "objective": result.get("objective"),
                    "model": result.get("model"),
                    "route": result.get("route"),
                    "header_message_id": result.get("header_message_id"),
                    "ack_message_id": result.get("ack_message_id"),
                    "telegram_chat_id": selected_meta.get("telegram_chat_id"),
                    "telegram_thread_id": selected_meta.get("telegram_thread_id"),
                    "session_id": selected_session_id,
                    "task_started_at": result.get("task_started_at") or result.get("last_card_update_at"),
                    "started_at": result.get("last_card_update_at"),
                    "last_progress_at": result.get("last_card_update_at"),
                    "last_card_update_at": result.get("last_card_update_at"),
                    "status": "active",
                    "retention": str(result.get("retention") or "persistent-edit-only"),
                }
            sent.append({"event": event_id, "result": result})
        else:
            if (
                result.get("handoff_terminal_failure")
                or result.get("handoff_indeterminate")
                or result.get("surface_indeterminate")
            ):
                acked.add(event_id)
                advance_event_cursor(event)
                retire_surface_retry(event_id)
            else:
                previous_retry = retry_record if isinstance(retry_record, dict) else {}
                attempts = int(previous_retry.get("attempts") or 0) + 1
                delay_seconds = min(
                    SURFACE_RETRY_BASE_SECONDS * (2 ** min(attempts - 1, 6)),
                    SURFACE_RETRY_MAX_SECONDS,
                )
                failed_at = dt.datetime.now(dt.timezone.utc)
                surface_retries[event_id] = {
                    "attempts": attempts,
                    "first_failed_at": str(
                        previous_retry.get("first_failed_at")
                        or failed_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    ),
                    "last_failed_at": failed_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "next_retry_at": (
                        failed_at + dt.timedelta(seconds=delay_seconds)
                    ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "reaction_ok": bool(
                        result.get("reaction_ok") or reaction_already_done
                    ),
                    "session_id": str(event.get("session_id") or ""),
                    "db_message_id": int(event.get("db_message_id") or 0),
                    "delivery_key": str(result.get("key") or ""),
                    "error": sanitize_error_text(
                        result.get("error") or "Managed Telegram surface was not confirmed",
                        limit=180,
                    ),
                }
            sent.append({"event": event_id, "result": result})
            break

    for retry_event_id, retry_record in list(surface_retries.items()):
        if not isinstance(retry_record, dict):
            continue
        retry_session = str(retry_record.get("session_id") or "")
        retry_db_id = int(retry_record.get("db_message_id") or 0)
        if (
            retry_session
            and retry_db_id > 0
            and int(state.get(f"direct_db_cursor:{retry_session}") or 0)
            >= retry_db_id
        ):
            # Any path that advances past a definitive no-effect failure—new
            # task, contextual follow-up, or multipart attachment—makes that
            # retry unreachable. Retire its exact incident in the same poll.
            acked.add(retry_event_id)
            retire_surface_retry(retry_event_id)

    ordered_retries = sorted(
        (
            (event_id, record)
            for event_id, record in surface_retries.items()
            if isinstance(record, dict)
        ),
        key=lambda item: str(item[1].get("last_failed_at") or ""),
    )[-SURFACE_RETRY_MAX_RECORDS:]
    state["surface_retry_events"] = dict(ordered_retries)

    state["acked_prompt_events"] = sorted(acked)[-300:]
    state["last_checked_at"] = utc_now()
    state["direct_session_id"] = selected_session_id
    state["owned_session_ids"] = session_ids
    state["model"] = selected_model
    delivery_error = state.get("last_telegram_delivery_error")
    if isinstance(delivery_error, dict):
        state["status"] = "telegram-delivery-error"
        state["last_error"] = "A managed Telegram card send or edit lacks a confirmed receipt."
        state["last_error_at"] = str(delivery_error.get("at") or utc_now())
    else:
        state["status"] = "ok"
        state.pop("last_error", None)
        state.pop("last_error_at", None)
    if sent:
        state["last_attempt_at"] = utc_now()
        state["last_result"] = sent[-1]["result"]
        latest_result = sent[-1]["result"]
        latest_message_id = positive_message_id(latest_result.get("ack_message_id"))
        if registerable_ack_result(latest_result) and latest_message_id:
            state["last_sent_at"] = state["last_attempt_at"]
            state["latest_pending_ack"] = {
                "message_id": latest_message_id,
                "key": latest_result.get("key"),
                "event": sent[-1]["event"],
                "created_at": utc_now(),
                "model": selected_model,
                "telegram_chat_id": latest_result.get("telegram_chat_id"),
                "telegram_thread_id": latest_result.get("telegram_thread_id"),
            }
        else:
            state.pop("latest_pending_ack", None)
    else:
        state["last_result"] = {"ok": True, "status": "watching", "session_ids": session_ids}
    pending = state.get("latest_pending_ack")
    if isinstance(pending, dict) and not positive_message_id(pending.get("message_id")):
        state.pop("latest_pending_ack", None)

    updates: list[dict[str, Any]] = []
    for sid in session_ids:
        state["cards_completed_from_final"] = int(state.get("cards_completed_from_final") or 0) + complete_cards_from_final_responses(
            state, sid, dry_run=dry_run
        )
        updates.extend(update_active_cards(state, sid, dry_run=dry_run))
    state["cards_confirmed_by_adapter"] = int(state.get("cards_confirmed_by_adapter") or 0) + reconcile_adapter_confirmed_deliveries(
        state, dry_run=dry_run
    )
    if not dry_run:
        now_stamp = dt.datetime.now(dt.timezone.utc)
        last_evidence = parse_final_timestamp(state.get("completion_evidence_written_at", ""))
        if last_evidence is None or now_stamp - last_evidence >= dt.timedelta(minutes=5):
            try:
                write_completion_evidence(now=now_stamp)
                state["completion_evidence_written_at"] = now_stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")
                state.pop("completion_evidence_error_at", None)
            except Exception:
                # Observability must never prevent Telegram state from saving.
                state["completion_evidence_error_at"] = utc_now()
        save_json(STATE_PATH, state)
    return {"ok": True, "session_id": selected_session_id, "session_ids": session_ids, "sent": sent, "updates": updates, "dry_run": dry_run}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--format-final-json-stdin",
        action="store_true",
        help="Read a private JSON payload from stdin and emit one canonical Topic 17 final.",
    )
    parser.add_argument("--once", action="store_true", help="Run one poll and exit.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--await-handoff", action="store_true", help="Wait for an exact Topic 1 JAIMES acceptance receipt.")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--thread-id", default="")
    parser.add_argument("--message-id", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    if args.format_final_json_stdin:
        payload = json.load(sys.stdin)
        print(structured_final_text(
            str(payload.get("text") or ""),
            objective=str(payload.get("objective") or "Complete the current Telegram task"),
            model=str(payload.get("model") or DEFAULT_MODEL),
            route=str(payload.get("route") or DEFAULT_ROUTE),
            why=str(payload.get("why") or ""),
            work_id=str(payload.get("work_id") or ""),
            run_id=str(payload.get("run_id") or ""),
            task_started_at=str(payload.get("task_started_at") or ""),
            response_recorded_at=str(payload.get("response_recorded_at") or ""),
        ))
        return 0

    if args.await_handoff:
        code, receipt = await_handoff(args.chat_id, args.thread_id, args.message_id, args.timeout)
        print(json.dumps(receipt, sort_keys=True))
        return code

    if args.once:
        print(json.dumps(poll_once(dry_run=args.dry_run), indent=2))
        return 0

    while True:
        try:
            poll_once()
        except Exception as exc:  # noqa: BLE001 - keep watcher alive
            state = load_json(STATE_PATH, {})
            if not isinstance(state, dict):
                state = {}
            state["last_error_at"] = utc_now()
            state["last_error"] = sanitize_error_text(exc, limit=320)
            save_json(STATE_PATH, state)
        time.sleep(max(0.5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
