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
import stat
import sys
import tempfile
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
CANARY_JOURNAL_FILENAME = "telegram-canary-cleanup.json"
CANARY_CLAIM_FILENAME = "telegram-canary-one-shot.claim"
CANARY_RECEIPT_FILENAME = "telegram-canary-cleanup-receipt.json"
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
    r"verified\s+(?:that|correctly|successfully|\d+)|cannot|can['’]?t|could not|does not|did not|"
    r"risk|limitation|recommend(?:ed|ation)?|\d+\s+(?:tests?|checks?|cases?))\b",
    re.I,
)
OPERATIONAL_RESULT_PATTERN = re.compile(
    r"(?:\b(?:gateway|service|daemon|watcher|process|socket|connection|endpoint|api|launchd|"
    r"runtime|bot|helper|logs?|files?|source|entry|entries|inventory|messages?|delivery)\b.{0,100}"
    r"\b(?:running|listening|connected|reachable|responding|registered|loaded|active|healthy|stopped|"
    r"offline|unreachable|empty|stale|missing|absent|unavailable|unverified|last\s+modified|"
    r"has\s+no|have\s+no|there\s+(?:is|are)\s+no|port\s+\d{2,5})\b|"
    r"\b(?:running|listening|connected|reachable|responding|registered|loaded|active|healthy|stopped|"
    r"offline|unreachable|empty|stale|missing|absent|unavailable|unverified|last\s+modified|"
    r"has\s+no|have\s+no|there\s+(?:is|are)\s+no|port\s+\d{2,5})\b.{0,100}"
    r"\b(?:gateway|service|daemon|watcher|process|socket|connection|endpoint|api|launchd|"
    r"runtime|bot|helper|logs?|files?|source|entry|entries|inventory|messages?|delivery)\b)",
    re.I,
)
OPERATIONAL_STATUS_FILLER_PATTERN = re.compile(
    r"\b(?:gateway|service|daemon|watcher|process|runtime|bot|helper|delivery)\s+"
    r"(?:(?:health|status|operational|connectivity|delivery)\s+){0,2}"
    r"(?:assessment|review|report|request|task|work)\s+(?:is\s+|was\s+|remains\s+)?"
    r"(?:active|running|connected|complete|completed|done|last\s+modified)\b",
    re.I,
)
RISK_OR_LIMITATION_PATTERN = re.compile(
    r"\b(?:risk|limitation|cannot|can['’]?t|could not|does not|did not|unsupported|blocked|failed|"
    r"failure|unable|do not|don['’]?t|avoid)\b",
    re.I,
)
OPERATIONAL_RISK_PATTERN = re.compile(
    r"(?:\b(?:gateway|service|daemon|watcher|process|socket|connection|endpoint|api|launchd|"
    r"runtime|bot|helper|logs?|files?|source|entry|entries|inventory|messages?|delivery)\b.{0,100}"
    r"\b(?:not\s+(?:running|listening|connected|reachable|responding|registered|loaded|active|healthy)|"
    r"stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified|"
    r"has\s+no|have\s+no|there\s+(?:is|are)\s+no)\b|"
    r"\b(?:not\s+(?:running|listening|connected|reachable|responding|registered|loaded|active|healthy)|"
    r"stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified|"
    r"has\s+no|have\s+no|there\s+(?:is|are)\s+no)\b.{0,100}"
    r"\b(?:gateway|service|daemon|watcher|process|socket|connection|endpoint|api|launchd|"
    r"runtime|bot|helper|logs?|files?|source|entry|entries|inventory|messages?|delivery)\b)",
    re.I,
)
NEGATED_OPERATIONAL_RISK_PATTERN = re.compile(
    r"\b(?:no|not|without)\s+(?:\w+\s+){0,2}"
    r"(?:stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified)\b",
    re.I,
)
POSITIVE_OPERATIONAL_ABSENCE_PATTERN = re.compile(
    r"\b(?:(?:has|have)\s+no|there\s+(?:is|are)\s+no)\s+"
    r"(?:remaining\s+)?(?:service\s+)?(?:issues?|failures?|errors?|problems?|risks?|blockers?)\b",
    re.I,
)


def _has_operational_risk(value: str) -> bool:
    text = NEGATED_OPERATIONAL_RISK_PATTERN.sub("", value)
    text = POSITIVE_OPERATIONAL_ABSENCE_PATTERN.sub("", text)
    return bool(OPERATIONAL_RISK_PATTERN.search(text))


