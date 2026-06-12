#!/usr/bin/env python3
"""Create and update one editable Josh-facing Telegram work card.

This file is intended to be synced to Josh 2.0's workspace `scripts/` folder.
It uses the direct Bot API lane through `send_josh_reply.py`, which lives next
to this script on Josh 2.0.
"""
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
WORKSPACE = ROOT.parent
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
ACK_STATE_PATH = Path(os.environ.get("JOSH_FAST_ACK_STATE", str(Path.home() / ".openclaw" / "telegram" / "fast_ack_state.json")))
TELEGRAM_COOLDOWN_PATH = Path(os.environ.get("JOSH_TELEGRAM_COOLDOWN_STATE", "memory/josh_telegram_cooldown.json"))
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
    for prefix in ("completed checking ", "completed ", "finished ", "checking ", "running "):
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
        return f"🔧 tool: {simplify_live_detail(detail)}"
    if lower.startswith(("finished ", "completed checking ", "completed ", "done:")):
        detail = text.split(":", 1)[1].strip() if lower.startswith("done:") else text
        return f"✅ done: {simplify_live_detail(detail)}"
    if lower.startswith("final response"):
        return "🏁 final: summary sent"
    if lower.startswith("still working"):
        return "⏳ working: waiting for the current model or tool step to finish"
    return f"• {compact(text, limit=150)}"


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

def live_lines(items: list[str], *, fallback: str = "waiting: first update", limit: int = 12) -> list[str]:
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
    if len(steps) < 3:
        steps.append(f"Closed out: {title}")

    issues = [] if is_empty_issue(blocker) else parse_list(blocker) or [blocker]
    next_steps = parse_list(next_step)
    if not next_steps:
        next_steps = ["Approve the next safe step for the issue."] if issues else ["No action needed."]
    approval_needed = next_steps if issues else ["n/a"]

    lines = [
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
    model_line = model or os.environ.get("JOSH_WORK_CARD_MODEL") or "Josh 2.0 Telegram task card"
    issues = [] if is_empty_issue(blocker) else parse_list(blocker) or [blocker]
    next_steps = parse_list(next_step) or default_next_steps(status, bool(issues))
    live_items = append_log(done, [now] if now else [])
    lines = [
        "Live work",
        "",
        *live_lines(live_items, fallback="waiting: first update", limit=10),
        "",
        "Issues:",
        *plain_bullet_lines(issues, fallback="None", limit=4),
        "",
        "Next:",
        *plain_bullet_lines(next_steps, fallback="No action needed.", limit=4),
        "",
        f"Status: {status_label(status)}",
        f"Using: {clean_live_text(model_line)}",
        f"Route: {clean_live_text(route, 'Josh 2.0 direct chat')}",
        f"Updated: {updated or now_label()}",
    ]
    if eta:
        lines.append(f"ETA: {compact(eta)}")
    return "\n".join(lines)


def api_call(method: str, payload: dict, timeout: int = 15) -> dict:
    if not API_BASE or not TARGET:
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


def send_card(text: str, buttons: list | None, timeout: int) -> dict:
    payload = build_payload(text, buttons, silent=True)
    return api_call("sendMessage", payload, timeout=timeout)


def edit_card(message_id: int | str, text: str, buttons: list | None, timeout: int) -> dict:
    payload = {
        "chat_id": TARGET,
        "message_id": message_id,
        "text": text,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return api_call("editMessageText", payload, timeout=timeout)


def send_final_summary(text: str, timeout: int, buttons: list | None = None) -> dict:
    payload = build_payload(text, buttons, silent=True)
    payload["parse_mode"] = "HTML"
    payload["disable_web_page_preview"] = True
    return api_call("sendMessage", payload, timeout=timeout)


def edit_final_summary(message_id: int | str, text: str, timeout: int, buttons: list | None = None) -> dict:
    payload = {
        "chat_id": TARGET,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return api_call("editMessageText", payload, timeout=timeout)


def edit_objective_message(message_id: int | str, title: str, model: str, timeout: int) -> dict:
    model_line = compact(model or "Auto route selecting best fit", limit=180)
    payload = {
        "chat_id": TARGET,
        "message_id": message_id,
        "text": f"Objective: {compact(title, limit=180)}\nModel: {model_line}",
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
    if status in {"done", "failed"}:
        if not args.no_final_summary:
            final_text = build_completion_summary(
                title=title,
                status=status,
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
    parser.add_argument("--no-final-summary", action="store_true", help="Update the card status without sending a separate final summary")
    parser.add_argument("--timeout", type=int, default=15)
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
