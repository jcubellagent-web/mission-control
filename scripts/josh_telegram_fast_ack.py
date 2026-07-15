#!/usr/bin/env python3
"""Handle Josh 2.0 Telegram direct-chat fast-ack/session state.

This watcher does not poll Telegram, so it does not compete with OpenCLAW's
Telegram channel. It watches OpenCLAW session metadata for the direct Josh chat
and can send a tiny acknowledgement when explicitly enabled. The real work card
starts after the objective is known.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

HOME = Path.home()
WORKSPACE = HOME / ".openclaw" / "workspace"
SESSIONS_PATH = HOME / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
SESSION_DIR = SESSIONS_PATH.parent
STATE_PATH = HOME / ".openclaw" / "telegram" / "fast_ack_state.json"
DIRECT_SESSION_KEY = "agent:main:telegram:direct:6218150306"
CONTROL_CENTER_CHAT_ID = "-1003589561528"
JOSH_CONTROL_CENTER_TOPICS = {"1", "18", "21", "22"}
TELEGRAM_GROUP_TOPIC_RE = re.compile(r"telegram:group:(-?\d+):(?:topic:)?(\d+)")
#JAIMES: JOSH 2.0 now advertises Gemini Flash as the front-line triage lane.
DEFAULT_MODEL = "Gemini Flash / local OpenCLAW session"
DEFAULT_ROUTE = "Josh 2.0 Telegram -> OpenCLAW task"
COORDINATOR_SCRIPT = WORKSPACE / "mission-control" / "scripts" / "inbox_coordinator.py"
#JAIMES: Fast-ack cards use the workspace copy, colocated with send_josh_reply.py and the real Telegram credentials path.
WORK_CARD_SCRIPT = WORKSPACE / "scripts" / "josh_work_card.py"
STALE_BOOTSTRAP_SECONDS = 120
MAX_UNACKED_PROMPT_AGE_SECONDS = 30
HEARTBEAT_SECONDS = 20
MAX_ACTIVE_CARD_SECONDS = 10 * 60
ORPHAN_WORK_CARD_GRACE_SECONDS = 10
TERMINAL_CARD_STATUSES = {"done", "failed", "paused"}
MAX_TERMINAL_CARD_RECORDS = 100
APPROVAL_ACTIONS_PATH = WORKSPACE / "memory" / "telegram_approval_actions.json"
WORK_CARD_STATE_PATH = WORKSPACE / "memory" / "josh_work_cards.json"
TELEGRAM_META_PATTERN = re.compile(r"Conversation info.*?```\s*\n\nSender .*?```\s*\n\n", re.S)
CONVERSATION_INFO_BLOCK_RE = re.compile(r"Conversation info \(untrusted metadata\):\s*```json\s*(\{.*?\})\s*```", re.S)
CARD_KEY_TS_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{6})")
CARD_KEY_SESSION_PATTERN = re.compile(r"^fast-ack-(.*)-\d{4}-\d{2}-\d{2}T\d{6}")
CURRENT_USER_REQUEST_PATTERN = re.compile(r"(?:^|\n)Current user request:\s*(.*?)\s*$", re.S)

if str(WORKSPACE / "scripts") not in sys.path:
    sys.path.insert(0, str(WORKSPACE / "scripts"))
if str(WORKSPACE / "mission-control" / "scripts") not in sys.path:
    sys.path.insert(0, str(WORKSPACE / "mission-control" / "scripts"))

try:
    from send_josh_reply import API_BASE, build_payload  # type: ignore
except Exception:  # noqa: BLE001
    API_BASE = ""
    build_payload = None

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


def normalize_telegram_chat_id(chat_id: Any) -> Any:
    text = str(chat_id or "").strip()
    if text.startswith("telegram:"):
        text = text.split(":", 1)[1].strip()
    return int(text) if text.lstrip("-").isdigit() else text


def apply_telegram_target(payload: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    if not meta:
        return payload
    chat_id = meta.get("telegram_chat_id")
    if chat_id:
        payload["chat_id"] = normalize_telegram_chat_id(chat_id)
    thread_id = meta.get("telegram_thread_id")
    if thread_id and str(thread_id).strip() != "1":
        payload["message_thread_id"] = int(thread_id) if str(thread_id).isdigit() else thread_id
    return payload


def target_chat_id(meta: dict[str, Any] | None = None) -> Any:
    if meta and meta.get("telegram_chat_id"):
        return normalize_telegram_chat_id(meta.get("telegram_chat_id"))
    if build_payload is None:
        return ""
    try:
        return build_payload("", None, silent=True).get("chat_id", "")
    except Exception:
        return ""


def api_post(method: str, payload: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    if not API_BASE:
        return {"ok": False, "error": "missing API base"}
    req = urllib.request.Request(
        f"{API_BASE}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def send_chat_action(action: str = "typing", meta: dict[str, Any] | None = None) -> None:
    chat_id = target_chat_id(meta)
    if not chat_id:
        return
    payload = apply_telegram_target({"chat_id": chat_id, "action": action}, meta)
    api_post("sendChatAction", payload, timeout=6)


def send_message_draft(draft_id: int, text: str = "", meta: dict[str, Any] | None = None) -> None:
    """Disabled by default: draft lane renders badly/overlaps in Telegram."""
    if os.environ.get("JOSH_TELEGRAM_DRAFTS", "").lower() not in {"1", "true", "yes"}:
        return
    chat_id = target_chat_id(meta)
    if not chat_id:
        return
    safe = " ".join((text or "").replace("\n", " · ").split())[:280]
    payload = apply_telegram_target({"chat_id": chat_id, "draft_id": draft_id, "text": safe}, meta)
    api_post("sendMessageDraft", payload, timeout=6)


def fast_ack_enabled() -> bool:
    raw = os.environ.get("JOSH_TELEGRAM_FAST_ACK", "").strip().lower()
    #JAIMES: default fast ack on for Josh Telegram so `👀` lands before normal task execution unless explicitly disabled.
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def live_cards_enabled(meta: dict[str, Any] | None = None) -> bool:
    """Default live cards on only for the owned group Inbox; allow explicit override."""
    raw = os.environ.get("JOSH_TELEGRAM_LIVE_CARDS", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool(
        meta
        and str(meta.get("telegram_chat_id") or "") == CONTROL_CENTER_CHAT_ID
        and str(meta.get("telegram_thread_id") or "") == "1"
    )


def send_initial_ack(text: str, timeout: int = 15, meta: dict[str, Any] | None = None) -> str:
    if not API_BASE or build_payload is None:
        return ""
    payload = apply_telegram_target(build_payload(text, None, silent=True), meta)
    req = urllib.request.Request(
        f"{API_BASE}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return str(data.get("result", {}).get("message_id") or "")
    except Exception:
        return ""


def prompt_conversation_info(prompt: str) -> dict[str, Any]:
    match = CONVERSATION_INFO_BLOCK_RE.search(prompt or "")
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(1))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def send_prompt_reaction(prompt: str, emoji: str = "👀", timeout: int = 10, meta: dict[str, Any] | None = None) -> bool:
    info = prompt_conversation_info(prompt)
    message_id = str(info.get("message_id") or "").strip()
    if not message_id:
        return False
    chat_id = str(info.get("chat_id") or meta.get("telegram_chat_id") if meta else info.get("chat_id") or "").strip()
    return send_message_reaction(message_id, chat_id=chat_id, emoji=emoji, timeout=timeout, meta=meta)


def send_message_reaction(message_id: str, chat_id: str = "", emoji: str = "👀", timeout: int = 10, meta: dict[str, Any] | None = None) -> bool:
    if not chat_id:
        chat_id = str(target_chat_id(meta) or "")
    if not chat_id or not message_id:
        return False
    payload = {"chat_id": normalize_telegram_chat_id(chat_id), "message_id": int(message_id) if message_id.isdigit() else message_id, "reaction": [{"type": "emoji", "emoji": emoji}]}
    if meta:
        payload = apply_telegram_target(payload, meta)
    result = api_post("setMessageReaction", payload, timeout=timeout)
    return bool(result.get("ok"))


def edit_message(message_id: str, text: str, timeout: int = 15, meta: dict[str, Any] | None = None) -> bool:
    if not message_id or not API_BASE or build_payload is None:
        return False
    payload = apply_telegram_target(build_payload(text, None, silent=True), meta)
    payload["message_id"] = message_id
    req = urllib.request.Request(
        f"{API_BASE}/editMessageText",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return bool(data.get("ok"))
    except Exception:
        return False


def delete_message(message_id: str, timeout: int = 15, meta: dict[str, Any] | None = None) -> bool:
    if not message_id:
        return False
    chat_id = target_chat_id(meta)
    if not chat_id:
        return False
    payload = apply_telegram_target({"chat_id": chat_id, "message_id": message_id}, meta)
    result = api_post("deleteMessage", payload, timeout=timeout)
    return bool(result.get("ok"))


def send_buttons_message(text: str, buttons: list, timeout: int = 15, meta: dict[str, Any] | None = None) -> str:
    if not API_BASE or build_payload is None:
        return ""
    payload = apply_telegram_target(build_payload(text, buttons, silent=True), meta)
    req = urllib.request.Request(
        f"{API_BASE}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return str(data.get("result", {}).get("message_id") or "")
    except Exception:
        return ""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # #JAIMES: use a unique temp file so concurrent pollers do not clobber the same ack-state write.
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            tmp = Path(handle.name)
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        if tmp and tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def local_time_label() -> str:
    return dt.datetime.now().astimezone().strftime("%H:%M:%S %Z")


def parse_utc(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def card_started_at(card: dict[str, Any]) -> dt.datetime | None:
    started = parse_utc(card.get("started_at"))
    if started:
        return started
    key = str(card.get("key") or "")
    match = CARD_KEY_TS_PATTERN.search(key)
    if not match:
        return None
    try:
        return dt.datetime.strptime("".join(match.groups()), "%Y-%m-%d%H%M%S").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def card_session_id(card: dict[str, Any]) -> str:
    explicit = str(card.get("session_id") or "")
    if explicit:
        return explicit
    key = str(card.get("key") or "")
    match = CARD_KEY_SESSION_PATTERN.search(key)
    return match.group(1) if match else ""


def session_metadata() -> dict[str, Any]:
    sessions = load_json(SESSIONS_PATH, {})
    if not isinstance(sessions, dict):
        return {}

    candidates: list[dict[str, Any]] = []
    for key, value in sessions.items():
        if not isinstance(value, dict):
            continue
        session_id = value.get("sessionId") or value.get("session_id")
        if not session_id:
            continue
        channel = value.get("channel") or value.get("platform") or value.get("origin", {}).get("platform")
        if channel != "telegram" and "telegram" not in key:
            continue
        target = parse_telegram_target_from_key(key)
        topic = str(target.get("telegram_thread_id") or "")
        is_direct = "telegram:direct:" in key or "telegram:dm:" in key
        if not is_direct and not (target.get("telegram_chat_id") == CONTROL_CENTER_CHAT_ID and topic in JOSH_CONTROL_CENTER_TOPICS):
            continue
        normalized = dict(value)
        normalized["sessionId"] = session_id
        normalized["channel"] = "telegram"
        normalized["model"] = value.get("model") or DEFAULT_MODEL
        normalized.update(target)
        try:
            normalized["_sort_updated_at"] = int(value.get("updatedAt") or 0)
        except Exception:
            normalized["_sort_updated_at"] = 0
        candidates.append(normalized)
    if candidates:
        return max(candidates, key=lambda item: item.get("_sort_updated_at") or 0)
    return {}


def session_paths_for(session_id: str, meta: dict[str, Any] | None = None) -> list[Path]:
    """Return possible trajectory paths for direct and Telegram-topic sessions."""
    candidates: list[Path] = [SESSION_DIR / f"{session_id}.trajectory.jsonl"]
    thread_id = str((meta or {}).get("telegram_thread_id") or "")
    if thread_id:
        candidates.append(SESSION_DIR / f"{session_id}-topic-{thread_id}.trajectory.jsonl")
    candidates.extend(
        sorted(
            SESSION_DIR.glob(f"{session_id}-topic-*.trajectory.jsonl"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
            reverse=True,
        )
    )
    seen: set[str] = set()
    paths: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def first_existing_session_path(session_id: str, meta: dict[str, Any] | None = None) -> Path:
    for path in session_paths_for(session_id, meta):
        if path.exists():
            return path
    return SESSION_DIR / f"{session_id}.trajectory.jsonl"


def recent_prompt_events(session_id: str, meta: dict[str, Any] | None = None) -> list[dict[str, str]]:
    path = first_existing_session_path(session_id, meta)
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


def friendly_tool_name(name: str) -> str:
    raw = (name or "").split(".")[-1].replace("_", " ").strip().lower()
    labels = {
        "exec command": "local check",
        "apply patch": "file edit",
        "parallel": "parallel checks",
        "tool search tool": "tool lookup",
    }
    return labels.get(raw, raw or "task step")


def safe_compact(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"(sk-[A-Za-z0-9_-]{8,}|xai-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]+|sb_secret_[A-Za-z0-9_-]+|sb_publishable_[A-Za-z0-9_-]+)", "[redacted]", text)
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def plain_command_summary(value: Any) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"^/bin/(?:zsh|bash)\s+-lc\s+", "", text).strip().strip("'\"")
    text = re.sub(r"^cd\s+[^&;]+(?:&&|;)\s*", "", text).strip().strip("'\"")
    lower = text.lower()
    if "state_visibility_guard.py" in lower:
        return "refreshing Control Tower and Brain Feed visibility"
    if "update_mission_control.py" in lower:
        return "regenerating Control Tower dashboard data"
    if "ecosystem_health_sweep.py" in lower:
        return "checking Josh 2.0, JAIMES, J.AI.N, and Control Tower health"
    if "xai_agent.py" in lower:
        return "checking the xAI/Grok helper connection"
    if lower.startswith("jq ") or " jq " in lower:
        return "reading the dashboard health summary"
    if lower.startswith("scp "):
        return "copying a needed helper script to the worker host"
    if "agent_publish.py" in lower:
        return "publishing the latest status to Brain Feed"
    if "open_mission_control_kiosk" in lower:
        return "bringing Control Tower back onto the Josh 2.0 screen"
    if "openclaw update status" in lower:
        return "checking whether OpenCLAW has an update available"
    if "openclaw update" in lower:
        return "updating OpenCLAW and installed plugins"
    if "openclaw doctor" in lower:
        return "checking OpenCLAW configuration for repairable issues"
    if "openclaw gateway status" in lower:
        return "checking that the OpenCLAW gateway is running"
    if "openclaw gateway" in lower:
        return "restarting or repairing the OpenCLAW gateway"
    if "openclaw health" in lower:
        return "checking Josh 2.0 auth, gateway, Telegram, jobs, and Control Tower"
    if "openclaw infer" in lower or "model run" in lower:
        return "testing that Josh 2.0 can reach the selected model"
    if "npm run build" in lower:
        return "building Control Tower to catch UI/runtime errors"
    if "python3" in lower and "mission-control/scripts/" in lower:
        script = lower.split("mission-control/scripts/", 1)[1].split()[0].strip("'\"")
        return f"running the {script.replace('_', ' ').replace('.py', '')} helper"
    if lower.startswith("date "):
        return "checking the current time on Josh 2.0"
    return safe_compact(text, 120) or "checking the next needed system signal"


def tool_arguments(data: dict[str, Any]) -> dict[str, Any]:
    args = data.get("arguments") or {}
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {"text": args}
    return args if isinstance(args, dict) else {}


def tool_call_detail(name: str, data: dict[str, Any]) -> str:
    tool = friendly_tool_name(name)
    args = tool_arguments(data)
    query = args.get("query") or args.get("q") or args.get("search")
    if query:
        return f"{tool}: searching {safe_compact(query, 90)} to verify context"
    pattern = args.get("pattern")
    if pattern:
        return f"{tool}: finding {safe_compact(pattern, 90)} in the current files/output"
    cmd = args.get("cmd") or args.get("command")
    if cmd:
        return f"{tool}: {plain_command_summary(cmd)}"
    path = args.get("path") or args.get("file") or args.get("ref_id")
    if path:
        return f"{tool}: inspecting {safe_compact(path, 90)}"
    prompt = args.get("prompt") or args.get("text")
    if prompt:
        return f"{tool}: evaluating {safe_compact(prompt, 90)}"
    return f"{tool}: checking the next relevant signal"


def content_preview(data: dict[str, Any]) -> str:
    items = data.get("contentItems") or data.get("content") or []
    if isinstance(items, list):
        texts = []
        for item in items[:2]:
            if isinstance(item, dict):
                texts.append(str(item.get("text") or item.get("content") or ""))
            else:
                texts.append(str(item))
        raw = " ".join(texts)
    else:
        raw = str(items)
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            raw = str(parsed.get("summary") or parsed.get("content") or parsed.get("result") or raw)
    except Exception:
        pass
    raw = re.sub(r"<<<EXTERNAL_UNTRUSTED_CONTENT.*?>>>", " ", raw)
    raw = re.sub(r"Source:\s*[^-]+---", " ", raw)
    return safe_compact(raw, 100)


def tool_result_detail(name: str, data: dict[str, Any], call_detail: str = "") -> str:
    tool = friendly_tool_name(name)
    if data.get("success") is False:
        return f"{tool}: internal check returned no actionable result"
    if call_detail and ":" in call_detail and tool in {"local check", "file edit"}:
        return f"{tool}: completed {call_detail.split(':', 1)[1].strip()}"
    preview = content_preview(data)
    if re.search(r"(?i)(?:tool.*failed|agent\)\s+failed|🛠️|⚠️.*failed|traceback|exit\s+\d+)", preview):
        return f"{tool}: internal check returned no actionable result"
    if "web search" in tool:
        return f"{tool}: found relevant web context" + (f": {preview}" if preview else "")
    if "memory search" in tool:
        return f"{tool}: found prior context" if preview else f"{tool}: checked memory; no key context found"
    if preview:
        return f"{tool}: {preview}"
    if call_detail and ":" in call_detail:
        return f"{tool}: completed {call_detail.split(':', 1)[1].strip()}"
    return f"{tool}: completed"


def recent_progress_events(session_id: str, meta: dict[str, Any] | None = None) -> list[dict[str, str]]:
    path = first_existing_session_path(session_id, meta)
    if not path.exists():
        return []
    events: list[dict[str, str]] = []
    call_details: dict[str, str] = {}
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 384_000))
            raw = handle.read().decode("utf-8", errors="ignore")
    except Exception:
        return []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except Exception:
            continue
        event_type = str(item.get("type") or "")
        if event_type not in {"tool.call", "tool.result", "model.completed"}:
            continue
        data = item.get("data") or {}
        name = str(data.get("name") or data.get("toolName") or event_type)
        friendly_name = friendly_tool_name(name)
        summary = ""
        if event_type == "tool.call":
            detail = tool_call_detail(name, data)
            call_details[str(data.get("toolCallId") or "")] = detail
            summary = f"Running {detail}"
        elif event_type == "tool.result":
            call_detail = call_details.get(str(data.get("toolCallId") or ""), "")
            summary = f"Finished {tool_result_detail(name, data, call_detail)}"
        elif event_type == "model.completed":
            summary = "Final response sent"
        final_text = ""
        if event_type == "model.completed":
            texts = data.get("assistantTexts") or data.get("assistant_texts") or []
            if isinstance(texts, list) and texts:
                final_text = str(texts[0] or "")
        events.append({
            "event_id": f"{item.get('runId') or ''}:{item.get('seq') or ''}:{event_type}",
            "run_id": str(item.get("runId") or ""),
            "ts": str(item.get("ts") or ""),
            "type": event_type,
            "summary": summary,
            "final_text": final_text,
        })
    return events


def is_ack_only_final(text: str) -> bool:
    """True when the model stopped after echoing only the helper-owned ack."""
    if (text or "").strip() == "👀":
        return True
    normalized = re.sub(r"[^a-z]+", " ", (text or "").lower()).strip()
    return normalized in {
        "recieved determining objective",
        "received determining objective",
    }


def mitigation_steps_from_text(text: str) -> list[str]:
    if ux_final_action_steps is not None:
        return ux_final_action_steps(text)[1]
    if not text:
        return []
    match = re.search(r"(?im)^\s*(?:\*\*)?(?:Approval needed|Mitigation steps for approval):?(?:\*\*)?\s*$", text)
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


def approval_callback(objective: str, step: str, index: int) -> str:
    digest = hashlib.sha1(f"josh2|{objective}|{step}|{index}".encode("utf-8")).hexdigest()[:10]
    return f"approve:josh2:{digest}:{index}"


def save_approval_actions(actions: dict[str, Any]) -> None:
    existing = load_json(APPROVAL_ACTIONS_PATH, {})
    if not isinstance(existing, dict):
        existing = {}
    existing.update(actions)
    save_json(APPROVAL_ACTIONS_PATH, existing)


def approval_button_label(step: str) -> str:
    label = clean_approval_step(step)
    label = re.sub(r"(?i)^(optional:\s*)", "", label).strip()
    label = re.sub(r"(?i)^(approve|approval to|approval for)\s+", "", label).strip()
    label = label.rstrip(".")
    label = label[:38] + ("..." if len(label) > 38 else "")
    return f"Approve: {label or 'next action'}"


def send_approval_options(objective: str, final_text: str, dry_run: bool = False) -> str:
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
    if ux_steps_are_all_applicable is not None and ux_steps_are_all_applicable(mode, steps, final_text):
        all_step = ux_approve_all_step(steps) if ux_approve_all_step is not None else "Run all listed steps"
        callback = approval_callback(objective, all_step, 0)
        actions[callback] = {
            "agent": "josh2",
            "objective": objective,
            "step": all_step,
            "created_at": utc_now(),
        }
        buttons.append([{"text": "Approve all", "callback_data": callback}])
    prefix = "Approve" if mode == "approval" else "Next"
    for index, step in enumerate(steps[:4], start=1):
        callback = approval_callback(objective, step, index)
        actions[callback] = {
            "agent": "josh2",
            "objective": objective,
            "step": step,
            "created_at": utc_now(),
        }
        if ux_button_label is not None:
            label = ux_button_label(step, prefix=prefix, limit=46)
        else:
            label = approval_button_label(step)
        buttons.append([{"text": label, "callback_data": callback}])
    buttons.append([{"text": "Hold / no action", "callback_data": "next:hold"}])
    if dry_run:
        return "dry-run-approval-buttons"
    save_approval_actions(actions)
    title = "Approval options:" if mode == "approval" else "Next step options:"
    return send_buttons_message(title, buttons)


def clean_prompt(prompt: str) -> str:
    raw = prompt or ""
    match = CURRENT_USER_REQUEST_PATTERN.search(raw)
    if match:
        text = match.group(1).strip()
    else:
        text = TELEGRAM_META_PATTERN.sub("", raw).strip()
    text = re.sub(r"\[media attached:[^\n]*\]\s*", "", text, flags=re.I)
    text = re.sub(r"media://inbound/[^\s)]+", "", text, flags=re.I)
    text = re.sub(r"\(\s*image(?:/[a-z0-9.+-]+)?\s*\)", "", text, flags=re.I)
    text = " ".join(text.split())
    if not text:
        return "Review attached image"
    return text


def prompt_layout_for_objective(prompt: str) -> str:
    """Clean transport metadata while preserving pasted-card line boundaries."""
    raw = prompt or ""
    match = CURRENT_USER_REQUEST_PATTERN.search(raw)
    text = match.group(1).strip() if match else TELEGRAM_META_PATTERN.sub("", raw).strip()
    text = re.sub(r"\[media attached:[^\n]*\]\s*", "", text, flags=re.I)
    text = re.sub(r"media://inbound/[^\s)]+", "", text, flags=re.I)
    text = re.sub(r"\(\s*image(?:/[a-z0-9.+-]+)?\s*\)", "", text, flags=re.I)
    return text or "Review attached image"


def is_hold_request(prompt: str) -> bool:
    text = clean_prompt(prompt).strip().lower()
    return text in {"next:hold", "hold", "hold / no action", "no action", "pause", "stop"}


OBJECTIVE_RULES = [
    (("post-restart readiness", "route-readiness", "route readiness", "workhorse readiness", "readiness pressure test", "readiness test"), "Run readiness test"),
    (("telegram/route", "telegram route", "route repair", "route-readiness repair", "readiness repair"), "Repair Telegram routing"),
    (("jaimes", "strict", "settings", "prevent him", "following my instructions"), "Tune JAIMES instruction-following settings"),
    (("crypto", "wallet", "portfolio", "profit target", "trade card", "trading autonomy"), "Tune JAIMES crypto action mode"),
    (("next week", "next matchup", "week 7", "future lineup"), "Check ESPN next-week lineup"),
    (("fantasy baseball", "espn", "roster", "lineup", "trade", "add/drop", "waiver"), "Sync fantasy baseball roster"),
    (("telegram", "button", "buttons", "work card", "live card"), "Tune Telegram UX"),
    (("openclaw", "upgrade", "update", "latest version"), "Update OpenCLAW stack"),
    (("keychain", "cookie.codex", "codex cookie"), "Fix Codex keychain alert"),
    (("automation", "automations", "cron", "crons", "schedule", "jobs"), "Review automation schedule"),
    (("gmail", "inbox", "email"), "Triage Gmail inbox"),
    (("sorare", "lineup", "game week", "gw"), "Review Sorare lineup state"),
    (("jaimes", "j.a.i.n", "jain", "josh 2.0", "joshex", "agent ecosystem"), "Sync agent ecosystem state"),
]

LEADING_REQUEST_RE = re.compile(
    r"^(please\s+)?(can you|could you|would you|may you|make sure|check|review|look at|help me|i want you to)\s+",
    re.I,
)

CONTROL_TOWER_MARKERS = ("control tower", "mission control", "misson control", "brain feed", "live work board", "work board", "dashboard", "kiosk")
CONTROL_TOWER_ACTION_RE = re.compile(
    r"\b(check|review|look at|inspect|open|bring up|pulled up|pull up|verify|refresh|fix|resolve|clean up|close|show|make sure)\b",
    re.I,
)
CONTROL_TOWER_STATUS_RE = re.compile(
    r"\b(why|what'?s going on|what is going on|what'?s happening|how come|keep saying|keeps saying|stuck|been switching|switching in and out)\b",
    re.I,
)


def current_request_text(text: str) -> str:
    """Exclude pasted status-card rows before objective classification."""
    embedded_card_row = re.compile(
        r"^(?:[🎯🤖📊⏱️✅⚠️➡️🔐]\s*)?"
        r"(?:objective|model|steps?|eta|complete|what was done|issues|"
        r"appropriate next steps|approval needed|status|progress)\s*(?::|$)",
        re.I,
    )
    eligible: list[str] = []
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
        eligible.append(line)
    parts = [
        p.strip(" ,.-")
        for p in re.split(r"(?<=[.!?])\s+|\n+", "\n".join(eligible))
        if p.strip()
    ]
    request_markers = ("please", "can you", "could you", "would you", "fix ", "make ", "change ", "add ", "remove ", "check ", "find ", "build ", "run ", "verify ")
    candidates = [p for p in parts if any(marker in p.lower() for marker in request_markers)]
    return candidates[-1] if candidates else " ".join(eligible)


def summarize_objective(text: str) -> str:
    clean = " ".join(current_request_text(text).split())
    lowered = clean.lower()
    if "correct objective" in lowered and "current task" in lowered:
        return "Fix current-task objective mapping"
    for markers, summary in OBJECTIVE_RULES:
        if any(marker in lowered for marker in markers):
            return summary
    if any(marker in lowered for marker in CONTROL_TOWER_MARKERS):
        # If Josh asks why the board says something, explain/fix the status
        # instead of re-entering a generic "Check Control Tower state" loop.
        if CONTROL_TOWER_STATUS_RE.search(lowered):
            return "Explain Control Tower status"
        if CONTROL_TOWER_ACTION_RE.search(lowered):
            return "Check Control Tower state"
    for markers, summary in ():
        if any(marker in lowered for marker in markers):
            return summary
    clean = LEADING_REQUEST_RE.sub("", clean).strip(" .")
    words = clean.split()
    if len(words) > 8:
        clean = " ".join(words[:8])
    return clean[:80] or "Handle Telegram task"


def objective_from_prompt(prompt: str) -> str:
    text = clean_prompt(prompt)
    lowered = text.lower().strip()
    if is_hold_request(prompt):
        return "Hold / no action"
    if lowered.startswith("/overview"):
        return "Run Control Tower overview"
    if lowered.startswith("/steer"):
        rest = text[len("/steer"):].strip()
        return rest or "Handle steering request"
    if lowered.startswith("/status"):
        return "Report Josh 2.0 status"
    if lowered.startswith("/mc"):
        return "Check Control Tower"
    if lowered.startswith("/models"):
        return "Show active model routing"
    if lowered.startswith("/route"):
        return "Show routing options"
    if lowered.startswith("/daily"):
        return "Run daily overview"
    return summarize_objective(prompt_layout_for_objective(prompt))


def classify_privacy(prompt: str) -> str:
    text = clean_prompt(prompt).lower()
    if any(marker in text for marker in ("espn", "fantasy baseball", "roster", "lineup", "matchup", "waiver", "trade")):
        return "agent-private"
    private_markers = {
        "password", "cookie", "oauth", "token", "keychain", "gmail", "email",
        "calendar", "account", "login", "sorare", "browser", "chrome",
        "bank", "stripe", "payment", "private", "personal account",
    }
    return "sensitive-account" if any(marker in text for marker in private_markers) else "dashboard-safe"


def classify_task_type(prompt: str) -> str:
    text = clean_prompt(prompt).lower()
    if any(marker in text for marker in ("espn", "fantasy baseball", "roster", "lineup", "matchup", "waiver", "trade")):
        return "connected-account-triage"
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
    provider = str(model_route.get("provider") or first_stop)
    raw_model = str(model_route.get("model") or "").strip()
    model = raw_model if raw_model and raw_model.lower() not in {provider.lower(), first_stop.lower(), "codex"} else fallback_model
    reason = str(model_route.get("reason") or "").strip()
    route_label = str(model_route.get("routeLabel") or "").strip()
    model_lower = model.lower()
    agent = str(model_route.get("owner") or route_result.get("agent") or "josh")
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
        fallback = "Codex/OpenAI if tools or private execution are needed"
    elif first_stop == "xai":
        why = "public current-events"
        fallback = "Codex/OpenAI for execution"
    elif first_stop == "openrouter":
        why = "fallback/specialist check"
        fallback = "Codex/OpenAI for execution"
    else:
        friendly_model = "Codex"
        why = "execution/private fit"
        fallback = "Gemini was not selected"
    lane = route_label or first_stop
    if "gemini auth is blocked" in reason.lower() or "unsupported-client" in reason.lower():
        why = "Gemini unavailable; using Codex/OpenAI for execution"
        fallback = "Gemini blocked: UNSUPPORTED_CLIENT / unsupported CLI subscription client"
    detail = f"provider={provider}; model={model}; lane={lane}; why={why}; fallback={fallback}"
    if reason:
        detail += f"; router_reason={reason}"
    return detail, f"provider={provider}; model={model}; lane={lane}; owner={agent}; fallback={fallback}"


def auto_route_for_prompt(prompt: str, fallback_model: str) -> dict[str, str]:
    task_type = classify_task_type(prompt)
    privacy = classify_privacy(prompt)
    if COORDINATOR_SCRIPT.exists():
        try:
            result = run_cmd([
                "python3",
                str(COORDINATOR_SCRIPT),
                "route",
                "--privacy",
                privacy,
                "--telemetry",
            ], timeout=12, input_text=prompt)
            if result.get("ok") and result.get("stdout"):
                route_result = json.loads(str(result["stdout"]))
                provider = str(route_result.get("provider") or "unknown")
                model = str(route_result.get("model") or fallback_model)
                worker = str(route_result.get("worker") or "unknown-worker")
                host = str(route_result.get("host") or "unknown-host")
                reason = str(route_result.get("routingReason") or "auto route")
                fallback = str(route_result.get("fallback") or "none")
                role = str(route_result.get("role") or route_result.get("routeId") or "coordinator")
                display = f"planned provider={provider}; model={model}; worker={worker}; host={host}; why={reason}; fallback={fallback}"
                route_line = f"planned provider={provider}; model={model}; lane={role}; worker={worker}; host={host}; reason={reason}; fallback={fallback}"
                return {"model": display, "route": route_line, "task_type": task_type, "privacy": privacy}
        except Exception:
            pass
    cmd = [
        "python3",
        "mission-control/scripts/agent_route.py",
        "--task-type",
        task_type,
        "--title",
        "Josh 2.0 Telegram task",
        "--objective",
        "Josh 2.0 Telegram task",
        "--privacy",
        privacy,
        "--requester",
        "josh2",
        "--prefer",
        "josh",
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
        selection = select_skill(prompt, "josh")
        if write_selection is not None:
            write_selection(selection, clean_prompt(prompt))
        return {
            "id": str(selection.get("id") or ""),
            "label": str(selection.get("label") or ""),
            "reason": str(selection.get("reason") or ""),
        }
    except Exception:
        return {"id": "", "label": "", "reason": ""}


def run_cmd(cmd: list[str], timeout: int = 20, input_text: str | None = None) -> dict[str, str | int | bool]:
    proc = subprocess.run(cmd, cwd=WORKSPACE, text=True, input=input_text, capture_output=True, timeout=timeout)
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


def with_work_card_target(cmd: list[str], meta: dict[str, Any] | None = None) -> list[str]:
    if not meta:
        return cmd
    routed = list(cmd)
    chat_id = str(meta.get("telegram_chat_id") or "").strip()
    thread_id = str(meta.get("telegram_thread_id") or "").strip()
    if chat_id:
        routed.extend(["--chat-id", chat_id])
    # The work-card feature gates need the logical topic id even though the
    # Telegram transport later omits General/Topic 1 from the Bot API payload.
    if thread_id:
        routed.extend(["--thread-id", thread_id])
    return routed


def publish_josh(title: str, status: str, detail: str) -> None:
    cmd = [
        "python3",
        "mission-control/scripts/agent_publish.py",
        "--agent",
        "josh2",
        "--type",
        "status",
        "--status",
        status,
        "--title",
        title,
        "--tool",
        "Josh 2.0 Telegram",
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


def internal_replay_prompt(prompt: str) -> bool:
    """Ignore framework-injected compaction and continuation rows."""
    lowered = (prompt or "").lstrip().lower()
    return lowered.startswith((
        "[context compaction",
        "[prior context",
        "[your active task list was preserved",
        "[async delegation",
    ))


def should_skip_stale_prompt_event(ts: str, first_bootstrap: bool) -> bool:
    age = event_age_seconds(ts)
    if age is None:
        return False
    if first_bootstrap and age > STALE_BOOTSTRAP_SECONDS:
        return True
    return age > MAX_UNACKED_PROMPT_AGE_SECONDS


def send_ack(event: dict[str, str], model: str, dry_run: bool = False, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    message_id = str(event.get("message_id") or "")
    if message_id and meta:
        key = f"fast-ack-telegram-{meta.get('telegram_chat_id')}-{meta.get('telegram_thread_id')}-message-{message_id}"
    elif event.get("run_id"):
        stable_identity = hashlib.sha1(
            f"{event.get('session_id') or ''}:{event.get('run_id') or ''}".encode("utf-8")
        ).hexdigest()[:20]
        key = f"fast-ack-run-{stable_identity}"
    else:
        key = f"fast-ack-{event['session_id']}-{event['ts'].replace(':', '').replace('.', '-')}"
    prompt = event.get("prompt", "")
    draft_id = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    ack_message_id = ""
    ack_sent = False
    if dry_run:
        ack_message_id = "dry-run-message"
        ack_sent = True
    else:
        send_chat_action(meta=meta)
        send_message_draft(draft_id, "", meta=meta)
        if fast_ack_enabled():
            #JAIMES: use a real Telegram reaction for fast ack; never fall back to a visible eyes message bubble.
            ack_sent = send_message_reaction(message_id, meta=meta) if message_id else send_prompt_reaction(prompt, meta=meta)
        else:
            ack_sent = True
    # The visible acknowledgement must be first. Route and skill probes may
    # involve remote health checks and must never delay the eyes reaction.
    objective = objective_from_prompt(prompt)
    route = auto_route_for_prompt(prompt, model or DEFAULT_MODEL)
    skill = skill_for_prompt(prompt)
    display_model = route["model"]
    display_route = route["route"]
    if skill.get("label"):
        display_model = f"{display_model}; skill: {skill['label']}"
        display_route = f"{display_route}; runbook={skill['id']}"
    if is_hold_request(prompt):
        if not dry_run:
            publish_josh(objective, "done", "Hold requested; no live work card started.")
        return {
            # #JAIMES: a reaction is best-effort UI only; never make it a gate
            # for a claimed task because global before_dispatch lacks messageId.
            "ok": True,
            "reaction_ok": bool(dry_run or ack_sent),
            "ack_message_id": ack_message_id,
            "key": key,
            "model": display_model,
            "route": display_route,
            "skill": skill,
            "objective": objective,
            "run_id": "",
            "last_card_update_at": utc_now(),
        }
    start_visible_card = live_cards_enabled(meta)
    if not dry_run and start_visible_card:
        card_start = run_cmd(with_work_card_target([
            "python3",
            str(WORK_CARD_SCRIPT),
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
            "Objective and runbook confirmed",
            "--done",
            f"Received Telegram task|Objective determined: {objective}|Skill selected: {skill.get('label') or 'none'}",
            "--next",
            "Work automatically; show buttons only for final approval steps if needed",
            "--ack-message-id",
            ack_message_id,
        ], meta))
    else:
        card_start = {"ok": True, "skipped": True}
    if not dry_run:
        publish_josh(objective, "active", f"Objective confirmed; {display_model}; skill={skill.get('label') or 'none'}")
    return {
        # #JAIMES: queue delivery must survive a missing/failed optional reaction.
        "ok": True,
        "reaction_ok": bool(dry_run or ack_sent),
        "ack_message_id": ack_message_id,
        "key": key,
        "model": display_model,
        "route": display_route,
        "skill": skill,
        "objective": objective,
        "run_id": event.get("run_id") or "",
        "last_card_update_at": utc_now(),
        "card_start_ok": bool(card_start.get("ok")),
        "card_start_receipt": str(card_start.get("stdout") or ""),
    }


def coordinator_job_status(job_id: str) -> str:
    if not job_id or not COORDINATOR_SCRIPT.exists():
        return ""
    try:
        result = run_cmd([sys.executable, str(COORDINATOR_SCRIPT), "status", "--job-id", job_id], timeout=8)
        if not result.get("ok") or not result.get("stdout"):
            return ""
        payload = json.loads(str(result["stdout"]))
        return str((payload.get("job") or {}).get("status") or "")
    except Exception:
        return ""


def prune_terminal_cards(state: dict[str, Any], keep: int = MAX_TERMINAL_CARD_RECORDS) -> int:
    active = state.get("active_cards")
    if not isinstance(active, dict):
        return 0
    terminal = [
        (key, card)
        for key, card in active.items()
        if isinstance(card, dict) and str(card.get("status") or "").lower() in TERMINAL_CARD_STATUSES
    ]
    terminal.sort(
        key=lambda item: str(item[1].get("ended_at") or item[1].get("last_card_update_at") or item[1].get("started_at") or ""),
        reverse=True,
    )
    removed = 0
    for key, _ in terminal[max(0, keep):]:
        active.pop(key, None)
        removed += 1
    return removed


def update_active_cards(state: dict[str, Any], session_id: str, dry_run: bool = False, meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not live_cards_enabled(meta):
        state["processed_progress_events"] = sorted(set(state.get("processed_progress_events") or []))[-300:]
        return []
    active = state.get("active_cards") or {}
    processed = set(state.get("processed_progress_events") or [])
    updates: list[dict[str, Any]] = []
    for event in recent_progress_events(session_id, meta=meta):
        event_id = event["event_id"]
        if event_id in processed:
            continue
        processed.add(event_id)
        card = active.get(event["run_id"])
        if not card:
            continue
        if str(card.get("status") or "").lower() in {"done", "failed", "paused"}:
            continue
        objective = str(card.get("objective") or "Josh 2.0 Telegram task")
        key = str(card.get("key") or "")
        if not key:
            continue
        if event["type"] == "model.completed":
            final_text = str(event.get("final_text") or "")
            if is_ack_only_final(final_text):
                cmd = [
                    "python3",
                    str(WORK_CARD_SCRIPT),
                    "fail",
                    "--key",
                    key,
                    "--title",
                    objective,
                    "--model",
                    str(card.get("model") or DEFAULT_MODEL),
                    "--route",
                    str(card.get("route") or DEFAULT_ROUTE),
                    "--done",
                    "Model returned only the acknowledgement|No useful tool, API, browser, or account check ran|Marked this as a failed run instead of complete",
                    "--blocker",
                    "OpenCLAW stopped after the acknowledgement instead of executing the task",
                    "--next",
                    "Re-run the task with full execution on the appropriate Josh 2.0 or JAIMES host",
                ]
                target_status = "error"
                publish_detail = "OpenCLAW returned only the acknowledgement; no useful work ran."
                card_status = "failed"
            else:
                cmd = [
                    "python3",
                    str(WORK_CARD_SCRIPT),
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
                    "Final response delivered",
                    "--blocker",
                    "None",
                    "--no-final-summary",
                ]
                target_status = "done"
                publish_detail = "Final response sent in Josh 2.0 Telegram."
                card_status = "done"
            if not dry_run:
                result = run_cmd(with_work_card_target(cmd, meta)) if cmd else {"ok": True, "skipped_visible_completion": True}
                publish_josh(objective, target_status, publish_detail)
                #JAIMES: the model’s final reply is canonical; do not append a detached approval-button message after it.
            else:
                result = {"ok": True, "dry_run": True}
            card["status"] = card_status
            card["last_card_update_at"] = utc_now()
            card["last_progress_at"] = card["last_card_update_at"]
            updates.append({"event": event_id, "result": result})
        else:
            if not dry_run and (event_age_seconds(event.get("ts", "")) or 0) < MAX_UNACKED_PROMPT_AGE_SECONDS:
                send_chat_action()
            cmd = [
                "python3",
                str(WORK_CARD_SCRIPT),
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
                "--done",
                event["summary"],
            ]
            if not dry_run:
                result = run_cmd(with_work_card_target(cmd, meta))
                publish_josh(objective, "active", event["summary"])
            else:
                result = {"ok": True, "dry_run": True}
            card["status"] = "active"
            card["last_card_update_at"] = utc_now()
            card["last_progress_at"] = card["last_card_update_at"]
            updates.append({"event": event_id, "result": result})
    now = dt.datetime.now(dt.timezone.utc)
    for run_id, card in active.items():
        if not isinstance(card, dict) or str(card.get("status") or "").lower() in TERMINAL_CARD_STATUSES:
            continue
        last_raw = str(card.get("last_card_update_at") or "")
        try:
            last = dt.datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
        except Exception:
            last = now
        objective = str(card.get("objective") or "Josh 2.0 Telegram task")
        key = str(card.get("key") or "")
        if not key:
            continue
        coordinator_owned = bool(card.get("coordinator_owned"))
        worker_status = coordinator_job_status(str(card.get("job_id") or "")) if coordinator_owned else ""
        if worker_status in {"done", "failed"}:
            card["status"] = worker_status
            card["ended_at"] = utc_now()
            card["last_card_update_at"] = card["ended_at"]
            continue
        # #JAIMES: coordinator-owned workers still need visible heartbeat edits.
        # Let them bypass session-expiry handling below, then reach the shared
        # heartbeat path so a long model call never leaves the Inbox card frozen.
        if not coordinator_owned and card_session_id(card) and card_session_id(card) != session_id:
            summary = "Previous Telegram session ended; Josh 2.0 is back on standby."
            cmd = [
                "python3",
                str(WORK_CARD_SCRIPT),
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
            ]
            result = {"ok": True, "dry_run": True} if dry_run else run_cmd(with_work_card_target(cmd, meta))
            if not dry_run:
                publish_josh("Josh 2.0 standing by", "done", summary)
            card["status"] = "done"
            card["ended_at"] = utc_now()
            card["last_card_update_at"] = card["ended_at"]
            updates.append({"event": f"session-ended:{run_id}:{card['ended_at']}", "result": result})
            continue
        started_at = card_started_at(card) or last
        if not coordinator_owned and (now - started_at).total_seconds() > MAX_ACTIVE_CARD_SECONDS:
            summary = "No recent tool or model progress; Josh 2.0 is back on standby."
            cmd = [
                "python3",
                str(WORK_CARD_SCRIPT),
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
            ]
            result = {"ok": True, "dry_run": True} if dry_run else run_cmd(with_work_card_target(cmd, meta))
            if not dry_run:
                publish_josh("Josh 2.0 standing by", "done", summary)
            card["status"] = "done"
            card["ended_at"] = utc_now()
            card["last_card_update_at"] = card["ended_at"]
            updates.append({"event": f"expired:{run_id}:{card['ended_at']}", "result": result})
            continue
        if (now - last).total_seconds() < HEARTBEAT_SECONDS:
            continue
        summary = f"Still working; waiting for next model/tool update ({local_time_label()})"
        cmd = [
            "python3",
            str(WORK_CARD_SCRIPT),
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
            summary,
            "--done",
            summary,
            "--no-brain-feed",
        ]
        result = {"ok": True, "dry_run": True} if dry_run else run_cmd(with_work_card_target(cmd, meta))
        # Do not keep re-publishing heartbeat-only work-card text to
        # Brain Feed; it makes stale cards look like current truth.
        card["last_card_update_at"] = utc_now()
        updates.append({"event": f"heartbeat:{run_id}:{card['last_card_update_at']}", "result": result})
    state["processed_progress_events"] = sorted(processed)[-300:]
    return updates


def reconcile_orphan_work_cards(
    state: dict[str, Any],
    dry_run: bool = False,
    meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    work_state = load_json(WORK_CARD_STATE_PATH, {})
    cards = work_state.get("cards") if isinstance(work_state, dict) else {}
    if not isinstance(cards, dict):
        return []
    owned_keys = {
        str(card.get("key") or "")
        for card in (state.get("active_cards") or {}).values()
        if isinstance(card, dict) and str(card.get("status") or "").lower() not in {"done", "failed", "paused"}
    }
    now = dt.datetime.now(dt.timezone.utc)
    reconciled: list[dict[str, Any]] = []
    for key, card in cards.items():
        if not isinstance(card, dict):
            continue
        if str(card.get("status") or "").lower() not in {"running", "active"}:
            continue
        if key in owned_keys:
            continue
        updated = parse_utc(card.get("updated_at"))
        if updated and (now - updated).total_seconds() < ORPHAN_WORK_CARD_GRACE_SECONDS:
            continue
        title = str(card.get("title") or "Josh 2.0 Telegram task")
        summary = "No active model or tool run owns this card; Josh 2.0 is idle."
        cmd = [
            "python3",
            str(WORK_CARD_SCRIPT),
            "done",
            "--key",
            key,
            "--title",
            title,
            "--model",
            str(card.get("model") or DEFAULT_MODEL),
            "--route",
            str(card.get("route") or DEFAULT_ROUTE),
            "--done",
            summary,
            "--blocker",
            "None",
            "--no-final-summary",
        ]
        result = {"ok": True, "dry_run": True} if dry_run else run_cmd(with_work_card_target(cmd, meta))
        reconciled.append({"event": f"orphan-card:{key}", "result": result})
    return reconciled


def claim_inbox(args: argparse.Namespace) -> dict[str, Any]:
    prompt = sys.stdin.read()
    meta = {
        "telegram_chat_id": str(args.chat_id or CONTROL_CENTER_CHAT_ID),
        "telegram_thread_id": str(args.thread_id or "1"),
        "telegram_session_key": str(args.session_key or ""),
    }
    stable = str(args.run_id or f"message:{args.message_id}")
    session_id = hashlib.sha1(str(args.session_key or stable).encode("utf-8")).hexdigest()[:20]
    event = {
        "session_id": session_id,
        "ts": utc_now(),
        "run_id": stable,
        "message_id": str(args.message_id or ""),
        "prompt": prompt,
    }
    ack = send_ack(event, model=DEFAULT_MODEL, dry_run=args.dry_run, meta=meta)
    # #JAIMES: acknowledgement/reaction is best effort; the coordinator owns
    # delivery after claiming, even if Telegram cannot place the eyes reaction.
    if not ack.get("ok"):
        publish_josh("Inbox acknowledgement degraded", "active", "Reaction failed; continuing to queue the owned Inbox request.")

    cmd = [
        sys.executable,
        str(COORDINATOR_SCRIPT),
        "submit",
        "--privacy", classify_privacy(prompt),
        "--origin-run-id", stable,
        "--message-id", str(args.message_id or ""),
        "--card-key", str(ack.get("key") or ""),
        "--chat-id", str(meta["telegram_chat_id"]),
        "--thread-id", str(meta["telegram_thread_id"]),
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    submitted = run_cmd(cmd, timeout=30, input_text=prompt)
    if not submitted.get("ok") or not submitted.get("stdout"):
        if not args.dry_run:
            run_cmd(with_work_card_target([
                sys.executable,
                str(WORK_CARD_SCRIPT),
                "fail",
                "--key", str(ack.get("key") or stable),
                "--model", "unverified",
                "--route", "Inbox coordinator dispatch",
                "--done", "Acknowledged the Inbox request|Stopped before model execution",
                "--blocker", "The asynchronous worker could not be queued",
                "--next", "Retry after the coordinator service is healthy",
            ], meta))
            publish_josh("Inbox worker queue failed", "error", "The request was acknowledged but no worker was queued.")
        return {"ok": False, "status": "queue-failed", "key": ack.get("key")}

    try:
        envelope = json.loads(str(submitted["stdout"]))
    except Exception:
        envelope = {}
    job = envelope.get("job") if isinstance(envelope, dict) else {}
    route = envelope.get("route") if isinstance(envelope, dict) else {}
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    active = state.setdefault("active_cards", {})
    active[stable] = {
        "key": ack.get("key"),
        "objective": ack.get("objective"),
        "model": ack.get("model"),
        "route": ack.get("route"),
        "session_id": session_id,
        "message_id": str(args.message_id or ""),
        "job_id": str((job or {}).get("jobId") or ""),
        "coordinator_owned": True,
        "reaction_ok": bool(ack.get("reaction_ok")),
        "started_at": ack.get("last_card_update_at"),
        "last_progress_at": ack.get("last_card_update_at"),
        "last_card_update_at": ack.get("last_card_update_at"),
        "status": "active",
    }
    state["last_claim_at"] = utc_now()
    state["last_claim"] = {
        "run_id": stable,
        "message_id": str(args.message_id or ""),
        "job_id": str((job or {}).get("jobId") or ""),
        "route_id": str((route or {}).get("routeId") or ""),
        "reaction_ok": bool(ack.get("reaction_ok")),
    }
    if not args.dry_run:
        save_json(STATE_PATH, state)
    return {
        "ok": True,
        "status": "queued",
        "job_id": str((job or {}).get("jobId") or ""),
        "route_id": str((route or {}).get("routeId") or ""),
        "deduplicated": bool(envelope.get("deduplicated")) if isinstance(envelope, dict) else False,
    }


def coordinator_maintenance() -> None:
    if not COORDINATOR_SCRIPT.exists():
        return
    for extra in (["recover"], ["cleanup", "--max-age-seconds", str(24 * 60 * 60)]):
        try:
            subprocess.run(
                [sys.executable, str(COORDINATOR_SCRIPT), *extra],
                cwd=WORKSPACE,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
        except Exception:
            pass


def poll_once(dry_run: bool = False) -> dict[str, Any]:
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    acked = set(state.get("acked_prompt_events") or [])
    meta = session_metadata()
    session_id = str(meta.get("sessionId") or "")
    model = str(meta.get("model") or DEFAULT_MODEL)
    if not session_id:
        state["last_checked_at"] = utc_now()
        state["direct_session_id"] = ""
        state["model"] = model
        state["last_result"] = {"ok": False, "status": "no-direct-session"}
        state["status"] = "no-direct-session"
        if not dry_run:
            save_json(STATE_PATH, state)
        return {"ok": False, "status": "no-direct-session"}

    sent: list[dict[str, Any]] = []
    state.setdefault("active_cards", {})
    events = recent_prompt_events(session_id, meta=meta)
    first_bootstrap = not acked and not state.get("last_checked_at")
    for event in events:
        event_id = f"{event['session_id']}:{event['ts']}"
        if event_id in acked:
            continue
        if internal_replay_prompt(event.get("prompt") or ""):
            # Continuation plumbing is not a new Telegram task. Leave the
            # existing live card in place and wait for the real final summary.
            acked.add(event_id)
            continue
        if should_skip_stale_prompt_event(event["ts"], first_bootstrap):
            acked.add(event_id)
            continue
        result = send_ack(event, model=model, dry_run=dry_run, meta=meta)
        if result.get("ok"):
            acked.add(event_id)
            if result.get("run_id"):
                state["active_cards"][result["run_id"]] = {
                    "key": result.get("key"),
                    "objective": result.get("objective"),
                    "model": result.get("model"),
                    "route": result.get("route"),
                    "ack_message_id": result.get("ack_message_id"),
                    "session_id": session_id,
                    "started_at": result.get("last_card_update_at"),
                    "last_progress_at": result.get("last_card_update_at"),
                    "last_card_update_at": result.get("last_card_update_at"),
                    "status": "active",
                }
            sent.append({"event": event_id, "result": result})
        else:
            sent.append({"event": event_id, "result": result})
            break

    state["acked_prompt_events"] = sorted(acked)[-200:]
    state["last_checked_at"] = utc_now()
    state["direct_session_id"] = session_id
    state["model"] = model
    state["status"] = "ok"
    if sent:
        state["last_sent_at"] = utc_now()
        state["last_result"] = sent[-1]["result"]
        if sent[-1]["result"].get("ack_message_id"):
            state["latest_pending_ack"] = {
                "message_id": sent[-1]["result"].get("ack_message_id"),
                "key": sent[-1]["result"].get("key"),
                "event": sent[-1]["event"],
                "created_at": utc_now(),
                "model": model,
            }
        else:
            state.pop("latest_pending_ack", None)
    updates = update_active_cards(state, session_id, dry_run=dry_run, meta=meta)
    orphan_updates = reconcile_orphan_work_cards(state, dry_run=dry_run, meta=meta)
    if orphan_updates:
        updates.extend(orphan_updates)
    pruned_terminal_cards = prune_terminal_cards(state)
    if not dry_run:
        save_json(STATE_PATH, state)
    return {
        "ok": True,
        "session_id": session_id,
        "sent": sent,
        "updates": updates,
        "pruned_terminal_cards": pruned_terminal_cards,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one poll and exit.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--claim-inbox", action="store_true", help="Claim one Inbox event from stdin and queue its worker.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--message-id", default="")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--thread-id", default="")
    parser.add_argument("--session-key", default="")
    args = parser.parse_args()

    if args.claim_inbox:
        print(json.dumps(claim_inbox(args), indent=2))
        return 0

    if args.once:
        print(json.dumps(poll_once(dry_run=args.dry_run), indent=2))
        return 0

    coordinator_maintenance()
    while True:
        try:
            poll_once()
        except Exception as exc:  # noqa: BLE001 - keep watcher alive
            state = load_json(STATE_PATH, {})
            if not isinstance(state, dict):
                state = {}
            state["last_error_at"] = utc_now()
            state["last_error"] = type(exc).__name__
            save_json(STATE_PATH, state)
        time.sleep(max(0.5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
