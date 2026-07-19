#!/usr/bin/env python3
"""Publish counts-only evidence that JAIMES completions were actually delivered.

The private work-card and watcher states contain Telegram message IDs, task
identifiers, and objective text. None of that leaves JAIMES. This module emits
only aggregate counts and fixed issue codes for Control Tower ingestion.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
DEFAULT_WORK_CARDS = WORKSPACE / "memory" / "jaimes_work_cards.json"
DEFAULT_FAST_ACK = Path.home() / ".openclaw" / "telegram" / "jaimes_fast_ack_state.json"
DEFAULT_OUTPUT = ROOT / "data" / "jaimes-completion-evidence.json"
TERMINAL = {"done", "complete", "completed", "failed", "blocked", "cancelled", "canceled"}
UTC = dt.timezone.utc


def iso(value: dt.datetime | None = None) -> str:
    return (value or dt.datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return fallback


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def positive_message_id(value: Any) -> bool:
    text = str(value or "")
    return text.isdigit() and int(text) > 0


def card_time(card: dict[str, Any]) -> dt.datetime | None:
    for key in (
        "final_delivery_confirmed_at",
        "updated_at",
        "completed_at",
        "task_started_at",
        "started_at",
    ):
        parsed = parse_time(card.get(key))
        if parsed:
            return parsed
    return None


def build_completion_evidence(
    work_card_state: Any,
    fast_ack_state: Any,
    *,
    now: dt.datetime | None = None,
    window_hours: int = 24,
) -> dict[str, Any]:
    current = (now or dt.datetime.now(UTC)).astimezone(UTC)
    cards = work_card_state.get("cards") if isinstance(work_card_state, dict) else {}
    active = fast_ack_state.get("active_cards") if isinstance(fast_ack_state, dict) else {}
    cards = cards if isinstance(cards, dict) else {}
    active = active if isinstance(active, dict) else {}
    watchers = {
        str(row.get("key")): row
        for row in active.values()
        if isinstance(row, dict) and row.get("key")
    }

    completed = identity_bound = linked = verified = mismatches = unverified = stale = 0
    cutoff = current - dt.timedelta(hours=max(1, window_hours))
    for key, source in cards.items():
        if not isinstance(source, dict) or str(source.get("status") or "").lower() not in TERMINAL:
            continue
        stamp = card_time(source)
        if stamp is None or stamp < cutoff or stamp > current + dt.timedelta(minutes=2):
            continue
        completed += 1
        identity = (
            str(source.get("work_id") or ""),
            str(source.get("run_id") or ""),
            str(source.get("task_started_at") or ""),
        )
        if not all(identity) or parse_time(identity[2]) is None:
            continue
        identity_bound += 1
        watcher = watchers.get(str(key))
        mismatch = False
        evidence_current = False
        if isinstance(watcher, dict):
            watcher_identity = (
                str(watcher.get("work_id") or ""),
                str(watcher.get("ledger_run_id") or ""),
                str(watcher.get("task_started_at") or ""),
            )
            mismatch = any(observed and observed != expected for observed, expected in zip(watcher_identity, identity))
            evidence_status = str(watcher.get("final_evidence_status") or "").lower()
            evidence_current = evidence_status == "current"
            stale += int(evidence_status == "stale")

        message_linked = positive_message_id(source.get("final_message_id"))
        if message_linked:
            linked += 1
        task_start = parse_time(identity[2])
        confirmed_at = parse_time(source.get("final_delivery_confirmed_at"))
        delivery_verified = (
            message_linked
            and str(source.get("final_delivery_verified_by") or "") == "hermes-adapter-success"
            and confirmed_at is not None
            and task_start is not None
            and confirmed_at >= task_start
        )
        if delivery_verified:
            verified += 1
        if mismatch:
            mismatches += 1
        if mismatch or not message_linked or not delivery_verified or not evidence_current:
            unverified += 1

    ready = completed > 0 and completed == identity_bound == linked == verified and mismatches == 0 and unverified == 0
    attention = mismatches > 0
    issues = []
    if completed == 0:
        issues.append("no-recent-completed-samples")
    if completed > identity_bound:
        issues.append("legacy-or-unbound-completions")
    if identity_bound > linked:
        issues.append("missing-final-message-links")
    if linked > verified:
        issues.append("unverified-delivery-links")
    if unverified:
        issues.append("incomplete-current-run-evidence")
    if mismatches:
        issues.append("task-identity-mismatch")
    return {
        "version": 1,
        "owner": "jaimes",
        "privacy": "dashboard-safe",
        "checkedAt": iso(current),
        "status": "attention" if attention else "ok" if ready else "watch",
        "ok": ready,
        "scope": f"counts-only completed work-card audit over the last {max(1, window_hours)} hours",
        "completedRuns": completed,
        "identityBoundRuns": identity_bound,
        "finalMessagesRequired": identity_bound,
        "finalMessagesLinked": linked,
        "deliveryVerifiedRuns": verified,
        "mismatches": mismatches,
        "unverifiedCompletions": unverified,
        "staleEvidenceDetected": stale,
        "issues": issues,
        "contentPolicy": "No task IDs, message IDs, objectives, prompts, account data, or raw evidence leave JAIMES.",
    }


def write_completion_evidence(
    work_card_path: Path = DEFAULT_WORK_CARDS,
    fast_ack_path: Path = DEFAULT_FAST_ACK,
    output_path: Path = DEFAULT_OUTPUT,
    *,
    now: dt.datetime | None = None,
    window_hours: int = 24,
) -> dict[str, Any]:
    payload = build_completion_evidence(
        read_json(work_card_path, {}),
        read_json(fast_ack_path, {}),
        now=now,
        window_hours=window_hours,
    )
    atomic_write(output_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-cards", type=Path, default=DEFAULT_WORK_CARDS)
    parser.add_argument("--fast-ack", type=Path, default=DEFAULT_FAST_ACK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = build_completion_evidence(
        read_json(args.work_cards, {}),
        read_json(args.fast_ack, {}),
        window_hours=args.window_hours,
    )
    if not args.check:
        atomic_write(args.output, payload)
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0 if payload["status"] != "attention" else 1


if __name__ == "__main__":
    raise SystemExit(main())
