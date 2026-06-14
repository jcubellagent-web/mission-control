#!/usr/bin/env python3
"""Send an immediate JAIMES Telegram acknowledgement for new direct-chat turns."""

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
from pathlib import Path
from typing import Any


HOME = Path.home()
WORKSPACE = HOME / ".openclaw" / "workspace"
SESSIONS_PATH = HOME / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
HERMES_SESSIONS_PATH = HOME / ".hermes" / "sessions" / "sessions.json"
SESSION_DIR = SESSIONS_PATH.parent
STATE_PATH = HOME / ".openclaw" / "telegram" / "jaimes_fast_ack_state.json"
DIRECT_SESSION_KEYS = (
    "agent:main:telegram:dm:6218150306",
    "agent:main:telegram:direct:6218150306",
)
DEFAULT_MODEL = "GPT-5.5 / local JAIMES OpenCLAW session"
DEFAULT_ROUTE = "JAIMES Telegram -> Hermes task"
STALE_BOOTSTRAP_SECONDS = 120
HEARTBEAT_SECONDS = 20
MAX_ACTIVE_CARD_SECONDS = 45 * 60
APPROVAL_ACTIONS_PATH = WORKSPACE / "memory" / "telegram_approval_actions.json"
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


def send_initial_ack(text: str, timeout: int = 15) -> str:
    if work_card is None:
        return ""
    payload = {
        "chat_id": work_card.telegram_target(),
        "text": text,
        "disable_notification": True,
    }
    result = work_card.api_call("sendMessage", payload, timeout=timeout)
    return str(result.get("result", {}).get("message_id") or "") if result.get("ok") else ""


def send_chat_action(action: str = "typing") -> None:
    if os.environ.get("JAIMES_TELEGRAM_TYPING_ACTIONS", "").lower() not in {"1", "true", "yes"}:
        return
    if work_card is None:
        return
    work_card.api_call("sendChatAction", {"chat_id": work_card.telegram_target(), "action": action}, timeout=6)


def send_message_draft(draft_id: int, text: str = "") -> None:
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
    work_card.api_call(
        "sendMessageDraft",
        {"chat_id": work_card.telegram_target(), "draft_id": draft_id, "text": safe},
        timeout=6,
    )


def objective_card_text(objective: str, model: str = DEFAULT_MODEL) -> str:
    return (
        f"Model: {model}\n\n"
        "Objective:\n"
        f"- {objective}\n\n"
        "Working pattern:\n"
        "- I’ll update one live work card as tools, skills, and decisions happen.\n"
        "- I’ll send one final Complete summary when the work is done."
    )


def edit_message(message_id: str, text: str, timeout: int = 15) -> bool:
    if work_card is None or not message_id:
        return False
    payload = {
        "chat_id": work_card.telegram_target(),
        "message_id": message_id,
        "text": text,
        "disable_notification": True,
    }
    result = work_card.api_call("editMessageText", payload, timeout=timeout)
    return bool(result.get("ok"))


def send_buttons_message(text: str, buttons: list, timeout: int = 15) -> str:
    if work_card is None:
        return ""
    payload = {
        "chat_id": work_card.telegram_target(),
        "text": text,
        "reply_markup": {"inline_keyboard": buttons},
        "disable_notification": True,
    }
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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def local_time_label() -> str:
    return dt.datetime.now().astimezone().strftime("%H:%M:%S %Z")


def session_metadata() -> dict[str, Any]:
    session_stores = [load_json(SESSIONS_PATH, {}), load_json(HERMES_SESSIONS_PATH, {})]
    for sessions in session_stores:
        if not isinstance(sessions, dict):
            continue
        for direct_key in DIRECT_SESSION_KEYS:
            value = sessions.get(direct_key) or {}
            normalized = normalize_session_metadata(value)
            if normalized:
                return normalized
        for fallback_key in ("agent:main:main", *DIRECT_SESSION_KEYS):
            normalized = normalize_session_metadata(sessions.get(fallback_key) or {})
            if normalized:
                return normalized
        for fallback in sessions.values():
            normalized = normalize_session_metadata(fallback)
            if normalized:
                return normalized
    return {}


def normalize_session_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    session_id = value.get("sessionId") or value.get("session_id")
    channel = value.get("channel") or value.get("platform") or value.get("origin", {}).get("platform")
    if not session_id or channel != "telegram":
        return {}
    normalized = dict(value)
    normalized["sessionId"] = session_id
    normalized["channel"] = "telegram"
    normalized["model"] = value.get("model") or DEFAULT_MODEL
    return normalized


