#!/usr/bin/env python3
"""Create and update one editable Josh-facing Telegram work card.

This file is intended to be synced to Josh 2.0's workspace `scripts/` folder.
It uses the Bot API lane through `send_josh_reply.py`, which lives next to this
script on Josh 2.0.
"""
#JAIMES: topic-aware routing lives here so forum topics and JAIMES-managed chats can reuse the same work-card helper.
from __future__ import annotations

import argparse
import datetime as dt
from contextlib import contextmanager
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
SCRIPT_DIR = Path(__file__).resolve().parent
SESSIONS_PATH = Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
DIRECT_SESSION_KEY = "agent:main:telegram:direct:6218150306"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from send_josh_reply import API_BASE, TARGET, build_payload  # type: ignore
except Exception:  # noqa: BLE001 - dry-run and local validation can run without Josh helper
    API_BASE = ""
    TARGET = ""

    def build_payload(
        text: str,
        buttons: list | None,
        silent: bool = True,
        *,
        chat_id: str | int | None = None,
        thread_id: str | int | None = None,
    ) -> dict:
        payload = {"chat_id": chat_id or TARGET, "text": text, "disable_notification": silent}
        if thread_id not in {None, ""}:
            payload["message_thread_id"] = int(thread_id) if str(thread_id).isdigit() else thread_id
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return payload

STATE_PATH = Path(os.environ.get("JOSH_WORK_CARD_STATE", "memory/josh_work_cards.json"))
LOCK_PATH = STATE_PATH.with_suffix(STATE_PATH.suffix + ".lock")
ACK_STATE_PATH = Path(os.environ.get("JOSH_FAST_ACK_STATE", str(Path.home() / ".openclaw" / "telegram" / "fast_ack_state.json")))
TELEGRAM_COOLDOWN_PATH = Path(os.environ.get("JOSH_TELEGRAM_COOLDOWN_STATE", "memory/josh_telegram_cooldown.json"))
IMMUTABLE_TERMINAL_STATUSES = {"done", "failed", "paused"}
DEFAULT_BUTTONS = [
    [{"text": "1. Gemini review", "callback_data": "model:gemini_flash"}],
    [{"text": "2. JAIMES workhorse", "callback_data": "route:jaimes"}],
    [{"text": "Agent council", "callback_data": "route:agent_council"}],
    [{"text": "3. Run on Josh 2.0 device", "callback_data": "model:codex"}],
    [{"text": "4. JOSHeX Cloud / repo-safe", "callback_data": "route:joshex_cloud"}],
    [{"text": "5. JOSHeX private accounts", "callback_data": "route:joshex"}],
    [{"text": "Show model choices", "callback_data": "next:show_models"}],
    [{"text": "Hold / no action", "callback_data": "next:hold"}],
]
SECTION_SPACER = "⠀"
#JAIMES: every ecosystem live card uses the same Telegram <pre> geometry.
# Proportional-text spacing is not visually equivalent in fixed-width blocks.
CARD_WRAP_WIDTH = max(32, int(os.environ.get("JOSH_CARD_WRAP_WIDTH", "38")))
CARD_CONTINUATION_INDENT = "   "
CARD_BULLET_INDENT = "  "
CONTROL_CENTER_CHAT_ID = "-1003589561528"
INBOX_THREAD_ID = "1"
RICH_CARD_ENV = "JOSH_TELEGRAM_RICH_CARDS"
TASK_HEADER_ENV = "JOSH_TELEGRAM_TASK_HEADERS"
LIVE_STAGES = ("Accepted", "Planned", "Routed", "Working", "Verifying", "Delivered")
HEADER_LABEL_WIDTH = 9
HEADER_VALUE_WIDTH = CARD_WRAP_WIDTH - HEADER_LABEL_WIDTH - 4

# A final card is useful only when it carries the result, not merely the fact
# that a worker reached its terminal state.  Keep these checks local to the
# delivery boundary so a weak work-card close cannot consume Topic 1's single
# final-message slot before the native answer arrives.
SUMMARY_STATUS_FILLER_PATTERNS = (
    re.compile(
        r"^(?:the\s+)?(?:assessment|analysis|review|request|task|work|objective|"
        r"execution|result|summary|final(?:\s+(?:response|result))?|lifecycle|"
        r"delivery|card|format(?:ting)?)\s+(?:is\s+|was\s+|has\s+been\s+)?"
        r"(?:complete(?:d)?|done|finished|closed|prepared|delivered|verified|recovered)\.?$",
        re.I,
    ),
    re.compile(
        r"^(?:completed|closed\s+out|finished|verified|prepared|delivered)\s+"
        r"(?:the\s+)?(?:request|task|work|objective|execution|result|summary|"
        r"final(?:\s+(?:response|result))?|lifecycle|delivery|card|format(?:ting)?)\.?$",
        re.I,
    ),
    re.compile(r"^(?:agent|worker)\s+(?:work\s+)?reached\s+final\s+review\.?$", re.I),
    re.compile(r"^(?:live\s+)?card\s+(?:ordering|lifecycle)\s+(?:was\s+)?(?:preserved|closed)\.?$", re.I),
    re.compile(r"^(?:response\s+)?format(?:ting)?\s+(?:was\s+)?recovered\.?$", re.I),
)
SUMMARY_PROCESS_PREFIX = re.compile(
    r"^(?:review(?:ed|ing)?|check(?:ed|ing)?|analyz(?:ed|ing)|assess(?:ed|ing)?|"
    r"investigat(?:ed|ing)|research(?:ed|ing)?)\b",
    re.I,
)
SUMMARY_CONCRETE_RESULT = re.compile(
    r"\b(?:confirmed|found|identified|determined|show(?:s|ed)?|reveal(?:s|ed)?|"
    r"state(?:s|d)?|support(?:s|ed)?|cannot|can't|does\s+not|only|recommend(?:s|ed|ation)?|"
    r"avoid|do\s+not|risk|fixed|changed|added|removed|updated|implemented|configured|"
    r"differ(?:s|ed|ent)?|caus(?:e|es|ed)|repair(?:s|ed)?|(?:en|dis)abl(?:e|es|ed|ing)|"
    r"reconcil(?:e|es|ed)|retir(?:e|es|ed)|replac(?:e|es|ed)|rerout(?:e|es|ed)|"
    r"passed|failed|blocked|resolved|deployed|monitor(?:s|ed)?|trade(?:s|d)?|"
    r"credential|wallet|source)\b",
    re.I,
)
SUMMARY_PROCESS_RESULT = re.compile(
    r"\b(?:confirmed|found|identified|determined|show(?:s|ed)?|reveal(?:s|ed)?|"
    r"cannot|can't|does\s+not|only|recommend(?:s|ed|ation)?|fixed|changed|added|"
    r"removed|updated|implemented|differ(?:s|ed|ent)?|caus(?:e|es|ed)|repair(?:s|ed)?|"
    r"(?:en|dis)abl(?:e|es|ed|ing)|reconcil(?:e|es|ed)|retir(?:e|es|ed)|"
    r"replac(?:e|es|ed)|rerout(?:e|es|ed)|passed|failed|blocked|resolved|deployed)\b",
    re.I,
)
SUMMARY_RISK_OR_LIMITATION = re.compile(
    r"\b(?:risk|cannot|can't|does\s+not|unsupported|limitation|avoid|do\s+not|"
    r"blocked|failed|failure|unsafe|credential|wallet)\b",
    re.I,
)
SUMMARY_RECOMMENDATION_OR_ACTION = re.compile(
    r"\b(?:recommend(?:s|ed|ation)?|should|next\s+step|follow[- ]?up|retry|"
    r"avoid|do\s+not|use\s+.+\s+only|connect|install|approve)\b",
    re.I,
)
SUMMARY_NO_ACTION_SUPPORT = re.compile(
    r"\b(?:resolved|fixed|deployed|passed|healthy|fully\s+(?:complete|verified)|"
    r"no\s+(?:remaining|further)\s+(?:issue|work|action)|already\s+(?:configured|complete))\b",
    re.I,
)


def now_label() -> str:
    return dt.datetime.now().astimezone().strftime("%H:%M %Z")


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"cards": {}}
    except Exception:
        return {"cards": {}}


def load_json(path: Path, fallback: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else fallback
    except Exception:
        return fallback


def current_session_model(chat_id: str = "", thread_id: str = "") -> str:
    #JAIMES: resolve the exact group-topic session first; never borrow a direct-chat model for a visible card.
    sessions = load_json(SESSIONS_PATH, {})
    if not isinstance(sessions, dict):
        return ""
    normalized_chat = str(chat_id or "").removeprefix("telegram:")
    session_key = (
        f"agent:main:telegram:group:{normalized_chat}:topic:{thread_id}"
        if normalized_chat and thread_id
        else DIRECT_SESSION_KEY
    )
    session = sessions.get(session_key) or {}
    if not isinstance(session, dict):
        return ""
    provider = clean_live_text(str(session.get("modelProvider") or ""))
    model = clean_live_text(str(session.get("model") or ""))
    if provider and model:
        return f"{provider}/{model}"
    return model


def current_direct_session_model() -> str:
    return current_session_model()


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


@contextmanager
def ack_state_lock():
    """Use the same cross-process lock as josh_telegram_fast_ack.py."""
    lock_path = ACK_STATE_PATH.with_suffix(ACK_STATE_PATH.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def save_ack_state(data: dict) -> None:
    """Atomically replace ACK state without sharing a temporary filename."""
    ACK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=ACK_STATE_PATH.parent,
            prefix=f".{ACK_STATE_PATH.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, ACK_STATE_PATH)
        os.chmod(ACK_STATE_PATH, 0o600)
    finally:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)


def telegram_cooldown_active() -> dict | None:
    state = load_json(TELEGRAM_COOLDOWN_PATH, {})
    until = state.get("until")
    if not until:
        return None
    try:
        until_dt = dt.datetime.fromisoformat(str(until).replace("Z", "+00:00"))
        if until_dt > dt.datetime.now(dt.timezone.utc):
            return state
    except Exception:
        return None
    return None


def note_telegram_cooldown(method: str, body: str) -> None:
    retry_after = 0
    try:
        parsed = json.loads(body)
        retry_after = int((parsed.get("parameters") or {}).get("retry_after") or 0)
    except Exception:
        retry_after = 0
    if retry_after <= 0:
        return
    until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=retry_after)
    save_json(TELEGRAM_COOLDOWN_PATH, {
        "active": True,
        "method": method,
        "retry_after_seconds": retry_after,
        "until": until.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    })


