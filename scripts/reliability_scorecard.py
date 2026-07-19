#!/usr/bin/env python3
"""Build a dashboard-safe, evidence-backed reliability scorecard.

The scorecard deliberately avoids a composite score. A single green number can
hide a broken safety boundary, so each control reports its own evidence and
falls back to ``watch`` when a source is missing, stale, or malformed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_PATH = DATA_DIR / "reliability-upgrades.json"
UTC = dt.timezone.utc
TERMINAL_STATUSES = {"complete", "completed", "done", "failed", "blocked", "cancelled", "canceled"}
MIN_REUSE_OUTCOMES = 20
MIN_HELPFUL_REUSE_PCT = 60.0


def iso(value: dt.datetime | None = None) -> str:
    return (value or dt.datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None, "unreadable"


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def integer(value: Any, default: int = 0) -> int:
    return int(number(value, float(default)))


def first_nonempty(mapping: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def is_fresh(value: Any, now: dt.datetime, maximum_minutes: float) -> bool:
    stamp = parse_time(value)
    if stamp is None:
        return False
    age = (now - stamp).total_seconds() / 60.0
    return -2.0 <= age <= maximum_minutes


def item(
    item_id: str,
    label: str,
    owner: str,
    status: str,
    signal: str,
    why: str,
    evidence: str,
    next_step: str,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "label": label,
        "owner": owner,
        "status": status,
        "signal": signal,
        "whyItMatters": why,
        "evidence": evidence,
        "next": next_step,
    }


def evaluation_control(data_dir: Path, now: dt.datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, error = read_json(data_dir / "reliability-reuse-eval.json")
    if not isinstance(payload, dict):
        status, passed, total = "watch", 0, 0
        signal = "Golden workflow evaluation has not produced a readable result yet."
    else:
        passed = integer(first_nonempty(payload, ("checksPassed", "passed", "passedCount")))
        total = integer(first_nonempty(payload, ("checksTotal", "total", "checkCount")))
        fresh = is_fresh(first_nonempty(payload, ("checkedAt", "updatedAt", "generatedAt")), now, 1_500)
        declared_ok = payload.get("ok") is True or str(payload.get("status") or "").lower() in {"ok", "ready", "pass", "passed"}
        status = "ready" if fresh and total > 0 and passed == total and declared_ok else "watch"
        signal = (
            f"{passed}/{total} deterministic gates passed; result is current."
            if status == "ready"
            else f"{passed}/{total} gates passed; result is incomplete, failed, or older than 25 hours."
        )
    if error:
        signal = f"Golden workflow evaluation source is {error}; no success is inferred."
    return (
        item(
            "golden-workflows",
            "Golden workflow evaluation",
            "JOSHeX",
            status,
            signal,
            "Catches regressions in privacy, handoffs, reuse, evidence, delivery, and recovery before rollout.",
            "data/reliability-reuse-eval.json",
            "Run the bounded evaluator after each related code or policy change." if status != "ready" else "Keep the evaluator current and fail closed on regressions.",
        ),
        {"label": "Golden gates", "value": f"{passed}/{total}", "status": status, "detail": "deterministic workflow checks"},
    )


def handoff_control(data_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, error = read_json(data_dir / "handoff-queue.json")
    rows = payload.get("handoffs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = []
    strict = 0
    modern = 0
    terminal_modern = 0
    terminal_closed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        receipt = row.get("receipt") if isinstance(row.get("receipt"), dict) else {}
        work_id = first_nonempty(row, ("workId", "work_id"))
        run_id = first_nonempty(row, ("runId", "run_id"))
        sender_receipt = first_nonempty(row, ("receiptId", "senderReceiptId", "senderEventId")) or first_nonempty(
            receipt, ("id", "senderReceiptId", "senderEventId")
        )
        ack = first_nonempty(
            row,
            ("recipientAckReceiptId", "recipientAckId", "ackReceiptId", "ackEventId"),
        ) or first_nonempty(
            receipt,
            ("recipientAckReceiptId", "recipientAckId", "ackReceiptId", "ackEventId"),
        )
        terminal_receipt = first_nonempty(
            row,
            ("terminalResultReceiptId", "terminalReceiptId", "completionReceiptId"),
        ) or first_nonempty(
            receipt,
            ("terminalResultReceiptId", "terminalReceiptId", "completionReceiptId"),
        )
        is_modern = any((work_id, run_id, sender_receipt, ack, terminal_receipt))
        if is_modern:
            modern += 1
        terminal_status = first_nonempty(row, ("terminalResultStatus", "status"))
        terminal = bool(terminal_receipt) or terminal_status.lower() in TERMINAL_STATUSES
        if is_modern and terminal:
            terminal_modern += 1
        complete_receipt = bool(work_id and run_id and sender_receipt and ack and (not terminal or terminal_receipt))
        if complete_receipt:
            strict += 1
        if terminal and terminal_receipt:
            terminal_closed += 1
    if error:
        status = "watch"
        signal = f"Handoff source is {error}; traceability is unknown."
    elif not rows:
        status = "watch"
        signal = "No handoff rows are available; traceability is not yet demonstrated."
    elif modern == 0:
        status = "watch"
        signal = f"0/{len(rows)} historical handoffs carry the new work/run identity and receipt chain."
    elif strict == modern and terminal_closed == terminal_modern:
        status = "ready"
        signal = f"{strict}/{modern} identity-aware handoffs have sender, acknowledgement, and required terminal receipts."
    else:
        status = "watch"
        signal = f"{strict}/{modern} identity-aware handoffs have a complete receipt chain; {len(rows) - modern} legacy rows remain read-only."
    return (
        item(
            "traceable-handoffs",
            "Traceable handoffs",
            "JOSH 2.0 + JAIMES",
            status,
            signal,
            "A work ID and durable receipts prevent dropped, duplicated, or falsely completed cross-agent work.",
            "data/handoff-queue.json",
            "Require the receipt chain on every new handoff; preserve legacy rows without rewriting history." if status != "ready" else "Monitor new handoffs and alert on missing acknowledgements.",
        ),
        {"label": "Receipt-complete", "value": f"{strict}/{modern}", "status": status, "detail": f"identity-aware handoffs; {len(rows) - modern} legacy"},
    )


def memory_control(data_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, error = read_json(data_dir / "memory-operations.json")
    retrieval = payload.get("retrieval") if isinstance(payload, dict) and isinstance(payload.get("retrieval"), dict) else {}
    helpful = integer(retrieval.get("helpful30d"))
    ignored = integer(retrieval.get("ignored30d"))
    corrected = integer(retrieval.get("corrected30d"))
    harmful = integer(retrieval.get("harmful30d"))
    outcomes = helpful + ignored + corrected + harmful
    helpful_rate = round((helpful / outcomes) * 100.0, 1) if outcomes else None
    reuse = retrieval.get("reuse") if isinstance(retrieval.get("reuse"), dict) else {}
    selected = integer(
        first_nonempty(reuse, ("selected7d", "selected30d", "selected"))
        or first_nonempty(retrieval, ("selected7d", "selected30d", "reuseSelected7d"))
    )
    used = integer(
        first_nonempty(reuse, ("used7d", "used30d", "used"))
        or first_nonempty(retrieval, ("used7d", "used30d", "reuseUsed7d"))
    )
    privacy = payload.get("privacy") if isinstance(payload, dict) and isinstance(payload.get("privacy"), dict) else {}
    unsafe_shared = integer(
        first_nonempty(
            privacy,
            ("unsafeShared", "blockedVisibilityViolations", "sharedPrivateViolations", "crossOwnerPrivateLeaks"),
        )
    )
    privacy_checked = bool(privacy) and first_nonempty(privacy, ("checkedAt", "policy", "taxonomyVersion", "classified")) is not None
    telemetry_present = (
        selected > 0
        or used > 0
        or "reuse" in retrieval
        or any(key in retrieval for key in ("selected7d", "selected30d", "reuseSelected7d"))
    )
    if error:
        status = "watch"
        signal = f"Memory operations source is {error}; reuse quality is unknown."
    elif not outcomes:
        status = "watch"
        signal = "No explicit reuse outcomes are available; hit rate is not treated as quality."
    elif not privacy_checked or not telemetry_present:
        status = "watch"
        signal = f"{helpful}/{outcomes} outcomes were helpful ({helpful_rate:.1f}%); privacy and selected-to-used telemetry are not both proven yet."
    elif outcomes < MIN_REUSE_OUTCOMES or helpful_rate is None or helpful_rate < MIN_HELPFUL_REUSE_PCT:
        status = "watch"
        signal = (
            f"{helpful_rate:.1f}% helpful across {outcomes} outcomes; readiness requires at least "
            f"{MIN_REUSE_OUTCOMES} outcomes and {MIN_HELPFUL_REUSE_PCT:.0f}% helpful."
        )
    elif unsafe_shared > 0 or corrected > 0 or harmful > 0:
        status = "watch"
        signal = f"{helpful_rate:.1f}% helpful; {corrected} corrected, {harmful} harmful, and {unsafe_shared} unsafe-shared findings require review."
    else:
        status = "ready"
        signal = f"{helpful_rate:.1f}% helpful across {outcomes} outcomes; selected {selected}, used {used}, with no reported privacy violations."
    metric_value = "not measured" if helpful_rate is None else f"{helpful_rate:.1f}%"
    return (
        item(
            "memory-reuse",
            "Reuse before work",
            "JOSH 2.0",
            status,
            signal,
            "Useful prior work saves time only when agents record whether retrieved context was actually used and helpful.",
            "data/memory-operations.json",
            "Keep retrieval fail-open, record selected versus used, and review ignored/corrected/harmful outcomes." if status != "ready" else "Tune retrieval from explicit outcomes, never from hit rate alone.",
        ),
        {
            "label": "Helpful reuse",
            "value": metric_value,
            "status": status,
            "detail": f"{helpful}/{outcomes} explicit outcomes; target >=60% across >=20; selected {selected}, used {used}",
        },
    )


def telegram_control(data_dir: Path, now: dt.datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, error = read_json(data_dir / "telegram-inbox-qa.json")
    rolling = payload.get("rolling") if isinstance(payload, dict) and isinstance(payload.get("rolling"), dict) else {}
    contract = rolling.get("contractStress") if isinstance(rolling.get("contractStress"), dict) else {}
    lanes = payload.get("lanes") if isinstance(payload, dict) and isinstance(payload.get("lanes"), dict) else {}
    stress_lane = lanes.get("stress") if isinstance(lanes.get("stress"), dict) else {}
    last_sample = stress_lane.get("lastSample") if isinstance(stress_lane.get("lastSample"), dict) else {}
    stress = last_sample.get("stress") if isinstance(last_sample.get("stress"), dict) else {}
    rendered = integer(stress.get("renderedCards"))
    problems = integer(first_nonempty(stress, ("problemCount", "violations")) or last_sample.get("problemCount"))
    p95 = number(contract.get("p95Ms"), -1.0)
    slo = number(contract.get("sloMs"), 0.0)
    samples = integer(contract.get("samples"))
    minimum = integer(contract.get("minimumSamples"))
    fresh = is_fresh(first_nonempty(last_sample, ("checkedAt", "updatedAt")) or (payload.get("updatedAt") if isinstance(payload, dict) else None), now, 65)
    ready = (
        not error
        and fresh
        and rendered > 0
        and problems == 0
        and samples >= max(minimum, 1)
        and p95 >= 0
        and slo > 0
        and p95 <= slo
        and str(contract.get("status") or "").lower() in {"ok", "ready", "pass", "passed"}
    )
    status = "ready" if ready else "watch"
    if error:
        signal = f"Telegram contract source is {error}; no delivery health is inferred."
    elif ready:
        signal = f"0/{rendered} synthetic final cards violated the contract; rolling p95 {p95:.0f} ms is within {slo:.0f} ms."
    else:
        signal = f"Contract evidence is stale or incomplete: {problems}/{rendered} problem cards, {samples}/{minimum} timing samples."
    return (
        item(
            "telegram-final-contract",
            "Telegram final contract",
            "JOSH 2.0",
            status,
            signal,
            "A deterministic final format and single-delivery contract keep chats readable and prevent duplicate or orphaned results.",
            "data/telegram-inbox-qa.json",
            "Restore fresh zero-violation stress evidence before treating delivery as healthy." if status != "ready" else "Keep synthetic checks quiet; use real work only for passive transport evidence.",
        ),
        {"label": "Final-card problems", "value": f"{problems}/{rendered}", "status": status, "detail": "dashboard-safe synthetic contract QA"},
    )


def recovery_control(data_dir: Path, now: dt.datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, error = read_json(data_dir / "ecosystem-qa-scheduler.json")
    jobs = payload.get("jobs") if isinstance(payload, dict) and isinstance(payload.get("jobs"), dict) else {}
    probe = jobs.get("runtime-service-probe") if isinstance(jobs.get("runtime-service-probe"), dict) else {}
    stamp = first_nonempty(probe, ("completedAt", "startedAt"))
    fresh = is_fresh(stamp, now, 12)
    failure_streak = integer(probe.get("failureStreak"))
    ready = not error and fresh and str(probe.get("status") or "").lower() == "ok" and failure_streak == 0
    status = "ready" if ready else "watch"
    if error:
        signal = f"Scheduler source is {error}; recovery health is unknown."
    elif ready:
        signal = "The five-minute runtime probe is current and healthy with no failure streak."
    else:
        signal = f"Runtime probe is stale, incomplete, or unhealthy (status {probe.get('status') or 'unknown'}, streak {failure_streak})."
    return (
        item(
            "runtime-recovery",
            "Bounded runtime recovery",
            "JOSH 2.0",
            status,
            signal,
            "Two-probe detection, one bounded restart, cooldown, and clean-probe recovery reduce outages without restart loops.",
            "data/ecosystem-qa-scheduler.json",
            "Inspect the runtime probe before restarting any service manually." if status != "ready" else "Keep fault injection weekly and alert only on actionable transitions.",
        ),
        {"label": "Runtime recovery", "value": "healthy" if ready else "watch", "status": status, "detail": "fresh five-minute bounded-recovery probe"},
    )


def completion_control(data_dir: Path, now: dt.datetime) -> dict[str, Any]:
    candidates = (
        data_dir / "completion-evidence.json",
        data_dir / "jaimes-completion-evidence.json",
        data_dir / "reliability-completion-evidence.json",
    )
    payload: dict[str, Any] | None = None
    source: Path | None = None
    for candidate in candidates:
        loaded, _ = read_json(candidate)
        if isinstance(loaded, dict):
            payload, source = loaded, candidate
            break
    if payload is None:
        status = "watch"
        signal = "No current-task completion-evidence sidecar is available yet."
        evidence = "data/reliability-reuse-eval.json"
    else:
        fresh = is_fresh(first_nonempty(payload, ("checkedAt", "updatedAt", "generatedAt")), now, 65)
        mismatches = max(
            (integer(payload.get(name)) for name in ("mismatches", "staleEvidenceAccepted", "unverifiedCompletions")),
            default=0,
        )
        linked = integer(first_nonempty(payload, ("finalMessagesLinked", "linkedFinalMessages", "messageLinks")))
        required = integer(first_nonempty(payload, ("finalMessagesRequired", "requiredFinalMessages", "completedRuns")))
        completed = integer(payload.get("completedRuns"), required)
        identity_bound = integer(payload.get("identityBoundRuns"), required)
        declared_ok = payload.get("ok") is True or str(payload.get("status") or "").lower() in {"ok", "ready", "pass", "passed"}
        ready = fresh and declared_ok and mismatches == 0 and required > 0 and linked >= required
        status = "ready" if ready else "watch"
        if ready:
            signal = f"Current-task evidence is fresh with {linked}/{required} required final-message links and no mismatches."
        elif completed > identity_bound:
            signal = (
                f"{completed - identity_bound}/{completed} recent completion(s) predate exact work/run binding; "
                "new identity-aware runs will be measured without rewriting history."
            )
        elif required == 0:
            signal = "No recent identity-aware completion sample is available yet; no success is inferred."
        else:
            signal = f"Completion evidence is stale or incomplete: {linked}/{required} final-message links, {mismatches} mismatch(es)."
        evidence = str(source.relative_to(data_dir.parent)) if source else "data/reliability-reuse-eval.json"
    return item(
        "verified-completion",
        "Verified completion",
        "JAIMES",
        status,
        signal,
        "A completion claim should point to fresh evidence from the exact work/run and the actual delivered Telegram final.",
        evidence,
        "Bind completion evidence and final-message IDs to the exact work/run; reject stale history." if status != "ready" else "Keep evidence freshness and delivery linkage mandatory for new completions.",
    )


def build_scorecard(data_dir: Path = DATA_DIR, now: dt.datetime | None = None) -> dict[str, Any]:
    current = (now or dt.datetime.now(UTC)).astimezone(UTC)
    evaluation_item, evaluation_metric = evaluation_control(data_dir, current)
    handoff_item, handoff_metric = handoff_control(data_dir)
    memory_item, memory_metric = memory_control(data_dir)
    telegram_item, telegram_metric = telegram_control(data_dir, current)
    recovery_item, recovery_metric = recovery_control(data_dir, current)
    completion_item = completion_control(data_dir, current)
    items = [completion_item, handoff_item, memory_item, telegram_item, recovery_item, evaluation_item]
    metrics = [evaluation_metric, handoff_metric, memory_metric, telegram_metric, recovery_metric]
    ready = sum(1 for row in items if row["status"] == "ready")
    watch = len(items) - ready
    return {
        "version": 2,
        "updatedAt": iso(current),
        "summary": f"Reliability controls: {ready} ready, {watch} watch. Each control stands alone; there is no composite score.",
        "policy": {
            "missingOrStaleEvidence": "watch",
            "compositeScore": False,
            "dashboardSafe": True,
            "rawPromptsOrSecrets": False,
        },
        "items": items,
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--check", action="store_true", help="Build and print without writing")
    args = parser.parse_args()
    payload = build_scorecard(args.data_dir)
    if not args.check:
        atomic_write(args.output, payload)
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