def recent_prompt_events(session_id: str) -> list[dict[str, str]]:
    path = SESSION_DIR / f"{session_id}.trajectory.jsonl"
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


def recent_progress_events(session_id: str) -> list[dict[str, str]]:
    path = SESSION_DIR / f"{session_id}.trajectory.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, str]] = []
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
        if event_type == "tool.call":
            summary = f"Running {friendly_name}"
        elif event_type == "tool.result":
            summary = f"Finished {friendly_name}"
        else:
            summary = "Final response sent"
        final_text = ""
        if event_type == "model.completed":
            texts = data.get("assistantTexts") or data.get("assistant_texts") or []
            if isinstance(texts, list) and texts:
                final_text = str(texts[0] or "")
        events.append({
            "event_id": f"{item.get('runId') or ''}:{item.get('seq') or ''}:{event_type}",
            "run_id": str(item.get("runId") or ""),
            "type": event_type,
            "summary": summary,
            "final_text": final_text,
        })
    return events


def mitigation_steps_from_text(text: str) -> list[str]:
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
    digest = hashlib.sha1(f"jaimes|{objective}|{step}|{index}".encode("utf-8")).hexdigest()[:10]
    return f"approve:jaimes:{digest}:{index}"


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
    steps = [step for step in mitigation_steps_from_text(final_text) if actionable_approval_step(step)]
    if not steps:
        return ""
    actions: dict[str, Any] = {}
    buttons = []
    for index, step in enumerate(steps, start=1):
        callback = approval_callback(objective, step, index)
        actions[callback] = {
            "agent": "jaimes",
            "objective": objective,
            "step": step,
            "created_at": utc_now(),
        }
        buttons.append([{"text": approval_button_label(step), "callback_data": callback}])
    buttons.append([{"text": "Hold / no action", "callback_data": "next:hold"}])
    if dry_run:
        return "dry-run-approval-buttons"
    save_approval_actions(actions)
    return send_buttons_message("Approval options:", buttons)


def clean_prompt(prompt: str) -> str:
    text = TELEGRAM_META_PATTERN.sub("", prompt or "").strip()
    return text or "Handle latest Telegram task"


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
    (("jaimes", "strict", "settings", "prevent him", "following my instructions"), "Tune JAIMES instruction-following settings"),
    (("crypto", "wallet", "portfolio", "profit target", "trade card", "trading autonomy"), "Tune JAIMES crypto action mode"),
    (("what's happening to jaimes", "what is happening to jaimes", "jaimes status", "unresponsive"), "Check JAIMES status"),
    (("telegram ux", "telegram interface", "telegram formatting", "telegram button", "work card format", "live card"), "Tune JAIMES Telegram UX"),
    (("mission control", "brain feed", "dashboard", "kiosk"), "Check Control Tower state"),
    (("sorare", "lineup", "game week", "gw", "pre-lock", "mission"), "Review Sorare lineup state"),
    (("fantasy baseball", "espn", "roster", "lineup", "matchup", "waiver", "trade"), "Sync fantasy baseball roster"),
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
    lowered = clean.lower()
    for markers, summary in OBJECTIVE_RULES:
        if any(marker in lowered for marker in markers):
            return summary
    clean = LEADING_REQUEST_RE.sub("", clean).strip(" .")
    words = clean.split()
    if len(words) > 8:
        clean = " ".join(words[:8])
    return clean[:80] or "Handle Telegram task"


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
    agent = str(model_route.get("owner") or route_result.get("agent") or "jaimes")
    if "gemini-3-pro" in model:
        friendly_model = "Gemini Pro"
    elif "gemini" in model:
        friendly_model = "Gemini Flash"
    elif "grok" in model or first_stop == "xai":
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


def send_ack(event: dict[str, str], model: str, dry_run: bool = False) -> dict[str, Any]:
    key = f"jaimes-fast-ack-{event['session_id']}-{event['ts'].replace(':', '').replace('.', '-')}"
    prompt = event.get("prompt", "")
    objective = objective_from_prompt(prompt)
    route = auto_route_for_prompt(prompt, model or DEFAULT_MODEL)
    skill = skill_for_prompt(prompt)
    display_model = route["model"]
    display_route = route["route"]
    if skill.get("label"):
        display_model = f"{display_model}; skill: {skill['label']}"
        display_route = f"{display_route}; runbook={skill['id']}"
    draft_id = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    ack_message_id = "dry-run-message" if dry_run else send_initial_ack("received — determining objective")
    if not dry_run and ack_message_id:
        send_chat_action()
        send_message_draft(draft_id, objective_card_text(objective, display_model))
        edit_message(ack_message_id, objective_card_text(objective, display_model))
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
            "--now",
            "Objective, model route, and runbook confirmed",
            "--done",
            f"Received Telegram task|Objective determined: {objective}|Model selected: {display_model}|Skill selected: {skill.get('label') or 'none'}",
            "--next",
            "Work automatically; show buttons only for final approval steps if needed",
            "--ack-message-id",
            ack_message_id,
        ])
        publish_jaimes(objective, "active", f"Objective confirmed; {display_model}; skill={skill.get('label') or 'none'}")
    return {
        "ok": bool(dry_run or ack_message_id),
        "ack_message_id": ack_message_id,
        "key": key,
        "model": display_model,
        "route": display_route,
        "skill": skill,
        "objective": objective,
        "run_id": event.get("run_id") or "",
        "last_card_update_at": utc_now(),
    }