def claim_pending_ack(card_key: str) -> str:
    #JAIMES: This file is shared with the fast-ack poller and claim hook. Merge
    # only the pending-ack receipt while holding their exact lock so a work-card
    # start cannot erase active cards, durable claims, or processed-event sets.
    with ack_state_lock():
        state = load_json(ACK_STATE_PATH, {})
        if not isinstance(state, dict):
            state = {}
        pending = state.get("latest_pending_ack")
        if not isinstance(pending, dict):
            return ""
        message_id = str(pending.get("message_id") or "")
        if not message_id or pending.get("claimed_by"):
            return ""
        state["latest_pending_ack"] = {
            **pending,
            "claimed_by": card_key,
            "claimed_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        save_ack_state(state)
        return message_id


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_PATH)
    os.chmod(STATE_PATH, 0o600)


def save_card_state(card_key: str, state: dict) -> None:
    """Merge one card under the short global state lock.

    Telegram I/O is serialized per card, not across every Inbox task. Only the
    atomic read/merge/replace touches the global lock, so a slow API call for
    one task cannot stall an unrelated burst while card records remain lossless.
    """
    record = (state.get("cards") or {}).get(card_key)
    if not isinstance(record, dict):
        return
    with state_lock():
        latest = load_state()
        latest.setdefault("cards", {})[card_key] = record
        save_state(latest)


@contextmanager
def card_lock(card_key: str):
    digest = hashlib.sha256(str(card_key).encode("utf-8")).hexdigest()[:24]
    lock_path = LOCK_PATH.with_name(f"{LOCK_PATH.name}.{digest}.card")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def state_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def parse_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def effect_protocol_lock_path(effect_path: Path) -> Path:
    text = str(effect_path)
    suffix = ".effects.json"
    return Path(text[:-len(suffix)] + ".protocol.lock") if text.endswith(suffix) else Path(text + ".protocol.lock")


@contextmanager
def effect_protocol_lock(effect_path: Path, timeout: float = 0.25):
    lock_path = effect_protocol_lock_path(effect_path)
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.mkdir(lock_path, 0o700)
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 2.0:
                    os.rmdir(lock_path)
                    continue
            except (FileNotFoundError, OSError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("Telegram effect lock unavailable")
            time.sleep(0.005)
    try:
        yield
    finally:
        try:
            os.rmdir(lock_path)
        except FileNotFoundError:
            pass


def update_effect_protocol(args: argparse.Namespace, state: str, stage: str, **values) -> bool:
    effect_value = str(getattr(args, "effect_path", "") or "")
    cancel_value = str(getattr(args, "cancel_path", "") or "")
    if not effect_value or not cancel_value:
        return True
    effect_path = Path(effect_value)
    cancel_path = Path(cancel_value)
    if state == "attempting":
        surface_deadline_ms = int(getattr(args, "surface_deadline_ms", 0) or 0)
        if surface_deadline_ms and int(time.time() * 1000) >= surface_deadline_ms:
            return False
    try:
        with effect_protocol_lock(effect_path):
            if state in {"attempting", "surface-started"} and cancel_path.exists():
                return False
            current = load_json(effect_path, {})
            if not isinstance(current, dict):
                current = {}
            payload = {
                **current,
                **values,
                "version": 1,
                "state": state,
                "stage": stage,
                "updated_at": utc_now(),
            }
            effect_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=effect_path.parent, prefix=f".{effect_path.name}.", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, effect_path)
        return True
    except TimeoutError:
        return False


