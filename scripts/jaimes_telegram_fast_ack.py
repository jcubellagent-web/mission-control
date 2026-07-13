#!/usr/bin/env python3
"""Send an immediate JAIMES Telegram acknowledgement for new direct-chat turns."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


HOME = Path.home()
WORKSPACE = HOME / ".openclaw" / "workspace"
SESSIONS_PATH = HOME / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
HERMES_SESSIONS_PATH = HOME / ".hermes" / "sessions" / "sessions.json"
SESSION_DIR = SESSIONS_PATH.parent
HERMES_SESSION_DIR = HERMES_SESSIONS_PATH.parent
HERMES_STATE_DB = HOME / ".hermes" / "state.db"
STATE_PATH = HOME / ".openclaw" / "telegram" / "jaimes_fast_ack_state.json"
DIRECT_SESSION_KEYS = (
    "agent:main:telegram:dm:6218150306",
    "agent:main:telegram:direct:6218150306",
)
CONTROL_CENTER_CHAT_ID = "-1003589561528"
JAIMES_CONTROL_CENTER_TOPICS = {"17", "19", "20", "56"}
TELEGRAM_GROUP_TOPIC_RE = re.compile(r"telegram:group:(-?\d+):(?:topic:)?(\d+)")
DEFAULT_MODEL = "openai-codex/gpt-5.6-sol"
DEFAULT_ROUTE = "JAIMES Telegram -> Hermes task"
STALE_BOOTSTRAP_SECONDS = 120
HEARTBEAT_SECONDS = 20
MAX_ACTIVE_CARD_SECONDS = 45 * 60
APPROVAL_ACTIONS_PATH = WORKSPACE / "memory" / "telegram_approval_actions.json"
X_INTELLIGENCE_QUEUE = WORKSPACE / "memory" / "x_intelligence_intake_queue.jsonl"
X_STATUS_URL_RE = re.compile(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_]{1,15}/status/\d+", re.I)
TELEGRAM_META_PATTERN = re.compile(r"Conversation info.*?```\s*\n\nSender .*?```\s*\n\n", re.S)

if str(WORKSPACE / "mission-control" / "scripts") not in sys.path:
    sys.path.insert(0, str(WORKSPACE / "mission-control" / "scripts"))

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
    """Persist the originating Telegram chat/topic into work-card state."""
    if not meta:
        return []
    chat_id = meta.get("telegram_chat_id") or meta.get("chat_id")
    thread_id = meta.get("telegram_thread_id") or meta.get("thread_id")
    args: list[str] = []
    if chat_id not in {None, ""}:
        args += ["--chat-id", str(chat_id)]
    if thread_id not in {None, ""}:
        args += ["--thread-id", str(thread_id)]
    return args


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


def record_api_result(state: dict[str, Any], method: str, result: dict[str, Any]) -> None:
    """Keep short, secret-free Telegram API evidence in watcher state."""
    row = {"at": utc_now(), "method": method, "ok": bool(result.get("ok"))}
    if not row["ok"]:
        row["error"] = str(result.get("description") or result.get("error") or "Telegram API call failed")[:320]
    history = list(state.get("telegram_api_results") or [])
    history.append(row)
    state["telegram_api_results"] = history[-40:]
    if not row["ok"]:
        state["last_telegram_api_error"] = row


def set_eyes_reaction(platform_message_id: str, state: dict[str, Any], meta: dict[str, Any] | None = None) -> bool:
    if work_card is None or not platform_message_id:
        return False
    payload = apply_telegram_target({
        "chat_id": telegram_target(meta),
        "message_id": int(platform_message_id),
        "reaction": [{"type": "emoji", "emoji": "👀"}],
        "is_big": False,
    }, meta)
    result = work_card.api_call("setMessageReaction", payload, timeout=6)
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


def edit_message(message_id: str, text: str, timeout: int = 15, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    if work_card is None or not message_id:
        return {"ok": False, "error": "missing editable acknowledgement or work-card helper"}
    payload = {
        "chat_id": telegram_target(meta),
        "message_id": message_id,
        "text": text,
        "disable_notification": True,
    }
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
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


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
            is_owned_group_topic = chat_id == CONTROL_CENTER_CHAT_ID and thread_id in JAIMES_CONTROL_CENTER_TOPICS
            if not is_direct and not is_owned_group_topic:
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
                   AND (
                        chat_id = '6218150306'
                        OR (chat_id = ? AND thread_id IN ('17','19','20','56'))
                   )
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


def final_assistant_message_after(session_id: str, user_message_id: int) -> str:
    """Return the final non-empty assistant text for one user turn, if stored."""
    if not HERMES_STATE_DB.exists():
        return ""
    con = sqlite3.connect(f"file:{HERMES_STATE_DB}?mode=ro", uri=True, timeout=2)
    try:
        next_user = con.execute(
            "SELECT MIN(id) FROM messages WHERE session_id = ? AND role = 'user' AND id > ?",
            (session_id, user_message_id),
        ).fetchone()
        upper_id = int(next_user[0]) if next_user and next_user[0] else 2**63 - 1
        row = con.execute(
            """
            SELECT content FROM messages
             WHERE session_id = ? AND role = 'assistant'
               AND id > ? AND id < ? AND TRIM(COALESCE(content, '')) != ''
             ORDER BY id DESC LIMIT 1
            """,
            (session_id, user_message_id, upper_id),
        ).fetchone()
        return str(row[0] or "") if row else ""
    finally:
        con.close()


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


def run_cmd(cmd: list[str], timeout: int = 20) -> dict[str, str | int | bool]:
    proc = subprocess.run(cmd, cwd=WORKSPACE, text=True, capture_output=True, timeout=timeout)
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


def publish_jaimes(title: str, status: str, detail: str) -> None:
    # Fast path first: update the physical Control Tower via JOSH 2.0 local
    # kiosk/SSE. Keep it non-blocking so Telegram ack latency is not held by SSH.
    bf_state = "idle" if status in {"done", "idle", "ok", "complete"} else "active"
    try:
        subprocess.Popen(
            [str(HOME / "scripts" / "jaimes_bf_push.sh"), title, bf_state, "JAIMES Telegram", detail[:260]],
            cwd=str(HOME),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass

    cmd = [
        "python3",
        "mission-control/scripts/agent_publish.py",
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
        "--brain-feed",
    ]
    try:
        subprocess.run(cmd, cwd=WORKSPACE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12, check=False)
    except Exception:
        return


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


def send_ack(event: dict[str, str], model: str, state: dict[str, Any], dry_run: bool = False, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    task_identity = event.get("platform_message_id") or event.get("db_message_id") or event["ts"].replace(":", "").replace(".", "-")
    key = f"jaimes-fast-ack-{(meta or {}).get('telegram_chat_id') or 'telegram'}-{task_identity}"
    if key in set(state.get("processed_task_keys") or []):
        #JAIMES: a replayed state-db row with the same stable task key must not
        # create a second acknowledgement or work-card lifecycle.
        return {
            "ok": True,
            "duplicate_suppressed": True,
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
    draft_id = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    # Hermes' message table currently omits platform_message_id. The active
    # Telegram session metadata carries the latest inbound Telegram ID, which is
    # safe here because the watcher cursor processes only fresh direct turns.
    inbound_message_id = event.get("platform_message_id") or str(((meta or {}).get("origin") or {}).get("message_id") or "")
    reaction_ok = False
    if not dry_run:
        reaction_ok = set_eyes_reaction(inbound_message_id, state, meta=meta)
    ack_result = {"ok": True, "result": {"message_id": "dry-run-message"}} if dry_run else send_initial_ack("👀 JAIMES — Received\n🤖 confirming model and objective", meta=meta)
    if not dry_run:
        record_api_result(state, "sendMessage", ack_result)
    ack_message_id = str(ack_result.get("result", {}).get("message_id") or "") if ack_result.get("ok") else ""
    if not dry_run and not ack_message_id:
        # Do not silently mark this event deduplicated when Telegram did not
        # confirm the durable acknowledgement.
        record_api_result(state, "sendMessage", {"ok": False, "error": "No message_id returned by initial acknowledgement"})
    if not dry_run and ack_message_id:
        # Fast acknowledgement must not wait for a route subprocess. This is
        # the active Hermes session model, not a claimed model switch.
        active_lane, active_reason = runtime_route(model)
        fast_edit = edit_message(
            ack_message_id,
            f"🤖 {model}\n\n👀 Objective\n{objective}\n\nRoute: {active_lane} | Why: {active_reason}",
            meta=meta,
        )
        record_api_result(state, "editMessageText", fast_edit)

    route = auto_route_for_prompt(prompt, model or DEFAULT_MODEL)
    skill = skill_for_prompt(prompt)
    # A router recommendation is not a model switch. Keep the visible model
    # sourced from the active Hermes session until a new lane actually starts.
    display_model = model or DEFAULT_MODEL
    active_lane, active_reason = runtime_route(display_model)
    display_route = f"{active_lane} | Why: {active_reason}"
    if skill.get("label"):
        display_route = f"{display_route}; runbook={skill['id']}"
    if not dry_run and ack_message_id:
        send_chat_action(meta=meta)
        send_message_draft(draft_id, f"Objective: {objective}\nModel: {display_model}", meta=meta)
        edit_result = edit_message(ack_message_id, f"🤖 {display_model}\n\n👀 Objective\n{objective}", meta=meta)
        record_api_result(state, "editMessageText", edit_result)
        # The originating chat/topic is the only valid work-card surface.
        # Never fall back to Josh's DM for a Control Center topic task.
        cards_flag = os.environ.get("JAIMES_TELEGRAM_LIVE_CARDS", "").lower()
        start_visible_card = should_start_visible_card(prompt, meta, cards_flag)
        if start_visible_card:
            run_cmd([
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
                "--ack-message-id",
                ack_message_id,
                "--separate-message",
                "--now",
                "Objective, model route, and runbook confirmed",
                "--done",
                f"Received Telegram task|Objective determined: {objective}|Model selected: {display_model}|Skill selected: {skill.get('label') or 'direct execution'}|Decision: Apply {skill.get('label') or 'direct verified execution'} while preserving one origin-scoped live card",
                "--next",
                "Work automatically; show buttons only for final approval steps if needed",
            ] + work_card_target_args(meta))
        publish_jaimes(objective, "active", f"Objective confirmed; {display_model}; skill={skill.get('label') or 'none'}")
    return {
        "ok": bool(dry_run or ack_message_id),
        "ack_message_id": ack_message_id,
        "key": key,
        "model": display_model,
        "route": display_route,
        "skill": skill,
        "objective": objective,
        "reaction_ok": reaction_ok,
        "button_triggered": is_button_prompt(prompt),
        "run_id": event.get("run_id") or "",
        "last_card_update_at": utc_now(),
        "telegram_chat_id": (meta or {}).get("telegram_chat_id"),
        "telegram_thread_id": (meta or {}).get("telegram_thread_id"),
        "retention": "persistent-edit-only",
    }


def complete_cards_from_final_responses(state: dict[str, Any], session_id: str, dry_run: bool = False) -> int:
    """Align the current live card to 100% once its final answer is stored."""
    completed = 0
    for run_id, card in (state.get("active_cards") or {}).items():
        if not isinstance(card, dict) or card.get("status") == "done":
            continue
        match = re.fullmatch(r"telegram-message-(\d+)", str(run_id))
        if not match or not final_assistant_message_after(session_id, int(match.group(1))):
            continue
        key = str(card.get("key") or "")
        if not key:
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
        result = {"ok": True, "dry_run": True} if dry_run else run_cmd(cmd)
        if result.get("ok"):
            card["status"] = "done"
            card["ended_at"] = utc_now()
            card["last_card_update_at"] = card["ended_at"]
            completed += 1
    return completed


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
    for event in recent_progress_events(session_id):
        event_id = event["event_id"]
        if event_id in processed:
            continue
        processed.add(event_id)
        card = active.get(event["run_id"])
        if not card or card.get("status") == "done":
            continue
        if card.get("session_id") and str(card.get("session_id")) != session_id:
            continue
        # Coalesce a burst of tool/model events into one visible edit. Replaying
        # every micro-event after rollover can time out the work-card helper and
        # makes Telegram look noisy rather than live.
        pending_by_run[event["run_id"]] = event
    for event in pending_by_run.values():
        event_id = event["event_id"]
        card = active.get(event["run_id"])
        if not card:
            continue
        objective = str(card.get("objective") or "JAIMES Telegram task")
        key = str(card.get("key") or "")
        if not key:
            continue
        if event["type"] == "model.completed":
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
                "Final response sent",
                "--blocker",
                "None",
            ] + work_card_target_args(card)
            result = {"ok": True, "dry_run": True} if dry_run else run_cmd(cmd)
            if not dry_run:
                publish_jaimes(objective, "done", "Final response sent in JAIMES Telegram.")
                if (
                    event.get("final_text")
                    and event_id not in approval_sent
                    and os.environ.get("JAIMES_TELEGRAM_SEPARATE_APPROVAL_BUTTONS", "0") == "1"
                ):
                    approval_message_id = send_approval_options(objective, event["final_text"], dry_run=dry_run, meta=card)
                    if approval_message_id:
                        approval_sent.add(event_id)
                        card["approval_message_id"] = approval_message_id
            card["status"] = "done"
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
            result = {"ok": True, "dry_run": True} if dry_run else run_cmd(cmd)
            if not dry_run:
                publish_jaimes(objective, "active", event["summary"])
            card["status"] = "active"
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
        started_raw = str(card.get("started_at") or card.get("last_progress_at") or last_raw or "")
        try:
            started = dt.datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
        except Exception:
            started = last
        if (now - started).total_seconds() > MAX_ACTIVE_CARD_SECONDS:
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
            result = {"ok": True, "dry_run": True} if dry_run else run_cmd(cmd)
            if not dry_run:
                publish_jaimes("JAIMES standing by", "done", summary)
            card["status"] = "done"
            card["ended_at"] = utc_now()
            card["last_card_update_at"] = card["ended_at"]
            updates.append({"event": f"expired:{run_id}:{card['ended_at']}", "result": result})
            continue
        if (now - last).total_seconds() < HEARTBEAT_SECONDS:
            continue
        # Keep the last concrete phase visible. A synthetic "waiting" heartbeat
        # made active work look stalled and polluted Completed/progress. Tool and
        # model events are the only sources allowed to move the visible card.
        card["heartbeat_checked_at"] = utc_now()
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


def poll_once(dry_run: bool = False) -> dict[str, Any]:
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
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
            state[cursor_key] = max(int(state.get(cursor_key) or 0), int(event.get("db_message_id") or 0))
            age = event_age_seconds(event["ts"])
            event_ts = dt.datetime.fromisoformat(event["ts"].replace("Z", "+00:00")).timestamp()
            replay_adjacent = any(abs(event_ts - marker_ts) <= 2.0 for marker_ts in replay_times)
            replay_duplicate = compaction_session and replayed_prompt_from_other_session(event)
            if internal_replay_prompt(event.get("prompt") or "") or replay_adjacent or replay_duplicate or (age is not None and age > STALE_BOOTSTRAP_SECONDS):
                acked.add(event_id)
                continue
            if event_id not in acked:
                candidates.append((event_ts, event, session_meta))

    # Preserve the existing anti-replay rule: if multiple genuine turns arrive
    # during one catch-up pass, only the newest creates visible Telegram UX.
    candidates.sort(key=lambda item: item[0])
    if candidates:
        _, newest_event, newest_meta = candidates[-1]
        attached_card = recent_active_card_for_meta(state, newest_meta) if media_only_prompt(newest_event.get("prompt") or "") else None
        if attached_card:
            attached_id = f"{newest_event['session_id']}:{newest_event['ts']}"
            acked.add(attached_id)
            attached_card.setdefault("attachment_message_ids", []).append(str(newest_event.get("db_message_id") or ""))
            state["multipart_rows_attached"] = int(state.get("multipart_rows_attached") or 0) + 1
            candidates.pop()
    selected = candidates[-1:] if candidates else []
    for _, stale_event, _ in candidates[:-1]:
        acked.add(f"{stale_event['session_id']}:{stale_event['ts']}")

    sent: list[dict[str, Any]] = []
    selected_meta = selected[0][2] if selected else metas[0]
    selected_session_id = str(selected_meta.get("sessionId") or "")
    selected_model = str(selected_meta.get("model") or DEFAULT_MODEL)
    events = [selected[0][1]] if selected else []
    if events:
        state["silently_retired_cards"] = int(state.get("silently_retired_cards") or 0) + retire_for_genuine_events(state, events)
    for event in events:
        event_id = f"{event['session_id']}:{event['ts']}"
        queued_x = 0 if dry_run else queue_forwarded_x_intelligence(event, selected_meta)
        result = send_ack(event, model=selected_model, state=state, dry_run=dry_run, meta=selected_meta)
        if queued_x:
            result["x_intelligence_queued"] = queued_x
        if result.get("ok"):
            acked.add(event_id)
            state.setdefault("processed_task_keys", []).append(str(result.get("key") or ""))
            state["processed_task_keys"] = sorted({k for k in state["processed_task_keys"] if k})[-300:]
            if result.get("run_id"):
                state["active_cards"][result["run_id"]] = {
                    "key": result.get("key"),
                    "objective": result.get("objective"),
                    "model": result.get("model"),
                    "route": result.get("route"),
                    "ack_message_id": result.get("ack_message_id"),
                    "telegram_chat_id": selected_meta.get("telegram_chat_id"),
                    "telegram_thread_id": selected_meta.get("telegram_thread_id"),
                    "session_id": selected_session_id,
                    "started_at": result.get("last_card_update_at"),
                    "last_progress_at": result.get("last_card_update_at"),
                    "last_card_update_at": result.get("last_card_update_at"),
                    "status": "active",
                    "retention": str(result.get("retention") or "persistent-edit-only"),
                }
            sent.append({"event": event_id, "result": result})
        else:
            sent.append({"event": event_id, "result": result})
            break

    state["acked_prompt_events"] = sorted(acked)[-300:]
    state["last_checked_at"] = utc_now()
    state["direct_session_id"] = selected_session_id
    state["owned_session_ids"] = session_ids
    state["model"] = selected_model
    state["status"] = "ok"
    state.pop("last_error", None)
    state.pop("last_error_at", None)
    if sent:
        state["last_sent_at"] = utc_now()
        state["last_result"] = sent[-1]["result"]
        state["latest_pending_ack"] = {
            "message_id": sent[-1]["result"].get("ack_message_id"),
            "key": sent[-1]["result"].get("key"),
            "event": sent[-1]["event"],
            "created_at": utc_now(),
            "model": selected_model,
        }
    else:
        state["last_result"] = {"ok": True, "status": "watching", "session_ids": session_ids}

    updates: list[dict[str, Any]] = []
    for sid in session_ids:
        state["cards_completed_from_final"] = int(state.get("cards_completed_from_final") or 0) + complete_cards_from_final_responses(
            state, sid, dry_run=dry_run
        )
        updates.extend(update_active_cards(state, sid, dry_run=dry_run))
    if not dry_run:
        save_json(STATE_PATH, state)
    return {"ok": True, "session_id": selected_session_id, "session_ids": session_ids, "sent": sent, "updates": updates, "dry_run": dry_run}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one poll and exit.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--interval", type=float, default=1.5)
    args = parser.parse_args()

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
            state["last_error"] = str(exc)
            save_json(STATE_PATH, state)
        time.sleep(max(0.5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
