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
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
SESSIONS_PATH = Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
DIRECT_SESSION_KEY = "agent:main:telegram:direct:6218150306"
if str(WORKSPACE / "scripts") not in sys.path:
    sys.path.insert(0, str(WORKSPACE / "scripts"))

try:
    from send_josh_reply import API_BASE, TARGET, build_payload  # type: ignore
except Exception:  # noqa: BLE001 - dry-run and local validation can run without Josh helper
    API_BASE = ""
    TARGET = ""

    def build_payload(text: str, buttons: list | None, silent: bool = True) -> dict:
        payload = {"chat_id": TARGET, "text": text, "disable_notification": silent}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return payload

STATE_PATH = Path(os.environ.get("JOSH_WORK_CARD_STATE", "memory/josh_work_cards.json"))
LOCK_PATH = STATE_PATH.with_suffix(STATE_PATH.suffix + ".lock")
ACK_STATE_PATH = Path(os.environ.get("JOSH_FAST_ACK_STATE", str(Path.home() / ".openclaw" / "telegram" / "fast_ack_state.json")))
TELEGRAM_COOLDOWN_PATH = Path(os.environ.get("JOSH_TELEGRAM_COOLDOWN_STATE", "memory/josh_telegram_cooldown.json"))
IMMUTABLE_TERMINAL_STATUSES = {"done", "failed"}
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
    tmp.replace(path)


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
    state = load_json(ACK_STATE_PATH, {})
    pending = state.get("latest_pending_ack") or {}
    message_id = str(pending.get("message_id") or "")
    if not message_id or pending.get("claimed_by"):
        return ""
    pending["claimed_by"] = card_key
    pending["claimed_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state["latest_pending_ack"] = pending
    save_json(ACK_STATE_PATH, state)
    return message_id


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


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
    if "gemini" in lower and ("safe summary" in lower or "review" in lower):
        return "Josh 2.0, with a summary helper if needed"
    if "codex" in lower or "openclaw" in lower:
        return "Josh 2.0 / Codex"
    if "jaimes" in lower:
        return "JAIMES support lane"
    if "jain" in lower:
        return "J.AI.N worker support"
    
    route_match = re.search(r"provider=([^;]+);\s*model=([^;]+)", text, re.I)
    if route_match:
        provider = clean_live_text(route_match.group(1))
        model_name = clean_live_text(route_match.group(2))
        if provider and model_name:
            if "/" in model_name:
                return compact(model_name, limit=90)
            return compact(f"{provider}/{model_name}", limit=90)
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


def hanging_bullet_lines(item: str, *, width: int = 58) -> list[str]:
    #JAIMES: Telegram cards use real nonbreaking spaces for hanging indents; Bot API HTML does not reliably render `&nbsp;`.
    wrapped = textwrap.wrap(
        item,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [item]
    return [
        f"- {html.escape(wrapped[0])}",
        *(f"\u00a0\u00a0{html.escape(line)}" for line in wrapped[1:]),
    ]


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
        wrapped = textwrap.wrap(item, width=54, break_long_words=False, break_on_hyphens=False) or [item]
        lines.append(f"{index}. {html.escape(wrapped[0])}")
        lines.extend(f"\u00a0\u00a0\u00a0{html.escape(line)}" for line in wrapped[1:])
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
        return [line for item in clean for line in hanging_bullet_lines(item)]
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
        *hanging_bullet_lines(summary),
        *(line for item in clean[-limit:] for line in hanging_bullet_lines(item)),
    ]


COMPLETE_STATUSES = {"done", "complete", "completed", "final", "finished", "success"}


def is_complete_status(status: str) -> bool:
    return str(status or "").strip().lower() in COMPLETE_STATUSES


def progress_phase(items: list[str], status: str) -> tuple[int, str]:
    if is_complete_status(status):
        return 100, "complete"
    if status == "failed":
        return 90, "blocked near completion"
    if status == "paused":
        return 60, "paused"
    if not items:
        return 0, "waiting for first update"

    percent = 10
    detail = "task received"
    for item in items:
        text = live_line(item)
        lower = text.lower()
        if text.startswith(("📥", "👀")):
            percent = max(percent, 10)
            detail = "task received"
        elif text.startswith(("📌", "🤖")):
            percent = max(percent, 20)
            detail = "objective confirmed"
        elif text.startswith(("🧭", "🧠")):
            percent = max(percent, 35)
            detail = "triage and decisions"
        elif text.startswith(("🔧", "🛠️")):
            percent = max(percent, 55)
            detail = "tool work in progress"
        elif text.startswith("🤝"):
            percent = max(percent, 65)
            detail = "delegation in progress"
        elif text.startswith("✅"):
            if any(token in lower for token in ("verified", "canary", "passed", "success", "fixed")):
                percent = max(percent, 85)
                detail = "verification in progress"
            else:
                percent = max(percent, 75)
                detail = "implementation complete"
        elif text.startswith("🏁"):
            percent = max(percent, 95)
            detail = "wrapping up"

    # Nudge progress forward within the current phase as more distinct updates arrive.
    percent = min(95, percent + max(0, min(10, len(items) - 1)))
    return percent, detail


def progress_lines(items: list[str], status: str) -> list[str]:
    clean = []
    for item in items:
        text = live_line(item)
        if text and text not in clean:
            clean.append(text)
    complete_status = is_complete_status(status)
    if not clean:
        if complete_status:
            return ["100% complete", ""]
        return ["0% complete - waiting for first update", ""]

    percent, detail = progress_phase(clean, status)
    return [f"{percent}% complete - {detail}", ""]


def current_step_text(status: str, now: str, live_items: list[str]) -> str:
    if now:
        return compact(simplify_live_detail(now), limit=150)
    if status == "done":
        return "Finished and verified the result."
    if status == "failed":
        return "Stopped on an issue that needs attention."
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


def build_completion_summary(
    *,
    title: str,
    status: str,
    now: str = "",
    done: list[str] | None = None,
    next_step: str = "",
    blocker: str = "None",
    model: str = "",
) -> str:
    complete = "Yes" if status == "done" else "No"
    complete_title = compact(title, fallback="objective", limit=120)
    complete_detail = f"{complete_title} complete" if complete == "Yes" else f"{complete_title} not complete"

    steps = list(done or [])
    if now and now not in steps:
        steps.append(now)
    if len(steps) < 3:
        steps.append(f"Closed out: {title}")

    issues = [] if is_empty_issue(blocker) else parse_list(blocker) or [blocker]
    next_steps = parse_list(next_step)
    if not next_steps:
        next_steps = ["Approve the next safe step for the issue."] if issues else ["No action needed."]
    approval_needed = [*next_steps, "Adjust the plan", "Cancel this task"] if issues else ["n/a"]
    model_line = model or os.environ.get("JOSH_WORK_CARD_MODEL") or "unverified"

    def final_lines(items: list[str], fallback: str) -> list[str]:
        clean = [compact(item, limit=180) for item in items if compact(item, limit=180)]
        return [f"- {html.escape(item)}" for item in clean[:5]] or [f"- {fallback}"]

    lines = [
        f"Model: {html.escape(friendly_model_line(model_line))} | Route: {html.escape(resolve_auth_path(model_line))} | Why: verified task execution",
        "",
        f"Complete: {complete} - {html.escape(complete_detail)}",
        "",
        "What was done:",
        *final_lines(steps, f"Closed out: {title}"),
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
    return "\n".join(lines)


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
        f"🤖 Model: {friendly_model_line(model_line)} ({resolve_auth_path(model_line)})",
        f"🧭 Path: {friendly_route_line(route)}",
        card_title,
        "📌 Objective:",
        *hanging_bullet_lines(operator_objective(title)),
        "",
        "⚡️ Current step:",
        *hanging_bullet_lines(current_step_text(status, now, live_items)),
        "",
        "📈 Progress:",
        *progress_lines(live_items, status),
        *live_lines(live_items, fallback="complete" if is_complete_status(status) else "waiting: first update", limit=5),
    ]
    return f"<pre>{html.escape(html.unescape(chr(10).join(lines)))}</pre>"


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

    fallback_payload = build_payload(fallback_text, buttons, silent=silent, chat_id=chat_id, thread_id=thread_id)
    fallback_payload["disable_web_page_preview"] = True
    fallback = api_call("sendMessage", fallback_payload, timeout=timeout)
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
    if args.approval_buttons and status in {"done", "failed"}:
        return approval_buttons(args)
    return None


def upsert_card(args: argparse.Namespace, status: str) -> int:
    with state_lock():
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
        if not ack_message_id and status == "running" and title and title.lower() not in {"latest telegram task received", "determining objective"}:
            ack_message_id = claim_pending_ack(args.key)
        terminal_status = status in {"done", "failed"}
        #JAIMES: keep the live card visible through completion, then send one distinct final card for its concise outcome.
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
        )
        buttons = load_buttons(args, status)
        final_text = build_completion_summary(
            title=title,
            status=status,
            now=args.now or "",
            done=done,
            next_step=args.next or "",
            blocker=args.blocker or "None",
            model=model,
        ) if terminal_status and not args.no_final_summary else ""

        if args.dry_run:
            print(json.dumps({"ok": True, "dry_run": True, "text": text, "final_text": final_text, "buttons": buttons, "existing": existing}, indent=2))
            return 0

        # Approval controls govern the outcome, so they belong only on the separate final summary card.
        card_buttons = buttons if not terminal_status else None

        if existing.get("message_id"):
            result = edit_card(existing["message_id"], text, card_buttons, args.timeout, chat_id=chat_id, thread_id=thread_id)
            action = "edited"
        else:
            result = send_card(text, card_buttons, args.timeout, chat_id=chat_id, thread_id=thread_id)
            action = "sent"

        # A retry can encounter an already-updated live card after a later final-card send failed.
        # Treat Telegram's idempotent "not modified" response as success so the terminal flow can resume.
        if not result.get("ok") and "message is not modified" in str(result.get("error", "")).lower():
            result = {"ok": True, "result": {"message_id": existing.get("message_id")}}
        if not result.get("ok"):
            print(json.dumps({"ok": False, "action": action, "error": result.get("error") or result}, indent=2), file=sys.stderr)
            return 1

        message_id = existing.get("message_id")
        if action == "sent":
            message_id = result.get("result", {}).get("message_id")

        final_message_id = existing.get("final_message_id")
        final_action = None

        if final_text:
            if final_message_id:
                final_result = edit_final_summary(final_message_id, final_text, args.timeout, buttons, chat_id=chat_id, thread_id=thread_id)
                final_action = "edited"
            else:
                final_result = send_final_summary(final_text, args.timeout, buttons, chat_id=chat_id, thread_id=thread_id)
                final_action = "sent"
            if not final_result.get("ok"):
                print(json.dumps({"ok": False, "action": final_action, "error": final_result.get("error") or final_result}, indent=2), file=sys.stderr)
                return 1
            if final_action == "sent":
                final_message_id = final_result.get("result", {}).get("message_id")

        approval_message_id = None

        if ack_message_id and title and title.lower() not in {"latest telegram task received", "determining objective"}:
            edit_objective_message(ack_message_id, title, model, args.timeout, chat_id=chat_id, thread_id=thread_id)

        cards[args.key] = {
            "title": title,
            "message_id": message_id,
            "ack_message_id": ack_message_id,
            "final_message_id": final_message_id,
            "approval_message_id": approval_message_id,
            "status": status,
            "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "done": done,
            "work_log": done,
            "route": route,
            "model": model,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "next_step": args.next or existing.get("next_step") or "",
        }
        save_state(state)
        publish_brain_feed(args, status)
        print(json.dumps({"ok": True, "action": action, "final_action": final_action, "key": args.key, "message_id": message_id, "final_message_id": final_message_id}, indent=2))
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
    #JAIMES: default lifecycle preserves the live card and emits one final outcome card; opt out only for deliberately card-only runs.
    parser.add_argument("--no-final-summary", action="store_true", help="Complete the live card without a separate final summary card")
    parser.add_argument("--separate-final-summary", action="store_true", help="Compatibility no-op; separate final summary cards are the default")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--chat-id", help="Telegram chat id override for group or direct routing")
    parser.add_argument("--thread-id", help="Telegram forum topic id override for group-topic routing")
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