def update_active_cards(state: dict[str, Any], session_id: str, dry_run: bool = False) -> list[dict[str, Any]]:
    # Disabled by default after Telegram rendered overlapping live-card text.
    # Keep prompt acks/finals, but avoid streaming internal-looking progress.
    if os.environ.get("JAIMES_TELEGRAM_LIVE_CARDS", "").lower() not in {"1", "true", "yes"}:
        state["processed_progress_events"] = sorted(set(state.get("processed_progress_events") or []))[-300:]
        return []
    active = state.get("active_cards") or {}
    processed = set(state.get("processed_progress_events") or [])
    approval_sent = set(state.get("approval_buttons_sent") or [])
    updates: list[dict[str, Any]] = []
    for event in recent_progress_events(session_id):
        event_id = event["event_id"]
        if event_id in processed:
            continue
        processed.add(event_id)
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
            ]
            result = {"ok": True, "dry_run": True} if dry_run else run_cmd(cmd)
            if not dry_run:
                publish_jaimes(objective, "done", "Final response sent in JAIMES Telegram.")
                if event.get("final_text") and event_id not in approval_sent:
                    approval_message_id = send_approval_options(objective, event["final_text"], dry_run=dry_run)
                    if approval_message_id:
                        approval_sent.add(event_id)
                        card["approval_message_id"] = approval_message_id
            card["status"] = "done"
            card["last_card_update_at"] = utc_now()
            card["last_progress_at"] = card["last_card_update_at"]
        else:
            if not dry_run:
                send_chat_action()
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
                "--done",
                event["summary"],
            ]
            result = {"ok": True, "dry_run": True} if dry_run else run_cmd(cmd)
            if not dry_run:
                publish_jaimes(objective, "active", event["summary"])
            card["status"] = "active"
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
            ]
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
        summary = f"Still working; waiting for next model/tool update ({local_time_label()})"
        # Heartbeats update the visible work card but should not refresh
        # Telegram's typing indicator; otherwise stale jobs look alive forever.
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
            summary,
            "--done",
            summary,
        ]
        result = {"ok": True, "dry_run": True} if dry_run else run_cmd(cmd)
        if not dry_run:
            publish_jaimes(objective, "active", summary)
        card["last_card_update_at"] = utc_now()
        updates.append({"event": f"heartbeat:{run_id}:{card['last_card_update_at']}", "result": result})
    state["processed_progress_events"] = sorted(processed)[-300:]
    state["approval_buttons_sent"] = sorted(approval_sent)[-200:]
    return updates


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
    events = recent_prompt_events(session_id)
    first_bootstrap = not acked and not state.get("last_checked_at")
    for event in events:
        event_id = f"{event['session_id']}:{event['ts']}"
        if event_id in acked:
            continue
        age = event_age_seconds(event["ts"])
        if first_bootstrap and age is not None and age > STALE_BOOTSTRAP_SECONDS:
            acked.add(event_id)
            continue
        result = send_ack(event, model=model, dry_run=dry_run)
        if result.get("ok"):
            acked.add(event_id)
            if result.get("run_id"):
                state["active_cards"][result["run_id"]] = {
                    "key": result.get("key"),
                    "objective": result.get("objective"),
                    "model": result.get("model"),
                    "route": result.get("route"),
                    "ack_message_id": result.get("ack_message_id"),
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
        state["latest_pending_ack"] = {
            "message_id": sent[-1]["result"].get("ack_message_id"),
            "key": sent[-1]["result"].get("key"),
            "event": sent[-1]["event"],
            "created_at": utc_now(),
            "model": model,
        }
    updates = update_active_cards(state, session_id, dry_run=dry_run)
    if not dry_run:
        save_json(STATE_PATH, state)
    return {"ok": True, "session_id": session_id, "sent": sent, "updates": updates, "dry_run": dry_run}


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
