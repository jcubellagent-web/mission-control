#!/usr/bin/env python3
"""Create and update one editable JAIMES-facing Telegram work card."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import html
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
#JAIMES: Telegram <pre> cards use fixed-width glyph cells. Three spaces
# visually match the emoji/check prefix there; six spaces only matched
# proportional text and pushed continuation rows too far right.
CARD_WRAP_WIDTH = max(32, int(os.environ.get("JAIMES_CARD_WRAP_WIDTH", "38")))
CARD_CONTINUATION_INDENT = "   "
#JAIMES: one absolute state path prevents completion calls launched from a
#different cwd from rebuilding the card with only the last "summary sent" row.
STATE_PATH = Path(os.environ.get("JAIMES_WORK_CARD_STATE", str(ROOT.parent / "memory" / "jaimes_work_cards.json")))
LOCK_PATH = Path(os.environ.get("JAIMES_WORK_CARD_LOCK", str(STATE_PATH.with_suffix(".lock"))))
ACK_STATE_PATH = Path(os.environ.get("JAIMES_FAST_ACK_STATE", str(Path.home() / ".openclaw" / "telegram" / "jaimes_fast_ack_state.json")))
CONTROL_CENTER_CHAT_ID = "-1003589561528"
INBOX_THREAD_ID = "1"
JAIMES_OPS_THREAD_ID = "17"
PRIMARY_LIVE_STAGES = ("Accepted", "Planned", "Routed", "Working", "Verifying", "Delivered")
TASK_HEADER_ENV = "JAIMES_TELEGRAM_TASK_HEADERS"
RICH_CARD_ENV = "JAIMES_TELEGRAM_RICH_CARDS"
HEADER_LABEL_WIDTH = 9
HEADER_VALUE_WIDTH = 25
DEFAULT_RECONCILE_MAX_AGE_SECONDS = 12 * 60 * 60
ACTIVE_WORK_CARD_STATUSES = {"active", "running"}
TERMINAL_FAST_ACK_STATUSES = {"done", "failed", "paused", "retired", "complete", "completed"}
CONTROL_TOWER_SSH_HOST = os.environ.get("CONTROL_TOWER_SSH_HOST", "josh2.0@josh2")
CONTROL_TOWER_REMOTE_ROOT = os.environ.get(
    "CONTROL_TOWER_REMOTE_ROOT",
    "/Users/josh2.0/.openclaw/workspace/mission-control",
)
CONTROL_TOWER_REMOTE_PYTHON = os.environ.get(
    "CONTROL_TOWER_REMOTE_PYTHON",
    "/opt/homebrew/bin/python3",
)
ENV_PATHS = [
    HOME / ".hermes" / ".env",
    HOME / ".openclaw" / "service-env" / "ai.openclaw.gateway.env",
]
DEFAULT_BUTTONS = [
    [{"text": "1. Gemini review", "callback_data": "model:gemini_flash"}],
    [{"text": "2. JAIMES workhorse", "callback_data": "model:codex"}],
    [{"text": "Agent council", "callback_data": "route:agent_council"}],
    [{"text": "3. J.AI.N worker", "callback_data": "route:jain"}],
    [{"text": "4. JOSHeX Cloud / repo-safe", "callback_data": "route:joshex_cloud"}],
    [{"text": "5. JOSHeX private accounts", "callback_data": "route:joshex"}],
    [{"text": "Show model choices", "callback_data": "next:show_models"}],
    [{"text": "Hold / no action", "callback_data": "next:hold"}],
]


def load_env_value(key: str) -> str:
    if os.environ.get(key):
        return str(os.environ[key]).strip().strip('"').strip("'")
    for path in ENV_PATHS:
        try:
            rows = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for row in rows:
            stripped = row.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            raw_key, raw_value = stripped.split("=", 1)
            raw_key = raw_key.replace("export ", "").strip()
            if raw_key == key:
                return raw_value.strip().strip('"').strip("'")
    return ""


def telegram_target() -> str:
    direct = load_env_value("TELEGRAM_TARGET_CHAT_ID") or load_env_value("TELEGRAM_CHAT_ID")
    if direct:
        return direct
    allowed = load_env_value("TELEGRAM_ALLOWED_USERS")
    for item in allowed.replace(";", ",").replace(" ", ",").split(","):
        item = item.strip()
        if item:
            return item
    return ""


def api_base() -> str:
    token = load_env_value("TELEGRAM_BOT_TOKEN")
    return f"https://api.telegram.org/bot{token}" if token else ""


def now_label() -> str:
    return dt.datetime.now().astimezone().strftime("%H:%M %Z")


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"cards": {}}


def load_json_file(path: Path, fallback: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else fallback
    except Exception:
        return fallback


def save_json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        # State contains Telegram message identifiers and origin metadata. Keep
        # both newly-created stores and replacements private even when a caller
        # has an unusually permissive umask.
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def ack_state_lock():
    """Use the fast-ack daemon's exact cross-process state lock."""
    lock_path = ACK_STATE_PATH.with_suffix(ACK_STATE_PATH.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def claim_pending_ack(card_key: str, chat_id: str | None, thread_id: str | None) -> str:
    #JAIMES: merge only this receipt while holding the daemon's exact lock;
    # whole-state unlocked writes can erase active cards and poll cursors.
    with ack_state_lock():
        state = load_json_file(ACK_STATE_PATH, {})
        if not isinstance(state, dict):
            state = {}
        pending = state.get("latest_pending_ack")
        if not isinstance(pending, dict):
            return ""
        message_id = str(pending.get("message_id") or "")
        if not message_id or pending.get("claimed_by"):
            return ""
        pending_chat = str(pending.get("telegram_chat_id") or "")
        pending_thread = str(pending.get("telegram_thread_id") or "")
        requested_chat = str(chat_id or "")
        requested_thread = str(thread_id or "")
        # A pending acknowledgement is origin-scoped. Reusing the newest
        # message ID across topics edits the wrong card and makes both cards
        # appear to disappear or change objectives.
        if not pending_chat or pending_chat != requested_chat or pending_thread != requested_thread:
            return ""
        state["latest_pending_ack"] = {
            **pending,
            "claimed_by": card_key,
            "claimed_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        # save_json_file already uses a unique private temporary inode and an
        # atomic replace, so concurrent card processes cannot share `.tmp`.
        save_json_file(ACK_STATE_PATH, state)
        return message_id


def save_state(state: dict) -> None:
    save_json_file(STATE_PATH, state)


def parse_utc_timestamp(value: object) -> dt.datetime | None:
    """Parse a persisted ISO timestamp without guessing when it is invalid."""
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _strict_json_object(path: Path, *, missing: dict) -> dict:
    """Read state for maintenance without load_state's lossy fallback."""
    if not path.exists():
        return missing
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot safely read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Cannot safely reconcile {path.name}: root must be an object")
    return value


def active_fast_ack_card_keys(state: dict) -> set[str]:
    """Return work-card keys still owned by a non-terminal fast-ack run."""
    active = state.get("active_cards", {})
    if not isinstance(active, dict):
        raise RuntimeError("Cannot safely reconcile fast-ack state: active_cards must be an object")
    keys: set[str] = set()
    for run_id, record in active.items():
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "").strip().lower()
        if status in TERMINAL_FAST_ACK_STATUSES:
            continue
        key = str(record.get("key") or run_id or "").strip()
        if key:
            keys.add(key)
    return keys


def _timestamped_state_backup(now: dt.datetime) -> Path:
    """Copy the exact pre-mutation state into a private, timestamped backup."""
    if not STATE_PATH.exists():
        raise RuntimeError("Cannot create a reconciliation backup: work-card state is missing")
    timestamp = now.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = STATE_PATH.with_name(f"{STATE_PATH.name}.{timestamp}.bak")
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}.{suffix}")
        suffix += 1
    payload = STATE_PATH.read_bytes()
    fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        raise
    os.chmod(candidate, 0o600)
    return candidate


def _reconcile_work_cards(
    *,
    max_age_seconds: int = DEFAULT_RECONCILE_MAX_AGE_SECONDS,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> dict:
    """Retire stale, unowned records without touching their Telegram messages."""
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be zero or greater")
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)

    state = _strict_json_object(STATE_PATH, missing={"cards": {}})
    cards = state.get("cards")
    if not isinstance(cards, dict):
        raise RuntimeError("Cannot safely reconcile work-card state: cards must be an object")
    fast_ack_state = _strict_json_object(ACK_STATE_PATH, missing={})
    owned_keys = active_fast_ack_card_keys(fast_ack_state)

    candidates: list[str] = []
    skipped = {
        "fast_ack_active": 0,
        "fresh": 0,
        "invalid_updated_at": 0,
        "terminal": 0,
        "invalid_record": 0,
    }
    for key, record in cards.items():
        if not isinstance(record, dict):
            skipped["invalid_record"] += 1
            continue
        status = str(record.get("status") or "").strip().lower()
        if status not in ACTIVE_WORK_CARD_STATUSES:
            skipped["terminal"] += 1
            continue
        if str(key) in owned_keys:
            skipped["fast_ack_active"] += 1
            continue
        updated = parse_utc_timestamp(record.get("updated_at"))
        if updated is None:
            # Missing or malformed timestamps are ambiguous, so preserve them
            # for operator review instead of guessing that they are stale.
            skipped["invalid_updated_at"] += 1
            continue
        age_seconds = (current - updated).total_seconds()
        if age_seconds < max_age_seconds:
            skipped["fresh"] += 1
            continue
        candidates.append(str(key))

    backup_path: Path | None = None
    retired_at = current.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    reason = f"stale-for-{max_age_seconds}s-without-active-fast-ack-owner"
    if candidates and not dry_run:
        # Back up the exact on-disk bytes before modifying the in-memory state.
        backup_path = _timestamped_state_backup(current)
        for key in candidates:
            record = cards[key]
            record.setdefault("previous_status", record.get("status"))
            record["status"] = "retired"
            record["retired_at"] = retired_at
            record["retired_reason"] = reason
        save_state(state)

    return {
        "ok": True,
        "dry_run": dry_run,
        "max_age_seconds": max_age_seconds,
        "scanned": len(cards),
        "retired": len(candidates),
        "retired_keys": sorted(candidates),
        "active_fast_ack_keys": sorted(owned_keys),
        "skipped": skipped,
        "backup": str(backup_path) if backup_path else "",
        "telegram_messages_changed": False,
        "brain_feed_published": False,
    }