def parse_timestamp(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def parse_route_facts(value: str) -> dict[str, str]:
    """Extract dashboard-safe routing facts from the coordinator's compact line."""
    facts: dict[str, str] = {}
    for raw in str(value or "").split(";"):
        if "=" not in raw:
            continue
        key, item = raw.split("=", 1)
        key = re.sub(r"^(?:planned|actual|verified)\s+", "", key.strip().lower())
        item = clean_live_text(item)
        if key and item:
            facts[key] = item
    return facts


def rich_cards_enabled(chat_id: str | int | None, thread_id: str | int | None) -> bool:
    """Default native Rich Messages on only for the owned Inbox topic."""
    raw = os.environ.get(RICH_CARD_ENV, "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return str(chat_id or "") == CONTROL_CENTER_CHAT_ID and str(thread_id or "") == INBOX_THREAD_ID


def is_inbox_topic(chat_id: str | int | None, thread_id: str | int | None) -> bool:
    normalized_chat = str(chat_id or "").removeprefix("telegram:")
    return normalized_chat == CONTROL_CENTER_CHAT_ID and str(thread_id or "") == INBOX_THREAD_ID


def task_headers_enabled(chat_id: str | int | None, thread_id: str | int | None) -> bool:
    """Default the persistent task header on only for the owned Inbox topic."""
    raw = os.environ.get(TASK_HEADER_ENV, "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return str(chat_id or "") == CONTROL_CENTER_CHAT_ID and str(thread_id or "") == INBOX_THREAD_ID


def human_worker_name(value: str) -> str:
    normalized = clean_live_text(value).lower()
    if not normalized:
        return ""
    known = {
        "josh2-codex-luna": "Codex Luna",
        "josh2-codex-terra": "Codex Terra",
        "josh2-codex-sol": "Codex Sol",
        "jaimes-hermes": "JAIMES / Hermes",
        "jaimes-grok-public": "JAIMES / Grok",
        "jaimes-gemini": "JAIMES / Gemini",
        "jain": "J.AI.N",
    }
    if normalized in known:
        return known[normalized]
    if normalized.startswith("josh2-"):
        return " ".join(part.title() for part in normalized.removeprefix("josh2-").split("-") if part)
    if normalized.startswith("jaimes-"):
        suffix = " ".join(part.title() for part in normalized.removeprefix("jaimes-").split("-") if part)
        return f"JAIMES / {suffix}" if suffix else "JAIMES"
    return compact(value.replace("_", " ").replace("-", " ").title(), limit=70)


def planned_agent_name(model: str, route: str) -> str:
    model_facts = parse_route_facts(model)
    route_facts = parse_route_facts(route)
    worker = clean_live_text(model_facts.get("worker") or route_facts.get("worker") or "").lower()
    if not worker or worker in {"unknown", "unknown-worker", "system"}:
        return "Josh 2.0 system"
    if worker.startswith("josh2-"):
        return "Josh 2.0 system"
    if worker.startswith("jaimes-") or worker == "jaimes":
        return "JAIMES"
    if worker.startswith("jain") or worker.startswith("j.a.i.n"):
        return "J.A.I.N"
    if worker.startswith("joshex"):
        return "JOSHeX"
    return human_worker_name(worker) or "Task system"


def planned_models(model: str, route: str) -> list[str]:
    """Return the selected model chain without pretending an unverified switch occurred."""
    results: list[str] = []
    for facts in (parse_route_facts(model), parse_route_facts(route)):
        raw_models = facts.get("models") or facts.get("model") or ""
        provider = clean_live_text(facts.get("provider") or "")
        for raw in re.split(r"\s*(?:,|\||→|->)\s*", raw_models):
            item = clean_live_text(raw)
            if not item:
                continue
            label = item if "/" in item or not provider else f"{provider}/{item}"
            if label not in results:
                results.append(label)
    if not results:
        fallback = friendly_model_line(model)
        if fallback and fallback.lower() not in {"unknown", "unverified"}:
            results.append(fallback)
    return results or ["Route-selected model"]


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


def fixed_width_lines(value: str, *, width: int, subsequent_indent: str = "   ") -> list[str]:
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


def display_pad(value: str, width: int) -> str:
    return f"{value}{' ' * max(0, width - display_width(value))}"


def task_header_row(label: str, value: str) -> list[str]:
    parts = fixed_width_lines(clean_live_text(value), width=HEADER_VALUE_WIDTH, subsequent_indent="")
    rows = []
    for index, part in enumerate(parts):
        row_label = compact(label, limit=HEADER_LABEL_WIDTH) if index == 0 else ""
        rows.append(f"│{display_pad(row_label, HEADER_LABEL_WIDTH)}│ {display_pad(part, HEADER_VALUE_WIDTH)}│")
    return rows


def build_task_header(*, title: str, model: str, route: str) -> str:
    """Build the persistent fixed-width receipt shown before the live card."""
    divider_top = f"┌{'─' * HEADER_LABEL_WIDTH}┬{'─' * (HEADER_VALUE_WIDTH + 1)}┐"
    divider_mid = f"├{'─' * HEADER_LABEL_WIDTH}┼{'─' * (HEADER_VALUE_WIDTH + 1)}┤"
    divider_bottom = f"└{'─' * HEADER_LABEL_WIDTH}┴{'─' * (HEADER_VALUE_WIDTH + 1)}┘"
    models = " → ".join(planned_models(model, route))
    lines = [
        "TASK HEADER",
        divider_top,
        *task_header_row("Objective", operator_objective(title)),
        divider_mid,
        *task_header_row("Owner", "Josh 2.0"),
        divider_mid,
        *task_header_row("Agent", planned_agent_name(model, route)),
        divider_mid,
        *task_header_row("Models", models),
        divider_bottom,
    ]
    return f"<pre>{html.escape(chr(10).join(lines))}</pre>"


def worker_visibility_lines(model: str, route: str, status: str) -> list[str]:
    model_facts = parse_route_facts(model)
    route_facts = parse_route_facts(route)
    owner_raw = route_facts.get("owner") or "josh2"
    owner = "Josh 2.0" if owner_raw.lower() in {"josh", "josh2", "josh 2.0"} else human_worker_name(owner_raw)
    planned = str(model or "").lstrip().lower().startswith("planned ") or str(route or "").lstrip().lower().startswith("planned ")
    state = "complete" if is_complete_status(status) else "needs attention" if status == "failed" else "planned" if planned else "active"
    lines = [f"{owner or 'Josh 2.0'} · owner/coordinator"]
    worker_raw = model_facts.get("worker") or route_facts.get("worker")
    worker = human_worker_name(worker_raw or "")
    if worker:
        provider = model_facts.get("provider") or route_facts.get("provider")
        model_name = model_facts.get("model") or route_facts.get("model")
        detail = f"{provider}/{model_name}" if provider and model_name else ""
        suffix = f" · {detail}" if detail and detail.lower() not in worker.lower() else ""
        lines.append(f"↳ {worker}{suffix} · {state}")
    return lines


def elapsed_text(started_at: str | None, updated_at: str | None = None) -> str:
    started = parse_timestamp(started_at)
    updated = parse_timestamp(updated_at) or dt.datetime.now(dt.timezone.utc)
    if not started:
        return "elapsed <1m"
    seconds = max(0, int((updated - started).total_seconds()))
    if seconds < 60:
        return f"elapsed {seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"elapsed {minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"elapsed {hours}h {minutes:02d}m"


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
        "running": "Working",
        "done": "Done",
        "failed": "Needs attention",
        "paused": "Paused",
    }
    return labels.get(status, status)


def status_headline(status: str) -> str:
    if status == "running":
        return "Josh 2.0 is working"
    if status == "done":
        return "Josh 2.0 is done"
    if status == "failed":
        return "Josh 2.0 needs attention"
    if status == "paused":
        return "Josh 2.0 is paused"
    return f"Josh 2.0 status: {status}"


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


def operator_objective(title: str) -> str:
    text = clean_live_text(title, "Handle the current Telegram task")
    lowered = text.lower()
    if lowered in {"latest telegram task received", "determining objective", "handle latest telegram task"}:
        return "Work out the real objective and start the right check."
    return compact(text, limit=150)


def resolve_auth_path(raw: str) -> str:
    normalized = (raw or "").strip().lower()
    facts = parse_route_facts(raw)
    provider = facts.get("provider", "").lower()
    if normalized.startswith("provider=codex;") or provider in {"codex", "openai", "openai-codex"}:
        return "subscription"
    if normalized.startswith("openai/") or normalized.startswith("openai-codex/"):
        return "subscription"
    if normalized.startswith("google-gemini-cli/"):
        return "subscription"
    if normalized.startswith("xai/"):
        return "oauth"
    return "auth-path"


def friendly_model_line(model: str) -> str:
    text = clean_live_text(model)
    lower = text.lower()
    if not text:
        return "Josh 2.0"

    route_match = re.search(r"provider=([^;]+);\s*model=([^;]+)", text, re.I)
    if route_match:
        provider = clean_live_text(route_match.group(1))
        model_name = clean_live_text(route_match.group(2))
        if provider and model_name:
            if "/" in model_name:
                return compact(model_name, limit=90)
            return compact(f"{provider}/{model_name}", limit=90)
    if "gemini" in lower and ("safe summary" in lower or "review" in lower):
        return "Josh 2.0, with a summary helper if needed"
    if "codex" in lower or "openclaw" in lower:
        return "Josh 2.0 / Codex"
    if "jaimes" in lower:
        return "JAIMES support lane"
    if "jain" in lower:
        return "J.AI.N worker support"
    
    if "/" in text:
        return compact(text, limit=90)
    session_model = current_direct_session_model()
    if session_model and text in {"gpt-5.4", "gpt-5.5", "gpt-5.4-mini", "gpt-5.3-codex-spark"}:
        return session_model.replace(f"/{text}", f"/{text}")
    return compact(text, limit=90)


def friendly_route_line(route: str) -> str:
    text = clean_live_text(route)
    lower = text.lower()
    if not text:
        return "Josh 2.0 Telegram task"
    facts = parse_route_facts(text)
    worker = human_worker_name(facts.get("worker", ""))
    route_id = facts.get("route") or facts.get("lane")
    reason = facts.get("reason") or facts.get("why")
    fallback = facts.get("fallback")
    if facts:
        target = worker or (route_id.title() if route_id else "selected worker")
        parts = [f"Josh 2.0 → {target}"]
        if reason:
            parts.append(reason)
        if fallback and fallback.lower() != "none":
            parts.append(f"fallback: {fallback}")
        return compact("; ".join(parts), limit=150)
    if "jaimes" in lower:
        return "Josh 2.0 coordinating with JAIMES"
    if "gemini" in lower:
        return "Josh 2.0 using a summary helper"
    if "joshex" in lower:
        return "JOSHeX support lane"
    if "auto:" in lower:
        return "Auto-routed to the safest available helper"
    return compact(text, limit=110)


def describe_shell_command(value: str) -> str:
    text = clean_live_text(value)
    text = re.sub(r"^/bin/(?:zsh|bash)\s+-lc\s+", "", text).strip()
    text = text.strip("'\"")
    text = re.sub(r"^cd\s+[^&;]+(?:&&|;)\s*", "", text).strip()
    text = text.strip("'\"")
    lower = text.lower()
    if "state_visibility_guard.py" in lower:
        return "refreshing Control Tower and Brain Feed visibility"
    if "update_mission_control.py" in lower:
        return "regenerating Control Tower dashboard data"
    if "agent_publish.py" in lower:
        return "publishing the latest status to Brain Feed"
    if "open_mission_control_kiosk" in lower:
        return "bringing Control Tower back onto the Josh 2.0 screen"
    if "openclaw update status" in lower:
        return "checking whether OpenCLAW has an update available"
    if "openclaw update" in lower:
        return "updating OpenCLAW and its installed plugins"
    if "openclaw doctor" in lower:
        return "checking OpenCLAW configuration for repairable issues"
    if "openclaw gateway status" in lower:
        return "checking that the OpenCLAW gateway is running"
    if "openclaw gateway" in lower:
        return "restarting or repairing the OpenCLAW gateway"
    if "openclaw health" in lower:
        return "checking Josh 2.0 health: auth, gateway, Telegram, and jobs"
    if "openclaw infer" in lower or "model run" in lower:
        return "testing that Josh 2.0 can reach the selected model"
    if "npm run build" in lower:
        return "building Control Tower to catch UI/runtime errors"
    if "npm run" in lower:
        return "running the Control Tower app command"
    if "python3" in lower and "mission-control/scripts/" in lower:
        script = lower.split("mission-control/scripts/", 1)[1].split()[0].strip("'\"")
        script = script.replace("_", " ").replace(".py", "")
        return f"running the Control Tower {script} helper"
    if lower.startswith("date "):
        return "checking the current time on Josh 2.0"
    if not text:
        return "checking the next needed system signal"
    return compact(text, limit=110)


def simplify_live_detail(value: str) -> str:
    text = clean_live_text(value)
    lower = text.lower()
    for prefix in ("completed checking ", "completed ", "checking ", "running "):
        if lower.startswith(prefix):
            text = text[len(prefix):].strip()
            lower = text.lower()
            break
    if any(marker in lower for marker in ("/bin/zsh", "/bin/bash", " -lc ")):
        return describe_shell_command(text)
    if lower.startswith(("cd ", "python3 ", "openclaw ", "npm ", "hermes ")):
        return describe_shell_command(text)
    text = text.replace("local check | checking", "local check: checking")
    text = text.replace("local check | completed checking", "local check: completed")
    text = text.replace("bash | checking", "local check: checking")
    text = text.replace("bash | completed", "local check: completed")
    if " | checking " in text:
        left, right = text.split(" | checking ", 1)
        return f"{clean_live_text(left)} | {describe_shell_command(right)}"
    if " | completed checking " in text:
        left, right = text.split(" | completed checking ", 1)
        return f"{clean_live_text(left)} | {describe_shell_command(right)}"
    if ":" in text:
        left, right = text.split(":", 1)
        label = clean_live_text(left).lower()
        if label in {"bash", "exec command"}:
            label = "local check"
        if label:
            right_text = re.sub(r"^(?:completed\s+checking|completed|checking)\s+", "", right.strip(), flags=re.I)
            if any(marker in right_text.lower() for marker in ("/bin/zsh", "/bin/bash", " -lc ")):
                right_text = describe_shell_command(right_text)
            return f"{label} | {compact(right_text, limit=120)}" if right_text else label
    if text.lower() in {"bash", "local check", "exec command"}:
        return "local check | system check completed"
    return compact(text, limit=150)


def live_line(item: str) -> str:
    text = clean_live_text(item)
    lower = text.lower()
    if not text:
        return f"- {html.escape('waiting: first update')}"
    if text.startswith(("🧭 ", "🧰 ", "⚙️ ", "🧠 ", "🧪 ", "✅ ", "🏁 ", "🔧 ", "⏳ ", "📝 ")):
        return html.escape(text)
    if lower.startswith("received"):
        return f"📥 received: {html.escape(text.removeprefix('Received').strip() or 'task')}"
    if lower.startswith("objective determined:"):
        return f"📌 objective: {html.escape(text.split(':', 1)[1].strip())}"
    if lower.startswith("model selected:"):
        return f"🤖 model: {html.escape(text.split(':', 1)[1].strip())}"
    if lower.startswith("skill selected:"):
        return f"🧭 skill: {html.escape(text.split(':', 1)[1].strip())}"
    if lower.startswith(("decision:", "decided:", "approved:", "choose ")):
        detail = text.split(":", 1)[1].strip() if ":" in text else text
        return f"🧠 decision: {html.escape(simplify_live_detail(detail))}"
    if lower.startswith(("local check | running", "local check | checking", "system check | running", "system check | checking")):
        return f"🔧 tool: {html.escape(simplify_live_detail(text))}"
    if lower.startswith(("local check | completed", "system check | completed")):
        return f"✅ done: {html.escape(simplify_live_detail(text))}"
    if lower.startswith(("running ", "checking ", "tool:")):
        detail = text.split(":", 1)[1].strip() if lower.startswith("tool:") else text
        return f"🔧 tool: {html.escape(simplify_live_detail(detail))}"
    if lower.startswith(("finished ", "completed checking ", "completed ", "done:")):
        detail = text.split(":", 1)[1].strip() if lower.startswith("done:") else text
        return f"✅ done: {html.escape(simplify_live_detail(detail))}"
    if lower.startswith("final response"):
        return "🏁 final: summary sent"
    if lower.startswith("still working"):
        return "⏳ working: waiting for the current model or tool step to finish"
    return f"📝 update: {html.escape(compact(text, limit=90))}"


def is_empty_issue(value: str | None) -> bool:
    text = " ".join((value or "").strip().lower().split())
    return text in {"", "none", "no", "n/a", "na", "not applicable"}


def hanging_lines(item: str, *, prefix: str = "", width: int = CARD_WRAP_WIDTH) -> list[str]:
    """Pre-wrap one fixed-width Telegram row with the ecosystem indent policy."""
    text = clean_live_text(item)
    first = f"{prefix}{text}"
    indent = CARD_BULLET_INDENT if prefix == "- " else (" " * len(prefix) if prefix else CARD_CONTINUATION_INDENT)
    return fixed_width_lines(first, width=width, subsequent_indent=indent)


def hanging_bullet_lines(item: str, *, width: int = CARD_WRAP_WIDTH) -> list[str]:
    return [html.escape(line) for line in hanging_lines(item, prefix="- ", width=width)]


def hanging_status_lines(item: str, *, width: int = CARD_WRAP_WIDTH) -> list[str]:
    return [html.escape(line) for line in hanging_lines(html.unescape(item), width=width)]


def bullet_lines(items: list[str], *, fallback: str = "n/a", limit: int = 5) -> list[str]:
    clean = []
    for item in items:
        text = compact(item, limit=170)
        if text and text not in clean:
            clean.append(text)
    if not clean:
        clean = [fallback]
    lines = []
    for item in clean[:limit]:
        lines.extend(hanging_bullet_lines(item))
    return lines


def numbered_lines(items: list[str], *, fallback: str = "n/a", limit: int = 7) -> list[str]:
    clean = []
    for item in items:
        text = clean_live_text(item)
        if text and text not in clean:
            clean.append(text)
    if not clean:
        clean = [fallback]

    lines = []
    for index, item in enumerate(clean[:limit], start=1):
        prefix = f"{index}. "
        lines.extend(html.escape(line) for line in hanging_lines(item, prefix=prefix))
    return lines


def plain_bullet_lines(items: list[str], *, fallback: str = "None", limit: int = 10) -> list[str]:
    clean = []
    for item in items:
        text = clean_live_text(item)
        if text and text not in clean:
            clean.append(text)
    if not clean:
        clean = [fallback]
    return [f"- {item}" for item in clean[:limit]]


def live_lines(items: list[str], *, fallback: str = "waiting: first update", limit: int = 12) -> list[str]:
    clean = []
    for item in items:
        text = live_line(item)
        if text and text not in clean:
            clean.append(text)
    if not clean:
        clean = [fallback]
    if len(clean) <= limit:
        return [line for item in clean for line in hanging_status_lines(item)]
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
    summary = f"Earlier: {', '.join(parts)} consolidated so the card stays readable."
    return [
        *hanging_status_lines(summary),
        *(line for item in clean[-limit:] for line in hanging_status_lines(item)),
    ]


COMPLETE_STATUSES = {"done", "complete", "completed", "final", "finished", "success"}
#JAIMES: terminal delivery completion is distinct from whether the requested objective succeeded.
TERMINAL_LIFECYCLE_STATUSES = {*COMPLETE_STATUSES, "failed", "failure", "error"}


def is_complete_status(status: str) -> bool:
    return str(status or "").strip().lower() in COMPLETE_STATUSES


def is_terminal_lifecycle_status(status: str) -> bool:
    """Return whether work and result delivery have reached their terminal phase.

    This is intentionally separate from objective success: a verified
    ``Complete: No`` outcome still finishes the six-stage delivery lifecycle
    and can remain visibly labelled as needing attention.
    """
    return str(status or "").strip().lower() in TERMINAL_LIFECYCLE_STATUSES


def milestone_count(items: list[str], status: str, *, route: str = "") -> int:
    """Return the current milestone position from events, never update volume."""
    if is_terminal_lifecycle_status(status):
        return len(LIVE_STAGES)
    if not items:
        return 0
    completed = 1  # accepted
    lowered = [clean_live_text(item).lower() for item in items]
    if any(any(token in item for token in ("objective", "runbook", "skill selected", "plan")) for item in lowered):
        completed = max(completed, 2)
    if parse_route_facts(route) or any(any(token in item for token in ("route", "delegat", "worker queued")) for item in lowered):
        completed = max(completed, 3)
    if any(any(token in item for token in ("worker started", "tool", "running", "checking", "execut")) for item in lowered):
        completed = max(completed, 4)
    if any(any(token in item for token in ("verified", "verification", "test passed", "canary", "formatting final")) for item in lowered):
        completed = max(completed, 5)
    return min(len(LIVE_STAGES) - 1, completed)


def stage_rows(items: list[str], status: str, *, route: str = "") -> list[str]:
    position = milestone_count(items, status, route=route)
    terminal = is_terminal_lifecycle_status(status)
    rows: list[str] = []
    for index, label in enumerate(LIVE_STAGES, start=1):
        if terminal or index < position:
            marker = "✓"
        elif index == position and not terminal:
            marker = "!" if status == "failed" else "▶"
        else:
            marker = "·"
        rows.append(f"{marker} {label}")
    return rows


def progress_phase(items: list[str], status: str, *, route: str = "") -> tuple[int, str]:
    position = milestone_count(items, status, route=route)
    if is_complete_status(status):
        return 100, "complete"
    if is_terminal_lifecycle_status(status):
        return 100, "delivery complete · needs attention"
    if not position:
        return 0, "waiting for first update"
    percent = round((position / len(LIVE_STAGES)) * 100)
    if status == "failed":
        return percent, "needs attention"
    if status == "paused":
        return percent, "paused"
    current_label = LIVE_STAGES[max(0, min(position - 1, len(LIVE_STAGES) - 1))].lower()
    return percent, f"{current_label} in progress"


def progress_lines(items: list[str], status: str, *, route: str = "") -> list[str]:
    clean = []
    for item in items:
        text = live_line(item)
        if text and text not in clean:
            clean.append(text)
    terminal_status = is_terminal_lifecycle_status(status)
    if not clean:
        if terminal_status:
            detail = "complete" if is_complete_status(status) else "delivery complete - needs attention"
            return [
                *hanging_status_lines(
                    f"██████████ 100% · stage {len(LIVE_STAGES)}/{len(LIVE_STAGES)} · {detail}"
                ),
                "",
            ]
        return ["░░░░░░░░░░ 0% complete - waiting for first update", ""]

    percent, detail = progress_phase(items, status, route=route)
    filled = max(0, min(10, round(percent / 10)))
    bar = "█" * filled + "░" * (10 - filled)
    position = milestone_count(items, status, route=route)
    return [*hanging_status_lines(f"{bar} {percent}% · stage {position}/{len(LIVE_STAGES)} · {detail}"), ""]


def current_step_text(status: str, now: str, live_items: list[str]) -> str:
    if now:
        return compact(simplify_live_detail(now), limit=150)
    if status == "done":
        return "Finished and verified the result."
    if status == "failed":
        return "Result delivered; objective needs attention."
    if status == "paused":
        return "Paused until the next instruction."
    if live_items:
        return compact(simplify_live_detail(live_items[-1]), limit=150)
    return default_current_step(status)


def status_chip(status: str) -> str:
    return {
        "running": "🟡 Working",
        "done": "🟢 Complete",
        "failed": "🔴 Needs attention",
        "paused": "⚪️ Waiting",
    }.get(status, f"⚪️ {status.title()}")


def latest_change_text(status: str, now: str, done: list[str]) -> str:
    if now:
        return compact(simplify_live_detail(now), limit=150)
    if done:
        return compact(simplify_live_detail(done[-1]), limit=150)
    if status == "done":
        return "Marked complete."
    return "Card created; no new update yet."


def unique_summary_items(items: list[str]) -> list[str]:
    """Return compact, de-duplicated summary items without changing their order."""
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = compact(html.unescape(str(item or "")), limit=180).strip(" -")
        marker = value.casefold()
        if value and marker not in seen:
            result.append(value)
            seen.add(marker)
    return result


def is_status_only_summary_item(item: str) -> bool:
    """Identify process/status prose that does not tell Josh what was learned."""
    value = compact(html.unescape(str(item or "")), limit=220)
    if not value:
        return True
    if any(pattern.fullmatch(value) for pattern in SUMMARY_STATUS_FILLER_PATTERNS):
        return True
    # "Reviewed the docs" is activity, not a finding.  A process-prefixed
    # sentence becomes useful only when it also states a concrete outcome.
    return bool(SUMMARY_PROCESS_PREFIX.search(value) and not SUMMARY_PROCESS_RESULT.search(value))


def substantive_summary_items(items: list[str]) -> list[str]:
    return [item for item in unique_summary_items(items) if not is_status_only_summary_item(item)]


def is_no_action_item(item: str) -> bool:
    normalized = " ".join(str(item or "").strip().lower().rstrip(".").split())
    return normalized in {"no action", "no action needed", "none", "n/a", "na", "not applicable"}


def summary_semantic_issues(
    *,
    complete_yes: bool,
    model: str,
    route: str,
    why: str = "",
    done_items: list[str],
    issue_items: list[str],
    next_items: list[str],
) -> list[str]:
    """Validate that a successful final contains findings rather than ceremony."""
    problems: list[str] = []
    done = unique_summary_items(done_items)
    substantive = substantive_summary_items(done)
    issues = [item for item in unique_summary_items(issue_items) if not is_empty_issue(item)]
    next_steps = unique_summary_items(next_items)
    combined_results = " ".join([*substantive, *next_steps])

    if complete_yes:
        if any("unverified" in str(value or "").lower() for value in (model, route, why)):
            problems.append("Complete: Yes requires verified Model, Route, and Why values")
        if not 3 <= len(done) <= 5:
            problems.append("Complete: Yes requires 3-5 What was done bullets")
        if len(substantive) < 3:
            problems.append("What was done must contain at least three substantive findings, outcomes, or changes")
        result_count = sum(bool(SUMMARY_CONCRETE_RESULT.search(item)) for item in substantive)
        if result_count < 2:
            problems.append("What was done must state at least two concrete results")

    has_risk_or_limitation = bool(SUMMARY_RISK_OR_LIMITATION.search(combined_results))
    if has_risk_or_limitation and not issues:
        problems.append("Risks and limitations must be surfaced under Issues")

    no_action = bool(next_steps) and all(is_no_action_item(item) for item in next_steps)
    if no_action:
        has_recommendation = bool(SUMMARY_RECOMMENDATION_OR_ACTION.search(" ".join(substantive)))
        has_explicit_support = bool(SUMMARY_NO_ACTION_SUPPORT.search(" ".join(substantive)))
        if (
            not complete_yes
            or issues
            or has_risk_or_limitation
            or has_recommendation
            or not has_explicit_support
        ):
            problems.append("No action needed is not supported by the reported result")
    return problems


def truthful_incomplete_steps(items: list[str]) -> list[str]:
    """Preserve real details, then meet the card shape without inventing success."""
    result = substantive_summary_items(items)[:5]
    for disclosure in (
        "Detailed findings were not captured in the work card.",
        "Missing facts were not inferred to fill the final summary.",
        "A substantive final response is still required.",
    ):
        if len(result) >= 3:
            break
        if disclosure not in result:
            result.append(disclosure)
    return result[:5]


def build_completion_summary(
    *,
    title: str,
    status: str,
    now: str = "",
    done: list[str] | None = None,
    next_step: str = "",
    blocker: str = "None",
    model: str = "",
    route: str = "",
) -> str:
    complete_title = compact(title, fallback="objective", limit=120)
    steps = unique_summary_items(list(done or []))
    if now and now not in steps:
        steps.append(now)
    issues = [] if is_empty_issue(blocker) else parse_list(blocker) or [blocker]
    next_steps = parse_list(next_step)
    model_line = model or os.environ.get("JOSH_WORK_CARD_MODEL") or "unverified"
    route_facts = parse_route_facts(route)
    route_label = route_facts.get("route") or route_facts.get("lane") or compact(route, fallback="unverified", limit=90)
    complete_requested = status == "done"
    why = route_facts.get("reason") or route_facts.get("why") or (
        "verified task execution" if status == "done" and "unverified" not in model_line.lower() and not model_line.lower().startswith("planned ")
        else "reported work-card outcome"
    )
    quality_issues = summary_semantic_issues(
        complete_yes=complete_requested,
        model=model_line,
        route=route_label,
        why=why,
        done_items=steps,
        issue_items=issues,
        next_items=next_steps or ["No action needed."],
    )
    complete = "Yes" if complete_requested and not quality_issues else "No"
    if complete == "No" and complete_requested:
        steps = truthful_incomplete_steps(steps)
        issues = unique_summary_items([
            *issues,
            "The work card did not capture enough concrete findings for a reliable final summary.",
        ])
        next_steps = ["Retry the final response with concrete findings and a supported recommendation."]
    else:
        steps = substantive_summary_items(steps) if complete == "Yes" else truthful_incomplete_steps(steps)
        if not next_steps:
            if issues:
                next_steps = ["Approve the next safe step for the issue."]
            elif status == "paused":
                next_steps = ["Send the next instruction when ready."]
            elif complete == "No":
                next_steps = ["Review the incomplete result and retry with concrete findings."]
            else:
                next_steps = ["No action needed."]
    if complete == "No" and next_steps and all(is_no_action_item(item) for item in next_steps):
        next_steps = ["Review the incomplete result and retry with concrete findings."]
    complete_detail = (
        f"{complete_title} complete"
        if complete == "Yes"
        else ("detailed findings are incomplete" if complete_requested else f"{complete_title} not complete")
    )
    approval_needed = [*next_steps, "Adjust the plan", "Cancel this task"] if issues else ["n/a"]
    def final_lines(items: list[str], fallback: str) -> list[str]:
        clean = [compact(item, limit=180) for item in items if compact(item, limit=180)]
        return [line for item in (clean[:5] or [fallback]) for line in hanging_bullet_lines(item)]

    lines = [
        *hanging_status_lines(f"Model: {friendly_model_line(model_line)} | Route: {route_label} | Why: {why}"),
        "",
        *hanging_status_lines(f"Complete: {complete} - {complete_detail}"),
        "",
        "What was done:",
        *final_lines(steps, "Detailed findings were not captured."),
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
    return f"<pre>{html.escape(html.unescape(chr(10).join(lines)))}</pre>"


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
    updated: str | None = None,
    started_at: str | None = None,
) -> str:
    done = done or []
    model_line = model or os.environ.get("JOSH_WORK_CARD_MODEL") or current_direct_session_model() or "unknown"
    live_items = append_log(done, [now] if now else [])
    
    card_title = {
        "running": "⏳ Live work - in progress",
        "done": "✅ Work complete",
        "failed": "⚠️ Work needs attention",
        "paused": "⏸️ Work paused",
    }.get(status, f"Live work status: {status}")
    
    #JAIMES: live cards use the stable six-section mobile layout inside a Telegram code block; progress detail belongs under one shared timeline.
    lines = [
        *hanging_status_lines(f"🤖 Model: {friendly_model_line(model_line)} ({resolve_auth_path(model_line)})"),
        *hanging_status_lines(f"🧭 Path: {friendly_route_line(route)}"),
        card_title,
        "📌 Objective:",
        *hanging_bullet_lines(operator_objective(title)),
        "",
        "⚡️ Current step:",
        *hanging_bullet_lines(current_step_text(status, now, live_items)),
        "",
        "📈 Progress:",
        *progress_lines(live_items, status, route=route),
        "🪜 Stages:",
        *(line for row in stage_rows(live_items, status, route=route) for line in hanging_status_lines(row)),
        "",
        "👥 Active work:",
        *(line for row in worker_visibility_lines(model_line, route, status) for line in hanging_bullet_lines(row)),
        "",
        "🕒 Timing:",
        *hanging_status_lines(f"{elapsed_text(started_at, updated)} · updated {now_label()}"),
        "",
        "🗂 Recent activity:",
        *live_lines(live_items, fallback="complete" if is_terminal_lifecycle_status(status) else "waiting: first update", limit=5),
    ]
    return f"<pre>{html.escape(html.unescape(chr(10).join(lines)))}</pre>"


def build_rich_card(
    *,
    title: str,
    status: str,
    model: str = "",
    route: str = "",
    now: str = "",
    done: list[str] | None = None,
    updated: str | None = None,
    started_at: str | None = None,
) -> str:
    """Render the Codex-style Inbox card with Telegram Rich Message blocks."""
    done = done or []
    model_line = model or os.environ.get("JOSH_WORK_CARD_MODEL") or current_direct_session_model() or "unknown"
    live_items = append_log(done, [now] if now else [])
    position = milestone_count(live_items, status, route=route)
    percent, detail = progress_phase(live_items, status, route=route)
    filled = max(0, min(10, round(percent / 10)))
    bar = "█" * filled + "░" * (10 - filled)
    heading = {
        "running": "JOSH 2.0 · LIVE WORK",
        "done": "JOSH 2.0 · COMPLETE",
        "failed": "JOSH 2.0 · NEEDS ATTENTION",
        "paused": "JOSH 2.0 · PAUSED",
    }.get(status, f"JOSH 2.0 · {status.upper()}")
    step = current_step_text(status, now, live_items)

    stage_items = []
    for index, label in enumerate(LIVE_STAGES, start=1):
        checked = " checked" if is_terminal_lifecycle_status(status) or index < position else ""
        active = index == position and not is_terminal_lifecycle_status(status)
        label_html = f"<mark>{html.escape(label)}</mark>" if active else html.escape(label)
        stage_items.append(f'<li><input type="checkbox"{checked}>{label_html}</li>')

    workers = "".join(f"<li>{html.escape(line)}</li>" for line in worker_visibility_lines(model_line, route, status))
    activity = []
    for item in live_items[-8:]:
        rendered = html.unescape(live_line(item))
        activity.append(f"<li>{html.escape(rendered)}</li>")
    activity_html = "".join(activity) or "<li>Waiting for the first verified update.</li>"
    updated_label = parse_timestamp(updated)
    updated_text = updated_label.astimezone().strftime("%H:%M %Z") if updated_label else now_label()

    return "".join([
        f"<h3>{html.escape(heading)}</h3>",
        f"<p><b>Objective</b><br>{html.escape(operator_objective(title))}</p>",
        f"<p><code>{html.escape(friendly_model_line(model_line))}</code> · Josh 2.0 owns delivery</p>",
        f"<pre>{bar} {percent}% · stage {position}/{len(LIVE_STAGES)}\n{html.escape(detail)}</pre>",
        f"<blockquote><b>Now</b><br>{html.escape(step)}</blockquote>",
        f"<h4>Progress</h4><ul>{''.join(stage_items)}</ul>",
        f"<h4>Active work</h4><ul>{workers}</ul>",
        f"<details><summary>Recent activity ({len(live_items)})</summary><ul>{activity_html}</ul></details>",
        f"<footer>{html.escape(elapsed_text(started_at, updated))} · updated {html.escape(updated_text)}</footer>",
    ])


def api_call(method: str, payload: dict, timeout: int = 15) -> dict:
    if not API_BASE:
        return {"ok": False, "error": "send_josh_reply.py helper is unavailable in this workspace"}
    cooldown = telegram_cooldown_active()
    if cooldown:
        return {"ok": False, "error": f"telegram rate limit active until {cooldown.get('until')}", "cooldown": cooldown}
    req = urllib.request.Request(
        f"{API_BASE}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        if exc.code == 429:
            note_telegram_cooldown(method, body)
        return {"ok": False, "error": f"{exc}: {body}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def send_card(
    text: str,
    buttons: list | None,
    timeout: int,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    payload = build_payload(text, buttons, silent=True, chat_id=chat_id, thread_id=thread_id)
    payload["parse_mode"] = "HTML"
    return api_call("sendMessage", payload, timeout=timeout)


def edit_card(
    message_id: int | str,
    text: str,
    buttons: list | None,
    timeout: int,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    payload = build_payload(text, buttons, silent=True, chat_id=chat_id, thread_id=thread_id)
    payload["message_id"] = message_id
    payload["parse_mode"] = "HTML"
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return api_call("editMessageText", payload, timeout=timeout)


def send_final_summary(
    text: str,
    timeout: int,
    buttons: list | None = None,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    payload = build_payload(text, buttons, silent=True, chat_id=chat_id, thread_id=thread_id)
    payload["parse_mode"] = "HTML"
    payload["disable_web_page_preview"] = True
    return api_call("sendMessage", payload, timeout=timeout)


def send_rich_message(
    rich_html: str,
    fallback_text: str,
    timeout: int,
    buttons: list | None = None,
    silent: bool = True,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    payload = build_payload("", buttons, silent=silent, chat_id=chat_id, thread_id=thread_id)
    payload.update({
        "rich_message": {
            "html": rich_html,
            "skip_entity_detection": True,
        },
    })
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    result = api_call("sendRichMessage", payload, timeout=timeout)
    if result.get("ok"):
        result["native_rich_message"] = True
        return result

    error_text = str(result.get("error") or result).lower()
    definitive_rejection = any(
        marker in error_text
        for marker in ("http error 400", "http error 404", "bad request", "method not found", "unsupported")
    )
    if not definitive_rejection:
        # A timeout/connection loss can happen after Telegram accepted the rich
        # message. Sending a second fallback in that ambiguous state would be a
        # duplicate; let the stable-key retry path decide instead.
        result["native_rich_message"] = False
        result["delivery_indeterminate"] = True
        return result

    fallback_payload = build_payload(fallback_text, buttons, silent=silent, chat_id=chat_id, thread_id=thread_id)
    fallback_payload["disable_web_page_preview"] = True
    if fallback_text.lstrip().startswith("<pre>"):
        fallback_payload["parse_mode"] = "HTML"
    fallback = api_call("sendMessage", fallback_payload, timeout=timeout)
    fallback["native_rich_message"] = False
    fallback["rich_error"] = result.get("error") or result
    return fallback


def delivery_indeterminate(result: dict) -> bool:
    if result.get("delivery_indeterminate"):
        return True
    error = str(result.get("error") or result.get("description") or "").lower()
    definitive = any(marker in error for marker in (
        "http error 400", "http error 403", "http error 404", "bad request",
        "forbidden", "method not found", "unsupported", "too many requests", "429",
    ))
    return bool(error) and not definitive


def edit_rich_card(
    message_id: int | str,
    rich_html: str,
    fallback_text: str,
    buttons: list | None,
    timeout: int,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    payload = build_payload("", buttons, silent=True, chat_id=chat_id, thread_id=thread_id)
    payload.pop("text", None)
    payload.pop("disable_notification", None)
    payload["message_id"] = message_id
    payload["rich_message"] = {"html": rich_html, "skip_entity_detection": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    result = api_call("editMessageText", payload, timeout=timeout)
    if result.get("ok"):
        result["native_rich_message"] = True
        return result
    if telegram_message_not_modified(result):
        return {"ok": True, "result": {"message_id": message_id}, "native_rich_message": True}

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


def edit_final_summary(
    message_id: int | str,
    text: str,
    timeout: int,
    buttons: list | None = None,
    chat_id: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict:
    payload = build_payload(text, buttons, silent=True, chat_id=chat_id, thread_id=thread_id)
    payload["message_id"] = message_id
    payload["parse_mode"] = "HTML"
    payload["disable_web_page_preview"] = True
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
    model_line = compact(model or "Auto route selecting best fit", limit=180)
    payload = build_payload(
        f"Objective: {compact(title, limit=180)}\nModel: {model_line}",
        None,
        silent=True,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    payload["message_id"] = message_id
    return api_call("editMessageText", payload, timeout=timeout)


def publish_brain_feed(args: argparse.Namespace, status: str) -> None:
    if args.no_brain_feed and (args.dry_run or os.environ.get("ALLOW_NO_BRAIN_FEED") == "1"):
        return
    mapped = {
        "running": "active",
        "done": "done",
        "failed": "error",
        "paused": "info",
    }.get(status, "active")
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_publish.py"),
        "--agent",
        "josh2",
        "--type",
        "status",
        "--status",
        mapped,
        "--title",
        args.title or args.key,
        "--tool",
        "telegram work card",
        "--detail",
        compact(args.now or args.next or args.blocker or args.title or args.key, 260),
        "--brain-feed",
    ]
    try:
        subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12, check=False)
    except Exception:
        return


def approval_buttons(args: argparse.Namespace) -> list | None:
    if is_empty_issue(args.blocker):
        return None
    steps = parse_list(args.next)
    if not steps:
        return None
    buttons = [
        {"text": f"Approve {index}", "callback_data": f"approve:{args.key}:{index}"}
        for index, _ in enumerate(steps[:5], start=1)
    ]
    buttons.extend([
        {"text": "Adjust plan", "callback_data": f"adjust:{args.key}"},
        {"text": "Cancel task", "callback_data": f"cancel:{args.key}"},
    ])
    #JAIMES: terminal controls have explicit labels and remain on the final card so selection never looks detached or ambiguous.
    return [buttons[index:index + 2] for index in range(0, len(buttons), 2)]


def load_buttons(args: argparse.Namespace, status: str) -> list | None:
    if args.no_buttons:
        return None
    if args.buttons_file:
        return json.loads(Path(args.buttons_file).read_text(encoding="utf-8"))
    if args.buttons:
        return json.loads(args.buttons)
    if args.routing_buttons and status == "running":
        return DEFAULT_BUTTONS
    if args.approval_buttons and status in {"done", "failed", "paused"}:
        return approval_buttons(args)
    return None


def telegram_message_not_modified(result: dict) -> bool:
    return "message is not modified" in str(result.get("error", "")).lower()


def final_section_items(lines: list[str], start: int, end: int, label: str) -> list[str]:
    """Reconstruct wrapped bullets (and legacy plain values) from one section."""
    values: list[str] = []
    inline = lines[start][len(label):].strip()
    if inline:
        values.append(inline)
    current = ""
    for line in lines[start + 1:end]:
        if line.startswith("- "):
            if current:
                values.append(current)
            current = line[2:].strip()
        elif line.startswith("  ") and current:
            current = f"{current} {line.strip()}".strip()
        elif line.strip():
            if current:
                values.append(current)
                current = ""
            values.append(line.strip())
    if current:
        values.append(current)
    return unique_summary_items(values)


def load_final_text_file(path: str) -> str:
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit("--final-text-file must not be empty")
    match = re.fullmatch(r"<pre>([\s\S]*)</pre>", text, flags=re.I)
    if not match:
        raise SystemExit("--final-text-file must use the canonical ordered final contract inside one <pre> block")
    plain = html.unescape(match.group(1)).strip("\n").replace("\r\n", "\n").replace("\r", "\n")
    lines = plain.splitlines()
    if not lines or any(len(line) > CARD_WRAP_WIDTH for line in lines):
        raise SystemExit("--final-text-file must pre-wrap every line to the canonical 38-column geometry")
    labels = ["Complete:", "What was done:", "Issues:", "Appropriate next steps:", "Approval needed:"]
    positions = [next((index for index, line in enumerate(lines) if line.startswith(label)), -1) for label in labels]
    complete_valid = bool(re.search(r"(?m)^Complete:\s+(?:Yes|No)\b", plain))
    ordered = all(position >= 0 for position in positions) and positions == sorted(positions) and len(set(positions)) == len(positions)
    header_lines = [line for line in lines[:positions[0]] if line.strip()] if ordered else []
    header = " ".join(line.strip() for line in header_lines)
    header_valid = bool(re.fullmatch(
        r"Model:\s*[^|]+?\s*\|\s*Route:\s*[^|]+?\s*\|\s*Why:\s*[^|]+",
        header,
        flags=re.I,
    ))
    header_wrap_valid = bool(header_lines) and all(line.startswith("   ") for line in header_lines[1:])
    done_lines = lines[positions[1] + 1:positions[2]] if ordered else []
    done_bullets = [line for line in done_lines if line.startswith("- ")]
    done_wrap_valid = all(line.startswith(("- ", "  ")) or not line.strip() for line in done_lines)
    required_values = []
    section_values: dict[str, list[str]] = {}
    if ordered:
        for index, label in enumerate(labels):
            end = positions[index + 1] if index + 1 < len(positions) else len(lines)
            values = final_section_items(lines, positions[index], end, label)
            section_values[label] = values
            required_values.append(" ".join(values).strip())
    if (
        not ordered
        or not complete_valid
        or not header_valid
        or not header_wrap_valid
        or not done_wrap_valid
        or not 3 <= len(done_bullets) <= 5
        or any(not value for value in required_values)
    ):
        raise SystemExit(
            "--final-text-file must use the canonical ordered final contract with verified Model/Route/Why, Complete: Yes/No, and 3-5 What was done bullets"
        )
    header_match = re.fullmatch(
        r"Model:\s*([^|]+?)\s*\|\s*Route:\s*([^|]+?)\s*\|\s*Why:\s*([^|]+)",
        header,
        flags=re.I,
    )
    assert header_match is not None  # guarded by header_valid above
    complete_yes = bool(re.search(r"(?m)^Complete:\s+Yes\b", plain, flags=re.I))
    semantic_issues = summary_semantic_issues(
        complete_yes=complete_yes,
        model=header_match.group(1),
        route=header_match.group(2),
        why=header_match.group(3),
        done_items=section_values.get("What was done:", []),
        issue_items=section_values.get("Issues:", []),
        next_items=section_values.get("Appropriate next steps:", []),
    )
    if semantic_issues:
        raise SystemExit("--final-text-file is not substantive: " + "; ".join(semantic_issues))
    return text


def upsert_card(args: argparse.Namespace, status: str) -> int:
    with card_lock(args.key):
        state = load_state()
        cards = state.setdefault("cards", {})
        existing = cards.get(args.key, {})
        existing_status = str(existing.get("status") or "")
        if status == "running" and existing_status in IMMUTABLE_TERMINAL_STATUSES:
            print(json.dumps({
                "ok": True,
                "action": "skipped",
                "reason": f"stale_running_after_{existing_status}",
                "key": args.key,
                "header_message_id": existing.get("header_message_id"),
                "message_id": existing.get("message_id"),
                "final_message_id": existing.get("final_message_id"),
            }, indent=2))
            return 0
        if (
            status in IMMUTABLE_TERMINAL_STATUSES
            and existing_status in IMMUTABLE_TERMINAL_STATUSES
            and args.no_final_summary
        ):
            # A pre-final gate and the trajectory watcher can observe the same
            # model completion concurrently. The first terminal edit wins;
            # later no-summary closures must never edit the card after the
            # native final has already been released.
            print(json.dumps({
                "ok": True,
                "action": "skipped",
                "reason": f"duplicate_terminal_after_{existing_status}",
                "key": args.key,
                "header_message_id": existing.get("header_message_id"),
                "message_id": existing.get("message_id"),
                "final_message_id": existing.get("final_message_id"),
            }, indent=2))
            return 0

        title = args.title or existing.get("title") or args.key
        new_done = parse_list(args.done)
        done = append_log(existing.get("work_log", existing.get("done", [])), new_done, [args.now] if args.now else [])
        route = args.route or existing.get("route") or ""
        model = args.model or existing.get("model") or ""
        ack_message_id = args.ack_message_id or existing.get("ack_message_id")
        chat_id = args.chat_id or existing.get("chat_id") or os.environ.get("JOSH_TELEGRAM_CHAT_ID")
        thread_id = args.thread_id or existing.get("thread_id") or os.environ.get("JOSH_TELEGRAM_THREAD_ID")
        model = model or current_session_model(str(chat_id or ""), str(thread_id or "")) or "unverified"
        started_at = str(existing.get("started_at") or utc_now())
        updated_at = utc_now()
        if not ack_message_id and status == "running" and title and title.lower() not in {"latest telegram task received", "determining objective"}:
            ack_message_id = claim_pending_ack(args.key)
        terminal_status = status in {"done", "failed", "paused"}
        #JAIMES: keep the live card visible through completion.  In Topic 1 the
        # native answer owns the single final slot unless a validated file is
        # supplied or a caller explicitly opts into a generated summary.
        text = build_card(
            title=title,
            status=status,
            model=model,
            route=route,
            now=args.now or "",
            done=done,
            next_step=args.next or "",
            blocker=args.blocker or "None",
            eta=args.eta or "",
            updated=updated_at,
            started_at=started_at,
        )
        rich_text = build_rich_card(
            title=title,
            status=status,
            model=model,
            route=route,
            now=args.now or "",
            done=done,
            updated=updated_at,
            started_at=started_at,
        )
        header_text = build_task_header(title=title, model=model, route=route)
        buttons = load_buttons(args, status)
        if terminal_status and args.final_text_file:
            final_text = load_final_text_file(args.final_text_file)
        else:
            generated_final_allowed = (
                terminal_status
                and not args.no_final_summary
                and (
                    not is_inbox_topic(chat_id, thread_id)
                    or bool(getattr(args, "separate_final_summary", False))
                )
            )
            final_text = build_completion_summary(
                title=title,
                status=status,
                now=args.now or "",
                done=done,
                next_step=args.next or "",
                blocker=args.blocker or "None",
                model=model,
                route=route,
            ) if generated_final_allowed else ""

        if args.dry_run:
            print(json.dumps({
                "ok": True,
                "dry_run": True,
                "renderer": "rich" if rich_cards_enabled(chat_id, thread_id) else "legacy",
                "task_header": task_headers_enabled(chat_id, thread_id),
                "header_text": header_text,
                "text": text,
                "rich_text": rich_text,
                "final_text": final_text,
                "buttons": buttons,
                "existing": existing,
            }, indent=2))
            return 0

        # Approval controls govern the outcome, so they belong only on the separate final summary card.
        card_buttons = buttons if not terminal_status else None

        def persist_checkpoint(
            *,
            live_message_id: int | str | None,
            final_id: int | str | None,
            active_renderer: str,
        ) -> dict:
            record = {
                "title": title,
                "header_message_id": header_message_id,
                "message_id": live_message_id,
                "ack_message_id": ack_message_id,
                "final_message_id": final_id,
                "approval_message_id": None,
                "status": status,
                "started_at": started_at,
                "updated_at": updated_at,
                "done": done,
                "work_log": done,
                "route": route,
                "model": model,
                "renderer": active_renderer,
                "chat_id": chat_id,
                "thread_id": thread_id,
                "next_step": args.next or existing.get("next_step") or "",
            }
            for surface in ("header", "live", "final"):
                for suffix in ("delivery_status", "delivery_error_at"):
                    field = f"{surface}_{suffix}"
                    if field in existing:
                        record[field] = existing[field]
            cards[args.key] = record
            save_card_state(args.key, state)
            return cards[args.key]

        header_message_id = existing.get("header_message_id")
        header_action = None
        if not header_message_id and not existing.get("message_id") and task_headers_enabled(chat_id, thread_id):
            if existing.get("header_delivery_status") == "indeterminate":
                print(json.dumps({
                    "ok": False,
                    "action": "header_send_quarantined",
                    "error": "Prior task-header send has an indeterminate Telegram receipt; automatic resend is blocked to prevent a duplicate.",
                }, indent=2), file=sys.stderr)
                return 1
            if not update_effect_protocol(
                args,
                "attempting",
                "task-header-send",
                resolve_by_ms=int(time.time() * 1000) + (args.timeout * 1000) + 250,
            ):
                print(json.dumps({"ok": False, "action": "cancelled_before_header_send"}), file=sys.stderr)
                return 1
            header_result = send_card(
                header_text,
                None,
                args.timeout,
                chat_id=chat_id,
                thread_id=thread_id,
            )
            if not header_result.get("ok"):
                if delivery_indeterminate(header_result):
                    update_effect_protocol(args, "indeterminate", "task-header-send")
                    existing = persist_checkpoint(
                        live_message_id=None,
                        final_id=existing.get("final_message_id"),
                        active_renderer=str(existing.get("renderer") or ""),
                    )
                    existing["header_delivery_status"] = "indeterminate"
                    existing["header_delivery_error_at"] = utc_now()
                    cards[args.key] = existing
                    save_card_state(args.key, state)
                else:
                    update_effect_protocol(args, "failed-before-surface", "task-header-send")
                print(json.dumps({
                    "ok": False,
                    "action": "send_header",
                    "error": header_result.get("error") or header_result,
                }, indent=2), file=sys.stderr)
                return 1
            header_message_id = (header_result.get("result") or {}).get("message_id")
            if not header_message_id:
                update_effect_protocol(args, "indeterminate", "task-header-send")
                existing = persist_checkpoint(
                    live_message_id=None,
                    final_id=existing.get("final_message_id"),
                    active_renderer=str(existing.get("renderer") or ""),
                )
                existing["header_delivery_status"] = "indeterminate"
                existing["header_delivery_error_at"] = utc_now()
                cards[args.key] = existing
                save_card_state(args.key, state)
                print(json.dumps({
                    "ok": False,
                    "action": "send_header",
                    "error": "Telegram accepted the header without returning a message id",
                }, indent=2), file=sys.stderr)
                return 1
            header_action = "sent"
            update_effect_protocol(
                args,
                "surface-started",
                "task-header-sent",
                header_message_id=str(header_message_id),
            )
            # Persist the receipt before sending the live card. A retry can then
            # continue without creating another immutable header.
            cards[args.key] = {
                "title": title,
                "header_message_id": header_message_id,
                "message_id": None,
                "ack_message_id": ack_message_id,
                "final_message_id": existing.get("final_message_id"),
                "approval_message_id": None,
                "status": status,
                "started_at": started_at,
                "updated_at": updated_at,
                "done": done,
                "work_log": done,
                "route": route,
                "model": model,
                "renderer": existing.get("renderer") or "",
                "chat_id": chat_id,
                "thread_id": thread_id,
                "next_step": args.next or existing.get("next_step") or "",
            }
            save_card_state(args.key, state)
            existing = cards[args.key]

        renderer = str(existing.get("renderer") or "")
        if existing.get("live_delivery_status") == "indeterminate" and not existing.get("message_id"):
            print(json.dumps({
                "ok": False,
                "action": "send_quarantined",
                "error": "Prior live-card send has an indeterminate Telegram receipt; automatic resend is blocked to prevent a duplicate.",
            }, indent=2), file=sys.stderr)
            return 1
        use_rich = renderer == "rich" or (not existing.get("message_id") and rich_cards_enabled(chat_id, thread_id))
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
        elif use_rich:
            if not update_effect_protocol(
                args,
                "attempting",
                "live-card-send",
                header_message_id=str(header_message_id or ""),
                resolve_by_ms=int(time.time() * 1000) + (args.timeout * 1000) + 250,
            ):
                print(json.dumps({"ok": False, "action": "cancelled_before_live_send"}), file=sys.stderr)
                return 1
            result = send_rich_message(
                rich_text,
                text,
                args.timeout,
                buttons=card_buttons,
                chat_id=chat_id,
                thread_id=thread_id,
            )
            action = "sent"
        else:
            if not update_effect_protocol(
                args,
                "attempting",
                "live-card-send",
                header_message_id=str(header_message_id or ""),
                resolve_by_ms=int(time.time() * 1000) + (args.timeout * 1000) + 250,
            ):
                print(json.dumps({"ok": False, "action": "cancelled_before_live_send"}), file=sys.stderr)
                return 1
            result = send_card(text, card_buttons, args.timeout, chat_id=chat_id, thread_id=thread_id)
            action = "sent"

        # A retry can encounter an already-updated live card after a later final-card send failed.
        # Treat Telegram's idempotent "not modified" response as success so the terminal flow can resume.
        if not result.get("ok") and telegram_message_not_modified(result):
            result = {"ok": True, "result": {"message_id": existing.get("message_id")}}
        if not result.get("ok"):
            if action == "sent" and delivery_indeterminate(result):
                update_effect_protocol(args, "indeterminate", "live-card-send", header_message_id=str(header_message_id or ""))
                existing = persist_checkpoint(
                    live_message_id=None,
                    final_id=existing.get("final_message_id"),
                    active_renderer=renderer,
                )
                existing["live_delivery_status"] = "indeterminate"
                existing["live_delivery_error_at"] = utc_now()
                cards[args.key] = existing
                save_card_state(args.key, state)
            elif action == "sent" and not header_message_id:
                update_effect_protocol(args, "failed-before-surface", "live-card-send")
            print(json.dumps({"ok": False, "action": action, "error": result.get("error") or result}, indent=2), file=sys.stderr)
            return 1

        message_id = existing.get("message_id")
        if action == "sent":
            message_id = result.get("result", {}).get("message_id")
            if not message_id:
                update_effect_protocol(args, "indeterminate", "live-card-send", header_message_id=str(header_message_id or ""))
                existing = persist_checkpoint(
                    live_message_id=None,
                    final_id=existing.get("final_message_id"),
                    active_renderer=renderer,
                )
                existing["live_delivery_status"] = "indeterminate"
                existing["live_delivery_error_at"] = utc_now()
                cards[args.key] = existing
                save_card_state(args.key, state)
                print(json.dumps({
                    "ok": False,
                    "action": action,
                    "error": "Telegram accepted the live card without returning a message id",
                }, indent=2), file=sys.stderr)
                return 1
            update_effect_protocol(
                args,
                "surface-started",
                "live-card-sent",
                header_message_id=str(header_message_id or ""),
                live_message_id=str(message_id),
            )
        if use_rich:
            renderer = "rich" if result.get("native_rich_message") else "legacy"
        elif not renderer:
            renderer = "legacy"

        final_message_id = existing.get("final_message_id")
        final_action = None
        # Persist the accepted live-card receipt before any final-card call.
        # A failed final delivery can then retry by editing this same live card.
        existing = persist_checkpoint(
            live_message_id=message_id,
            final_id=final_message_id,
            active_renderer=renderer,
        )

        if final_text:
            if not final_message_id and existing.get("final_delivery_status") == "indeterminate":
                print(json.dumps({
                    "ok": False,
                    "action": "final_send_quarantined",
                    "error": "Prior final send has an indeterminate Telegram receipt; automatic resend is blocked to prevent a duplicate.",
                }, indent=2), file=sys.stderr)
                return 1
            if final_message_id:
                final_result = edit_final_summary(final_message_id, final_text, args.timeout, buttons, chat_id=chat_id, thread_id=thread_id)
                final_action = "edited"
            else:
                final_result = send_final_summary(final_text, args.timeout, buttons, chat_id=chat_id, thread_id=thread_id)
                final_action = "sent"
            if not final_result.get("ok") and telegram_message_not_modified(final_result):
                final_result = {"ok": True, "result": {"message_id": final_message_id}}
            if not final_result.get("ok"):
                if final_action == "sent" and delivery_indeterminate(final_result):
                    existing["final_delivery_status"] = "indeterminate"
                    existing["final_delivery_error_at"] = utc_now()
                    cards[args.key] = existing
                    save_card_state(args.key, state)
                print(json.dumps({"ok": False, "action": final_action, "error": final_result.get("error") or final_result}, indent=2), file=sys.stderr)
                return 1
            if final_action == "sent":
                final_message_id = final_result.get("result", {}).get("message_id")
                if not final_message_id:
                    existing["final_delivery_status"] = "indeterminate"
                    existing["final_delivery_error_at"] = utc_now()
                    cards[args.key] = existing
                    save_card_state(args.key, state)
                    print(json.dumps({
                        "ok": False,
                        "action": final_action,
                        "error": "Telegram accepted the final summary without returning a message id",
                    }, indent=2), file=sys.stderr)
                    return 1
                # Save the final receipt immediately, before optional ack edits,
                # Brain Feed publishing, or response formatting can fail.
                existing = persist_checkpoint(
                    live_message_id=message_id,
                    final_id=final_message_id,
                    active_renderer=renderer,
                )

        approval_message_id = None

        if ack_message_id and title and title.lower() not in {"latest telegram task received", "determining objective"}:
            edit_objective_message(ack_message_id, title, model, args.timeout, chat_id=chat_id, thread_id=thread_id)

        cards[args.key] = {
            "title": title,
            "header_message_id": header_message_id,
            "message_id": message_id,
            "ack_message_id": ack_message_id,
            "final_message_id": final_message_id,
            "approval_message_id": approval_message_id,
            "status": status,
            "started_at": started_at,
            "updated_at": updated_at,
            "done": done,
            "work_log": done,
            "route": route,
            "model": model,
            "renderer": renderer,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "next_step": args.next or existing.get("next_step") or "",
        }
        save_card_state(args.key, state)
        publish_brain_feed(args, status)
        print(json.dumps({
            "ok": True,
            "header_action": header_action,
            "action": action,
            "final_action": final_action,
            "renderer": renderer,
            "key": args.key,
            "header_message_id": header_message_id,
            "message_id": message_id,
            "final_message_id": final_message_id,
        }, indent=2))
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """
            Send or edit a Josh-facing Telegram work card.
            Example:
              scripts/josh_work_card.py start --key mc-fix --title "Control Tower fix" --now "reading files"
              scripts/josh_work_card.py update --key mc-fix --now "running tests" --done "patched CSS|py_compile passed"
              scripts/josh_work_card.py done --key mc-fix --done "tests passed|pushed main"
              scripts/josh_work_card.py done --key mc-fix --final-text-file /private/path/final.html
            """
        ),
    )
    parser.add_argument("action", choices=["start", "update", "done", "fail", "pause"])
    parser.add_argument("--key", required=True, help="Stable task key, e.g. mission-control-polish")
    parser.add_argument("--title", help="Human-readable task title")
    parser.add_argument("--model", help="Visible model/auth line")
    parser.add_argument("--route", help="Visible route line")
    parser.add_argument("--now", help="Current step")
    parser.add_argument("--done", help="Pipe-separated completed steps")
    parser.add_argument("--next", help="Next step")
    parser.add_argument("--blocker", default="None")
    parser.add_argument("--eta")
    parser.add_argument("--ack-message-id")
    parser.add_argument("--buttons")
    parser.add_argument("--buttons-file")
    parser.add_argument("--routing-buttons", action="store_true", help="Show routing/model buttons on active cards only when steering is useful")
    parser.add_argument("--approval-buttons", action="store_true", help="Show approval buttons on the final summary when issues require approval")
    parser.add_argument("--no-buttons", action="store_true")
    #JAIMES: Topic 1 reserves its final slot for the validated native answer;
    # other topics keep the historical generated-summary default.
    parser.add_argument("--no-final-summary", action="store_true", help="Complete the live card without a generated final summary card")
    parser.add_argument("--final-text-file", help="Private file containing already-normalized Telegram HTML or escaped text")
    parser.add_argument("--separate-final-summary", action="store_true", help="Explicitly generate a separate final summary (Topic 1 normally waits for --final-text-file)")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--chat-id", help="Telegram chat id override for group or direct routing")
    parser.add_argument("--thread-id", help="Telegram forum topic id override for group-topic routing")
    parser.add_argument("--effect-path", default="", help=argparse.SUPPRESS)
    parser.add_argument("--cancel-path", default="", help=argparse.SUPPRESS)
    parser.add_argument("--surface-deadline-ms", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-brain-feed", action="store_true", help="Skip Brain Feed only for dry-runs or ALLOW_NO_BRAIN_FEED=1 maintenance")
    parser.add_argument("--status-button", action="store_true", help="Deprecated; buttons are attached by default")
    args = parser.parse_args()

    if args.buttons and args.buttons_file:
        parser.error("Use either --buttons or --buttons-file, not both")

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
