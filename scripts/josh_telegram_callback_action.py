#!/usr/bin/env python3
"""Handle common Josh 2.0 Telegram inline-button actions.

This script is intended to be called by OpenCLAW/agent callback handling when a
button tap arrives. It does not poll Telegram itself, avoiding getUpdates
conflicts with the active OpenCLAW Telegram channel.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
JOSHEX_HANDOFF_PROJECT = "Handoffs from Josh 2.0 and JAIMES"
JOSHEX_HANDOFF_THREAD = "codex://threads/019de576-8f21-7ba1-b477-623a5b9d0068"
APPROVAL_ACTIONS_PATH = WORKSPACE / "memory" / "telegram_approval_actions.json"
if str(WORKSPACE / "scripts") not in sys.path:
    sys.path.insert(0, str(WORKSPACE / "scripts"))

try:
    from send_josh_reply import send_message  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001 - local dry-runs can run away from Josh's Bot API helper
    def send_message(text: str, buttons: list | None = None, dedupe_key: str | None = None, force: bool = False) -> bool:
        print(text)
        if buttons:
            print(buttons)
        return False

BUTTONS = [
    [{"text": "Review with Gemini", "callback_data": "model:gemini_flash"}],
    [{"text": "Send to JAIMES", "callback_data": "route:jaimes"}],
    [{"text": "Ask agent council", "callback_data": "route:agent_council"}],
    [{"text": "Run on Josh 2.0", "callback_data": "model:codex"}],
    [{"text": "Send to JOSHeX Cloud", "callback_data": "route:joshex_cloud"}],
    [{"text": "Send to JOSHeX Mac", "callback_data": "route:joshex"}],
    [{"text": "Sync Control Tower", "callback_data": "next:check_mission_control"}],
    [{"text": "Send daily digest", "callback_data": "next:daily_digest"}],
    [{"text": "Run health sweep", "callback_data": "next:run_health_sweep"}],
    [{"text": "Show model choices", "callback_data": "next:show_models"}],
    [{"text": "Hold / no action", "callback_data": "next:hold"}],
]

PUBLIC_CONTEXT_BUTTONS = [
    [{"text": "Review with Gemini", "callback_data": "model:gemini_flash"}],
    [{"text": "Send to JAIMES", "callback_data": "route:jaimes"}],
    [{"text": "Run on Josh 2.0", "callback_data": "model:codex"}],
    [{"text": "Check with Grok", "callback_data": "model:grok"}],
    [{"text": "Hold / no action", "callback_data": "next:hold"}],
]


def run_text(cmd: list[str], timeout: int = 45) -> str:
    try:
        proc = subprocess.run(cmd, cwd=WORKSPACE, text=True, capture_output=True, timeout=timeout)
        text = (proc.stdout or proc.stderr or "").strip()
        return "\n".join(text.splitlines()[:16])
    except Exception as exc:  # noqa: BLE001 - user-facing callback should not crash noisily
        return f"error: {exc}"


def publish_selection(title: str, detail: str, status: str = "active", dry_run: bool = False) -> None:
    if dry_run:
        return
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_publish.py"),
        "--agent",
        "josh",
        "--type",
        "status",
        "--status",
        status,
        "--title",
        compact(title, 150),
        "--tool",
        "telegram selection",
        "--detail",
        compact(detail, 400),
        "--privacy",
        "dashboard-safe",
        "--brain-feed",
    ]
    subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12, check=False)


def compact(value: str, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def latest_work_card_title() -> str:
    cards_path = WORKSPACE / "memory" / "josh_work_cards.json"
    try:
        data = json.loads(cards_path.read_text())
        cards = [
            card for card in data.get("cards", {}).values()
            if isinstance(card, dict) and card.get("updated_at")
        ]
        cards.sort(key=lambda card: str(card.get("updated_at", "")), reverse=True)
        for card in cards:
            title = str(card.get("title") or "").strip()
            if title and title.lower() != "latest telegram task received":
                return title
    except Exception:
        pass
    return "JOSHeX private-account handoff from Josh 2.0 Telegram"


def load_approval_action(action: str) -> dict:
    try:
        data = json.loads(APPROVAL_ACTIONS_PATH.read_text(encoding="utf-8"))
        item = data.get(action) if isinstance(data, dict) else None
        return item if isinstance(item, dict) else {}
    except Exception:
        return {}


def create_approved_mitigation(info: dict) -> tuple[str, str]:
    step = compact(str(info.get("step") or ""), 300)
    source_objective = compact(str(info.get("objective") or latest_work_card_title()), 180)
    owner = str(info.get("agent") or "josh").lower()
    if owner not in {"josh", "jaimes", "jain", "joshex"}:
        owner = "josh"
    title = compact(f"Approved mitigation: {step}", 120)
    objective = (
        f"User approved this mitigation from the Josh 2.0 Telegram final summary: {step}. "
        f"Source objective: {source_objective}. Execute if it is safe and dashboard-safe; report done/blocked back through Telegram and Brain Feed."
    )
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_task.py"),
        "create",
        "--owner", owner,
        "--requester", "josh",
        "--title", title,
        "--objective", objective,
        "--priority", "normal",
        "--privacy", "dashboard-safe",
        "--approval", "approved",
        "--capability", "approved-mitigation",
        "--note", "Created by a Josh 2.0 Telegram approval button below a completed work summary.",
        "--brain-feed",
        "--job",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=45)
    output = proc.stdout or proc.stderr or ""
    if proc.returncode != 0:
        raise RuntimeError(compact(output, 500) or f"agent_task.py failed: {proc.returncode}")
    payload = json.loads(output)
    task = payload.get("task", {})
    return str(task.get("id") or "task-created"), str(task.get("title") or title)


def create_jaimes_handoff() -> tuple[str, str]:
    title = compact(latest_work_card_title(), 90)
    if "JAIMES" not in title:
        title = compact(f"JAIMES handoff: {title}", 120)
    objective = (
        "Josh 2.0 user tapped the JAIMES workhorse route. "
        "JAIMES should handle the headless/background work where practical, "
        "use Gemini for dashboard-safe first-pass review when efficient, "
        "keep Telegram and Brain Feed status visible, and report done/blocked results back to Josh 2.0."
    )
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_task.py"),
        "create",
        "--owner", "jaimes",
        "--requester", "josh",
        "--title", title,
        "--objective", objective,
        "--priority", "normal",
        "--privacy", "dashboard-safe",
        "--approval", "approved",
        "--capability", "hermes-background-work",
        "--capability", "headless-agent-workhorse",
        "--capability", "cross-agent-coordination",
        "--note", "Created by Josh 2.0 Telegram route button. Keep updates dashboard-safe and visible in Telegram plus Brain Feed.",
        "--brain-feed",
        "--job",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=45)
    output = proc.stdout or proc.stderr or ""
    if proc.returncode != 0:
        raise RuntimeError(compact(output, 500) or f"agent_task.py failed: {proc.returncode}")
    payload = json.loads(output)
    task = payload.get("task", {})
    return str(task.get("id") or "task-created"), str(task.get("title") or title)


def create_agent_council_handoff() -> tuple[str, str]:
    title = compact(latest_work_card_title(), 90)
    if "Agent council" not in title:
        title = compact(f"Agent council: {title}", 120)
    objective = (
        "Josh 2.0 user tapped the Agent council route. "
        "JAIMES should coordinate a dashboard-safe second opinion across Gemini first-pass review and J.AI.N worker context only when useful, "
        "avoid bot loops with max 3 hops and dedupe, then return one concise recommendation to Josh 2.0 Telegram and Brain Feed."
    )
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_task.py"),
        "create",
        "--owner", "jaimes",
        "--requester", "josh",
        "--title", title,
        "--objective", objective,
        "--priority", "normal",
        "--privacy", "dashboard-safe",
        "--approval", "approved",
        "--capability", "agent-council",
        "--capability", "bot-to-bot-guarded",
        "--capability", "gemini-first-review",
        "--note", "Created by Josh 2.0 Telegram agent-council button. Use task queue plus Brain Feed as durable record; do not bounce bots in loops.",
        "--brain-feed",
        "--job",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=45)
    output = proc.stdout or proc.stderr or ""
    if proc.returncode != 0:
        raise RuntimeError(compact(output, 500) or f"agent_task.py failed: {proc.returncode}")
    payload = json.loads(output)
    task = payload.get("task", {})
    return str(task.get("id") or "task-created"), str(task.get("title") or title)


def create_joshex_handoff() -> tuple[str, str]:
    title = compact(latest_work_card_title(), 90)
    if "JOSHeX" not in title:
        title = compact(f"JOSHeX handoff: {title}", 120)
    objective = (
        "Josh 2.0 user tapped the JOSHeX/private-accounts route. "
        "JOSHeX should pick up the sanitized handoff from Control Tower, "
        "handle personal-laptop/private-account work when appropriate, and "
        "report a dashboard-safe done/blocked result back to Josh 2.0."
    )
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_task.py"),
        "create",
        "--owner", "joshex",
        "--requester", "josh",
        "--title", title,
        "--objective", objective,
        "--priority", "normal",
        "--privacy", "sensitive-account",
        "--approval", "approved",
        "--capability", "private-account-access",
        "--capability", "personal-laptop-codex",
        "--capability", "cross-agent-coordination",
        "--artifact", JOSHEX_HANDOFF_THREAD,
        "--artifact", f"project:{JOSHEX_HANDOFF_PROJECT}",
        "--note", f"Created by Josh 2.0 Telegram route button for {JOSHEX_HANDOFF_PROJECT}. Do not expose private account data to Josh 2.0; return dashboard-safe status only.",
        "--brain-feed",
        "--job",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=45)
    output = proc.stdout or proc.stderr or ""
    if proc.returncode != 0:
        raise RuntimeError(compact(output, 500) or f"agent_task.py failed: {proc.returncode}")
    payload = json.loads(output)
    task = payload.get("task", {})
    return str(task.get("id") or "task-created"), str(task.get("title") or title)


def create_joshex_cloud_handoff() -> tuple[str, str]:
    title = compact(latest_work_card_title(), 90)
    if "JOSHeX" not in title:
        title = compact(f"JOSHeX cloud handoff: {title}", 120)
    objective = (
        "Josh 2.0 user tapped the JOSHeX Cloud/repo-safe route. "
        "JOSHeX should treat this as repo-safe work suitable for Codex Cloud when the task only needs GitHub/repository context. "
        "If private account, browser, keychain, local desktop, token, cookie, or other sensitive context is needed, stop and route back to local JOSHeX."
    )
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_task.py"),
        "create",
        "--owner", "joshex",
        "--requester", "josh",
        "--title", title,
        "--objective", objective,
        "--priority", "normal",
        "--privacy", "dashboard-safe",
        "--approval", "approved",
        "--capability", "repo-safe",
        "--capability", "codex-cloud",
        "--capability", "github-repo-work",
        "--artifact", JOSHEX_HANDOFF_THREAD,
        "--artifact", f"project:{JOSHEX_HANDOFF_PROJECT}",
        "--artifact", "cloud_candidate:true",
        "--note", f"Created by Josh 2.0 Telegram cloud route button for {JOSHEX_HANDOFF_PROJECT}. Repo/cloud-safe only; use local JOSHeX for private accounts or local device context.",
        "--brain-feed",
        "--job",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=45)
    output = proc.stdout or proc.stderr or ""
    if proc.returncode != 0:
        raise RuntimeError(compact(output, 500) or f"agent_task.py failed: {proc.returncode}")
    payload = json.loads(output)
    task = payload.get("task", {})
    return str(task.get("id") or "task-created"), str(task.get("title") or title)


def bullet_card(*, status: str, objective: str, now: str, done: str, blocker: str = "None", next_step: str = "Tap a button or send the next task.") -> str:
    return "\n".join(
        [
            "Codex 5.5 (openai-codex subscription)",
            "",
            "- Route: Josh 2.0 Telegram callback handler.",
            f"- Objective: {objective}",
            f"- Status: {status}",
            f"- Now: {now}",
            f"- Done: {done}",
            f"- Blocker: {blocker}",
            f"- Next: {next_step}",
        ]
    )


def handle(action: str, dry_run: bool = False) -> tuple[str, list | None]:
    if action.startswith("approve:"):
        info = load_approval_action(action)
        if not info:
            return bullet_card(
                status="blocked",
                objective="Approve mitigation step.",
                now="The approval button was tapped, but the stored step was not found.",
                done="No mitigation task was queued.",
                blocker="Approval action state is missing or expired.",
                next_step="Send the mitigation text directly if you still want it executed.",
            ), BUTTONS
        step = compact(str(info.get("step") or "approved mitigation"), 180)
        if dry_run:
            return bullet_card(
                status="would queue",
                objective="Approve mitigation step.",
                now=f"Dry-run: would queue approved mitigation: {step}",
                done="No task queued during dry-run.",
                next_step="A live tap creates a durable approved mitigation task.",
            ), BUTTONS
        try:
            task_id, title = create_approved_mitigation(info)
            publish_selection(
                "Telegram approval captured",
                f"Approved mitigation button queued task {task_id}: {step}",
                status="active",
                dry_run=False,
            )
            return bullet_card(
                status="queued",
                objective="Approve mitigation step.",
                now=f"Approval captured: {step}",
                done=f"Task queued: {task_id} - {compact(title, 120)}.",
                next_step="The assigned agent should execute the approved mitigation and report done/blocked.",
            ), None
        except Exception as exc:  # noqa: BLE001
            return bullet_card(
                status="blocked",
                objective="Approve mitigation step.",
                now=f"Approval captured, but task queue failed for: {step}",
                done="No mitigation task was queued.",
                blocker=compact(str(exc), 220),
                next_step="Send the mitigation text directly or retry after Control Tower task queue is healthy.",
            ), BUTTONS

    if action == "model:gemini_flash":
        publish_selection(
            "Telegram selection: Gemini review",
            "Josh 2.0 Telegram button selected Gemini for dashboard-safe first-pass review. Codex remains responsible for execution and private/sensitive work.",
            dry_run=dry_run,
        )
        return bullet_card(
            status="selected",
            objective="Use Gemini 3 Flash as the first-pass model.",
            now="Selected Gemini review. Use this for dashboard-safe review, summaries, long-context reads, log analysis, planning, and second opinions.",
            done="Button tap captured; Brain Feed updated; Codex stays responsible for execution and private/sensitive work.",
            next_step="Send the task, or tap Use Josh 2.0 Mac tools if this needs repo edits, auth, device actions, or approvals.",
        ), PUBLIC_CONTEXT_BUTTONS

    if action == "model:grok":
        publish_selection(
            "Telegram selection: Grok public context",
            "Josh 2.0 Telegram button selected Grok only for public/X/current-events context.",
            dry_run=dry_run,
        )
        return bullet_card(
            status="selected",
            objective="Use Grok only when public/X/current-events context is central.",
            now="Selected Grok context lane. Use it for X-native context, breaking news, public sentiment, market/social narrative, and public web interpretation.",
            done="Button tap captured; Brain Feed updated; Gemini remains the broad first-pass default.",
            next_step="Use this route only when the task depends on current public context or X/social signal.",
        ), PUBLIC_CONTEXT_BUTTONS

    if action == "model:codex":
        publish_selection(
            "Telegram selection: Josh 2.0 device execution",
            "Josh 2.0 Telegram button selected local Josh 2.0 Mac tools for execution, auth, device actions, repo work, or approvals.",
            dry_run=dry_run,
        )
        return bullet_card(
            status="selected",
            objective="Use Codex/GPT-5.5 for execution.",
            now="Selected Josh 2.0 device execution. Use this for repo edits, private connectors, secrets/auth, terminal/device actions, approvals, and final integration.",
            done="Button tap captured; Brain Feed updated; Josh 2.0 owns this lane.",
            next_step="Send the exact execution objective, or tap Send to JAIMES / Hermes for headless background work.",
        ), BUTTONS

    if action in {"next:daily_digest", "next:overview"}:
        kind = "daily" if action == "next:daily_digest" else "overview"
        cmd = ["python3", "mission-control/scripts/josh_telegram_digest.py", kind]
        if dry_run:
            cmd.append("--dry-run")
        text = run_text(cmd, timeout=45)
        if dry_run and '"rich_html":' in text:
            return bullet_card(
                status="ready",
                objective=f"Prepare {kind} digest.",
                now="Rich digest helper is available.",
                done=f"`python3 mission-control/scripts/josh_telegram_digest.py {kind}` dry-run returned native rich HTML and fallback text.",
            ), BUTTONS
        if '"ok": true' in text.lower():
            native = "native rich table" if '"native_rich_message": true' in text.lower() else "fallback table"
            return bullet_card(
                status="sent",
                objective=f"Send {kind} digest.",
                now="Digest button handled.",
                done=f"Sent {kind} digest using {native}.",
                next_step="Tap another action or send a task.",
            ), BUTTONS
        return text, BUTTONS

    if action == "next:show_models":
        models = run_text(["openclaw", "models", "list"], timeout=30)
        model_summary = "; ".join(line.strip() for line in models.splitlines()[1:6] if line.strip())
        return bullet_card(
            status="ready",
            objective="Show model routing.",
            now="Read OpenCLAW model list.",
            done=model_summary or "Model list checked.",
            next_step="Use /models for the full view or tap another action.",
        ), BUTTONS

    if action == "next:run_health_sweep":
        result = run_text([
            "python3",
            "mission-control/scripts/ecosystem_health_sweep.py",
            "--brain-feed",
            "--job",
            "--telegram-summary",
        ], timeout=120)
        return bullet_card(
            status="done" if '"ok": true' in result.lower() else "attention",
            objective="Run the ecosystem health sweep.",
            now="Checked agent hosts, gateway health, Telegram, model auth, jobs, and Control Tower freshness.",
            done=result.splitlines()[-1] if result else "Health sweep command ran.",
            next_step="Open Control Tower if any row reports attention.",
        ), BUTTONS

    if action.startswith("agent:"):
        agent = action.split(":", 1)[1]
        if agent in {"josh", "josh2", "jaimes", "jain", "joshex"}:
            cmd = ["python3", "mission-control/scripts/josh_agent_quick_card.py", agent]
            if dry_run:
                cmd.append("--dry-run")
            text = run_text(cmd, timeout=45)
            if dry_run and '"rich_html":' in text:
                return bullet_card(
                    status="ready",
                    objective=f"Prepare {agent} quick card.",
                    now="Rich agent-card helper is available.",
                    done=f"`python3 mission-control/scripts/josh_agent_quick_card.py {agent}` dry-run returned native rich HTML and fallback text.",
                ), BUTTONS
            if '"ok": true' in text.lower():
                native = "native rich card" if '"native_rich_message": true' in text.lower() else "plain fallback card"
                return bullet_card(
                    status="sent",
                    objective=f"Send {agent} quick card.",
                    now="Agent button handled.",
                    done=f"Sent {agent} quick card using {native}.",
                    next_step="Tap another agent, sync Control Tower, or send a task.",
                ), BUTTONS
            return text, BUTTONS

    if action == "next:check_mission_control":
        result = run_text(["python3", "mission-control/scripts/update_mission_control.py"], timeout=90)
        return bullet_card(
            status="done",
            objective="Refresh Control Tower.",
            now="Dashboard data regenerated.",
            done=result.splitlines()[-1] if result else "Control Tower update command ran.",
            next_step="Check the kiosk/dashboard or tap another action.",
        ), BUTTONS

    if action == "route:joshex":
        if dry_run:
            return bullet_card(
                status="would queue",
                objective="Send work to JOSHeX / private accounts.",
                now="Dry-run: this callback would create an approved JOSHeX-owned sensitive-account handoff task.",
                done="No task queued during dry-run.",
                next_step="A live button tap will create the task, publish Control Tower visibility, and ask JOSHeX to report back dashboard-safe results.",
            ), BUTTONS
        try:
            task_id, title = create_joshex_handoff()
            return bullet_card(
                status="queued",
                objective="Send work to JOSHeX / private accounts.",
                now="Created a durable JOSHeX-owned task in Control Tower. JOSHeX is the personal-laptop Codex lane, not the Josh 2.0 Mac.",
                done=f"Task queued: {task_id} - {compact(title, 120)}.",
                next_step="JOSHeX should handle private-account work and report a dashboard-safe done/blocked result back to Josh 2.0.",
            ), BUTTONS
        except Exception as exc:  # noqa: BLE001 - route button should report failure cleanly
            return bullet_card(
                status="blocked",
                objective="Send work to JOSHeX / private accounts.",
                now="Tried to create a durable JOSHeX-owned task.",
                done="No JOSHeX task was queued.",
                blocker=compact(str(exc), 220),
                next_step="Send the task here directly or try again after Control Tower task queue is healthy.",
            ), BUTTONS

    if action == "route:joshex_cloud":
        if dry_run:
            return bullet_card(
                status="would queue",
                objective="Send repo-safe work to JOSHeX Cloud.",
                now="Dry-run: this callback would create an approved JOSHeX-owned dashboard-safe handoff marked cloud_candidate.",
                done="No task queued during dry-run.",
                next_step="A live button tap will create the task; the JOSHeX watcher will submit to Codex Cloud when a cloud environment id is configured, otherwise it will prompt the pinned project locally.",
            ), BUTTONS
        try:
            task_id, title = create_joshex_cloud_handoff()
            return bullet_card(
                status="queued",
                objective="Send repo-safe work to JOSHeX Cloud.",
                now="Created a durable JOSHeX-owned dashboard-safe task marked cloud_candidate. JOSHeX Cloud is for repo/GitHub work only.",
                done=f"Task queued: {task_id} - {compact(title, 120)}.",
                next_step="JOSHeX will use Codex Cloud if configured; otherwise the pinned handoff project receives it for local pickup.",
            ), BUTTONS
        except Exception as exc:  # noqa: BLE001 - route button should report failure cleanly
            return bullet_card(
                status="blocked",
                objective="Send repo-safe work to JOSHeX Cloud.",
                now="Tried to create a durable JOSHeX-owned cloud-candidate task.",
                done="No JOSHeX Cloud task was queued.",
                blocker=compact(str(exc), 220),
                next_step="Use local JOSHeX/private-account route or try again after Control Tower task queue is healthy.",
            ), BUTTONS

    if action == "route:jaimes":
        if dry_run:
            return bullet_card(
                status="would queue",
                objective="Route work to JAIMES.",
                now="Dry-run: this callback would create an approved JAIMES-owned dashboard-safe handoff task.",
                done="No JAIMES task queued during dry-run.",
                next_step="A live button tap will queue the work for JAIMES/Hermes and publish Telegram plus Brain Feed visibility.",
            ), BUTTONS
        try:
            task_id, title = create_jaimes_handoff()
            return bullet_card(
                status="queued",
                objective="Route work to JAIMES.",
                now="Created a durable JAIMES-owned task. Target: JAIMES / Hermes; Gemini can be used first for safe review; Grok only if public/X context is central.",
                done=f"Task queued: {task_id} - {compact(title, 120)}.",
                next_step="JAIMES should work headlessly and report a dashboard-safe done/blocked result back through Telegram and Brain Feed.",
            ), BUTTONS
        except Exception as exc:  # noqa: BLE001 - route button should report failure cleanly
            publish_selection(
                "Telegram selection blocked: JAIMES route",
                f"JAIMES route button failed to queue a task: {compact(str(exc), 220)}",
                status="error",
                dry_run=False,
            )
            return bullet_card(
                status="blocked",
                objective="Route work to JAIMES.",
                now="Tried to create a durable JAIMES-owned task.",
                done="No JAIMES task was queued.",
                blocker=compact(str(exc), 220),
                next_step="Send the task details directly or try again after Control Tower task queue is healthy.",
            ), BUTTONS

    if action == "route:agent_council":
        if dry_run:
            return bullet_card(
                status="would queue",
                objective="Ask the agent council.",
                now="Dry-run: this callback would create a guarded JAIMES-owned agent-council task with Gemini/J.AI.N support when useful.",
                done="No council task queued during dry-run.",
                next_step="A live button tap will queue one coordinated council pass and require one concise return response.",
            ), BUTTONS
        try:
            task_id, title = create_agent_council_handoff()
            return bullet_card(
                status="queued",
                objective="Ask the agent council.",
                now="Created a guarded JAIMES-owned council task. JAIMES coordinates the answer; Gemini and J.AI.N are used only when useful.",
                done=f"Task queued: {task_id} - {compact(title, 120)}.",
                next_step="JAIMES should return one dashboard-safe recommendation to Josh 2.0 Telegram and Brain Feed.",
            ), BUTTONS
        except Exception as exc:  # noqa: BLE001
            publish_selection(
                "Telegram selection blocked: Agent council",
                f"Agent council button failed to queue a task: {compact(str(exc), 220)}",
                status="error",
                dry_run=False,
            )
            return bullet_card(
                status="blocked",
                objective="Ask the agent council.",
                now="Tried to create a guarded JAIMES-owned council task.",
                done="No agent-council task was queued.",
                blocker=compact(str(exc), 220),
                next_step="Send the task to JAIMES directly or try again after Control Tower task queue is healthy.",
            ), BUTTONS

    if action == "next:hold":
        return bullet_card(
            status="paused",
            objective="Hold current action.",
            now="No further action will run from this button.",
            done="Hold acknowledged.",
            next_step="Send a new command when ready.",
        ), None

    return bullet_card(
        status="needs approval",
        objective="Handle Telegram button tap.",
        now=f"Unknown action `{action}`.",
        done="No action executed.",
        blocker="Callback action is not mapped yet.",
        next_step="Send a normal message with what you want Josh 2.0 to do.",
    ), BUTTONS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    text, buttons = handle(args.action, dry_run=args.dry_run)
    if args.dry_run:
        print(text)
        return 0
    ok = send_message(text, buttons, dedupe_key=f"callback-action:{args.action}", force=True)
    print("ok" if ok else "fail")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