def _has_concrete_result(value: str) -> bool:
    return not OPERATIONAL_STATUS_FILLER_PATTERN.search(value) and bool(
        CONCRETE_RESULT_PATTERN.search(value) or OPERATIONAL_RESULT_PATTERN.search(value)
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


def _private_canary_directory(directory: Path | str) -> Path:
    path = Path(directory).expanduser()
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeError("canary journal directory must already exist") from exc
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("canary journal directory must be a real directory")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError("canary journal directory must be owner-only 0700")
    return path.resolve()


def _write_exclusive_at(directory_fd: int, name: str, payload: dict) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        encoded = (json.dumps(payload, indent=2, ensure_ascii=True) + "\n").encode()
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validated_canary_journal_filename(value: str) -> str:
    name = str(value)
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", name)
        or Path(name).name != name
        or name in {CANARY_CLAIM_FILENAME, CANARY_RECEIPT_FILENAME}
    ):
        raise RuntimeError("invalid canary journal filename")
    return name


def prepare_canary_journal(
    directory: Path | str,
    *,
    role: str,
    _journal_filename: str = CANARY_JOURNAL_FILENAME,
) -> Path:
    """Atomically claim one caller-created private directory for one live run."""
    path = _private_canary_directory(directory)
    journal_filename = _validated_canary_journal_filename(_journal_filename)
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_fd = os.open(path, flags)
    try:
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        try:
            _write_exclusive_at(
                directory_fd,
                CANARY_CLAIM_FILENAME,
                {
                    "version": 1,
                    "createdAt": now,
                    "role": role,
                    "status": "claimed",
                    "journalFilename": journal_filename,
                },
            )
        except FileExistsError as exc:
            raise RuntimeError("canary journal directory has already been used") from exc
        try:
            _write_exclusive_at(
                directory_fd,
                journal_filename,
                {
                    "version": 2,
                    "updatedAt": now,
                    "stage": "prepared",
                    "chatId": "",
                    "threadId": "",
                    "messageIds": [],
                    "indeterminateStages": [],
                },
            )
        except Exception:
            # The durable one-shot claim deliberately remains. A partial setup
            # must never turn into an automatic retry that could send twice.
            raise
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return path / journal_filename


def _journal_cleanup_projection(cleanup: dict | None) -> dict | None:
    if not isinstance(cleanup, dict):
        return None
    records = []
    for row in cleanup.get("records") or []:
        if not isinstance(row, dict):
            continue
        records.append({
            "messageId": str(row.get("messageId") or ""),
            "deleted": bool(row.get("deleted")),
            "alreadyAbsent": bool(row.get("alreadyAbsent")),
            "attempts": int(row.get("attempts") or 0),
            "deferredByCooldown": bool(row.get("deferredByCooldown")),
        })
    return {
        "attempted": int(cleanup.get("attempted") or 0),
        "deleted": int(cleanup.get("deleted") or 0),
        "failedIds": [str(value) for value in cleanup.get("failedIds") or []],
        "indeterminateIds": [str(value) for value in cleanup.get("indeterminateIds") or []],
        "indeterminateStages": [str(value)[:80] for value in cleanup.get("indeterminateStages") or []],
        "records": records,
    }


def _load_canary_journal(path: Path) -> dict:
    parent = _private_canary_directory(path.parent)
    if path.parent.resolve() != parent:
        raise RuntimeError("invalid canary journal path")
    claim_path = parent / CANARY_CLAIM_FILENAME
    try:
        claim_info = claim_path.lstat()
    except OSError as exc:
        raise RuntimeError("canary one-shot claim is missing") from exc
    if (
        claim_path.is_symlink()
        or not stat.S_ISREG(claim_info.st_mode)
        or claim_info.st_uid != os.getuid()
        or stat.S_IMODE(claim_info.st_mode) != 0o600
    ):
        raise RuntimeError("canary one-shot claim is invalid")
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    expected_filename = str(
        claim.get("journalFilename") if isinstance(claim, dict) else ""
    ) or CANARY_JOURNAL_FILENAME
    if path.name != _validated_canary_journal_filename(expected_filename):
        raise RuntimeError("invalid canary journal path")
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeError("canary journal is missing") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise RuntimeError("canary journal is not a regular file")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError("canary journal must remain owner-only 0600")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("canary journal shape is invalid")
    return value


