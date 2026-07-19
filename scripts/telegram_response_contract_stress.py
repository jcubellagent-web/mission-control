#!/usr/bin/env python3
"""Render and optionally transport-test the primary Telegram response contract."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path


LABELS = (
    "Model:",
    "Complete:",
    "What was done:",
    "Issues:",
    "Appropriate next steps:",
    "Approval needed:",
)
CARD_WIDTH = 38
PRODUCTION_CHAT_ID = "-1003589561528"
PRODUCTION_INBOX_THREAD_ID = "1"
PRODUCTION_JAIMES_THREAD_ID = "17"
MAX_CLEANUP_RETRY_WAIT_SECONDS = 60.0
MILESTONE_UPDATES = (
    "Received Telegram task",
    "Objective and runbook confirmed",
    "Route selected: Inbox QA system",
    "Worker started transport verification",
    "Verification test passed",
    "Final response delivered",
)
CANARY_JOURNAL_ENV = "TELEGRAM_CANARY_CLEANUP_JOURNAL"

STATUS_ONLY_PATTERNS = (
    re.compile(r"\b(?:assessment|task|work|review|objective|request)\s+(?:is\s+)?(?:complete|completed|done|finished|closed)\b", re.I),
    re.compile(r"\bcompleted\s+(?:the\s+)?requested\s+(?:task|work|review|assessment)\b", re.I),
    re.compile(r"\b(?:checked|reviewed)\s+(?:the\s+)?(?:request|task|objective)\b", re.I),
    re.compile(r"\bverified\s+(?:the\s+)?(?:worker|runtime|agent|execution)(?:\s+(?:state|status))?\b", re.I),
    re.compile(r"\b(?:result|summary|final(?:\s+(?:answer|response))?)\s+(?:was\s+)?(?:prepared|delivered|sent|posted)\b", re.I),
    re.compile(r"\b(?:prepared|delivered|sent|posted)\s+(?:the\s+)?(?:result|summary|final(?:\s+(?:answer|response))?)\b", re.I),
    re.compile(r"\b(?:closed|completed)\s+(?:the\s+)?(?:task\s+)?lifecycle\b", re.I),
    re.compile(r"\bagent work reached final review\b", re.I),
    re.compile(r"\blive card ordering (?:was )?preserved\b", re.I),
    re.compile(r"\bresponse formatting (?:was )?recovered\b", re.I),
)
CONCRETE_RESULT_PATTERN = re.compile(
    r"\b(?:added|changed|confirmed|created|determined|differ(?:s|ed|ent)?|caus(?:e|es|ed)|"
    r"repair(?:s|ed)?|(?:en|dis)abl(?:e|es|ed|ing)|failed|fixed|found|healthy|"
    r"identified|implemented|passed|rejected|removed|reproduced|resolved|restored|returned|supports?|"
    r"unsupported|updated|reconcil(?:e|es|ed)|retir(?:e|es|ed)|replac(?:e|es|ed)|"
    r"rerout(?:e|es|ed)|mov(?:e|es|ed)|prevent(?:s|ed)?|preserv(?:e|es|ed)|"
    r"verified\s+(?:that|correctly|successfully|\d+)|cannot|can['’]?t|does not|did not|risk|limitation|"
    r"recommend(?:ed|ation)?|\d+\s+(?:tests?|checks?|cases?))\b",
    re.I,
)
RISK_OR_LIMITATION_PATTERN = re.compile(
    r"\b(?:risk|limitation|cannot|can['’]?t|could not|does not|did not|unsupported|blocked|failed|"
    r"failure|unable|do not|don['’]?t|avoid)\b",
    re.I,
)
RECOMMENDATION_PATTERN = re.compile(
    r"\b(?:recommend(?:ed|ation)?|should|must|next step|follow[- ]?up|do not|don['’]?t|avoid|"
    r"needs? to|requires?)\b",
    re.I,
)
UNVERIFIED_HEADER_PATTERN = re.compile(
    r"(?:\b(?:unverified|unknown|unset|not verified)\b|^(?:n/?a|none)$)",
    re.I,
)


def canary_journal_path() -> Path | None:
    value = os.environ.get(CANARY_JOURNAL_ENV, "").strip()
    return Path(value).expanduser() if value else None


def write_canary_journal(
    *,
    stage: str,
    chat_id: str,
    thread_id: str,
    message_ids: list[str],
    indeterminate_stages: list[str],
) -> None:
    path = canary_journal_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    payload = {
        "version": 1,
        "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "chatId": str(chat_id),
        "threadId": str(thread_id),
        "stage": str(stage)[:80],
        "messageIds": list(dict.fromkeys(str(value) for value in message_ids if str(value).isdigit())),
        "indeterminateStages": list(dict.fromkeys(str(value)[:80] for value in indeterminate_stages)),
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def finalize_canary_journal(cleanup: dict, chat_id: str, thread_id: str) -> None:
    path = canary_journal_path()
    if path is None:
        return
    failed_ids = [str(value) for value in cleanup.get("failedIds") or [] if str(value).isdigit()]
    unknown = [str(value) for value in cleanup.get("indeterminateStages") or []]
    if failed_ids or unknown:
        write_canary_journal(
            stage="cleanup-pending",
            chat_id=chat_id,
            thread_id=thread_id,
            message_ids=failed_ids,
            indeterminate_stages=unknown,
        )
    else:
        path.unlink(missing_ok=True)


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("telegram_work_card_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pre_text(text: str) -> str:
    return html.unescape(re.sub(r"^\s*<pre>|</pre>\s*$", "", text, flags=re.I))


def _section_body(plain: str, start_label: str, end_label: str | None) -> str:
    start = plain.find(start_label)
    if start < 0:
        return ""
    start += len(start_label)
    end = plain.find(end_label, start) if end_label else len(plain)
    if end < 0:
        end = len(plain)
    return plain[start:end].strip()


def _bullet_items(block: str) -> list[str]:
    items: list[str] = []
    for raw_line in block.splitlines():
        if not raw_line.strip():
            continue
        if raw_line.startswith("- "):
            items.append(raw_line[2:].strip())
        elif raw_line.startswith("  ") and items:
            items[-1] = f"{items[-1]} {raw_line.strip()}".strip()
        else:
            return []
    return items


def _normalized_item(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _status_only(value: str) -> bool:
    text = value.strip()
    return not text or any(pattern.search(text) for pattern in STATUS_ONLY_PATTERNS)


def _substantive(value: str) -> bool:
    text = value.strip()
    return (
        len(text) >= 18
        and len(text.split()) >= 4
        and not _status_only(text)
        and not re.fullmatch(r"(?:n/?a|none|no action needed)\.?", text, flags=re.I)
    )


def _no_issue(value: str) -> bool:
    return bool(re.fullmatch(r"(?:-\s*)?(?:n/?a|none)\.?", value.strip(), flags=re.I))


def _no_action(value: str) -> bool:
    return bool(re.fullmatch(r"(?:-\s*)?no action needed\.?", value.strip(), flags=re.I))


def validate(text: str, module=None) -> list[str]:
    problems: list[str] = []
    if not re.fullmatch(r"\s*<pre>[\s\S]*</pre>\s*", text, flags=re.I):
        problems.append("final summary must be one Telegram HTML pre block")
    plain = pre_text(text)
    positions = [plain.find(label) for label in LABELS]
    if any(pos < 0 for pos in positions):
        problems.append("one or more final-summary labels are missing")
    elif positions != sorted(positions):
        problems.append("final-summary labels are out of order")
    for forbidden in ("<b>", "</b>", "**", "Objective Complete:", "TLDR:", "Challenges/Blockers:", "•"):
        if forbidden in text:
            problems.append(f"forbidden formatting remains: {forbidden}")
    approval = _section_body(plain, "Approval needed:", None)
    if not approval:
        problems.append("Approval needed must be n/a or contain at least one bullet")
    elif not _no_issue(approval) and not _bullet_items(approval):
        problems.append("Approval needed must be n/a or contain at least one bullet")
    width = module.display_width if module and hasattr(module, "display_width") else len
    if max((width(line) for line in plain.splitlines()), default=0) > CARD_WIDTH:
        problems.append(f"a rendered line exceeds {CARD_WIDTH} display columns")
    if not plain.startswith("Model:"):
        problems.append("verified model/route disclosure is not the first line")
    if not re.search(r"^Complete: (?:Yes|No)\b", plain, flags=re.M):
        problems.append("Complete must start with Yes or No")
    complete_at = plain.find("Complete:")
    header = " ".join(
        line.strip()
        for line in plain[:complete_at].splitlines()
        if line.strip()
    )
    header_match = re.fullmatch(
        r"Model:\s*([^|]+?)\s*\|\s*Route:\s*([^|]+?)\s*\|\s*Why:\s*([^|]+)",
        header,
        flags=re.I,
    )
    if not header_match:
        problems.append("final summary header must contain exactly one Model, Route, and Why field")
    done_start = plain.find("What was done:")
    issues_start = plain.find("Issues:")
    done_items: list[str] = []
    if done_start >= 0 and issues_start > done_start:
        done_items = _bullet_items(_section_body(plain, "What was done:", "Issues:"))
        if not 3 <= len(done_items) <= 5:
            problems.append("What was done must contain 3-5 bullets")
    complete_yes = bool(re.search(r"^Complete: Yes\b", plain, flags=re.M))
    issues = _section_body(plain, "Issues:", "Appropriate next steps:")
    next_steps = _section_body(plain, "Appropriate next steps:", "Approval needed:")
    if complete_yes:
        if not header_match or any(UNVERIFIED_HEADER_PATTERN.search(value.strip()) for value in header_match.groups()):
            problems.append("Complete Yes requires verified model, route, and why values")
        normalized = [_normalized_item(item) for item in done_items]
        substantive = [item for item in done_items if _substantive(item)]
        if len(set(normalized)) < 3 or len(substantive) < 3:
            problems.append("Complete Yes requires at least 3 unique substantive findings")
        if any(_status_only(item) for item in done_items):
            problems.append("Complete Yes cannot use status or process filler as findings")
        if sum(bool(CONCRETE_RESULT_PATTERN.search(item)) for item in done_items) < 2:
            problems.append("Complete Yes requires at least 2 concrete findings or outcomes")
    result_text = " ".join(done_items)
    if _no_issue(issues) and RISK_OR_LIMITATION_PATTERN.search(f"{result_text} {next_steps}"):
        problems.append("risk or limitation requires a substantive Issues entry")
    if _no_action(next_steps) and (
        not _no_issue(issues)
        or RECOMMENDATION_PATTERN.search(result_text)
    ):
        problems.append("No action needed conflicts with issues or recommendations")
    return problems


def message_id(result: dict) -> str:
    return str((result.get("result") or {}).get("message_id") or "")


def delivery_is_indeterminate(module, result: dict) -> bool:
    if not isinstance(result, dict):
        return True
    if result.get("delivery_indeterminate"):
        return True
    checker = getattr(module, "delivery_indeterminate", None)
    if callable(checker):
        try:
            return bool(checker(result))
        except Exception:
            pass
    if result.get("ok"):
        return not bool(message_id(result))
    error = str(result.get("error") or result.get("description") or "").strip().lower()
    definitive = any(marker in error for marker in (
        "http error 400",
        "http error 401",
        "http error 403",
        "http error 404",
        "bad request",
        "unauthorized",
        "forbidden",
        "chat not found",
        "method not found",
        "unsupported",
        "too many requests",
        "429",
        "blocked by persistent live-card retention policy",
        "token or target chat is unavailable",
        "helper is unavailable",
    ))
    return not definitive


def live_target_problems(chat_id: str, thread_id: str | None, confirm_production: bool) -> list[str]:
    problems: list[str] = []
    chat_text = str(chat_id or "").strip()
    thread_text = str(thread_id or "").strip()
    if not re.fullmatch(r"-?\d+", chat_text):
        problems.append("--chat-id must be a numeric Telegram chat ID")
    if not re.fullmatch(r"\d+", thread_text) or int(thread_text or 0) <= 0:
        problems.append("--thread-id must be a positive numeric Telegram topic ID")
    production_chat = bool(
        re.fullmatch(r"-?\d+", chat_text)
        and int(chat_text) == int(PRODUCTION_CHAT_ID)
    )
    production_inbox = bool(
        production_chat
        and re.fullmatch(r"\d+", thread_text)
        and int(thread_text) == int(PRODUCTION_INBOX_THREAD_ID)
    )
    if production_chat and not confirm_production:
        problems.append(
            "--confirm-production-canary is required for the production Inbox topic"
            if production_inbox
            else "--confirm-production-canary is required for the production Telegram chat"
        )
    return problems


def _retry_after_seconds(module, result: dict) -> float:
    """Read Telegram retry timing without retrying early during a known cooldown."""
    candidates: list[dict] = []
    for value in (result.get("parameters"), result.get("cooldown")):
        if isinstance(value, dict):
            candidates.append(value)
    active = getattr(module, "telegram_cooldown_active", None)
    if callable(active):
        try:
            value = active()
            if isinstance(value, dict):
                candidates.append(value)
        except Exception:
            pass

    now = dt.datetime.now(dt.timezone.utc)
    waits: list[float] = []
    for value in candidates:
        until = value.get("until")
        if until:
            try:
                until_dt = dt.datetime.fromisoformat(str(until).replace("Z", "+00:00"))
                waits.append(max(0.0, (until_dt - now).total_seconds()))
                continue
            except Exception:
                pass
        try:
            waits.append(max(0.0, float(value.get("retry_after") or value.get("retry_after_seconds") or 0)))
        except (TypeError, ValueError):
            pass

    error_text = str(result.get("error") or result.get("description") or "")
    match = re.search(r'["\']retry_after["\']\s*:\s*(\d+(?:\.\d+)?)', error_text)
    if match:
        waits.append(float(match.group(1)))
    return max(waits, default=0.0)


def delete_with_retry(module, chat_id: str, target_message_id: str, attempts: int = 3) -> dict:
    errors: list[str] = []
    waits: list[float] = []
    for attempt in range(max(1, attempts)):
        try:
            result = module.api_call(
                "deleteMessage",
                {"chat_id": int(chat_id), "message_id": int(target_message_id)},
                timeout=15,
            )
        except Exception as exc:  # cleanup must continue to the next tracked ID
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        error_text = str(result.get("error") or result.get("description") or "").lower()
        already_absent = any(marker in error_text for marker in (
            "message to delete not found",
            "message not found",
        ))
        if result.get("ok") or already_absent:
            return {
                "messageId": str(target_message_id),
                "deleted": True,
                "alreadyAbsent": already_absent,
                "attempts": attempt + 1,
                "waitsSeconds": waits,
                "error": "",
            }
        error = str(result.get("error") or result.get("description") or "delete failed")[:240]
        errors.append(error)
        if attempt + 1 >= max(1, attempts):
            break
        wait = _retry_after_seconds(module, result)
        if wait >= MAX_CLEANUP_RETRY_WAIT_SECONDS:
            return {
                "messageId": str(target_message_id),
                "deleted": False,
                "attempts": attempt + 1,
                "waitsSeconds": waits,
                "retryAfterSeconds": round(wait, 3),
                "deferredByCooldown": True,
                "error": error,
            }
        wait = wait + 0.05 if wait > 0 else 0.25 * (2**attempt)
        waits.append(round(wait, 3))
        time.sleep(wait)
    return {
        "messageId": str(target_message_id),
        "deleted": False,
        "attempts": max(1, attempts),
        "waitsSeconds": waits,
        "error": errors[-1] if errors else "delete failed",
    }


def cleanup_messages(
    module,
    chat_id: str,
    sent_ids: list[str],
    *,
    indeterminate_ids: list[str] | None = None,
    indeterminate_stages: list[str] | None = None,
) -> dict:
    records: list[dict] = []
    ordered_ids = list(dict.fromkeys(reversed(sent_ids)))
    for target in ordered_ids:
        try:
            records.append(delete_with_retry(module, chat_id, target))
        except Exception as exc:  # one bad delete must never prevent later cleanup
            records.append({
                "messageId": str(target),
                "deleted": False,
                "attempts": 0,
                "waitsSeconds": [],
                "error": f"{type(exc).__name__}: {exc}"[:240],
            })
    return {
        "attempted": len(ordered_ids),
        "deleted": sum(1 for row in records if row.get("deleted")),
        "failedIds": [row["messageId"] for row in records if not row.get("deleted")],
        "indeterminateIds": list(dict.fromkeys(indeterminate_ids or [])),
        "indeterminateStages": list(dict.fromkeys(indeterminate_stages or [])),
        "records": records,
    }


def render_stress(module, iterations: int) -> dict:
    problems: list[str] = []
    milestone_sequences: list[list[int]] = []
    route = "route=qa-canary; reason=transport verification; owner=josh2; worker=Inbox QA system"
    model = "system/transport-canary"
    for index in range(iterations):
        title = f"Verify Telegram Inbox response contract under deterministic load iteration {index + 1}"
        header = module.build_task_header(title=title, model=model, route=route)
        header_plain = pre_text(header)
        for label in ("Objective", "Owner", "Agent", "Models"):
            if label not in header_plain:
                problems.append(f"header missing {label}")
        if max((module.display_width(line) for line in header_plain.splitlines()), default=0) > CARD_WIDTH:
            problems.append("task header exceeds 38 display columns")

        counts = []
        for position in range(1, len(MILESTONE_UPDATES) + 1):
            items = list(MILESTONE_UPDATES[:position])
            status = "done" if position == len(MILESTONE_UPDATES) else "running"
            progress_counter = getattr(
                module,
                "compact_milestone_position",
                module.milestone_count,
            )
            counts.append(progress_counter(items, status, route=route))
            legacy = module.build_card(
                title=title,
                status=status,
                model=model,
                route=route,
                now=items[-1],
                done=items[:-1],
                updated="2026-01-01T00:00:06Z",
                started_at="2026-01-01T00:00:00Z",
            )
            plain = pre_text(legacy)
            if "█" not in plain and "░" not in plain:
                problems.append("legacy live card is missing the visual progress bar")
            if max((module.display_width(line) for line in plain.splitlines()), default=0) > CARD_WIDTH:
                problems.append("legacy live card exceeds 38 display columns")
        milestone_sequences.append(counts)
        if counts != sorted(counts) or counts[-1] != len(MILESTONE_UPDATES):
            problems.append("milestone progress is not monotonic through terminal delivery")

        rich = module.build_rich_card(
            title=title,
            status="done",
            model=model,
            route=route,
            now=MILESTONE_UPDATES[-1],
            done=list(MILESTONE_UPDATES[:-1]),
            updated="2026-01-01T00:00:06Z",
            started_at="2026-01-01T00:00:00Z",
        )
        rich_plain = pre_text(rich)
        if not re.fullmatch(r"\s*<pre>[\s\S]*</pre>\s*", rich, flags=re.I):
            problems.append("rich live card is not one fixed-width pre block")
        for required in ("JOSH 2.0 · Complete", "Progress [██████████] 6/6", "Now"):
            if required not in rich_plain:
                problems.append(f"rich live card missing {required}")
        if len(rich_plain.splitlines()) > 22:
            problems.append("rich live card exceeds 22 visible lines")
        if max((module.display_width(line) for line in rich_plain.splitlines()), default=0) > CARD_WIDTH:
            problems.append("rich live card exceeds 38 display columns")
        if re.search(
            r"(?i)(?:tool:|action:|brain feed|search_files|read_file|"
            r"<details>|type=\"checkbox\"|recent activity)",
            rich,
        ):
            problems.append("rich live card exposes implementation activity")

        # A terminal workflow can complete its delivery lifecycle even when
        # the objective result is Complete: No. Keep Needs attention truthful,
        # but require the same closed 6/6 delivery contract before final.
        failed_items = [
            *MILESTONE_UPDATES[:-1],
            "Structured issue summary prepared",
        ]
        failed_legacy = module.build_card(
            title=title,
            status="failed",
            model=model,
            route=route,
            now=failed_items[-1],
            done=failed_items[:-1],
            updated="2026-01-01T00:00:06Z",
            started_at="2026-01-01T00:00:00Z",
        )
        failed_rich = module.build_rich_card(
            title=title,
            status="failed",
            model=model,
            route=route,
            now=failed_items[-1],
            done=failed_items[:-1],
            updated="2026-01-01T00:00:06Z",
            started_at="2026-01-01T00:00:00Z",
        )
        for rendered in (pre_text(failed_legacy), pre_text(failed_rich)):
            if "Progress [██████████] 6/6" not in rendered:
                problems.append("needs-attention terminal card did not close through stage 6/6")
        if "Needs attention" not in failed_rich:
            problems.append("failed terminal rich card lost its needs-attention outcome label")

        final = module.build_completion_summary(
            title=title,
            status="done",
            model=model,
            route=route,
            now="Confirmed the canary final retained one delivery receipt",
            done=[
                "Confirmed each inbound task receives one reaction and one header.",
                "Verified 69 plugin cases reject duplicate or malformed finals.",
                "Found cleanup retries preserve exact Telegram message IDs.",
            ],
            next_step="Review the next scheduled canary result.",
            blocker="None",
        )
        problems.extend(validate(final, module))

    return {
        "ok": not problems,
        "iterations": iterations,
        "renderedCards": iterations * (len(MILESTONE_UPDATES) + 5),
        "milestoneSequences": milestone_sequences[:3],
        "problems": sorted(set(problems)),
    }


def render_final_stress(module, iterations: int) -> dict:
    problems: list[str] = []
    for index in range(iterations):
        final = module.build_completion_summary(
            title=f"Verify Telegram response contract iteration {index + 1}",
            status="done",
            model="system/transport-canary",
            now="Confirmed the canary final retained one delivery receipt",
            done=[
                "Confirmed exact topic ownership for the synthetic request.",
                "Verified the live card reaches its terminal delivery state.",
                "Found the structured final preserves findings and next steps.",
            ],
            next_step="Review the next scheduled canary result.",
            blocker="None",
        )
        problems.extend(validate(final, module))
    return {
        "ok": not problems,
        "iterations": iterations,
        "renderedCards": iterations,
        "milestoneSequences": [],
        "problems": sorted(set(problems)),
    }


def basic_live_canary(module, chat_id: str, thread_id: str) -> dict:
    start = time.monotonic()
    sent_ids: list[str] = []
    indeterminate_ids: list[str] = []
    indeterminate_stages: list[str] = []
    write_canary_journal(
        stage="basic-send-intent",
        chat_id=chat_id,
        thread_id=thread_id,
        message_ids=sent_ids,
        indeterminate_stages=["basic-send"],
    )
    try:
        sent = module.send_card(
            "<pre>TEMPORARY QA CANARY\n- send\n- edit\n- delete</pre>",
            None,
            15,
            chat_id=chat_id,
            thread_id=thread_id,
        )
    except Exception as exc:
        sent = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    target = message_id(sent)
    if target:
        sent_ids.append(target)
    if delivery_is_indeterminate(module, sent):
        (indeterminate_ids if target else indeterminate_stages).append(target or "basic-send")
    write_canary_journal(
        stage="basic-send-receipt",
        chat_id=chat_id,
        thread_id=thread_id,
        message_ids=sent_ids,
        indeterminate_stages=indeterminate_stages,
    )
    if not sent.get("ok") or not target:
        cleanup = cleanup_messages(
            module,
            chat_id,
            sent_ids,
            indeterminate_ids=indeterminate_ids,
            indeterminate_stages=indeterminate_stages,
        )
        finalize_canary_journal(cleanup, chat_id, thread_id)
        return {
            "ok": False,
            "stage": "send",
            "scope": "synthetic cumulative transport timing only; never p95 or inbound-path evidence",
            "error": str(sent.get("error") or "send failed")[:240],
            "cleanup": cleanup,
        }
    try:
        edited = module.edit_card(
            target,
            "<pre>TEMPORARY QA CANARY\n- send ok\n- edit ok\n- deleting</pre>",
            None,
            15,
            chat_id=chat_id,
            thread_id=thread_id,
        )
    except Exception as exc:
        edited = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    cleanup = cleanup_messages(
        module,
        chat_id,
        sent_ids,
        indeterminate_ids=indeterminate_ids,
        indeterminate_stages=indeterminate_stages,
    )
    finalize_canary_journal(cleanup, chat_id, thread_id)
    failures = []
    if not edited.get("ok"):
        failures.append(f"basic edit failed: {str(edited.get('error') or 'edit failed')[:160]}")
    if cleanup["failedIds"] or cleanup["indeterminateStages"]:
        failures.append("temporary canary cleanup is incomplete or indeterminate")
    return {
        "ok": not failures,
        "scope": "synthetic cumulative transport timing only; never p95 or inbound-path evidence",
        "send": bool(sent.get("ok")),
        "edit": bool(edited.get("ok")),
        "cleanup": cleanup,
        "failures": failures,
        "timing": {
            "kind": "synthetic cumulative transport timing",
            "cumulativeMs": {"sendEditCleanup": round((time.monotonic() - start) * 1000, 1)},
        },
    }


def live_canary(module, chat_id: str, thread_id: str) -> dict:
    if not all(hasattr(module, name) for name in ("build_task_header", "build_card", "build_rich_card", "send_rich_message", "edit_rich_card")):
        return basic_live_canary(module, chat_id, thread_id)
    start = time.monotonic()
    response_start: float | None = None
    setup_ms: float | None = None
    sent_ids: list[str] = []
    stage_ms: dict[str, float] = {}
    failures: list[str] = []
    indeterminate_ids: list[str] = []
    indeterminate_stages: list[str] = []
    final_attempts = 0
    final_successes = 0
    final_ids: list[str] = []
    edit_results: list[bool] = []
    terminal_render_verified = False
    terminal_edit_verified = False
    route = "route=qa-canary; reason=transport verification; owner=josh2; worker=Inbox QA system"
    model = "system/transport-canary"
    title = "Temporary Telegram Inbox response canary"
    renderer = "unknown"
    pending_send_stage: str | None = None

    def before_send(stage: str) -> None:
        nonlocal pending_send_stage
        pending_send_stage = stage
        write_canary_journal(
            stage=f"{stage}-intent",
            chat_id=chat_id,
            thread_id=thread_id,
            message_ids=sent_ids,
            indeterminate_stages=[*indeterminate_stages, stage],
        )

    def track_delivery(result: dict, stage: str) -> str:
        nonlocal pending_send_stage
        target = message_id(result)
        if target and target not in sent_ids:
            sent_ids.append(target)
        if delivery_is_indeterminate(module, result):
            if target:
                indeterminate_ids.append(target)
            else:
                indeterminate_stages.append(stage)
        if pending_send_stage == stage:
            pending_send_stage = None
        write_canary_journal(
            stage=f"{stage}-receipt",
            chat_id=chat_id,
            thread_id=thread_id,
            message_ids=sent_ids,
            indeterminate_stages=indeterminate_stages,
        )
        return target

    def finish() -> dict:
        unresolved_stages = list(indeterminate_stages)
        if pending_send_stage:
            unresolved_stages.append(pending_send_stage)
        cleanup = cleanup_messages(
            module,
            chat_id,
            sent_ids,
            indeterminate_ids=indeterminate_ids,
            indeterminate_stages=unresolved_stages,
        )
        finalize_canary_journal(cleanup, chat_id, thread_id)
        result_failures = list(failures)
        exactly_one_final = final_attempts == 1 and final_successes == 1 and len(final_ids) == 1
        synthetic_checks = {
            "eyesUnder2s": stage_ms.get("eyes", float("inf")) <= 2_000,
            "headerUnder5s": stage_ms.get("header", float("inf")) <= 5_000,
            "liveCardUnder8s": stage_ms.get("liveCard", float("inf")) <= 8_000,
            "terminalLiveCard100Percent": terminal_render_verified and terminal_edit_verified,
            "exactlyOneFinal": exactly_one_final,
        }
        if final_successes == 1 and not all(
            synthetic_checks[key] for key in ("eyesUnder2s", "headerUnder5s", "liveCardUnder8s")
        ):
            result_failures.append("synthetic cumulative response timing exceeded one or more thresholds")
        if final_attempts and not exactly_one_final:
            result_failures.append("exactly-one-final contract failed")
        if cleanup["failedIds"] or cleanup["indeterminateStages"]:
            result_failures.append("temporary canary cleanup is incomplete or indeterminate")
        result_failures = list(dict.fromkeys(result_failures))
        return {
            "ok": not result_failures,
            "scope": "synthetic cumulative response timing begins after the canary anchor receipt; never p95 or inbound-path evidence",
            "renderer": renderer,
            "timing": {
                "kind": "synthetic cumulative response timing after anchor receipt",
                "setupMs": setup_ms,
                "cumulativeMs": stage_ms,
                "checks": synthetic_checks,
            },
            "milestoneEdits": len(edit_results),
            "final": {
                "attempts": final_attempts,
                "successes": final_successes,
                "messageIds": final_ids,
                "exactlyOne": exactly_one_final,
            },
            "cleanup": cleanup,
            "failures": result_failures,
            "elapsedMs": round((time.monotonic() - start) * 1000, 1),
        }

    try:
        before_send("anchor")
        anchor = module.send_card(
            "<pre>TEMPORARY QA CANARY\nCleanup will be attempted.</pre>",
            None,
            15,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        anchor_id = track_delivery(anchor, "anchor")
        if not anchor.get("ok") or not anchor_id:
            failures.append(f"anchor send failed: {str(anchor.get('error') or 'send failed')[:160]}")
            return finish()
        # The anchor stands in for a Telegram message that already exists when
        # Josh receives a real Inbox update. Its outbound setup latency is not
        # part of Josh's response SLO, so start the synthetic response clock
        # only after Telegram has returned the anchor receipt.
        response_start = time.monotonic()
        setup_ms = round((response_start - start) * 1000, 1)

        reaction = module.api_call(
            "setMessageReaction",
            {
                "chat_id": int(chat_id),
                "message_id": int(anchor_id),
                "reaction": [{"type": "emoji", "emoji": "👀"}],
                "is_big": False,
            },
            timeout=15,
        )
        stage_ms["eyes"] = round((time.monotonic() - response_start) * 1000, 1)
        if not reaction.get("ok"):
            failures.append("eyes reaction failed")
            return finish()

        before_send("task-header")
        header = module.send_card(
            module.build_task_header(title=title, model=model, route=route),
            None,
            15,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        header_id = track_delivery(header, "task-header")
        stage_ms["header"] = round((time.monotonic() - response_start) * 1000, 1)
        if not header.get("ok") or not header_id:
            failures.append("task header send failed")
            return finish()

        first_items = list(MILESTONE_UPDATES[:2])
        legacy = module.build_card(
            title=title,
            status="running",
            model=model,
            route=route,
            now=first_items[-1],
            done=first_items[:-1],
        )
        rich = module.build_rich_card(
            title=title,
            status="running",
            model=model,
            route=route,
            now=first_items[-1],
            done=first_items[:-1],
        )
        before_send("live-card")
        live = module.send_rich_message(
            rich,
            legacy,
            15,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        live_id = track_delivery(live, "live-card")
        renderer = "rich" if live.get("native_rich_message") else "fallback"
        stage_ms["liveCard"] = round((time.monotonic() - response_start) * 1000, 1)
        if not live.get("ok") or not live_id:
            failures.append("live card send failed")
            return finish()

        for position in range(3, len(MILESTONE_UPDATES) + 1):
            items = list(MILESTONE_UPDATES[:position])
            terminal = position == len(MILESTONE_UPDATES)
            status = "done" if terminal else "running"
            legacy = module.build_card(
                title=title,
                status=status,
                model=model,
                route=route,
                now=items[-1],
                done=items[:-1],
            )
            rich = module.build_rich_card(
                title=title,
                status=status,
                model=model,
                route=route,
                now=items[-1],
                done=items[:-1],
            )
            if terminal:
                terminal_render_verified = all(
                    "Progress [██████████] 6/6" in rendered
                    for rendered in (pre_text(legacy), pre_text(rich))
                )
                if not terminal_render_verified:
                    failures.append("terminal live card did not render the closed 6/6 state")
                    return finish()
            edited = module.edit_rich_card(
                live_id,
                rich,
                legacy,
                None,
                15,
                chat_id=chat_id,
                thread_id=thread_id,
            )
            edit_ok = bool(edited.get("ok"))
            if edited.get("native_rich_message") is False:
                renderer = "fallback"
            edit_results.append(edit_ok)
            if not edit_ok:
                failures.append("live card milestone edit failed")
                return finish()
            if terminal:
                terminal_edit_verified = True
            else:
                time.sleep(0.35)

        final_text = module.build_completion_summary(
            title=title,
            status="done",
            model=model,
            route=route,
            now="Final response delivered",
            done=["Eyes reaction verified", "Header and live card verified", "Terminal live card verified"],
            next_step="No action needed.",
            blocker="None",
        )
        final_problems = validate(final_text, module)
        if final_problems:
            failures.append(f"structured final validation failed: {', '.join(final_problems)}")
            return finish()
        final_attempts += 1
        before_send("structured-final")
        final = module.send_final_summary(
            final_text,
            15,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        final_id = track_delivery(final, "structured-final")
        if final_id:
            final_ids.append(final_id)
        stage_ms["final"] = round((time.monotonic() - response_start) * 1000, 1)
        if not final.get("ok") or not final_id:
            failures.append("structured final send failed")
        else:
            final_successes += 1
    except Exception as exc:
        failures.append(f"unexpected canary error: {type(exc).__name__}: {str(exc)[:160]}")
    return finish()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("josh2", "jaimes"), required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--chat-id", default=PRODUCTION_CHAT_ID)
    parser.add_argument("--thread-id")
    parser.add_argument(
        "--work-card-path",
        help="offline-only override for the renderer under test",
    )
    parser.add_argument(
        "--confirm-production-canary",
        action="store_true",
        help="allow temporary canary messages in production Inbox topic 1",
    )
    args = parser.parse_args()
    if args.live and args.work_card_path:
        parser.error("--work-card-path is available only for offline render stress")

    home = Path.home()
    script = Path(args.work_card_path).expanduser() if args.work_card_path else (
        home / ".openclaw/workspace/scripts/josh_work_card.py"
        if args.role == "josh2"
        else home / ".openclaw/workspace/mission-control/scripts/jaimes_work_card.py"
    )
    module = load_module(script)
    summary_args = dict(
        title="Primary topic readiness",
        status="done",
        model="openai/gpt-5.6-terra" if args.role == "josh2" else "openai-codex/gpt-5.6-sol",
        now="Confirmed the readiness canary retained one delivery receipt",
        done=[
            "Confirmed exact topic ownership for the readiness request.",
            "Verified shared memory is available to the selected agent route.",
            "Found the live card and substantive final preserve their required order.",
        ],
        next_step="Review the next scheduled canary result.",
        blocker="None",
    )
    if args.role == "josh2":
        summary_args["route"] = "route=josh2-inbox; reason=verified readiness canary; owner=josh2"
    rendered = module.build_completion_summary(**summary_args)
    problems = validate(rendered, module)
    stress = (
        render_stress(module, max(1, args.iterations))
        if args.role == "josh2"
        else render_final_stress(module, max(1, args.iterations))
    )
    problems.extend(stress["problems"])
    transport = None
    if args.live:
        target_problems = live_target_problems(
            args.chat_id,
            args.thread_id,
            args.confirm_production_canary,
        )
        if re.fullmatch(r"-?\d+", str(args.chat_id or "").strip()) and re.fullmatch(
            r"\d+", str(args.thread_id or "").strip()
        ):
            numeric_chat = int(str(args.chat_id).strip())
            numeric_thread = int(str(args.thread_id).strip())
            expected_thread = (
                int(PRODUCTION_INBOX_THREAD_ID)
                if args.role == "josh2"
                else int(PRODUCTION_JAIMES_THREAD_ID)
            )
            if numeric_chat == int(PRODUCTION_CHAT_ID) and numeric_thread != expected_thread:
                target_problems.append(
                    f"--role {args.role} may only canary its owned production topic {expected_thread}"
                )
        if args.role == "jaimes" and os.environ.get("JAIMES_ALLOW_EXPLICIT_CARD_DELETE") != "1":
            target_problems.append(
                "JAIMES live canary requires JAIMES_ALLOW_EXPLICIT_CARD_DELETE=1 so cleanup is permitted"
            )
        problems.extend(target_problems)
        if not target_problems:
            transport = live_canary(module, args.chat_id, args.thread_id)
            if not transport.get("ok"):
                problems.append("live send/edit/delete canary failed")
    result = {"role": args.role, "ok": not problems, "problems": sorted(set(problems)), "stress": stress, "transport": transport}
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
