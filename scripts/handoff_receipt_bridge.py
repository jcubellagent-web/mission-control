#!/usr/bin/env python3
"""Identity and receipt bridge for Control Tower cross-agent handoffs.

The handoff queue is a compatibility sidecar, while the SQLite work ledger is
the canonical lifecycle store.  This module gives each new handoff an explicit
link to that lifecycle and records small, dashboard-safe receipts without
copying prompts, chat identifiers, objectives, or other private content.

Receipt writes are deterministic and additive.  The ``report`` command is
strictly read-only: it describes legacy gaps and exact-identity conflicts but
never migrates, deletes, or rewrites historical rows.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("CONTROL_TOWER_DATA_DIR", ROOT / "data"))
DEFAULT_HANDOFF_PATH = DATA_DIR / "handoff-queue.json"
DEFAULT_TASK_PATH = DATA_DIR / "agent-task-queue.json"
DEFAULT_WORK_DB_PATH = Path(
    os.environ.get("CONTROL_TOWER_WORK_DB", DATA_DIR / "control-tower-work.sqlite3")
)

HANDOFF_SCHEMA_VERSION = 2
RECEIPT_SCHEMA_VERSION = 1
RECEIPT_KINDS = {"sent", "acknowledged", "terminal"}
TERMINAL_STATUSES = {"done", "blocked", "error", "cancelled"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
AGENT_ALIASES = {
    "josh": "josh2",
    "josh2": "josh2",
    "josh2.0": "josh2",
    "josh 2.0": "josh2",
    "jaimes": "jaimes",
    "jain": "jain",
    "j.a.i.n": "jain",
    "joshex": "joshex",
    "codex": "joshex",
}


class HandoffReceiptError(RuntimeError):
    """Raised when a receipt would be ambiguous, conflicting, or unsafe."""


def utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def canonical_agent(value: Any, *, allow_generic: bool = False) -> str:
    raw = " ".join(str(value or "").strip().lower().replace("_", " ").split())
    if allow_generic and raw in {"", "agent"}:
        return raw
    canonical = AGENT_ALIASES.get(raw)
    if not canonical:
        raise HandoffReceiptError(f"Unknown agent identifier: {value!r}")
    return canonical


def safe_identifier(value: Any, label: str, *, required: bool = True) -> str:
    text = str(value or "").strip()
    if not text and not required:
        return ""
    if not IDENTIFIER.fullmatch(text):
        raise HandoffReceiptError(f"{label} must be a dashboard-safe identifier.")
    return text


def safe_origin_hash(value: Any, *, required: bool = True) -> str:
    text = str(value or "").strip().lower()
    if not text and not required:
        return ""
    if not SHA256.fullmatch(text):
        raise HandoffReceiptError("originClaimHash must be a lowercase SHA-256 digest.")
    return text


def safe_timestamp(value: Any) -> str:
    text = str(value or "").strip() or utc_now()
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HandoffReceiptError("recordedAt must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise HandoffReceiptError("recordedAt must include a timezone.")
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        raise HandoffReceiptError(f"Cannot read valid JSON from {path.name}.") from exc


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def receipt_for(row: dict[str, Any], kind: str) -> dict[str, Any] | None:
    receipts = row.get("receipts")
    if not isinstance(receipts, list):
        return None
    for receipt in receipts:
        if isinstance(receipt, dict) and receipt.get("kind") == kind:
            return receipt
    return None


def receipt_state(row: dict[str, Any]) -> dict[str, Any]:
    sender = receipt_for(row, "sent")
    acknowledgement = receipt_for(row, "acknowledged")
    terminal = receipt_for(row, "terminal")
    return {
        "senderReceiptId": (sender or {}).get("id") or row.get("senderReceiptId"),
        "recipientAckReceiptId": (acknowledgement or {}).get("id")
        or row.get("recipientAckReceiptId"),
        "terminalResultReceiptId": (terminal or {}).get("id")
        or row.get("terminalResultReceiptId"),
        "terminalStatus": (terminal or {}).get("status") or row.get("terminalResultStatus"),
    }


def terminal_result_receipt(row: dict[str, Any]) -> dict[str, Any] | None:
    receipt = receipt_for(row, "terminal")
    if receipt and str(receipt.get("status") or "").lower() in TERMINAL_STATUSES:
        return receipt
    return None


def deterministic_receipt_id(
    *,
    handoff_id: str,
    kind: str,
    agent: str,
    work_id: str = "",
    run_id: str = "",
    task_id: str = "",
    status: str = "",
) -> str:
    if kind not in RECEIPT_KINDS:
        raise HandoffReceiptError(f"Unknown receipt kind: {kind}")
    stable_identity = json.dumps(
        {
            "schema": RECEIPT_SCHEMA_VERSION,
            "handoffId": safe_identifier(handoff_id, "handoff_id"),
            "kind": kind,
            "agent": canonical_agent(agent),
            "workId": safe_identifier(work_id, "work_id", required=False),
            "runId": safe_identifier(run_id, "run_id", required=False),
            "taskId": safe_identifier(task_id, "task_id", required=False),
            "status": status,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(stable_identity.encode("utf-8")).hexdigest()[:24]
    label = {"sent": "sent", "acknowledged": "ack", "terminal": "result"}[kind]
    return f"hreceipt-{label}-{digest}"


def make_receipt(
    row: dict[str, Any],
    *,
    kind: str,
    agent: Any,
    event_id: Any = "",
    task_id: Any = "",
    status: Any = "",
    recorded_at: Any = "",
) -> dict[str, Any]:
    handoff_id = safe_identifier(row.get("id"), "handoff_id")
    work_id = safe_identifier(row.get("workId"), "work_id", required=False)
    run_id = safe_identifier(row.get("runId"), "run_id", required=False)
    agent_id = canonical_agent(agent)
    event = safe_identifier(event_id, "event_id", required=False)
    task = safe_identifier(task_id, "task_id", required=False)
    normalized_status = str(status or "").strip().lower()
    if kind == "sent" and not event:
        raise HandoffReceiptError("A sender receipt requires its canonical event_id.")
    if kind == "terminal" and normalized_status not in TERMINAL_STATUSES:
        raise HandoffReceiptError(
            "A terminal receipt requires status done, blocked, error, or cancelled."
        )
    if kind != "terminal" and normalized_status:
        raise HandoffReceiptError("Only a terminal receipt may carry status.")
    receipt = {
        "id": deterministic_receipt_id(
            handoff_id=handoff_id,
            kind=kind,
            agent=agent_id,
            work_id=work_id,
            run_id=run_id,
            task_id=task,
            status=normalized_status,
        ),
        "kind": kind,
        "agent": agent_id,
        "recordedAt": safe_timestamp(recorded_at),
    }
    if event:
        receipt["eventId"] = event
    if task:
        receipt["taskId"] = task
    if work_id:
        receipt["workId"] = work_id
    if run_id:
        receipt["runId"] = run_id
    if normalized_status:
        receipt["status"] = normalized_status
    return receipt


def attach_sender_receipt(record: dict[str, Any]) -> dict[str, Any]:
    """Return a new canonical handoff row with a deterministic sender receipt."""
    handoff = dict(record)
    safe_identifier(handoff.get("id"), "handoff_id")
    safe_identifier(handoff.get("workId"), "work_id")
    safe_identifier(handoff.get("runId"), "run_id")
    safe_origin_hash(handoff.get("originClaimHash"))
    sender_event_id = safe_identifier(
        handoff.get("senderEventId") or handoff.get("id"), "sender_event_id"
    )
    sender = canonical_agent(handoff.get("from"))
    receipt = make_receipt(
        handoff,
        kind="sent",
        agent=sender,
        event_id=sender_event_id,
        recorded_at=handoff.get("time"),
    )
    handoff["handoffSchemaVersion"] = HANDOFF_SCHEMA_VERSION
    handoff["receiptSchemaVersion"] = RECEIPT_SCHEMA_VERSION
    handoff["senderEventId"] = sender_event_id
    handoff["senderReceiptId"] = receipt["id"]
    handoff["receipts"] = [receipt]
    return handoff


def _merge_receipts(
    existing: Iterable[Any], incoming: Iterable[Any]
) -> tuple[list[dict[str, Any]], bool]:
    rows = [dict(row) for row in existing if isinstance(row, dict)]
    by_id = {str(row.get("id")): row for row in rows if row.get("id")}
    changed = False
    for receipt in incoming:
        if not isinstance(receipt, dict):
            continue
        receipt_id = safe_identifier(receipt.get("id"), "receipt_id")
        prior = by_id.get(receipt_id)
        if prior:
            stable_keys = ("kind", "agent", "workId", "runId", "taskId", "status")
            if any(str(prior.get(key) or "") != str(receipt.get(key) or "") for key in stable_keys):
                raise HandoffReceiptError(f"Receipt id {receipt_id} has conflicting identity.")
            continue
        kind = str(receipt.get("kind") or "")
        same_kind = next((row for row in rows if row.get("kind") == kind), None)
        if same_kind:
            raise HandoffReceiptError(
                f"Handoff already has a different {kind} receipt: {same_kind.get('id')}"
            )
        copied = dict(receipt)
        rows.append(copied)
        by_id[receipt_id] = copied
        changed = True
    return rows, changed


def write_new_handoff(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Insert one handoff without truncating history; retries only add missing fields."""
    prepared = attach_sender_receipt(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        document = read_json(path, {"handoffs": []})
        if not isinstance(document, dict):
            raise HandoffReceiptError("handoff-queue.json must contain an object.")
        handoffs = document.get("handoffs", [])
        if not isinstance(handoffs, list):
            raise HandoffReceiptError("handoff-queue.json handoffs must be an array.")
        existing_index = next(
            (
                index
                for index, row in enumerate(handoffs)
                if isinstance(row, dict) and row.get("id") == prepared["id"]
            ),
            None,
        )
        created = existing_index is None
        changed = created
        if created:
            handoffs.insert(0, prepared)
            result_row = prepared
        else:
            existing = dict(handoffs[existing_index])
            for key in ("workId", "runId", "originClaimHash", "senderEventId"):
                old, new = str(existing.get(key) or ""), str(prepared.get(key) or "")
                if old and new and old != new:
                    raise HandoffReceiptError(
                        f"Existing handoff {prepared['id']} conflicts on {key}."
                    )
                if not old and new:
                    existing[key] = prepared[key]
                    changed = True
            receipts, receipt_changed = _merge_receipts(
                existing.get("receipts", []), prepared.get("receipts", [])
            )
            if receipt_changed or "receipts" not in existing:
                existing["receipts"] = receipts
                changed = True
            for key in ("handoffSchemaVersion", "receiptSchemaVersion", "senderReceiptId"):
                if not existing.get(key):
                    existing[key] = prepared[key]
                    changed = True
            handoffs[existing_index] = existing
            result_row = existing
        if changed:
            document["handoffs"] = handoffs
            atomic_write_json(path, document)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return {"created": created, "updated": changed and not created, "handoff": result_row}


def _find_handoff(
    handoffs: list[Any],
    *,
    handoff_id: str = "",
    work_id: str = "",
    run_id: str = "",
    origin_claim_hash: str = "",
    recipient: str = "",
) -> tuple[int, dict[str, Any]]:
    rows = [(index, row) for index, row in enumerate(handoffs) if isinstance(row, dict)]
    if handoff_id:
        matches = [(index, row) for index, row in rows if row.get("id") == handoff_id]
    else:
        if not work_id:
            raise HandoffReceiptError("Provide handoff_id or exact work_id to record a receipt.")
        matches = [(index, row) for index, row in rows if row.get("workId") == work_id]
        if run_id:
            matches = [(index, row) for index, row in matches if row.get("runId") == run_id]
        if origin_claim_hash:
            matches = [
                (index, row)
                for index, row in matches
                if row.get("originClaimHash") == origin_claim_hash
            ]
    if recipient and len(matches) > 1:
        targeted = []
        for index, row in matches:
            target = canonical_agent(row.get("to"), allow_generic=True)
            if target in {"", "agent", recipient}:
                targeted.append((index, row))
        matches = targeted
    if not matches:
        raise HandoffReceiptError("No handoff matches the exact supplied identity.")
    if len(matches) != 1:
        raise HandoffReceiptError("Receipt target is ambiguous; provide handoff_id.")
    index, row = matches[0]
    return index, dict(row)


def record_receipt(
    path: Path,
    *,
    kind: str,
    agent: Any,
    handoff_id: Any = "",
    work_id: Any = "",
    run_id: Any = "",
    origin_claim_hash: Any = "",
    event_id: Any = "",
    task_id: Any = "",
    status: Any = "",
    recorded_at: Any = "",
) -> dict[str, Any]:
    """Append one acknowledgement/result receipt, or return its prior write."""
    if kind not in {"acknowledged", "terminal"}:
        raise HandoffReceiptError("Runtime receipt kind must be acknowledged or terminal.")
    handoff_identity = safe_identifier(handoff_id, "handoff_id", required=False)
    work_identity = safe_identifier(work_id, "work_id", required=False)
    run_identity = safe_identifier(run_id, "run_id", required=False)
    claim_identity = safe_origin_hash(origin_claim_hash, required=False)
    agent_id = canonical_agent(agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        document = read_json(path, {"handoffs": []})
        if not isinstance(document, dict) or not isinstance(document.get("handoffs", []), list):
            raise HandoffReceiptError("handoff-queue.json has an invalid shape.")
        handoffs = document.get("handoffs", [])
        index, row = _find_handoff(
            handoffs,
            handoff_id=handoff_identity,
            work_id=work_identity,
            run_id=run_identity,
            origin_claim_hash=claim_identity,
            recipient=agent_id,
        )
        for key, supplied in (
            ("workId", work_identity),
            ("runId", run_identity),
            ("originClaimHash", claim_identity),
        ):
            stored = str(row.get(key) or "")
            if supplied and stored and supplied != stored:
                raise HandoffReceiptError(f"Receipt conflicts with handoff {key}.")
        target = canonical_agent(row.get("to"), allow_generic=True)
        if target not in {"", "agent", agent_id}:
            raise HandoffReceiptError(
                f"Agent {agent_id} cannot acknowledge a handoff addressed to {target}."
            )
        receipt = make_receipt(
            row,
            kind=kind,
            agent=agent_id,
            event_id=event_id,
            task_id=task_id,
            status=status,
            recorded_at=recorded_at,
        )
        receipts, changed = _merge_receipts(row.get("receipts", []), [receipt])
        if not changed:
            prior = next(item for item in receipts if item.get("id") == receipt["id"])
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return {"created": False, "receipt": prior, "handoffId": row.get("id")}
        row["handoffSchemaVersion"] = HANDOFF_SCHEMA_VERSION
        row["receiptSchemaVersion"] = RECEIPT_SCHEMA_VERSION
        row["receipts"] = receipts
        if kind == "acknowledged":
            row["recipientAckReceiptId"] = receipt["id"]
        else:
            row["terminalResultReceiptId"] = receipt["id"]
            row["terminalResultStatus"] = receipt["status"]
        handoffs[index] = row
        document["handoffs"] = handoffs
        atomic_write_json(path, document)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return {"created": True, "receipt": receipt, "handoffId": row.get("id")}


def _task_matches(
    tasks: list[dict[str, Any]], handoff: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    work_id = str(handoff.get("workId") or "")
    if work_id:
        return (
            [task for task in tasks if str(task.get("workId") or "") == work_id],
            "work-id",
        )
    task_id = str(
        handoff.get("taskId")
        or handoff.get("receivingTaskId")
        or handoff.get("terminalTaskId")
        or ""
    )
    if task_id:
        return (
            [task for task in tasks if str(task.get("id") or "") == task_id],
            "task-id",
        )
    return [], ""


def _ledger_events(db_path: Path, event_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not db_path.exists() or not event_ids:
        return {}
    quoted = ",".join("?" for _ in event_ids)
    uri = f"file:{db_path.resolve()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"SELECT event_id, work_id, run_id, origin_claim_hash "
                f"FROM work_events WHERE event_id IN ({quoted})",
                tuple(sorted(event_ids)),
            ).fetchall()
    except sqlite3.Error as exc:
        raise HandoffReceiptError("Cannot read the canonical work ledger in read-only mode.") from exc
    return {
        str(row["event_id"]): {
            "workId": row["work_id"],
            "runId": row["run_id"],
            "originClaimHash": row["origin_claim_hash"],
        }
        for row in rows
    }


def build_reconciliation_report(
    handoff_path: Path,
    task_path: Path,
    work_db_path: Path | None = None,
    *,
    generated_at: str | None = None,
    include_row_identifiers: bool = False,
) -> dict[str, Any]:
    """Inspect exact links and receipt gaps without changing any source file."""
    handoff_doc = read_json(handoff_path, {"handoffs": []})
    task_doc = read_json(task_path, {"tasks": []})
    handoffs = [
        row for row in handoff_doc.get("handoffs", []) if isinstance(row, dict)
    ] if isinstance(handoff_doc, dict) else []
    tasks = [
        row for row in task_doc.get("tasks", []) if isinstance(row, dict)
    ] if isinstance(task_doc, dict) else []
    sender_event_ids = {
        str(row.get("senderEventId") or "") for row in handoffs if row.get("senderEventId")
    }
    ledger = _ledger_events(work_db_path, sender_event_ids) if work_db_path else {}
    ledger_available = bool(work_db_path and work_db_path.exists())
    counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for handoff in handoffs:
        counts["totalHandoffs"] += 1
        handoff_id = str(handoff.get("id") or "")
        work_id = str(handoff.get("workId") or "")
        run_id = str(handoff.get("runId") or "")
        origin_hash = str(handoff.get("originClaimHash") or "")
        sender_event_id = str(handoff.get("senderEventId") or "")
        state = receipt_state(handoff)
        issues: list[str] = []
        canonical_identity = bool(
            IDENTIFIER.fullmatch(work_id)
            and IDENTIFIER.fullmatch(run_id)
            and SHA256.fullmatch(origin_hash)
        )
        if canonical_identity:
            counts["canonicalIdentity"] += 1
        else:
            counts["legacyOrPartialIdentity"] += 1
            issues.append("legacy-or-partial-identity")
        matches, match_identity = _task_matches(tasks, handoff)
        task_match = "none"
        task_id = ""
        task_status = ""
        if len(matches) == 1:
            counts["exactTaskMatches"] += 1
            counts[
                "exactWorkIdMatches" if match_identity == "work-id" else "exactTaskIdMatches"
            ] += 1
            task_match = f"exact-{match_identity}"
            task_id = str(matches[0].get("id") or "")
            task_status = str(matches[0].get("status") or "")
            if run_id and matches[0].get("runId") and matches[0].get("runId") != run_id:
                issues.append("task-run-id-conflict")
            if origin_hash and matches[0].get("originClaimHash") and matches[0].get("originClaimHash") != origin_hash:
                issues.append("task-origin-claim-conflict")
        elif len(matches) > 1:
            counts["ambiguousTaskMatches"] += 1
            task_match = "ambiguous-work-id"
            issues.append("ambiguous-task-work-id")
        else:
            counts["unmatchedHandoffs"] += 1
            if work_id:
                issues.append("no-exact-task-match")
        if state["senderReceiptId"]:
            counts["senderReceipts"] += 1
        else:
            issues.append("missing-sender-receipt")
        if state["recipientAckReceiptId"]:
            counts["recipientAcknowledgements"] += 1
        else:
            issues.append("missing-recipient-acknowledgement")
        if state["terminalResultReceiptId"]:
            counts["terminalResultReceipts"] += 1
        elif str(handoff.get("status") or "").lower() in {"open", "active", "queued", "blocked"}:
            issues.append("missing-terminal-result")
        ledger_match = "not-checked"
        if ledger_available:
            canonical_event = ledger.get(sender_event_id)
            if not sender_event_id:
                ledger_match = "missing-sender-event-id"
                issues.append("missing-sender-event-id")
            elif not canonical_event:
                ledger_match = "event-not-found"
                issues.append("sender-event-not-in-work-ledger")
            elif (
                canonical_event["workId"] == work_id
                and canonical_event["runId"] == run_id
                and canonical_event["originClaimHash"] == origin_hash
            ):
                ledger_match = "exact"
                counts["exactLedgerMatches"] += 1
            else:
                ledger_match = "identity-conflict"
                counts["ledgerIdentityConflicts"] += 1
                issues.append("sender-event-identity-conflict")
        if issues:
            counts["rowsNeedingReview"] += 1
        rows.append({
            "handoffId": handoff_id,
            "workId": work_id or None,
            "runId": run_id or None,
            "taskMatch": task_match,
            "taskId": task_id or None,
            "taskStatus": task_status or None,
            "ledgerMatch": ledger_match,
            "senderReceiptId": state["senderReceiptId"] or None,
            "recipientAckReceiptId": state["recipientAckReceiptId"] or None,
            "terminalResultReceiptId": state["terminalResultReceiptId"] or None,
            "terminalStatus": state["terminalStatus"] or None,
            "issues": issues,
        })
    ordered_counts = {
        key: counts.get(key, 0)
        for key in (
            "totalHandoffs",
            "canonicalIdentity",
            "legacyOrPartialIdentity",
            "exactTaskMatches",
            "exactWorkIdMatches",
            "exactTaskIdMatches",
            "ambiguousTaskMatches",
            "unmatchedHandoffs",
            "senderReceipts",
            "recipientAcknowledgements",
            "terminalResultReceipts",
            "exactLedgerMatches",
            "ledgerIdentityConflicts",
            "rowsNeedingReview",
        )
    }
    issue_counts: Counter[str] = Counter(
        issue for row in rows for issue in row.get("issues", [])
    )
    report = {
        "schemaVersion": 1,
        "handoffSchemaVersion": HANDOFF_SCHEMA_VERSION,
        "receiptSchemaVersion": RECEIPT_SCHEMA_VERSION,
        "generatedAt": safe_timestamp(generated_at or utc_now()),
        "mode": "read-only-reconciliation",
        "sourceFiles": {
            "handoffs": handoff_path.name,
            "tasks": task_path.name,
            "workLedger": work_db_path.name if work_db_path else None,
            "workLedgerAvailable": ledger_available,
        },
        "counts": ordered_counts,
        "issueCounts": dict(sorted(issue_counts.items())),
        "mutationPolicy": "No source rows were added, deleted, backfilled, or rewritten.",
    }
    if include_row_identifiers:
        report["rows"] = rows
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-path", type=Path, default=DEFAULT_HANDOFF_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    acknowledge = subparsers.add_parser("acknowledge")
    acknowledge.add_argument("--agent", required=True)
    acknowledge.add_argument("--handoff-id", default="")
    acknowledge.add_argument("--work-id", default="")
    acknowledge.add_argument("--run-id", default="")
    acknowledge.add_argument("--origin-claim-hash", default="")
    acknowledge.add_argument("--event-id", default="")
    acknowledge.add_argument("--task-id", default="")
    acknowledge.add_argument("--recorded-at", default="")

    result = subparsers.add_parser("result")
    result.add_argument("--agent", required=True)
    result.add_argument("--status", required=True, choices=sorted(TERMINAL_STATUSES))
    result.add_argument("--handoff-id", default="")
    result.add_argument("--work-id", default="")
    result.add_argument("--run-id", default="")
    result.add_argument("--origin-claim-hash", default="")
    result.add_argument("--event-id", default="")
    result.add_argument("--task-id", default="")
    result.add_argument("--recorded-at", default="")

    report = subparsers.add_parser("report")
    report.add_argument("--task-path", type=Path, default=DEFAULT_TASK_PATH)
    report.add_argument("--work-db", type=Path, default=DEFAULT_WORK_DB_PATH)
    report.add_argument(
        "--include-row-identifiers",
        action="store_true",
        help="Include dashboard-safe handoff/task identifiers; aggregate-only by default.",
    )
    args = parser.parse_args()

    if args.command == "report":
        payload = build_reconciliation_report(
            args.handoff_path,
            args.task_path,
            args.work_db,
            include_row_identifiers=args.include_row_identifiers,
        )
    else:
        payload = record_receipt(
            args.handoff_path,
            kind="acknowledged" if args.command == "acknowledge" else "terminal",
            agent=args.agent,
            handoff_id=args.handoff_id,
            work_id=args.work_id,
            run_id=args.run_id,
            origin_claim_hash=args.origin_claim_hash,
            event_id=args.event_id,
            task_id=args.task_id,
            status=getattr(args, "status", ""),
            recorded_at=args.recorded_at,
        )
    print(json.dumps({"ok": True, **payload}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