def write_canary_journal(
    journal_path: Path,
    *,
    stage: str,
    chat_id: str,
    thread_id: str,
    message_ids: list[str],
    indeterminate_stages: list[str],
    cleanup: dict | None = None,
    _replace_message_ids: bool = False,
) -> None:
    path = Path(journal_path)
    current = _load_canary_journal(path)
    prior_ids = [] if _replace_message_ids else [
        str(value) for value in current.get("messageIds") or []
    ]
    payload = {
        "version": 2,
        "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "chatId": str(chat_id),
        "threadId": str(thread_id),
        "stage": str(stage)[:80],
        "messageIds": list(dict.fromkeys(
            str(value)
            for value in [*prior_ids, *message_ids]
            if str(value).isdigit()
        )),
        "indeterminateStages": list(dict.fromkeys(str(value)[:80] for value in indeterminate_stages)),
    }
    cleanup_projection = _journal_cleanup_projection(cleanup)
    if cleanup_projection is not None:
        payload["cleanup"] = cleanup_projection
    directory = _private_canary_directory(path.parent)
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_fd = os.open(directory, flags)
    temporary_name = f".{path.name}.{os.getpid()}.{os.urandom(6).hex()}.tmp"
    try:
        _write_exclusive_at(directory_fd, temporary_name, payload)
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def cleanup_fully_confirmed(cleanup: dict, expected_ids: list[str]) -> bool:
    expected = list(dict.fromkeys(str(value) for value in expected_ids if str(value).isdigit()))
    records = [row for row in cleanup.get("records") or [] if isinstance(row, dict)]
    record_ids = [str(row.get("messageId") or "") for row in records]
    confirmed = {
        str(row.get("messageId") or "")
        for row in records
        if bool(row.get("deleted"))
    }
    indeterminate_ids = {
        str(value) for value in cleanup.get("indeterminateIds") or [] if str(value).isdigit()
    }
    return bool(
        int(cleanup.get("attempted") or 0) == len(expected)
        and int(cleanup.get("deleted") or 0) == len(expected)
        and len(records) == len(expected)
        and len(record_ids) == len(set(record_ids))
        and set(expected) == confirmed
        and not cleanup.get("failedIds")
        and not cleanup.get("indeterminateStages")
        and indeterminate_ids.issubset(confirmed)
    )