def reconcile_work_cards(
    *,
    max_age_seconds: int = DEFAULT_RECONCILE_MAX_AGE_SECONDS,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> dict:
    with state_lock():
        return _reconcile_work_cards(max_age_seconds=max_age_seconds, dry_run=dry_run, now=now)


@contextlib.contextmanager
def state_lock():
    """Serialize send/edit/checkpoint operations across overlapping workers."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def parse_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def append_log(existing: list[str], *groups: list[str]) -> list[str]:
    log = [clean_live_text(item) for item in existing if clean_live_text(item)]
    for group in groups:
        for item in group:
            text = clean_live_text(item)
            if text and (not log or log[-1] != text):
                log.append(text)
    return log[-40:]


def status_label(status: str) -> str:
    labels = {
        "running": "In progress - not done yet",
        "done": "Done - no more work is running",
        "failed": "Needs attention",
        "paused": "Paused",
    }
    return labels.get(status, status)


def status_headline(status: str) -> str:
    if status == "running":
        return "JAIMES is working"
    if status == "done":
        return "JAIMES is done"
    if status == "failed":
        return "JAIMES needs attention"
    if status == "paused":
        return "JAIMES is paused"
    return f"JAIMES status: {status}"


def default_current_step(status: str) -> str:
    if status == "running":
        return "Working through the task."
    if status == "done":
        return "Finished."
    if status == "failed":
        return "Blocked or needs approval."
    if status == "paused":
        return "Paused."
    return "Checking status."


def default_next_steps(status: str, has_issue: bool) -> list[str]:
    if has_issue:
        return ["Review the issue and approve the next safe step."]
    if status == "done":
        return ["No action needed."]
    if status == "paused":
        return ["Send the next instruction when ready."]
    return ["Keep working and update this card when the phase changes."]


def compact(value: str, fallback: str = "", limit: int = 220) -> str:
    text = " ".join((value or fallback).split())
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].strip()
    return clipped or text[:limit].strip()


def clean_live_text(value: str, fallback: str = "") -> str:
    return " ".join((value or fallback).replace("...", "").replace("…", "").split())


def hanging_wrap(value: str, *, width: int = CARD_WRAP_WIDTH) -> str:
    """Wrap a card row with a mobile-friendly hanging indent."""
    text = clean_live_text(value)
    if not text:
        return ""
    indent = "  " if text.startswith("- ") else CARD_CONTINUATION_INDENT
    return "\n".join(
        textwrap.wrap(
            text,
            width=width,
            subsequent_indent=indent,
            break_long_words=False,
            break_on_hyphens=False,
        )
    )


def estimate_initial_plan(title: str) -> tuple[int, str]:
    """Give the first bubble a conservative phase count and ETA range."""
    text = clean_live_text(title).lower()
    heavy = ("build", "implement", "deep", "migrate", "deploy", "refactor", "integrate")
    medium = ("fix", "repair", "audit", "review", "test", "verify", "configure", "update", "change", "switch")
    if any(word in text for word in heavy):
        return 4, "~8–15 min"
    if any(word in text for word in medium):
        return 3, "~5–10 min"
    if text.endswith("?") and not any(word in text for word in ("can you", "could you", "please")):
        return 1, "<2 min"
    return 2, "~3–6 min"


def operator_objective(title: str) -> str:
    """Convert conversational Telegram text into a short intent statement."""
    text = clean_live_text(title, "Handle the current Telegram task")
    text = re.sub(r"^\[[A-Za-z]\|[^\]]+\]\s*", "", text).strip()
    lowered = text.lower()
    if lowered in {"latest telegram task received", "determining objective", "handle latest telegram task"}:
        return "Work out the real objective and start the right check."

    #JAIMES: Objective cards summarize intent; never display a clipped copy of
    # the Telegram prompt or its conversational/courtesy preamble.
    if "button" in lowered and ("approval" in lowered or "steps" in lowered):
        return "Check whether the unexpected approval button was intentional."
    if "card" in lowered and "summar" in lowered and "objective" in lowered:
        return "Make objective cards summarize intent instead of quoting prompts."
    if "alert" in lowered and any(word in lowered for word in ("hard to read", "format", "section")):
        return "Reformat alerts into clear, easy-to-scan sections."
    if "market cap" in lowered and any(word in lowered for word in ("bought", "buy", "sold", "sell")):
        return "Investigate the matching buy and sell market-cap labels."

    # Prefer the sentence containing the actual ask over acknowledgements such
    # as "much better, thank you" that often precede it.
    parts = [p.strip(" ,.-") for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]
    request_markers = ("please", "can you", "could you", "would you", "why ", "did you", "fix ", "make ", "change ", "add ", "remove ", "check ", "find ", "build ", "run ", "verify ")
    candidates = [p for p in parts if any(marker in p.lower() for marker in request_markers)]
    intent = candidates[-1] if candidates else text
    intent = re.sub(r"^(?:okay|ok|perfect|great|thanks|thank you|much better)[,! .-]*", "", intent, flags=re.I)
    intent = re.sub(r"^(?:can|could|would) you\s+", "", intent, flags=re.I)
    intent = re.sub(r"^please\s+(?:actually\s+)?", "", intent, flags=re.I)
    intent = re.sub(r"^why\s+(?:does|did|is|are)\s+", "Investigate why ", intent, flags=re.I)
    intent = re.sub(r"^did you mean to\s+", "Confirm whether to ", intent, flags=re.I)
    intent = intent.strip(" ?.!,-")
    if intent:
        intent = intent[0].upper() + intent[1:]
        if not intent.endswith("."):
            intent += "."
    return compact(intent or "Handle the current Telegram task.", limit=90)


def task_headers_enabled(chat_id: str | int | None, thread_id: str | int | None) -> bool:
    """Default the immutable routing receipt on for the Control Center Inbox."""
    raw = os.environ.get(TASK_HEADER_ENV, "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return str(chat_id or "") == CONTROL_CENTER_CHAT_ID and str(thread_id or "") == INBOX_THREAD_ID


def rich_cards_enabled(chat_id: str | int | None, thread_id: str | int | None) -> bool:
    """Use the same native card surface as Inbox for the owned JAIMES topic."""
    raw = os.environ.get(RICH_CARD_ENV, "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    normalized_chat = str(chat_id or "").removeprefix("telegram:")
    return normalized_chat == CONTROL_CENTER_CHAT_ID and str(thread_id or "") == JAIMES_OPS_THREAD_ID


def route_fact(value: str, key: str) -> str:
    for item in str(value or "").split(";"):
        if "=" not in item:
            continue
        raw_key, raw_value = item.split("=", 1)
        normalized = re.sub(r"^(?:planned|actual|verified)\s+", "", raw_key.strip().lower())
        if normalized == key:
            return clean_live_text(raw_value)
    return ""


def planned_agent_name(model: str, route: str) -> str:
    worker = (route_fact(model, "worker") or route_fact(route, "worker")).lower()
    haystack = f"{worker} {route}".lower()
    if "joshex" in haystack:
        return "JOSHeX"
    if "jain" in haystack or "j.a.i.n" in haystack:
        return "J.AI.N"
    if "gemini" in haystack:
        return "JAIMES / Gemini"
    if "grok" in haystack or "xai" in haystack:
        return "JAIMES / Grok"
    return "JAIMES system"


def planned_models(model: str, route: str) -> list[str]:
    results: list[str] = []
    for source in (model, route):
        provider = route_fact(source, "provider")
        raw_models = route_fact(source, "models") or route_fact(source, "model")
        for raw in re.split(r"\s*(?:,|\||→|->)\s*", raw_models):
            item = clean_live_text(raw)
            if not item:
                continue
            label = item if "/" in item or not provider else f"{provider}/{item}"
            if label not in results:
                results.append(label)
    fallback = friendly_model_line(model)
    if not results and fallback:
        results.append(fallback)
    return results or ["Route-selected model"]


def task_header_row(label: str, value: str) -> list[str]:
    parts = textwrap.wrap(
        clean_live_text(value),
        width=HEADER_VALUE_WIDTH,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [""]
    rows = []
    for index, part in enumerate(parts):
        row_label = compact(label, limit=HEADER_LABEL_WIDTH) if index == 0 else ""
        rows.append(f"│{row_label:<{HEADER_LABEL_WIDTH}}│ {part:<{HEADER_VALUE_WIDTH}}│")
    return rows


def build_task_header(*, title: str, model: str, route: str) -> str:
    """Build the fixed-width routing receipt shown before the editable card."""
    divider_top = f"┌{'─' * HEADER_LABEL_WIDTH}┬{'─' * (HEADER_VALUE_WIDTH + 1)}┐"
    divider_mid = f"├{'─' * HEADER_LABEL_WIDTH}┼{'─' * (HEADER_VALUE_WIDTH + 1)}┤"
    divider_bottom = f"└{'─' * HEADER_LABEL_WIDTH}┴{'─' * (HEADER_VALUE_WIDTH + 1)}┘"
    lines = [
        "TASK HEADER",
        divider_top,
        *task_header_row("Objective", operator_objective(title)),
        divider_mid,
        *task_header_row("Agent", planned_agent_name(model, route)),
        divider_mid,
        *task_header_row("Models", " → ".join(planned_models(model, route))),
        divider_bottom,
    ]
    return f"<pre>{html.escape(chr(10).join(lines))}</pre>"


def friendly_model_line(model: str) -> str:
    text = clean_live_text(model)
    lower = text.lower()
    if not text:
        return "JAIMES"
    # Preserve an authoritative runtime provider/model identifier. Generic
    # labels such as "JAIMES / OpenCLAW" hide tier changes and made Terra or
    # Sol sessions appear to still be GPT-5.5.
    runtime_match = re.search(
        r"(?:provider=)?(openai-codex|codex|openai|google-gemini-cli|gemini|xai|grok|openrouter)"
        r"[/; ,]+(?:model=)?([a-z0-9][a-z0-9._:\-]+)",
        text,
        re.I,
    )
    if runtime_match:
        return compact(f"{runtime_match.group(1)}/{runtime_match.group(2)}", limit=90)
    if "gemini" in lower and ("safe summary" in lower or "review" in lower):
        return "JAIMES, with a summary helper if needed"
    if "codex" in lower or "openclaw" in lower:
        return "JAIMES / OpenCLAW"
    if "jain" in lower:
        return "J.AI.N worker support"
    return compact(text, limit=90)


def friendly_route_line(route: str) -> str:
    text = clean_live_text(route)
    lower = text.lower()
    if not text:
        return "JAIMES direct chat"
    if "jain" in lower:
        return "JAIMES coordinating with J.AI.N"
    if "gemini" in lower:
        return "JAIMES using a summary helper"
    if "joshex" in lower:
        return "JOSHeX support lane"
    if "auto:" in lower:
        return "Auto-routed to the safest available helper"
    return compact(text, limit=110)


def unwrap_shell_command(value: str) -> str:
    text = clean_live_text(value)
    text = re.sub(r"^/bin/(?:zsh|bash)\s+-lc\s+", "", text).strip()
    text = text.strip("'\"")
    text = re.sub(r"^cd\s+[^&;]+(?:&&|;)\s*", "", text).strip()
    text = re.sub(r"^(?:PYTHONPATH|PATH|HOME|OPENCLAW_HOME|CODEX_HOME)=[^ ]+\s+", "", text).strip()
    text = text.strip("'\"")
    return text


def describe_shell_command(value: str) -> str:
    text = unwrap_shell_command(value)
    lower = text.lower()
    if "cua-driver" in lower or "computer use" in lower:
        return "checking Computer Use screen/control service"
    if "launchctl" in lower:
        if "kickstart" in lower or "bootstrap" in lower or "bootout" in lower:
            return "restarting a local agent service"
        return "checking a local agent service"
    if lower.startswith("ssh ") or " josh2 " in lower or " jaimes-via-josh " in lower:
        return "checking a dedicated agent host"
    if lower.startswith("curl ") or "127.0.0.1" in lower or "localhost" in lower:
        return "checking a local service endpoint"
    if lower.startswith("git status"):
        return "checking local repository changes"
    if lower.startswith("git diff"):
        return "reviewing local changes"
    if lower.startswith("rg "):
        return "searching project files for the right code path"
    if lower.startswith("sed "):
        return "reading the relevant project file"
    if "py_compile" in lower:
        return "checking Python files for syntax errors"
    if "pytest" in lower:
        return "running verification tests"
    if "state_visibility_guard.py" in lower:
        return "refreshing Control Tower and Brain Feed visibility"
    if "update_mission_control.py" in lower:
        return "regenerating Control Tower dashboard data"
    if "ecosystem_health_sweep.py" in lower:
        return "checking Josh 2.0, JAIMES, J.AI.N, and Control Tower health"
    if "xai_agent.py" in lower:
        return "checking the xAI/Grok helper connection"
    if "agent_publish.py" in lower:
        return "publishing the latest status to Brain Feed"
    if lower.startswith("jq ") or " jq " in lower:
        return "reading the dashboard health summary"
    if lower.startswith("scp "):
        return "copying a needed helper script to the worker host"
    if "hermes status" in lower:
        return "checking Hermes gateway, Telegram, and model auth"
    if "hermes auth" in lower:
        return "refreshing Hermes provider authentication"
    if "hermes model" in lower:
        return "checking or setting the active Hermes model"
    if "hermes gateway" in lower or "ai.hermes.gateway" in lower:
        return "checking or restarting the Hermes gateway"
    if "openclaw update status" in lower:
        return "checking whether OpenCLAW has an update available"
    if "openclaw update" in lower:
        return "updating OpenCLAW and installed plugins"
    if "openclaw doctor" in lower:
        return "checking OpenCLAW configuration for repairable issues"
    if "npm run build" in lower:
        return "building Control Tower to catch UI/runtime errors"
    if "python3" in lower and "mission-control/scripts/" in lower:
        script = lower.split("mission-control/scripts/", 1)[1].split()[0].strip("'\"")
        script = script.replace("_", " ").replace(".py", "")
        return f"running the Control Tower {script} helper"
    if lower.startswith("date "):
        return "checking the current time on JAIMES"
    if not text:
        return "checking the next needed system signal"
    return compact(text, limit=110)


def simplify_live_detail(value: str) -> str:
    text = clean_live_text(value)
    lower = text.lower()
    for prefix in ("completed checking ", "completed ", "finished ", "checking ", "running "):
        if lower.startswith(prefix):
            text = text[len(prefix):].strip()
            lower = text.lower()
            break
    text = unwrap_shell_command(text)
    lower = text.lower()
    if "cua-driver" in lower or "computer use" in lower:
        return "checking Computer Use screen/control service"
    if " | checking " in text:
        left, right = text.split(" | checking ", 1)
        label = clean_live_text(left).lower()
        if label in {"bash", "exec command", "tool", "local check"}:
            label = "system check"
        return f"{label}: {describe_shell_command(right)}"
    if " | completed checking " in text:
        left, right = text.split(" | completed checking ", 1)
        label = clean_live_text(left).lower()
        if label in {"bash", "exec command", "tool", "local check"}:
            label = "system check"
        return f"{label}: {describe_shell_command(right)}"
    if " | " in text:
        left, right = text.split(" | ", 1)
        label = clean_live_text(left).lower()
        if label in {"bash", "exec command", "tool", "local check"}:
            label = "system check"
        right_summary = describe_shell_command(right)
        if right_summary and right_summary != compact(right, limit=110):
            return f"{label}: {right_summary}"
    if any(marker in lower for marker in ("/bin/zsh", "/bin/bash", " -lc ")):
        return describe_shell_command(text)
    if lower.startswith(("cd ", "python3 ", "openclaw ", "npm ", "hermes ", "launchctl ", "curl ", "git ", "rg ", "sed ", "ssh ")):
        return describe_shell_command(text)
    text = text.replace("bash | checking", "local check: checking")
    text = text.replace("bash | completed", "local check: completed")
    if ":" in text:
        left, right = text.split(":", 1)
        label = clean_live_text(left).lower()
        if label in {"bash", "exec command", "tool", "local check"}:
            label = "system check"
        if label:
            right_text = re.sub(r"^(?:completed\s+checking|completed|checking)\s+", "", right.strip(), flags=re.I)
            if any(marker in right_text.lower() for marker in ("/bin/zsh", "/bin/bash", " -lc ")):
                right_text = describe_shell_command(right_text)
            return f"{label}: {compact(right_text, limit=120)}" if right_text else label
    if text.lower() in {"bash", "local check", "exec command"}:
        return "running a system check"
    if text.lower() in {"running local check", "checking local check"}:
        return "running a system check"
    return compact(text, limit=90)


INTERNAL_PATH_RE = re.compile(
    r"(?:^|\s)(?:~?/|/(?:Users|private|var|tmp|etc)/)|"
    r"\b[\w.-]+\.(?:py|js|ts|tsx|json|plist|sh|log|db)\b",
    re.I,
)
GENERIC_NEXT_RE = re.compile(
    r"(?i)^(?:keep working|continue working|work automatically|show buttons only|"
    r"update this card|see the final summary|no action needed|finish verification)\b"
)


def semantic_progress_detail(item: str) -> str:
    """Convert runtime telemetry into one safe, operator-facing milestone."""
    text = clean_live_text(item)
    if not text:
        return ""
    lower = text.lower()
    if "brain feed" in lower or INTERNAL_PATH_RE.search(text):
        return ""
    commandish = unwrap_shell_command(text).lower()
    if commandish.startswith((
        "cd ", "python3 ", "openclaw ", "npm ", "hermes ", "launchctl ",
        "curl ", "git ", "rg ", "sed ", "ssh ", "scp ", "jq ",
    )):
        return ""
    text = re.sub(r"^[^\w]+", "", text).strip()
    lower = text.lower()
    if lower.startswith((
        "received", "objective determined:", "model selected:", "skill selected:",
        "skill:", "skill applied:",
    )):
        return ""
    for prefix in (
        "tool result:", "tool:", "action completed:", "action:",
        "verification passed:", "verification:", "decision:",
    ):
        if lower.startswith(prefix):
            detail = text[len(prefix):].strip()
            if "—" in detail:
                detail = detail.split("—", 1)[1].strip()
            elif " - " in detail:
                detail = detail.split(" - ", 1)[1].strip()
            elif prefix.startswith("tool"):
                return ""
            text = detail
            lower = text.lower()
            break
    if not text or "brain feed" in lower or INTERNAL_PATH_RE.search(text):
        return ""
    if lower in {"summary sent", "final summary sent"} or lower.startswith("final response"):
        return "Summary ready"
    if lower.startswith("still working"):
        return "Still working on the current check"
    text = re.sub(
        r"^(?:finished|completed checking|completed|done|running|checking|reading|"
        r"tracing|updating|loading|reloading|publishing|researching|using)\s+",
        "",
        text,
        flags=re.I,
    ).strip()
    return compact(text, limit=90)


def live_line(item: str) -> str:
    detail = semantic_progress_detail(item)
    if not detail:
        return ""
    lower = clean_live_text(item).lower()
    completed = lower.startswith((
        "tool result:", "action completed:", "verification passed:", "decision:",
        "finished ", "completed checking ", "completed ", "done:",
    )) or detail == "Summary ready"
    return f"{'✓' if completed else '-'} {detail}"


def plain_progress_text(item: str) -> str:
    text = live_line(item)
    text = re.sub(r"^[^\w]+", "", text).strip()
    return compact(text, limit=90)


def current_step_text(status: str, now: str, live_items: list[str]) -> str:
    source = now or (live_items[-1] if live_items else "")
    text = plain_progress_text(source)
    if status == "done":
        return text or "Finished and verified the result."
    if status == "failed":
        return "Stopped on an issue that needs attention."
    if status == "paused":
        return "Paused until you send the next instruction."
    source = now or (live_items[-1] if live_items else "")
    text = plain_progress_text(source)
    if not text or "gathering the next needed signal" in text.lower():
        return "Checking the next useful signal and reporting only what matters."
    return text


def is_empty_issue(value: str | None) -> bool:
    text = " ".join((value or "").strip().lower().split())
    return text in {"", "none", "no", "n/a", "na", "not applicable"}


def bullet_lines(items: list[str], *, fallback: str = "n/a", limit: int = 5) -> list[str]:
    clean = []
    for item in items:
        text = compact(item, limit=170)
        if text and text not in clean:
            clean.append(text)
    if not clean:
        clean = [fallback]
    return [html.escape(hanging_wrap(f"- {item}")) for item in clean[:limit]]


def plain_bullet_lines(items: list[str], *, fallback: str = "None", limit: int = 10) -> list[str]:
    clean = []
    for item in items:
        text = clean_live_text(item)
        if text and text not in clean:
            clean.append(text)
    if not clean:
        clean = [fallback]
    return [html.escape(hanging_wrap(f"- {item}")) for item in clean[:limit]]


def live_lines(items: list[str], *, fallback: str = "waiting: first update", limit: int = 6) -> list[str]:
    clean = []
    for item in items:
        text = live_line(item)
        if text and text not in clean:
            clean.append(text)
    if not clean:
        clean = [f"- {fallback}"]
    if len(clean) <= limit:
        return clean
    earlier = clean[:-limit]
    done_count = sum(1 for line in earlier if line.startswith("✅"))
    check_count = sum(1 for line in earlier if line.startswith("🔧"))
    parts = []
    if done_count:
        parts.append(f"{done_count} completed")
    if check_count:
        parts.append(f"{check_count} checks")
    if not parts:
        parts.append(f"{len(earlier)} earlier updates")
    return [f"Earlier: {', '.join(parts)} consolidated so the card stays readable.", "", *clean[-limit:]]


COMPLETE_STATUSES = {"done", "complete", "completed", "final", "finished", "success"}


def is_complete_status(status: str) -> bool:
    return str(status or "").strip().lower() in COMPLETE_STATUSES


def progress_lines(items: list[str], status: str, planned_steps: int = 0) -> list[str]:
    complete_status = is_complete_status(status)
    raw = [clean_live_text(item) for item in items if clean_live_text(item)]
    if not raw:
        if complete_status:
            return ["Progress: ██████████ 100% - complete", ""]
        return ["Progress: ░░░░░░░░░░ 0% - starting", ""]

    combined = " ".join(raw).lower()
    phase_hits = [
        any(marker in combined for marker in ("received", "objective determined")),
        any(marker in combined for marker in ("model selected", "skill selected", "route", "runbook")),
        any(marker in combined for marker in ("action", "tool", "implement", "deploy", "build", "running")),
        any(marker in combined for marker in ("verification", "verified", "test", "passed", "healthy")),
    ]
    done_count = sum(phase_hits)
    active_count = sum(
        1 for line in raw
        if any(marker in line.lower() for marker in ("action", "tool", "running", "checking", "verification"))
    )
    total = max(4, int(planned_steps or 0))
    if complete_status:
        percent = 100
    elif status == "failed":
        percent = min(95, round((done_count / total) * 100))
    elif done_count:
        percent = min(95, round((done_count / total) * 100))
    else:
        percent = 5 if active_count else 0
    filled = max(0, min(10, round(percent / 10)))
    bar = "█" * filled + "░" * (10 - filled)
    if complete_status:
        detail = "complete"
    elif active_count:
        detail = f"{done_count}/{total} phases complete"
    else:
        detail = f"{done_count}/{total} phases complete"
    return [f"Progress: {bar} {percent}% - {detail}", ""]


def build_completion_summary(
    *,
    title: str,
    status: str,
    model: str = "",
    now: str = "",
    done: list[str] | None = None,
    next_step: str = "",
    blocker: str = "None",
) -> str:
    complete = status == "done"
    steps = list(done or [])
    if now:
        steps.append(now)
    steps = [plain_progress_text(step) for step in steps if plain_progress_text(step)]
    unique_steps = []
    for step in steps:
        if step not in unique_steps:
            unique_steps.append(step)
    issues = [] if is_empty_issue(blocker) else (parse_list(blocker) or [blocker])
    next_steps = parse_list(next_step)
    if not next_steps:
        next_steps = ["Approve the next safe step."] if issues else []
    model_line = model or os.environ.get("JAIMES_WORK_CARD_MODEL") or "JAIMES Telegram task card"

    def final_lines(items: list[str], fallback: str) -> list[str]:
        clean = [compact(item, limit=180) for item in items if compact(item, limit=180)]
        return [hanging_wrap(f"- {item}") for item in clean[:5]] or [hanging_wrap(f"- {fallback}")]

    approval_needed = next_steps if issues else []
    lines = [
        hanging_wrap(f"Model: {friendly_model_line(model_line)} | Route: Hermes workhorse | Why: verified task execution"),
        "",
        hanging_wrap(f"Complete: {'Yes' if complete else 'No'} - {compact(title, limit=120)}"),
        "",
        "What was done:",
        *final_lines(unique_steps[-5:], f"Closed out: {title}"),
        "",
        "Issues:",
        *final_lines(issues, "n/a"),
        "",
        "Appropriate next steps:",
        *final_lines(next_steps, "No action needed."),
        "",
        "Approval needed:",
        *final_lines(approval_needed, "n/a"),
    ]
    return f"<pre>{html.escape(chr(10).join(lines))}</pre>"


def live_phase(status: str, current: str) -> str:
    if status == "done":
        return "Complete"
    if status == "failed":
        return "Blocked"
    if status == "paused":
        return "Paused"
    lowered = current.lower()
    if any(word in lowered for word in ("test", "verify", "canary", "check", "audit")):
        return "Verification"
    if any(word in lowered for word in ("patch", "build", "implement", "write", "edit", "apply")):
        return "Implementation"
    if any(word in lowered for word in ("research", "inspect", "read", "review", "locate", "diagnos")):
        return "Investigation"
    return "Execution"


def check_lines(items: list[str], *, fallback: str, limit: int = 4) -> list[str]:
    clean: list[str] = []
    for item in items:
        text = re.sub(r"^[^\w]+(?:done|final|received|objective|model|skill|step)?:?\s*", "", clean_live_text(item), flags=re.I)
        text = compact(text, limit=120)
        if text and text not in clean:
            clean.append(text)
    return [html.escape(hanging_wrap(f"✓ {item}")) for item in clean[-limit:]] if clean else [fallback]


def activity_lines(items: list[str], *, fallback: str, limit: int = 3) -> list[str]:
    """Render at most three semantic milestones, consolidating older checks."""
    limit = max(1, min(3, int(limit or 3)))
    clean: list[str] = []
    for item in items:
        text = live_line(item)
        text = compact(text, limit=132)
        if text and text not in clean:
            clean.append(text)
    if len(clean) > limit:
        earlier = len(clean) - max(1, limit - 1)
        clean = [f"… {earlier} earlier checks", *clean[-max(1, limit - 1):]]
    return [html.escape(hanging_wrap(item)) for item in clean] if clean else [fallback]


def build_card(
    *,
    title: str,
    status: str,
    model: str = "",
    route: str = "",
    now: str = "",
    done: list[str] | None = None,
    next_step: str = "",
    blocker: str = "none",
    eta: str = "",
    planned_steps: int = 0,
    updated: str | None = None,
) -> str:
    done = done or []
    model_line = model or os.environ.get("JAIMES_WORK_CARD_MODEL") or "JAIMES Telegram task card"
    live_items = append_log(done, [now] if now else [])
    status_line = {
        "running": "⚙️ <b>JAIMES — Working</b>",
        "done": "✅ <b>JAIMES — Complete</b>",
        "failed": "⚠️ <b>JAIMES — Blocked</b>",
        "paused": "⏸️ <b>JAIMES — Paused</b>",
    }.get(status, f"<b>JAIMES — {html.escape(status.title())}</b>")
    current_plain = current_step_text(status, now, live_items)
    current = semantic_progress_detail(now or current_plain) or current_plain
    phase = live_phase(status, current_plain)
    completed = [item for item in done if semantic_progress_detail(item)]
    blocker_items = [] if is_empty_issue(blocker) else parse_list(blocker) or [blocker]
    next_items = [
        item for item in parse_list(next_step)
        if not GENERIC_NEXT_RE.match(clean_live_text(item))
    ]
    progress_raw = progress_lines(live_items, status, planned_steps=planned_steps)[0].replace("Progress:", "").strip()
    progress = progress_raw.split(" - ", 1)[0] + f" · {phase}"

    lines = [
        status_line,
        f"Model: {html.escape(friendly_model_line(model_line))}",
        "",
        "Objective",
        html.escape(hanging_wrap(operator_objective(title))),
        "",
        "Progress",
        html.escape(progress),
        "",
        "Now",
        html.escape(hanging_wrap(current)),
    ]
    if completed:
        lines += ["", "Done", *activity_lines(completed, fallback="", limit=3)]
    if blocker_items:
        lines += ["", "Blocker", *plain_bullet_lines(blocker_items, limit=2)]
    if next_items:
        lines += ["", "Next", *plain_bullet_lines(next_items, limit=2)]
    lines += ["", f"Updated {updated or now_label()}"]
    if eta:
        lines.insert(-1, f"ETA {html.escape(compact(eta))}")
    rendered = "\n".join(lines).replace("<b>", "").replace("</b>", "")
    return f"<pre>{rendered}</pre>"


def primary_stage_position(status: str, items: list[str]) -> int:
    """Map semantic work events onto the shared six-stage Telegram contract."""
    if status in {"done", "failed", "paused"}:
        return len(PRIMARY_LIVE_STAGES)
    combined = " ".join(clean_live_text(item).lower() for item in items)
    position = 1
    if any(marker in combined for marker in ("objective", "plan", "received")):
        position = 2
    if any(marker in combined for marker in ("model selected", "skill selected", "route", "runbook")):
        position = 3
    if any(marker in combined for marker in ("tool", "action", "execut", "working", "implement", "build", "deploy")):
        position = 4
    if any(marker in combined for marker in ("verif", "test passed", "canary", "final summary validated")):
        position = 5
    return position


def primary_elapsed_text(started_at: str | None, updated_at: str | None = None) -> str:
    started = parse_utc_timestamp(started_at)
    updated = parse_utc_timestamp(updated_at) or dt.datetime.now(dt.timezone.utc)
    if not started:
        return "Elapsed <1m"
    seconds = max(0, int((updated - started).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"Elapsed {hours}h {minutes}m"
    return f"Elapsed {minutes}m {seconds:02d}s" if minutes else f"Elapsed {seconds}s"


def build_rich_card(
    *,
    title: str,
    status: str,
    model: str = "",
    route: str = "",
    now: str = "",
    done: list[str] | None = None,
    next_step: str = "",
    blocker: str = "None",
    updated: str | None = None,
    started_at: str | None = None,
) -> str:
    """Render JAIMES Ops with the same native block hierarchy as Inbox."""
    done = done or []
    model_line = model or os.environ.get("JAIMES_WORK_CARD_MODEL") or "JAIMES"
    live_items = append_log(done, [now] if now else [])
    position = primary_stage_position(status, live_items)
    terminal = status in {"done", "failed", "paused"}
    percent = 100 if terminal else round((position / len(PRIMARY_LIVE_STAGES)) * 100)
    filled = max(0, min(10, round(percent / 10)))
    bar = "█" * filled + "░" * (10 - filled)
    phase = PRIMARY_LIVE_STAGES[position - 1]
    heading = {
        "running": "JAIMES · LIVE WORK",
        "done": "JAIMES · COMPLETE",
        "failed": "JAIMES · NEEDS ATTENTION",
        "paused": "JAIMES · PAUSED",
    }.get(status, f"JAIMES · {phase.upper()}")
    current_plain = current_step_text(status, now, live_items)
    current = semantic_progress_detail(now or current_plain) or current_plain

    stage_items: list[str] = []
    for index, label in enumerate(PRIMARY_LIVE_STAGES, start=1):
        checked = " checked" if terminal or index < position else ""
        active = index == position and not terminal
        label_html = f"<mark>{html.escape(label)}</mark>" if active else html.escape(label)
        stage_items.append(f'<li><input type="checkbox"{checked}>{label_html}</li>')

    workers = [
        "JAIMES owns Telegram delivery",
        f"Hermes runs {friendly_model_line(model_line)}",
    ]
    route_line = friendly_route_line(route)
    if route_line:
        workers.append(route_line)
    worker_html = "".join(f"<li>{html.escape(item)}</li>" for item in workers)

    activity: list[str] = []
    for item in live_items:
        value = compact(live_line(item), limit=140)
        if value and value not in activity:
            activity.append(value)
    activity = activity[-5:]
    activity_html = "".join(f"<li>{html.escape(item)}</li>" for item in activity)
    if not activity_html:
        activity_html = "<li>Waiting for the first verified update.</li>"

    issue = "" if is_empty_issue(blocker) else compact(clean_live_text(blocker), limit=180)
    next_items = [
        item for item in parse_list(next_step)
        if not GENERIC_NEXT_RE.match(clean_live_text(item))
    ]
    updated_at = parse_utc_timestamp(updated)
    updated_text = updated_at.astimezone().strftime("%H:%M %Z") if updated_at else now_label()
    blocks = [
        f"<h3>{html.escape(heading)}</h3>",
        f"<p><b>Objective</b><br>{html.escape(operator_objective(title))}</p>",
        f"<p><code>{html.escape(friendly_model_line(model_line))}</code> · JAIMES owns delivery</p>",
        f"<pre>{bar} {percent}% · stage {position}/{len(PRIMARY_LIVE_STAGES)}\n{html.escape(phase.lower())}</pre>",
        f"<blockquote><b>Now</b><br>{html.escape(current)}</blockquote>",
        f"<h4>Progress</h4><ul>{''.join(stage_items)}</ul>",
        f"<h4>Active work</h4><ul>{worker_html}</ul>",
    ]
    if issue:
        blocks.append(f"<blockquote><b>Needs attention</b><br>{html.escape(issue)}</blockquote>")
    if next_items:
        blocks.append(f"<p><b>Next</b><br>{html.escape(compact(next_items[0], limit=180))}</p>")
    blocks.extend([
        f"<details><summary>Recent activity ({len(activity)})</summary><ul>{activity_html}</ul></details>",
        f"<footer>{html.escape(primary_elapsed_text(started_at, updated))} · updated {html.escape(updated_text)}</footer>",
    ])
    return "".join(blocks)


def api_call(method: str, payload: dict, timeout: int = 15) -> dict:
    #JAIMES: live cards are persistent, edit-only records. Automatic cleanup
    # must never make a work card disappear; deletion requires an explicit
    # maintenance override tied to Josh's request.
    if method == "deleteMessage" and os.environ.get("JAIMES_ALLOW_EXPLICIT_CARD_DELETE") != "1":
        return {"ok": False, "error": "blocked by persistent live-card retention policy"}
    base = api_base()
    if not base or not telegram_target():
        return {"ok": False, "error": "JAIMES Telegram token or target chat is unavailable"}
    req = urllib.request.Request(
        f"{base}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        return {"ok": False, "error": f"{exc}: {body}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def delivery_indeterminate(result: dict) -> bool:
    """Return true when Telegram may have accepted a send without a receipt."""
    if result.get("delivery_indeterminate"):
        return True
    error = str(result.get("error") or result.get("description") or "").lower()
    definitive = any(marker in error for marker in (
        "http error 400", "http error 403", "http error 404", "bad request",
        "forbidden", "method not found", "unsupported", "too many requests", "429",
        "telegram token or target chat is unavailable",
    ))
    return bool(error) and not definitive


def rich_transport_definitively_rejected(result: dict) -> bool:
    error = str(result.get("error") or result.get("description") or result).lower()
    return any(marker in error for marker in (
        "http error 400", "http error 403", "http error 404", "bad request",
        "forbidden", "method not found", "unsupported",
    ))


def telegram_message_not_modified(result: dict) -> bool:
    return "message is not modified" in str(result.get("error") or result.get("description") or "").lower()


def send_card(
    text: str,
    buttons: list | None,
    timeout: int,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    payload = {
        "chat_id": chat_id or telegram_target(),
        "text": text,
        "disable_notification": True,
        "parse_mode": "HTML",
    }
    if thread_id not in {None, ""}:
        payload["message_thread_id"] = int(thread_id)
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return api_call("sendMessage", payload, timeout=timeout)


def send_rich_message(
    rich_html: str,
    fallback_text: str,
    buttons: list | None,
    timeout: int,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    payload: dict[str, object] = {
        "chat_id": chat_id or telegram_target(),
        "disable_notification": True,
        "rich_message": {"html": rich_html, "skip_entity_detection": True},
    }
    if thread_id not in {None, ""}:
        payload["message_thread_id"] = int(thread_id)
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    result = api_call("sendRichMessage", payload, timeout=timeout)
    if result.get("ok"):
        result["native_rich_message"] = True
        return result
    if not rich_transport_definitively_rejected(result):
        result["native_rich_message"] = False
        result["delivery_indeterminate"] = True
        return result
    fallback = send_card(
        fallback_text,
        buttons,
        timeout,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    fallback["native_rich_message"] = False
    fallback["rich_error"] = result.get("error") or result
    return fallback


def edit_card(
    message_id: int | str,
    text: str,
    buttons: list | None,
    timeout: int,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    payload = {
        "chat_id": chat_id or telegram_target(),
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if thread_id not in {None, ""}:
        payload["message_thread_id"] = int(thread_id)
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return api_call("editMessageText", payload, timeout=timeout)


def edit_rich_card(
    message_id: int | str,
    rich_html: str,
    fallback_text: str,
    buttons: list | None,
    timeout: int,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    payload: dict[str, object] = {
        "chat_id": chat_id or telegram_target(),
        "message_id": message_id,
        "rich_message": {"html": rich_html, "skip_entity_detection": True},
    }
    if thread_id not in {None, ""}:
        payload["message_thread_id"] = int(thread_id)
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    result = api_call("editMessageText", payload, timeout=timeout)
    if result.get("ok"):
        result["native_rich_message"] = True
        return result
    if telegram_message_not_modified(result):
        return {"ok": True, "result": {"message_id": message_id}, "native_rich_message": True}
    if not rich_transport_definitively_rejected(result):
        result["native_rich_message"] = True
        result["delivery_indeterminate"] = True
        return result
    fallback = edit_card(
        message_id,
        fallback_text,
        buttons,
        timeout,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    fallback["native_rich_message"] = False
    fallback["rich_error"] = result.get("error") or result
    return fallback


def send_final_summary(
    text: str,
    timeout: int,
    buttons: list | None = None,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    payload = {
        "chat_id": chat_id or telegram_target(),
        "text": text,
        "disable_notification": True,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id not in {None, ""}:
        payload["message_thread_id"] = int(thread_id)
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return api_call("sendMessage", payload, timeout=timeout)


def edit_final_summary(
    message_id: int | str,
    text: str,
    timeout: int,
    buttons: list | None = None,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    payload = {
        "chat_id": chat_id or telegram_target(),
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id not in {None, ""}:
        payload["message_thread_id"] = int(thread_id)
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return api_call("editMessageText", payload, timeout=timeout)


def edit_objective_message(
    message_id: int | str,
    title: str,
    model: str,
    timeout: int,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    model_line = model or os.environ.get("JAIMES_WORK_CARD_MODEL") or "JAIMES Telegram task card"
    steps, eta = estimate_initial_plan(title)
    compact_text = "\n".join([
        f"Model: {friendly_model_line(model_line)}",
        f"Objective: {operator_objective(title)}",
        f"Steps: {steps}",
        f"ETA: {eta}",
    ])
    payload = {
        "chat_id": chat_id or telegram_target(),
        "message_id": message_id,
        "text": f"<pre>{html.escape(compact_text)}</pre>",
        "parse_mode": "HTML",
    }
    if thread_id not in {None, ""}:
        payload["message_thread_id"] = int(thread_id)
    return api_call("editMessageText", payload, timeout=timeout)


def publish_brain_feed(
    args: argparse.Namespace,
    status: str,
    *,
    work_id: str = "",
    run_id: str = "",
) -> bool:
    if args.no_brain_feed and (args.dry_run or os.environ.get("ALLOW_NO_BRAIN_FEED") == "1"):
        return True
    if bool(work_id) != bool(run_id):
        # Never let the canonical publisher synthesize half of an identity;
        # a partial pair would split one Telegram task into two ledger rows.
        return False
    mapped = {
        "running": "active",
        "done": "done",
        "failed": "error",
        "paused": "info",
    }.get(status, "active")
    publish_args = [
        "--agent",
        "jaimes",
        "--type",
        "status",
        "--status",
        mapped,
        "--title",
        args.title or args.key,
        "--tool",
        "telegram work card",
        "--detail",
        compact(args.now or args.next or args.blocker or args.title or args.key, limit=260),
        "--privacy",
        "dashboard-safe",
        "--brain-feed",
    ]
    if work_id:
        publish_args += ["--work-id", work_id]
    if run_id:
        publish_args += ["--run-id", run_id]
    remote_command = "cd {} && {}".format(
        shlex.quote(CONTROL_TOWER_REMOTE_ROOT),
        shlex.join([
            CONTROL_TOWER_REMOTE_PYTHON,
            f"{CONTROL_TOWER_REMOTE_ROOT}/scripts/agent_publish.py",
            *publish_args,
        ]),
    )
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=4",
        CONTROL_TOWER_SSH_HOST,
        remote_command,
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=12,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def approval_buttons(args: argparse.Namespace) -> list | None:
    if is_empty_issue(args.blocker):
        return None
    steps = parse_list(args.next)
    if not steps:
        return None
    rows = []
    for index, step in enumerate(steps[:5], start=1):
        label = compact(step, limit=42)
        if label.lower().startswith("approve "):
            label = label[8:].strip()
        rows.append([{"text": f"Approve {index}: {label}", "callback_data": f"approve:{args.key}:{index}"}])
    return rows or None


def load_buttons(args: argparse.Namespace, status: str) -> list | None:
    if args.no_buttons:
        return None
    if args.buttons_file:
        return json.loads(Path(args.buttons_file).read_text(encoding="utf-8"))
    if args.buttons:
        return json.loads(args.buttons)
    if args.routing_buttons and status == "running":
        return DEFAULT_BUTTONS
    if args.approval_buttons and status in {"done", "failed"}:
        return approval_buttons(args)
    return None


def _upsert_card(args: argparse.Namespace, status: str) -> int:
    state = load_state()
    cards = state.setdefault("cards", {})
    action = str(getattr(args, "action", "start" if status == "running" else status))
    if args.key not in cards and action != "start":
        print(json.dumps({
            "ok": False,
            "action": "missing_live_card",
            "error": f"{action} cannot create a new Telegram card; start must own the first surface.",
        }, indent=2), file=sys.stderr)
        return 1
    existing = cards.get(args.key, {})
    title = args.title or existing.get("title") or args.key
    new_done = parse_list(args.done)
    previous_current = str(existing.get("current_step") or "").strip()
    incoming_current = str(args.now or "").strip()
    # A manual phase transition closes the prior concrete phase. Tool-result
    # updates already supply an explicit completion and must not double-count.
    if (
        status == "running"
        and incoming_current
        and previous_current
        and incoming_current != previous_current
        and not new_done
        and not incoming_current.lower().startswith(("still working", "waiting"))
        and not previous_current.lower().startswith(("task received", "still working", "waiting"))
    ):
        new_done = [previous_current]
    done = append_log(existing.get("work_log", existing.get("done", [])), new_done)
    planned_steps = int(existing.get("planned_steps") or estimate_initial_plan(title)[0])
    render_now = incoming_current
    if status == "done" and not render_now:
        render_now = (new_done[-1] if new_done else previous_current) or "Finished and verified the result"
    route = args.route or existing.get("route") or ""
    model = args.model or existing.get("model") or ""
    supplied_identity = {
        "work_id": str(getattr(args, "work_id", "") or ""),
        "run_id": str(getattr(args, "run_id", "") or ""),
        "task_started_at": str(getattr(args, "task_started_at", "") or ""),
    }
    for field, supplied in supplied_identity.items():
        recorded = str(existing.get(field) or "")
        if supplied and recorded and supplied != recorded:
            print(json.dumps({
                "ok": False,
                "action": "task_identity_conflict",
                "error": f"{field} is already bound to a different task identity.",
            }, indent=2), file=sys.stderr)
            return 1
    work_id = supplied_identity["work_id"] or str(existing.get("work_id") or "")
    run_id = supplied_identity["run_id"] or str(existing.get("run_id") or "")
    task_started_at = supplied_identity["task_started_at"] or str(
        existing.get("task_started_at") or ""
    )
    supplied_final_message_id = str(getattr(args, "final_message_id", "") or "")
    prior_final_message_id = str(existing.get("final_message_id") or "")
    if supplied_final_message_id and prior_final_message_id and supplied_final_message_id != prior_final_message_id:
        print(json.dumps({
            "ok": False,
            "action": "final_message_link_conflict",
            "error": "A different final Telegram message is already linked to this task.",
        }, indent=2), file=sys.stderr)
        return 1
    final_delivery_verified_by = str(
        getattr(args, "final_delivery_verified_by", "")
        or existing.get("final_delivery_verified_by")
        or ""
    )
    final_delivery_confirmed_at = str(existing.get("final_delivery_confirmed_at") or "")
    if supplied_final_message_id and final_delivery_verified_by and not final_delivery_confirmed_at:
        final_delivery_confirmed_at = (
            dt.datetime.now(dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    objective_message_id = args.ack_message_id or existing.get("ack_message_id")
    ack_message_id = "" if args.separate_message else objective_message_id
    chat_id = args.chat_id or existing.get("chat_id") or os.environ.get("TELEGRAM_TARGET_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    thread_id = args.thread_id or existing.get("thread_id") or os.environ.get("TELEGRAM_THREAD_ID")
    if not args.separate_message and not ack_message_id and status == "running" and title and title.lower() not in {"latest telegram task received", "determining objective"}:
        ack_message_id = claim_pending_ack(args.key, chat_id, thread_id)
        objective_message_id = ack_message_id or objective_message_id
    header_enabled = task_headers_enabled(chat_id, thread_id)
    header_text = build_task_header(title=title, model=model, route=route)
    text = build_card(
        title=title,
        status=status,
        model=model,
        route=route,
        now=render_now,
        done=done,
        next_step=args.next or "",
        blocker=args.blocker or "None",
        eta=args.eta or "",
        planned_steps=planned_steps,
    )
    rich_text = build_rich_card(
        title=title,
        status=status,
        model=model,
        route=route,
        now=render_now,
        done=done,
        next_step=args.next or "",
        blocker=args.blocker or "None",
        started_at=task_started_at or str(existing.get("task_started_at") or ""),
    )
    buttons = load_buttons(args, status)
    final_text = ""
    if status in {"done", "failed"} and args.final_summary and not args.no_final_summary:
        final_text = build_completion_summary(
            title=title,
            status=status,
            model=model,
            now=render_now,
            done=done,
            next_step=args.next or "",
            blocker=args.blocker or "None",
        )

    if args.dry_run:
        print(json.dumps({
            "ok": True,
            "dry_run": True,
            "task_header": header_enabled,
            "header_text": header_text,
            "text": text,
            "rich_text": rich_text,
            "renderer": "rich" if rich_cards_enabled(chat_id, thread_id) else "legacy",
            "final_text": final_text,
            "buttons": buttons,
            "existing": existing,
        }, indent=2))
        return 0

    card_buttons = buttons if status == "running" else None
    final_buttons = buttons if status in {"done", "failed"} else None
    strict_group_delivery = (
        str(chat_id or "") == CONTROL_CENTER_CHAT_ID
        and str(thread_id or "") in {INBOX_THREAD_ID, JAIMES_OPS_THREAD_ID}
    )
    renderer = str(existing.get("renderer") or "")

    def persist_checkpoint(
        *,
        header_id: int | str | None,
        live_id: int | str | None,
        final_id: int | str | None,
        extra: dict | None = None,
        clear_delivery: tuple[str, ...] = (),
    ) -> dict:
        record = {
            "title": title,
            "header_message_id": header_id,
            "message_id": live_id,
            "ack_message_id": objective_message_id,
            "final_message_id": final_id,
            "approval_message_id": existing.get("approval_message_id"),
            "status": status,
            "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "done": done,
            "work_log": done,
            "current_step": render_now,
            "planned_steps": planned_steps,
            "route": route,
            "model": model,
            "renderer": renderer,
            "next_step": args.next or existing.get("next_step") or "",
            "retention": "persistent-edit-only",
            "chat_id": chat_id,
            "thread_id": thread_id,
            "work_id": work_id,
            "run_id": run_id,
            "task_started_at": task_started_at,
            "final_delivery_verified_by": final_delivery_verified_by,
            "final_delivery_confirmed_at": final_delivery_confirmed_at,
        }
        for surface in ("header", "live", "final"):
            for suffix in ("delivery_status", "delivery_error_at"):
                field = f"{surface}_{suffix}"
                if field in existing:
                    record[field] = existing[field]
        for surface in clear_delivery:
            record.pop(f"{surface}_delivery_status", None)
            record.pop(f"{surface}_delivery_error_at", None)
        if extra:
            record.update(extra)
        cards[args.key] = record
        save_state(state)
        return record

    header_message_id = existing.get("header_message_id")
    header_action = None
    if status == "running" and not header_message_id and not existing.get("message_id") and header_enabled:
        if strict_group_delivery and existing.get("header_delivery_status") == "indeterminate":
            print(json.dumps({
                "ok": False,
                "action": "header_send_quarantined",
                "error": "Prior task-header send has an indeterminate Telegram receipt; automatic resend is blocked to prevent a duplicate.",
            }, indent=2), file=sys.stderr)
            return 1
        if objective_message_id:
            header_result = edit_card(
                objective_message_id,
                header_text,
                None,
                args.timeout,
                chat_id=chat_id,
                thread_id=thread_id,
            )
            header_action = "adopted"
            header_message_id = objective_message_id
        else:
            header_result = send_card(
                header_text,
                None,
                args.timeout,
                chat_id=chat_id,
                thread_id=thread_id,
            )
            header_action = "sent"
            header_message_id = (header_result.get("result") or {}).get("message_id")
        if (
            strict_group_delivery
            and header_action == "adopted"
            and not header_result.get("ok")
            and telegram_message_not_modified(header_result)
        ):
            header_result = {"ok": True, "result": {"message_id": header_message_id}}
        if not header_result.get("ok") or not header_message_id:
            if strict_group_delivery and header_action == "sent" and (
                delivery_indeterminate(header_result) or (header_result.get("ok") and not header_message_id)
            ):
                existing = persist_checkpoint(
                    header_id=None,
                    live_id=None,
                    final_id=existing.get("final_message_id"),
                    extra={
                        "header_delivery_status": "indeterminate",
                        "header_delivery_error_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    },
                )
            print(json.dumps({
                "ok": False,
                "action": f"header_{header_action}",
                "error": header_result.get("error") or "Telegram did not return a task-header message id",
            }, indent=2), file=sys.stderr)
            return 1
        # Checkpoint the immutable header before the editable live-card send.
        # A retry can then resume without duplicating the header.
        existing = persist_checkpoint(
            header_id=header_message_id,
            live_id=None,
            final_id=existing.get("final_message_id"),
            clear_delivery=("header",),
        )
    if header_enabled and header_message_id:
        # The header is its own durable receipt; never adopt or overwrite it as
        # the editable live work card.
        ack_message_id = ""

    if strict_group_delivery and existing.get("live_delivery_status") == "indeterminate" and not existing.get("message_id"):
        print(json.dumps({
            "ok": False,
            "action": "live_send_quarantined",
            "error": "Prior live-card send has an indeterminate Telegram receipt; automatic resend is blocked to prevent a duplicate.",
        }, indent=2), file=sys.stderr)
        return 1

    use_rich = renderer == "rich" or (
        not existing.get("message_id") and rich_cards_enabled(chat_id, thread_id)
    )
    if existing.get("message_id") and use_rich:
        result = edit_rich_card(
            existing["message_id"],
            rich_text,
            text,
            card_buttons,
            args.timeout,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        action = "edited"
    elif existing.get("message_id"):
        result = edit_card(existing["message_id"], text, card_buttons, args.timeout, chat_id=chat_id, thread_id=thread_id)
        action = "edited"
    elif ack_message_id and use_rich:
        result = edit_rich_card(
            ack_message_id,
            rich_text,
            text,
            card_buttons,
            args.timeout,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        action = "adopted"
    elif ack_message_id:
        #JAIMES: adopt the fresh acknowledgement as the one live card. Sending
        # another message here created the duplicate objective/work-card bubble.
        result = edit_card(ack_message_id, text, card_buttons, args.timeout, chat_id=chat_id, thread_id=thread_id)
        action = "adopted"
    elif use_rich:
        result = send_rich_message(
            rich_text,
            text,
            card_buttons,
            args.timeout,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        action = "sent"
    else:
        result = send_card(text, card_buttons, args.timeout, chat_id=chat_id, thread_id=thread_id)
        action = "sent"

    if (
        strict_group_delivery
        and action in {"edited", "adopted"}
        and not result.get("ok")
        and telegram_message_not_modified(result)
    ):
        result = {"ok": True, "result": {"message_id": existing.get("message_id") or ack_message_id}}
    if not result.get("ok"):
        if strict_group_delivery and action == "sent" and delivery_indeterminate(result):
            existing = persist_checkpoint(
                header_id=header_message_id,
                live_id=None,
                final_id=existing.get("final_message_id"),
                extra={
                    "live_delivery_status": "indeterminate",
                    "live_delivery_error_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                },
            )
        print(json.dumps({"ok": False, "action": action, "error": result.get("error") or result}, indent=2), file=sys.stderr)
        return 1

    message_id = existing.get("message_id")
    if action == "sent":
        message_id = result.get("result", {}).get("message_id")
    elif action == "adopted":
        message_id = ack_message_id
    if not message_id:
        if strict_group_delivery and action == "sent":
            existing = persist_checkpoint(
                header_id=header_message_id,
                live_id=None,
                final_id=existing.get("final_message_id"),
                extra={
                    "live_delivery_status": "indeterminate",
                    "live_delivery_error_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                },
            )
        print(json.dumps({
            "ok": False,
            "action": action,
            "error": "Telegram accepted the live card without returning a message id",
        }, indent=2), file=sys.stderr)
        return 1
    if use_rich:
        renderer = "rich" if result.get("native_rich_message") else "legacy"
    elif not renderer:
        renderer = "legacy"
    final_message_id = supplied_final_message_id or existing.get("final_message_id")
    existing = persist_checkpoint(
        header_id=header_message_id,
        live_id=message_id,
        final_id=final_message_id,
        clear_delivery=("live",),
    )
    final_action = None
    if final_text:
        if strict_group_delivery and not final_message_id and existing.get("final_delivery_status") == "indeterminate":
            print(json.dumps({
                "ok": False,
                "action": "final_send_quarantined",
                "error": "Prior final-summary send has an indeterminate Telegram receipt; automatic resend is blocked to prevent a duplicate.",
            }, indent=2), file=sys.stderr)
            return 1
        if final_message_id:
            final_result = edit_final_summary(final_message_id, final_text, args.timeout, chat_id=chat_id, thread_id=thread_id)
            final_action = "edited"
            if (
                strict_group_delivery
                and not final_result.get("ok")
                and telegram_message_not_modified(final_result)
            ):
                final_result = {"ok": True, "result": {"message_id": final_message_id}}
            if not final_result.get("ok") and not strict_group_delivery:
                final_result = send_final_summary(final_text, args.timeout, chat_id=chat_id, thread_id=thread_id)
                final_action = "sent"
        else:
            final_result = send_final_summary(final_text, args.timeout, chat_id=chat_id, thread_id=thread_id)
            final_action = "sent"
        if not final_result.get("ok"):
            if strict_group_delivery and final_action == "sent" and delivery_indeterminate(final_result):
                existing = persist_checkpoint(
                    header_id=header_message_id,
                    live_id=message_id,
                    final_id=None,
                    extra={
                        "final_delivery_status": "indeterminate",
                        "final_delivery_error_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    },
                )
            print(json.dumps({"ok": False, "action": final_action, "error": final_result.get("error") or final_result}, indent=2), file=sys.stderr)
            return 1
        if final_action == "sent":
            final_message_id = final_result.get("result", {}).get("message_id")
            if not final_message_id:
                if strict_group_delivery:
                    existing = persist_checkpoint(
                        header_id=header_message_id,
                        live_id=message_id,
                        final_id=None,
                        extra={
                            "final_delivery_status": "indeterminate",
                            "final_delivery_error_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        },
                    )
                print(json.dumps({
                    "ok": False,
                    "action": final_action,
                    "error": "Telegram accepted the final summary without returning a message id",
                }, indent=2), file=sys.stderr)
                return 1
        existing = persist_checkpoint(
            header_id=header_message_id,
            live_id=message_id,
            final_id=final_message_id,
            clear_delivery=("final",),
        )

    approval_message_id = existing.get("approval_message_id")
    if final_buttons and not approval_message_id:
        approval_result = send_card("Approval options:", final_buttons, args.timeout, chat_id=chat_id, thread_id=thread_id)
        if approval_result.get("ok"):
            approval_message_id = approval_result.get("result", {}).get("message_id")

    if args.separate_message and objective_message_id and not header_enabled and title and title.lower() not in {"latest telegram task received", "determining objective"}:
        edit_objective_message(objective_message_id, title, model, args.timeout, chat_id=chat_id, thread_id=thread_id)

    cards[args.key] = {
        "title": title,
        "header_message_id": header_message_id,
        "message_id": message_id,
        "ack_message_id": objective_message_id,
        "final_message_id": final_message_id,
        "approval_message_id": approval_message_id,
        "status": status,
        "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "done": done,
        "work_log": done,
        "current_step": render_now,
        "planned_steps": planned_steps,
        "route": route,
        "model": model,
        "renderer": renderer,
        "next_step": args.next or existing.get("next_step") or "",
        "retention": "persistent-edit-only",
        "chat_id": chat_id,
        "thread_id": thread_id,
        "work_id": work_id,
        "run_id": run_id,
        "task_started_at": task_started_at,
        "final_delivery_verified_by": final_delivery_verified_by,
        "final_delivery_confirmed_at": final_delivery_confirmed_at,
    }
    save_state(state)
    control_tower_published = bool(publish_brain_feed(
        args,
        status,
        work_id=work_id,
        run_id=run_id,
    ))
    output = {
        "ok": True,
        "header_action": header_action,
        "action": action,
        "final_action": final_action,
        "key": args.key,
        "header_message_id": header_message_id,
        "message_id": message_id,
        "final_message_id": final_message_id,
        "control_tower_published": control_tower_published,
    }
    if not control_tower_published:
        output["control_tower_warning"] = "Canonical Control Tower publication was not confirmed."
    print(json.dumps(output, indent=2))
    return 0


def upsert_card(args: argparse.Namespace, status: str) -> int:
    with state_lock():
        return _upsert_card(args, status)


def main() -> int:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """
            Send or edit a JAIMES-facing Telegram work card.
            Example:
              scripts/jaimes_work_card.py start --key mc-fix --title "Control Tower fix" --now "reading files"
              scripts/jaimes_work_card.py update --key mc-fix --now "running tests" --done "patched CSS|py_compile passed"
              scripts/jaimes_work_card.py done --key mc-fix --done "tests passed|pushed main"
              scripts/jaimes_work_card.py reconcile --dry-run
            """
        ),
    )
    parser.add_argument("action", choices=["start", "update", "done", "fail", "pause", "reconcile"])
    parser.add_argument("--key", help="Stable task key, e.g. sorare-lineup-check")
    parser.add_argument("--title", help="Human-readable task title")
    parser.add_argument("--model", help="Visible model/auth line")
    parser.add_argument("--route", help="Visible route line")
    parser.add_argument("--now", help="Current step")
    parser.add_argument("--done", help="Pipe-separated completed steps")
    parser.add_argument("--next", help="Next step")
    parser.add_argument("--blocker", default="None")
    parser.add_argument("--eta")
    parser.add_argument("--ack-message-id")
    parser.add_argument("--separate-message", action="store_true", help="Keep the objective bubble and send the work card as its own persistent message")
    parser.add_argument("--chat-id")
    parser.add_argument("--thread-id")
    parser.add_argument("--work-id", help="Canonical Control Tower work identifier")
    parser.add_argument("--run-id", help="Canonical Control Tower run identifier")
    parser.add_argument("--task-started-at", help="UTC timestamp of the originating Telegram task")
    parser.add_argument("--final-message-id", help="Telegram message ID confirmed by the Hermes adapter")
    parser.add_argument("--final-delivery-verified-by", help="Runtime component that confirmed final delivery")
    parser.add_argument("--buttons")
    parser.add_argument("--buttons-file")
    parser.add_argument("--routing-buttons", action="store_true", help="Show routing/model buttons on active cards only when steering is useful")
    parser.add_argument("--approval-buttons", action="store_true", help="Show approval buttons on the final summary when issues require approval")
    parser.add_argument("--no-buttons", action="store_true")
    parser.add_argument("--final-summary", action="store_true", help="Opt in to sending a separate final summary from the card helper")
    parser.add_argument("--no-final-summary", action="store_true", help="Deprecated default; card status updates no longer send separate final summaries")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=DEFAULT_RECONCILE_MAX_AGE_SECONDS,
        help="For reconcile only: retire unowned active records at least this old (default: 43200)",
    )
    parser.add_argument("--no-brain-feed", action="store_true", help="Skip Brain Feed only for dry-runs or ALLOW_NO_BRAIN_FEED=1 maintenance")
    args = parser.parse_args()

    if args.action == "reconcile":
        if args.max_age_seconds < 0:
            parser.error("--max-age-seconds must be zero or greater")
        try:
            result = reconcile_work_cards(max_age_seconds=args.max_age_seconds, dry_run=args.dry_run)
        except (OSError, RuntimeError, ValueError) as exc:
            print(json.dumps({
                "ok": False,
                "action": "reconcile",
                "error": str(exc),
                "telegram_messages_changed": False,
                "brain_feed_published": False,
            }, indent=2), file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0

    if not args.key:
        parser.error("--key is required for start, update, done, fail, and pause")
    if args.buttons and args.buttons_file:
        parser.error("Use either --buttons or --buttons-file, not both")
    if args.final_message_id and (
        not str(args.final_message_id).isdigit() or int(str(args.final_message_id)) <= 0
    ):
        parser.error("--final-message-id must be a positive Telegram message identifier")

    status = {
        "start": "running",
        "update": "running",
        "done": "done",
        "fail": "failed",
        "pause": "paused",
    }[args.action]
    return upsert_card(args, status)


if __name__ == "__main__":
    raise SystemExit(main())
