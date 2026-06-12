#!/usr/bin/env python3
"""Create and update one editable JAIMES-facing Telegram work card."""
from __future__ import annotations

import argparse
import datetime as dt
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
HOME = Path.home()
STATE_PATH = Path(os.environ.get("JAIMES_WORK_CARD_STATE", "memory/jaimes_work_cards.json"))
ACK_STATE_PATH = Path(os.environ.get("JAIMES_FAST_ACK_STATE", str(Path.home() / ".openclaw" / "telegram" / "jaimes_fast_ack_state.json")))
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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def claim_pending_ack(card_key: str) -> str:
    state = load_json_file(ACK_STATE_PATH, {})
    pending = state.get("latest_pending_ack") or {}
    message_id = str(pending.get("message_id") or "")
    if not message_id or pending.get("claimed_by"):
        return ""
    pending["claimed_by"] = card_key
    pending["claimed_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state["latest_pending_ack"] = pending
    save_json_file(ACK_STATE_PATH, state)
    return message_id


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


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


def operator_objective(title: str) -> str:
    text = clean_live_text(title, "Handle the current Telegram task")
    lowered = text.lower()
    if lowered in {"latest telegram task received", "determining objective", "handle latest telegram task"}:
        return "Work out the real objective and start the right check."
    return compact(text, limit=150)


def friendly_model_line(model: str) -> str:
    text = clean_live_text(model)
    lower = text.lower()
    if not text:
        return "JAIMES"
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
        return "refreshing Mission Control and Brain Feed visibility"
    if "update_mission_control.py" in lower:
        return "regenerating Mission Control dashboard data"
    if "ecosystem_health_sweep.py" in lower:
        return "checking Josh 2.0, JAIMES, J.AI.N, and Mission Control health"
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
        return "building Mission Control to catch UI/runtime errors"
    if "python3" in lower and "mission-control/scripts/" in lower:
        script = lower.split("mission-control/scripts/", 1)[1].split()[0].strip("'\"")
        script = script.replace("_", " ").replace(".py", "")
        return f"running the Mission Control {script} helper"
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
    return compact(text, limit=150)


def live_line(item: str) -> str:
    text = clean_live_text(item)
    lower = text.lower()
    if not text:
        return "- waiting: first update"
    if lower.startswith("received"):
        return f"📥 received: {text.removeprefix('Received').strip() or 'task'}"
    if lower.startswith("objective determined:"):
        return f"📌 objective: {text.split(':', 1)[1].strip()}"
    if lower.startswith("model selected:"):
        return f"🤖 model: {text.split(':', 1)[1].strip()}"
    if lower.startswith("skill selected:"):
        return f"🧭 skill: {text.split(':', 1)[1].strip()}"
    if lower.startswith(("running ", "checking ", "tool:")):
        detail = text.split(":", 1)[1].strip() if lower.startswith("tool:") else text
        return f"🔧 step: {simplify_live_detail(detail)}"
    if lower.startswith(("finished ", "completed checking ", "completed ", "done:")):
        detail = text.split(":", 1)[1].strip() if lower.startswith("done:") else text
        return f"✅ done: {simplify_live_detail(detail)}"
    if lower.startswith("final response"):
        return "🏁 final: summary sent"
    if lower.startswith("still working"):
        return "⏳ working: waiting for the current model or tool step to finish"
    commandish = unwrap_shell_command(text).lower()
    if commandish.startswith(("cd ", "python3 ", "openclaw ", "npm ", "hermes ", "launchctl ", "curl ", "git ", "rg ", "sed ", "ssh ", "scp ", "jq ")):
        return f"🔧 step: {describe_shell_command(text)}"
    return f"• {compact(text, limit=150)}"


def plain_progress_text(item: str) -> str:
    text = live_line(item)
    text = re.sub(r"^[^\w]+", "", text).strip()
    text = re.sub(r"^(?:step|done|objective|model|skill|working|received|final):\s*", "", text, flags=re.I).strip()
    return compact(text, limit=150)


def current_step_text(status: str, now: str, live_items: list[str]) -> str:
    if status == "done":
        return "Finished and verified the result."
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
    return [f"- {html.escape(item)}" for item in clean[:limit]]


def plain_bullet_lines(items: list[str], *, fallback: str = "None", limit: int = 10) -> list[str]:
    clean = []
    for item in items:
        text = clean_live_text(item)
        if text and text not in clean:
            clean.append(text)
    if not clean:
        clean = [fallback]
    return [f"- {item}" for item in clean[:limit]]


def live_lines(items: list[str], *, fallback: str = "waiting: first update", limit: int = 10) -> list[str]:
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
    complete = "Yes" if status == "done" else "No"
    complete_title = compact(title, fallback="objective", limit=120)
    complete_detail = f"{complete_title} complete" if complete == "Yes" else f"{complete_title} not complete"

    steps = list(done or [])
    if now:
        steps.append(now)
    steps = [plain_progress_text(step) for step in steps if plain_progress_text(step)]
    if len(steps) < 3:
        steps.append(f"Closed out: {title}")

    issues = [] if is_empty_issue(blocker) else parse_list(blocker) or [blocker]
    next_steps = parse_list(next_step)
    if not next_steps:
        next_steps = ["Approve the next safe step for the issue."] if issues else ["No action needed."]
    approval_needed = next_steps if issues else ["n/a"]
    model_line = model or os.environ.get("JAIMES_WORK_CARD_MODEL") or "JAIMES Telegram task card"

    lines = [
        f"Model: {html.escape(friendly_model_line(model_line))}",
        "",
        "<b>Complete:</b>",
        f"{complete} - {html.escape(complete_detail)}",
        "",
        "<b>What was done:</b>",
        *bullet_lines(steps, fallback=f"Closed out: {title}", limit=5),
        "",
        "<b>Issues:</b>",
        *bullet_lines(issues, fallback="n/a", limit=5),
        "",
        "<b>Appropriate next steps:</b>",
        *bullet_lines(next_steps, fallback="No action needed.", limit=5),
        "",
        "<b>Approval needed:</b>",
        *bullet_lines(approval_needed, fallback="n/a", limit=5),
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
    model_line = model or os.environ.get("JAIMES_WORK_CARD_MODEL") or "JAIMES Telegram task card"
    issues = [] if is_empty_issue(blocker) else parse_list(blocker) or [blocker]
    next_steps = parse_list(next_step) or default_next_steps(status, bool(issues))
    live_items = append_log(done, [now] if now else [])
    card_title = {
        "running": "Live work - in progress",
        "done": "Work complete",
        "failed": "Work needs attention",
        "paused": "Work paused",
    }.get(status, "Live work")
    lines = [
        f"Model: {friendly_model_line(model_line)}",
        "",
        card_title,
        "",
        "Objective:",
        f"- {operator_objective(title)}",
        "",
        "Current step:",
        f"- {current_step_text(status, now, live_items)}",
        "",
        "Done so far:",
        *live_lines(live_items, fallback="waiting: first update", limit=10),
        "",
        "Issues:",
        *plain_bullet_lines(issues, fallback="None", limit=4),
        "",
        "Next:",
        *plain_bullet_lines(next_steps, fallback="No action needed.", limit=4),
        "",
        f"Status: {status_label(status)}",
        f"Running on: {friendly_model_line(model_line)}",
        f"Path: {friendly_route_line(route)}",
        f"Updated: {updated or now_label()}",
    ]
    if eta:
        lines.append(f"ETA: {compact(eta)}")
    return "\n".join(lines)


def api_call(method: str, payload: dict, timeout: int = 15) -> dict:
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


def send_card(text: str, buttons: list | None, timeout: int) -> dict:
    payload = {"chat_id": telegram_target(), "text": text, "disable_notification": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return api_call("sendMessage", payload, timeout=timeout)


def edit_card(message_id: int | str, text: str, buttons: list | None, timeout: int) -> dict:
    payload = {"chat_id": telegram_target(), "message_id": message_id, "text": text}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return api_call("editMessageText", payload, timeout=timeout)


def send_final_summary(text: str, timeout: int, buttons: list | None = None) -> dict:
    payload = {
        "chat_id": telegram_target(),
        "text": text,
        "disable_notification": True,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return api_call("sendMessage", payload, timeout=timeout)


def edit_final_summary(message_id: int | str, text: str, timeout: int, buttons: list | None = None) -> dict:
    payload = {
        "chat_id": telegram_target(),
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return api_call("editMessageText", payload, timeout=timeout)


def edit_objective_message(message_id: int | str, title: str, model: str, timeout: int) -> dict:
    model_line = model or os.environ.get("JAIMES_WORK_CARD_MODEL") or "JAIMES Telegram task card"
    payload = {
        "chat_id": telegram_target(),
        "message_id": message_id,
        "text": (
            f"Model: {friendly_model_line(model_line)}\n\n"
            "Objective:\n"
            f"- {operator_objective(title)}\n\n"
            "Working pattern:\n"
            "- I’ll update one live work card as tools, skills, and decisions happen.\n"
            "- I’ll send one final Complete summary when the work is done."
        ),
    }
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


def upsert_card(args: argparse.Namespace, status: str) -> int:
    state = load_state()
    cards = state.setdefault("cards", {})
    existing = cards.get(args.key, {})
    title = args.title or existing.get("title") or args.key
    new_done = parse_list(args.done)
    done = append_log(existing.get("work_log", existing.get("done", [])), new_done)
    route = args.route or existing.get("route") or ""
    model = args.model or existing.get("model") or ""
    ack_message_id = args.ack_message_id or existing.get("ack_message_id")
    if not ack_message_id and status == "running" and title and title.lower() not in {"latest telegram task received", "determining objective"}:
        ack_message_id = claim_pending_ack(args.key)
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
    final_text = ""
    if status in {"done", "failed"} and args.final_summary and not args.no_final_summary:
        final_text = build_completion_summary(
            title=title,
            status=status,
            model=model,
            now=args.now or "",
            done=done,
            next_step=args.next or "",
            blocker=args.blocker or "None",
        )

    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "text": text, "final_text": final_text, "buttons": buttons, "existing": existing}, indent=2))
        return 0

    card_buttons = buttons if status == "running" else None
    final_buttons = buttons if status in {"done", "failed"} else None

    if existing.get("message_id"):
        result = edit_card(existing["message_id"], text, card_buttons, args.timeout)
        action = "edited"
    else:
        result = send_card(text, card_buttons, args.timeout)
        action = "sent"

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
            final_result = edit_final_summary(final_message_id, final_text, args.timeout)
            final_action = "edited"
            if not final_result.get("ok"):
                final_result = send_final_summary(final_text, args.timeout)
                final_action = "sent"
        else:
            final_result = send_final_summary(final_text, args.timeout)
            final_action = "sent"
        if not final_result.get("ok"):
            print(json.dumps({"ok": False, "action": final_action, "error": final_result.get("error") or final_result}, indent=2), file=sys.stderr)
            return 1
        if final_action == "sent":
            final_message_id = final_result.get("result", {}).get("message_id")

    approval_message_id = existing.get("approval_message_id")
    if final_buttons:
        approval_result = send_card("Approval options:", final_buttons, args.timeout)
        if approval_result.get("ok"):
            approval_message_id = approval_result.get("result", {}).get("message_id")

    if ack_message_id and title and title.lower() not in {"latest telegram task received", "determining objective"}:
        edit_objective_message(ack_message_id, title, model, args.timeout)

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
            Send or edit a JAIMES-facing Telegram work card.
            Example:
              scripts/jaimes_work_card.py start --key mc-fix --title "Mission Control fix" --now "reading files"
              scripts/jaimes_work_card.py update --key mc-fix --now "running tests" --done "patched CSS|py_compile passed"
              scripts/jaimes_work_card.py done --key mc-fix --done "tests passed|pushed main"
            """
        ),
    )
    parser.add_argument("action", choices=["start", "update", "done", "fail", "pause"])
    parser.add_argument("--key", required=True, help="Stable task key, e.g. sorare-lineup-check")
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
    parser.add_argument("--final-summary", action="store_true", help="Opt in to sending a separate final summary from the card helper")
    parser.add_argument("--no-final-summary", action="store_true", help="Deprecated default; card status updates no longer send separate final summaries")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-brain-feed", action="store_true", help="Skip Brain Feed only for dry-runs or ALLOW_NO_BRAIN_FEED=1 maintenance")
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