def finalize_canary_journal(
    journal_path: Path,
    cleanup: dict,
    chat_id: str,
    thread_id: str,
    sent_ids: list[str],
) -> bool:
    current = _load_canary_journal(journal_path)
    expected = list(dict.fromkeys(
        str(value)
        for value in [
            *(current.get("messageIds") or []),
            *sent_ids,
            *(cleanup.get("indeterminateIds") or []),
        ]
        if str(value).isdigit()
    ))
    confirmed = cleanup_fully_confirmed(cleanup, expected)
    legacy_filename = journal_path.name != CANARY_JOURNAL_FILENAME
    unresolved_ids = list(dict.fromkeys(
        str(value)
        for value in [
            *(cleanup.get("failedIds") or []),
            *(cleanup.get("indeterminateIds") or []),
            *(
                str(row.get("messageId") or "")
                for row in cleanup.get("records") or []
                if isinstance(row, dict) and not bool(row.get("deleted"))
            ),
        ]
        if str(value).isdigit()
    ))
    write_canary_journal(
        journal_path,
        stage="cleanup-confirmed" if confirmed else "cleanup-pending",
        chat_id=chat_id,
        thread_id=thread_id,
        message_ids=(unresolved_ids if legacy_filename and not confirmed else expected),
        indeterminate_stages=list(cleanup.get("indeterminateStages") or []),
        cleanup=cleanup,
        _replace_message_ids=legacy_filename and not confirmed,
    )
    if not confirmed:
        # The journal is the recovery source of truth until every possible ID
        # is reconciled.  Never remove it on partial or ambiguous cleanup.
        return False

    final_journal = _load_canary_journal(journal_path)
    directory = _private_canary_directory(journal_path.parent)
    receipt_payload = {
        **final_journal,
        "receiptVersion": 1,
        "cleanupConfirmed": True,
    }
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_fd = os.open(directory, flags)
    try:
        try:
            _write_exclusive_at(directory_fd, CANARY_RECEIPT_FILENAME, receipt_payload)
        except FileExistsError:
            receipt_path = directory / CANARY_RECEIPT_FILENAME
            receipt_info = receipt_path.lstat()
            if (
                receipt_path.is_symlink()
                or not stat.S_ISREG(receipt_info.st_mode)
                or receipt_info.st_uid != os.getuid()
                or stat.S_IMODE(receipt_info.st_mode) != 0o600
                or json.loads(receipt_path.read_text(encoding="utf-8")) != receipt_payload
            ):
                raise RuntimeError("canary cleanup receipt conflicts")
        os.fsync(directory_fd)
        os.unlink(journal_path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except Exception:
        # If receipt creation or journal removal fails, report cleanup as not
        # fully finalized.  The one-shot claim prevents a blind rerun.
        return False
    finally:
        os.close(directory_fd)
    return not journal_path.exists() and (directory / CANARY_RECEIPT_FILENAME).is_file()


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


def final_plain_text(text: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    value = re.sub(r"</(?:blockquote|p|h[1-6]|li|details|summary|footer)>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(re.sub(r"^\s*•\s+", "- ", line) for line in value.splitlines()).strip()


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
    legacy_pre = bool(re.fullmatch(r"\s*<pre>[\s\S]*</pre>\s*", text, flags=re.I))
    if not legacy_pre and not text.startswith("<b>JOSH 2.0 ·"):
        problems.append("final summary must use the polished proportional contract or its pre fallback")
    plain = pre_text(text) if legacy_pre else final_plain_text(text)
    positions = [plain.find(label) for label in LABELS]
    if any(pos < 0 for pos in positions):
        problems.append("one or more final-summary labels are missing")
    elif positions != sorted(positions):
        problems.append("final-summary labels are out of order")
    for forbidden in ("**", "Objective Complete:", "TLDR:", "Challenges/Blockers:"):
        if forbidden in text:
            problems.append(f"forbidden formatting remains: {forbidden}")
    approval = _section_body(plain, "Approval needed:", None)
    if not approval:
        problems.append("Approval needed must be n/a or contain at least one bullet")
    elif not _no_issue(approval) and not _bullet_items(approval):
        problems.append("Approval needed must be n/a or contain at least one bullet")
    width = module.display_width if module and hasattr(module, "display_width") else len
    if legacy_pre and max((width(line) for line in plain.splitlines()), default=0) > CARD_WIDTH:
        problems.append(f"a rendered line exceeds {CARD_WIDTH} display columns")
    if not re.search(r"^Complete: (?:Yes|No)\b", plain, flags=re.M):
        problems.append("Complete must start with Yes or No")
    complete_at = plain.find("Complete:")
    header_lines = [
        line.strip()
        for line in plain[:complete_at].splitlines()
        if line.strip()
    ]
    model_index = next((index for index, line in enumerate(header_lines) if line.startswith("Model:")), -1)
    header = " ".join(header_lines[model_index:]) if model_index >= 0 else ""
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
        if sum(
            _has_concrete_result(item)
            for item in done_items
        ) < 2:
            problems.append("Complete Yes requires at least 2 concrete findings or outcomes")
    result_text = " ".join(done_items)
    combined_risk_text = f"{result_text} {next_steps}"
    if _no_issue(issues) and (
        RISK_OR_LIMITATION_PATTERN.search(combined_risk_text)
        or _has_operational_risk(combined_risk_text)
    ):
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
        # Exercise the optional diagnostic header renderer offline. Production
        # Inbox delivery is headerless unless explicitly opted in.
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
        rich_plain = final_plain_text(rich)
        if not rich.startswith("<h3>") or rich.startswith("<pre>"):
            problems.append("rich live card is not using native block markup")
        for required in ("JOSH 2.0 · COMPLETE", "██████████ 100% · stage 6/6", "Now"):
            if required not in rich_plain:
                problems.append(f"rich live card missing {required}")
        if rich.count('type="checkbox"') != len(MILESTONE_UPDATES):
            problems.append("rich live card is missing the six-stage checklist")
        if rich.count(" checked") != len(MILESTONE_UPDATES):
            problems.append("terminal rich live card did not check every stage")
        if "<details><summary>Recent activity" not in rich or "<footer>" not in rich:
            problems.append("rich live card is missing collapsible activity or timing")
        if re.search(
            r"(?i)(?:tool:|action:|brain feed|search_files|read_file|"
            r"/(?:Users|private|tmp|var)/)",
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
        if "Progress [██████████] 6/6" not in pre_text(failed_legacy):
            problems.append("needs-attention legacy card did not close through stage 6/6")
        if "██████████ 100% · stage 6/6" not in final_plain_text(failed_rich):
            problems.append("needs-attention rich card did not close through stage 6/6")
        if "NEEDS ATTENTION" not in failed_rich:
            problems.append("failed terminal rich card lost its needs-attention outcome label")

        final = module.build_completion_summary(
            title=title,
            status="done",
            model=model,
            route=route,
            now="Confirmed the canary final retained one delivery receipt",
            done=[
                "Confirmed each inbound task receives one reaction and one native rich live card.",
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


def _production_chat(chat_id: str) -> bool:
    try:
        return int(str(chat_id).strip()) == int(PRODUCTION_CHAT_ID)
    except (TypeError, ValueError):
        return False


def _legacy_live_canary_journal(chat_id: str, thread_id: str) -> Path:
    """Provide a safe journal for the old three-argument direct-call API.

    Production calls still require an explicit caller-created 0700 directory.
    Non-production direct callers get a one-shot 0700 directory, 0600 journal,
    durable claim, and final receipt. The former environment-file convention
    remains available only for non-production compatibility tests and tools.
    """
    configured = os.environ.get(CANARY_JOURNAL_ENV, "").strip()
    role = "jaimes" if str(thread_id).strip() == PRODUCTION_JAIMES_THREAD_ID else "josh2"
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.suffix.lower() == ".json":
            if _production_chat(chat_id):
                raise RuntimeError(
                    "production direct calls require an explicit prepared canary journal"
                )
            directory = candidate.parent
            created = False
            try:
                directory.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                pass
            if created:
                os.chmod(directory, 0o700)
            return prepare_canary_journal(
                directory,
                role=role,
                _journal_filename=candidate.name,
            )
        if _production_chat(chat_id):
            return prepare_canary_journal(candidate, role=role)

    if _production_chat(chat_id):
        raise RuntimeError(
            "production direct calls require an explicit prepared canary journal"
        )
    directory = Path(tempfile.mkdtemp(prefix="telegram-canary-compat-"))
    os.chmod(directory, 0o700)
    return prepare_canary_journal(directory, role=role)


def basic_live_canary(module, chat_id: str, thread_id: str, journal_path: Path) -> dict:
    start = time.monotonic()
    sent_ids: list[str] = []
    indeterminate_ids: list[str] = []
    indeterminate_stages: list[str] = []
    final_attempts = 0
    final_successes = 0
    final_ids: list[str] = []
    write_canary_journal(
        journal_path,
        stage="basic-send-intent",
        chat_id=chat_id,
        thread_id=thread_id,
        message_ids=sent_ids,
        indeterminate_stages=["basic-send"],
    )
    try:
        sent = module.send_card(
            "<pre>TEMPORARY QA CANARY\n- send\n- edit\n- separate final\n- delete</pre>",
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
        journal_path,
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
        cleanup_confirmed = finalize_canary_journal(
            journal_path,
            cleanup,
            chat_id,
            thread_id,
            sent_ids,
        )
        return {
            "ok": False,
            "stage": "send",
            "scope": "synthetic cumulative transport timing only; never p95 or inbound-path evidence",
            "error": str(sent.get("error") or "send failed")[:240],
            "cleanup": cleanup,
            "cleanupConfirmed": cleanup_confirmed,
        }
    try:
        edited = module.edit_card(
            target,
            "<pre>TEMPORARY QA CANARY\n- send ok\n- edit ok\n- final pending</pre>",
            None,
            15,
            chat_id=chat_id,
            thread_id=thread_id,
        )
    except Exception as exc:
        edited = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    failures = []
    if not edited.get("ok"):
        failures.append(f"basic edit failed: {str(edited.get('error') or 'edit failed')[:160]}")
    else:
        try:
            final_text = module.build_completion_summary(
                title="Temporary Telegram response canary",
                status="done",
                model="system/transport-canary",
                now="Confirmed the separate final retained one delivery receipt",
                done=[
                    "Confirmed the temporary card was sent to the owned topic.",
                    "Verified the same card accepted its terminal edit.",
                ],
                next_step="No action needed.",
                blocker="None",
            )
            final_problems = validate(final_text, module)
        except Exception as exc:
            final_text = ""
            final_problems = [f"final renderer failed: {type(exc).__name__}"]
        if final_problems:
            failures.append("basic structured final validation failed")
        else:
            final_attempts = 1
            write_canary_journal(
                journal_path,
                stage="basic-final-intent",
                chat_id=chat_id,
                thread_id=thread_id,
                message_ids=sent_ids,
                indeterminate_stages=[*indeterminate_stages, "basic-final"],
            )
            try:
                final = module.send_final_summary(
                    final_text,
                    15,
                    chat_id=chat_id,
                    thread_id=thread_id,
                )
            except Exception as exc:
                final = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            final_target = message_id(final)
            if final_target:
                sent_ids.append(final_target)
                final_ids.append(final_target)
            if delivery_is_indeterminate(module, final):
                (indeterminate_ids if final_target else indeterminate_stages).append(
                    final_target or "basic-final"
                )
            write_canary_journal(
                journal_path,
                stage="basic-final-receipt",
                chat_id=chat_id,
                thread_id=thread_id,
                message_ids=sent_ids,
                indeterminate_stages=indeterminate_stages,
            )
            if final.get("ok") and final_target:
                final_successes = 1
            else:
                failures.append("basic separate final failed")
    cleanup = cleanup_messages(
        module,
        chat_id,
        sent_ids,
        indeterminate_ids=indeterminate_ids,
        indeterminate_stages=indeterminate_stages,
    )
    cleanup_confirmed = finalize_canary_journal(
        journal_path,
        cleanup,
        chat_id,
        thread_id,
        sent_ids,
    )
    if not (
        final_attempts == 1
        and final_successes == 1
        and len(final_ids) == 1
    ):
        failures.append("exactly-one-final contract failed")
    if not cleanup_confirmed:
        failures.append("temporary canary cleanup is incomplete or indeterminate")
    failures = list(dict.fromkeys(failures))
    return {
        "ok": not failures,
        "scope": "synthetic cumulative transport timing only; never p95 or inbound-path evidence",
        "send": bool(sent.get("ok")),
        "edit": bool(edited.get("ok")),
        "final": {
            "attempts": final_attempts,
            "successes": final_successes,
            "messageIds": final_ids,
            "exactlyOne": final_attempts == 1 and final_successes == 1 and len(final_ids) == 1,
        },
        "cleanup": cleanup,
        "cleanupConfirmed": cleanup_confirmed,
        "failures": failures,
        "timing": {
            "kind": "synthetic cumulative transport timing",
            "cumulativeMs": {"sendEditCleanup": round((time.monotonic() - start) * 1000, 1)},
        },
    }


def live_canary(
    module,
    chat_id: str,
    thread_id: str,
    journal_path: Path | None = None,
) -> dict:
    journal_path = Path(journal_path) if journal_path is not None else _legacy_live_canary_journal(
        chat_id,
        thread_id,
    )
    if not all(hasattr(module, name) for name in ("build_card", "build_rich_card", "send_rich_message", "edit_rich_card")):
        return basic_live_canary(module, chat_id, thread_id, journal_path)
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
    header_checker = getattr(module, "task_headers_enabled", None)
    header_required = bool(header_checker(chat_id, thread_id)) if callable(header_checker) else False

    def before_send(stage: str) -> None:
        nonlocal pending_send_stage
        pending_send_stage = stage
        write_canary_journal(
            journal_path,
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
            journal_path,
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
        cleanup_confirmed = finalize_canary_journal(
            journal_path,
            cleanup,
            chat_id,
            thread_id,
            sent_ids,
        )
        result_failures = list(failures)
        exactly_one_final = final_attempts == 1 and final_successes == 1 and len(final_ids) == 1
        synthetic_checks = {
            "eyesUnder2s": stage_ms.get("eyes", float("inf")) <= 2_000,
            "headerUnder5s": (not header_required) or stage_ms.get("header", float("inf")) <= 5_000,
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
        if not cleanup_confirmed:
            result_failures.append("temporary canary cleanup is incomplete or indeterminate")
        result_failures = list(dict.fromkeys(result_failures))
        return {
            "ok": not result_failures,
            "scope": "synthetic cumulative response timing begins after the canary anchor receipt; never p95 or inbound-path evidence",
            "renderer": renderer,
            "headerRequired": header_required,
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
            "cleanupConfirmed": cleanup_confirmed,
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

        if header_required:
            if not hasattr(module, "build_task_header"):
                failures.append("diagnostic task header is enabled but its renderer is unavailable")
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
                terminal_render_verified = (
                    "Progress [██████████] 6/6" in pre_text(legacy)
                    and "██████████ 100% · stage 6/6" in final_plain_text(rich)
                    and rich.count(" checked") == len(MILESTONE_UPDATES)
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
            done=[
                "Confirmed the eyes reaction reached the exact Inbox topic.",
                "Verified the native rich live card closed through all six stages.",
                "Confirmed exactly one final delivery passed with no remaining work.",
            ],
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


def production_canary_stdout(result: dict) -> dict:
    """Project a live run to statuses/counts; identifiers stay in the journal."""
    stress = result.get("stress") if isinstance(result.get("stress"), dict) else {}
    transport = result.get("transport") if isinstance(result.get("transport"), dict) else None
    projected: dict[str, object] = {
        "role": str(result.get("role") or ""),
        "ok": bool(result.get("ok")),
        "status": "passed" if result.get("ok") else "failed",
        "problemCount": len(result.get("problems") or []),
        "stress": {
            "ok": bool(stress.get("ok")),
            "status": "passed" if stress.get("ok") else "failed",
            "iterations": int(stress.get("iterations") or 0),
            "renderedCards": int(stress.get("renderedCards") or 0),
            "problemCount": len(stress.get("problems") or []),
        },
        "transport": {
            "ok": False,
            "status": "not-run",
            "failureCount": 0,
            "cleanup": {
                "status": "not-run",
                "attempted": 0,
                "deleted": 0,
                "failedCount": 0,
                "indeterminateCount": 0,
            },
            "final": {"attempts": 0, "successes": 0, "count": 0},
        },
    }
    if transport is None:
        return projected
    cleanup = transport.get("cleanup") if isinstance(transport.get("cleanup"), dict) else {}
    final = transport.get("final") if isinstance(transport.get("final"), dict) else {}
    cleanup_confirmed = bool(transport.get("cleanupConfirmed"))
    projected["transport"] = {
        "ok": bool(transport.get("ok")),
        "status": "passed" if transport.get("ok") else "failed",
        "failureCount": len(transport.get("failures") or []) + int(bool(transport.get("error"))),
        "cleanup": {
            "status": "confirmed" if cleanup_confirmed else "pending",
            "attempted": int(cleanup.get("attempted") or 0),
            "deleted": int(cleanup.get("deleted") or 0),
            "failedCount": len(cleanup.get("failedIds") or []),
            "indeterminateCount": len(cleanup.get("indeterminateIds") or [])
            + len(cleanup.get("indeterminateStages") or []),
        },
        "final": {
            "attempts": int(final.get("attempts") or 0),
            "successes": int(final.get("successes") or 0),
            "count": len(final.get("messageIds") or []),
        },
    }
    return projected


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
    parser.add_argument(
        "--canary-journal-dir",
        default=os.environ.get(CANARY_JOURNAL_ENV, ""),
        help=(
            "caller-created one-shot 0700 directory for the private 0600 live-canary journal; "
            "the directory cannot be reused; defaults to TELEGRAM_CANARY_CLEANUP_JOURNAL"
        ),
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
        if not args.canary_journal_dir:
            target_problems.append("--canary-journal-dir is required for a one-shot live canary")
        problems.extend(target_problems)
        if not target_problems:
            try:
                journal_path = prepare_canary_journal(
                    args.canary_journal_dir,
                    role=args.role,
                )
            except (OSError, RuntimeError):
                problems.append("private one-shot canary journal preparation failed")
            else:
                transport = live_canary(
                    module,
                    args.chat_id,
                    args.thread_id,
                    journal_path,
                )
                if not transport.get("ok"):
                    problems.append("live send/edit/delete canary failed")
    result = {"role": args.role, "ok": not problems, "problems": sorted(set(problems)), "stress": stress, "transport": transport}
    print(json.dumps(production_canary_stdout(result) if args.live else result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
