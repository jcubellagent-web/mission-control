#!/usr/bin/env python3
"""Evaluate reliability/reuse evidence without exposing operational payloads.

The default path reads existing dashboard-safe sidecars and writes one bounded
summary.  It does not use SSH, call providers, send Telegram messages, or run
heavy test suites.  Optional local scorecard contract tests are fixed to the
checked-in test artifact and execute with a caller-controlled timeout.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_PATH = DATA_DIR / "reliability-reuse-eval.json"
UTC = dt.timezone.utc

GATE_IDS = (
    "memory-privacy-reuse",
    "handoff-receipts",
    "completion-final-linkage",
    "telegram-contract",
    "recovery-proof",
    "scorecard-semantics",
)
EXPECTED_SCORECARD_ITEMS = {
    "golden-workflows",
    "traceable-handoffs",
    "memory-reuse",
    "telegram-final-contract",
    "runtime-recovery",
    "verified-completion",
}
TERMINAL_STATUSES = {
    "complete", "completed", "done", "failed", "blocked", "cancelled", "canceled",
}
PASS_WORDS = {"ok", "ready", "pass", "passed"}
STATE_RANK = {"pass": 0, "watch": 1, "fail": 2}
MAX_FUTURE_SECONDS = 120
MEMORY_MAX_AGE_MINUTES = 1_500
HANDOFF_MAX_AGE_MINUTES = 65
HANDOFF_EVIDENCE_WINDOW_MINUTES = 24 * 60
COMPLETION_MAX_AGE_MINUTES = 65
TELEGRAM_MAX_AGE_MINUTES = 65
RECOVERY_PROBE_MAX_AGE_MINUTES = 12
RECOVERY_PROOF_MAX_AGE_MINUTES = 8 * 24 * 60
MIN_REUSE_OUTCOMES = 20
MIN_HELPFUL_REUSE_PCT = 60.0
RECOVERY_HARD_MAX_SECONDS = 60
RECOVERY_MAX_RESTARTS = 1
RECOVERY_CLEAN_PROBES = 2
MAX_INPUT_BYTES = 5_000_000


def iso(value: dt.datetime | None = None) -> str:
    current = (value or dt.datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    return current.isoformat().replace("+00:00", "Z")


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
        if path.stat().st_size > MAX_INPUT_BYTES:
            return None, "malformed"
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite-json")),
        ), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeDecodeError, ValueError):
        return None, "malformed"


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


def first_nonempty(mapping: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def strict_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 0 else None
    return None


def strict_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else None


def named_int(mapping: dict[str, Any], names: Iterable[str]) -> int | None:
    for name in names:
        if name in mapping:
            return strict_int(mapping.get(name))
    return None


def declared_pass(payload: dict[str, Any]) -> bool | None:
    if "ok" in payload:
        return payload.get("ok") is True
    if "status" in payload:
        return str(payload.get("status") or "").lower() in PASS_WORDS
    return None


def worst_state(states: Iterable[str]) -> str:
    values = list(states)
    return max(values or ["pass"], key=lambda value: STATE_RANK[value])


def freshness(
    value: Any,
    now: dt.datetime,
    maximum_minutes: float,
) -> tuple[str, str | None, str | None]:
    stamp = parse_time(value)
    if stamp is None:
        return "fail", "timestamp-invalid", None
    age_seconds = (now - stamp).total_seconds()
    if age_seconds < -MAX_FUTURE_SECONDS:
        return "fail", "timestamp-in-future", iso(stamp)
    if age_seconds > maximum_minutes * 60:
        return "watch", "source-stale", iso(stamp)
    return "pass", None, iso(stamp)


def gate(
    gate_id: str,
    label: str,
    source: str,
    states: Iterable[str],
    reasons: Iterable[str],
    counts: dict[str, int | float],
    observed_at: str | None = None,
) -> dict[str, Any]:
    return {
        "id": gate_id,
        "label": label,
        "state": worst_state(states),
        "source": source,
        "observedAt": observed_at,
        "reasonCodes": sorted(set(reasons)),
        "counts": counts,
    }


def source_error_gate(
    gate_id: str,
    label: str,
    source: str,
    error: str,
    count_names: Iterable[str],
) -> dict[str, Any]:
    state = "watch" if error == "missing" else "fail"
    return gate(
        gate_id,
        label,
        source,
        [state],
        [f"source-{error}"],
        {name: 0 for name in count_names},
    )


def memory_gate(data_dir: Path, now: dt.datetime) -> dict[str, Any]:
    source = "data/memory-operations.json"
    payload, error = read_json(data_dir / "memory-operations.json")
    count_names = (
        "outcomes", "helpful", "ignored", "corrected", "harmful",
        "helpfulRatePct", "selected", "used", "unsafeShared",
    )
    if error or not isinstance(payload, dict):
        return source_error_gate(
            "memory-privacy-reuse", "Memory privacy and reuse", source,
            error or "malformed", count_names,
        )

    states: list[str] = []
    reasons: list[str] = []
    fresh_state, fresh_reason, observed = freshness(payload.get("updatedAt"), now, MEMORY_MAX_AGE_MINUTES)
    states.append(fresh_state)
    if fresh_reason:
        reasons.append(fresh_reason)

    retrieval = payload.get("retrieval")
    if not isinstance(retrieval, dict):
        return gate(
            "memory-privacy-reuse", "Memory privacy and reuse", source,
            states + ["fail"], reasons + ["retrieval-telemetry-malformed"],
            {name: 0 for name in count_names}, observed,
        )
    outcome_names = ("helpful30d", "ignored30d", "corrected30d", "harmful30d")
    raw_outcomes = {name: strict_int(retrieval.get(name)) for name in outcome_names}
    if any(value is None for value in raw_outcomes.values()):
        states.append("fail")
        reasons.append("reuse-outcomes-malformed")
    helpful = raw_outcomes["helpful30d"] or 0
    ignored = raw_outcomes["ignored30d"] or 0
    corrected = raw_outcomes["corrected30d"] or 0
    harmful = raw_outcomes["harmful30d"] or 0
    outcomes = helpful + ignored + corrected + harmful
    helpful_rate = round(helpful * 100.0 / outcomes, 1) if outcomes else 0.0
    if outcomes == 0:
        states.append("watch")
        reasons.append("reuse-outcomes-missing")
    elif outcomes < MIN_REUSE_OUTCOMES:
        states.append("watch")
        reasons.append("reuse-sample-insufficient")
    elif helpful_rate < MIN_HELPFUL_REUSE_PCT:
        states.append("fail")
        reasons.append("helpful-reuse-below-threshold")
    if corrected > 0:
        states.append("fail")
        reasons.append("corrected-reuse-observed")
    if harmful > 0:
        states.append("fail")
        reasons.append("harmful-reuse-observed")

    reuse = retrieval.get("reuse") if isinstance(retrieval.get("reuse"), dict) else retrieval
    selected = named_int(reuse, ("selected7d", "selected30d", "selected", "reuseSelected7d"))
    used = named_int(reuse, ("used7d", "used30d", "used", "reuseUsed7d"))
    if selected is None or used is None:
        states.append("watch")
        reasons.append("selected-used-telemetry-missing")
    elif used > selected:
        states.append("fail")
        reasons.append("used-exceeds-selected")

    privacy = payload.get("privacy")
    unsafe_shared: int | None = None
    if not isinstance(privacy, dict):
        states.append("watch")
        reasons.append("privacy-telemetry-missing")
    else:
        unsafe_shared = named_int(
            privacy,
            ("unsafeShared", "blockedVisibilityViolations", "sharedPrivateViolations", "crossOwnerPrivateLeaks"),
        )
        privacy_state, privacy_reason, _ = freshness(privacy.get("checkedAt"), now, MEMORY_MAX_AGE_MINUTES)
        states.append(privacy_state)
        if privacy_reason:
            reasons.append(f"privacy-{privacy_reason}")
        if unsafe_shared is None:
            states.append("watch")
            reasons.append("privacy-count-missing")
        elif unsafe_shared > 0:
            states.append("fail")
            reasons.append("unsafe-shared-memory-observed")

    return gate(
        "memory-privacy-reuse",
        "Memory privacy and reuse",
        source,
        states,
        reasons,
        {
            "outcomes": outcomes,
            "helpful": helpful,
            "ignored": ignored,
            "corrected": corrected,
            "harmful": harmful,
            "helpfulRatePct": helpful_rate,
            "selected": selected or 0,
            "used": used or 0,
            "unsafeShared": unsafe_shared or 0,
        },
        observed,
    )


def handoff_gate(data_dir: Path, now: dt.datetime) -> dict[str, Any]:
    source = "data/handoff-queue.json"
    payload, error = read_json(data_dir / "handoff-queue.json")
    count_names = (
        "rows", "currentRows", "historicalRows", "modern", "receiptComplete",
        "terminalModern", "terminalLinked", "legacy", "malformed",
    )
    if error or not isinstance(payload, dict):
        return source_error_gate("handoff-receipts", "Handoff receipts", source, error or "malformed", count_names)
    states: list[str] = []
    reasons: list[str] = []
    fresh_state, fresh_reason, observed = freshness(payload.get("reconciledAt"), now, HANDOFF_MAX_AGE_MINUTES)
    states.append(fresh_state)
    if fresh_reason:
        reasons.append(fresh_reason)
    rows = payload.get("handoffs")
    if not isinstance(rows, list):
        return gate(
            "handoff-receipts", "Handoff receipts", source,
            states + ["fail"], reasons + ["handoff-list-malformed"],
            {name: 0 for name in count_names}, observed,
        )
    modern = complete = terminal_modern = terminal_linked = legacy = malformed = 0
    current_rows = historical_rows = 0
    cutoff = now - dt.timedelta(minutes=HANDOFF_EVIDENCE_WINDOW_MINUTES)
    for row in rows:
        if not isinstance(row, dict):
            malformed += 1
            continue
        row_time = parse_time(first_nonempty(row, ("updatedAt", "createdAt", "time", "recordedAt")))
        if row_time is not None and row_time < cutoff:
            historical_rows += 1
            continue
        current_rows += 1
        receipt = row.get("receipt") if isinstance(row.get("receipt"), dict) else {}
        work_id = first_nonempty(row, ("workId", "work_id"))
        run_id = first_nonempty(row, ("runId", "run_id"))
        sender = first_nonempty(row, ("receiptId", "senderReceiptId", "senderEventId")) or first_nonempty(
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
            row, ("terminalResultReceiptId", "terminalReceiptId", "completionReceiptId")
        ) or first_nonempty(
            receipt, ("terminalResultReceiptId", "terminalReceiptId", "completionReceiptId")
        )
        is_modern = any((work_id, run_id, sender, ack, terminal_receipt))
        if not is_modern:
            legacy += 1
            continue
        modern += 1
        terminal_status = first_nonempty(row, ("terminalResultStatus", "status"))
        terminal = bool(terminal_receipt) or terminal_status.lower() in TERMINAL_STATUSES
        if terminal:
            terminal_modern += 1
        is_complete = bool(work_id and run_id and sender and ack and (not terminal or terminal_receipt))
        if is_complete:
            complete += 1
        if terminal and terminal_receipt:
            terminal_linked += 1
    if malformed:
        states.append("fail")
        reasons.append("handoff-rows-malformed")
    if not rows:
        states.append("watch")
        reasons.append("handoff-evidence-missing")
    elif current_rows == 0:
        states.append("watch")
        reasons.append("current-handoff-evidence-missing")
    elif modern == 0:
        states.append("watch")
        reasons.append("modern-receipts-not-observed")
    elif complete < modern:
        states.append("fail")
        reasons.append("receipt-chain-incomplete")
    if terminal_linked < terminal_modern:
        states.append("fail")
        reasons.append("terminal-receipt-missing")
    return gate(
        "handoff-receipts", "Handoff receipts", source, states, reasons,
        {
            "rows": len(rows),
            "currentRows": current_rows,
            "historicalRows": historical_rows,
            "modern": modern,
            "receiptComplete": complete,
            "terminalModern": terminal_modern,
            "terminalLinked": terminal_linked,
            "legacy": legacy,
            "malformed": malformed,
        },
        observed,
    )


def completion_row_counts(rows: list[Any]) -> tuple[int, int, int, int, int]:
    required = linked = verified = mismatches = malformed = 0
    for row in rows:
        required += 1
        if not isinstance(row, dict):
            malformed += 1
            continue
        work_id = first_nonempty(row, ("workId", "work_id"))
        run_id = first_nonempty(row, ("runId", "run_id"))
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        delivery = row.get("delivery") if isinstance(row.get("delivery"), dict) else {}
        evidence_work = first_nonempty(evidence, ("workId", "work_id")) or work_id
        evidence_run = first_nonempty(evidence, ("runId", "run_id")) or run_id
        evidence_receipt = first_nonempty(
            evidence, ("receiptId", "completionReceiptId", "terminalReceiptId", "evidenceReceiptId")
        ) or first_nonempty(row, ("completionReceiptId", "terminalReceiptId", "evidenceReceiptId"))
        final_message = first_nonempty(
            row, ("finalMessageId", "telegramFinalMessageId", "deliveredMessageId")
        ) or first_nonempty(delivery, ("finalMessageId", "telegramFinalMessageId", "messageId"))
        final_work = first_nonempty(delivery, ("workId", "work_id")) or work_id
        final_run = first_nonempty(delivery, ("runId", "run_id")) or run_id
        if final_message:
            linked += 1
        identity_mismatch = bool(
            work_id and run_id
            and (evidence_work != work_id or evidence_run != run_id or final_work != work_id or final_run != run_id)
        )
        if identity_mismatch:
            mismatches += 1
        elif work_id and run_id and evidence_receipt and final_message:
            verified += 1
    return required, linked, verified, mismatches, malformed


def completion_gate(data_dir: Path, now: dt.datetime) -> dict[str, Any]:
    candidates = (
        ("completion-evidence.json", "data/completion-evidence.json"),
        ("jaimes-completion-evidence.json", "data/jaimes-completion-evidence.json"),
        ("reliability-completion-evidence.json", "data/reliability-completion-evidence.json"),
    )
    payload: Any = None
    source = "data/completion-evidence.json"
    error = "missing"
    for filename, label in candidates:
        candidate, candidate_error = read_json(data_dir / filename)
        if candidate_error == "missing":
            continue
        payload, source, error = candidate, label, candidate_error
        break
    count_names = ("required", "evidenceVerified", "finalLinked", "mismatches", "malformed")
    if error or not isinstance(payload, dict):
        return source_error_gate(
            "completion-final-linkage", "Completion evidence and final linkage",
            source, error or "malformed", count_names,
        )
    states: list[str] = []
    reasons: list[str] = []
    stamp = first_nonempty(payload, ("checkedAt", "updatedAt", "generatedAt"))
    fresh_state, fresh_reason, observed = freshness(stamp, now, COMPLETION_MAX_AGE_MINUTES)
    states.append(fresh_state)
    if fresh_reason:
        reasons.append(fresh_reason)
    rows = first_nonempty(payload, ("completions", "records"))
    if isinstance(rows, list):
        required, linked, verified, mismatches, malformed = completion_row_counts(rows)
    else:
        required = named_int(payload, ("finalMessagesRequired", "requiredFinalMessages", "completedRuns"))
        linked = named_int(payload, ("finalMessagesLinked", "linkedFinalMessages", "messageLinks"))
        verified = named_int(
            payload,
            ("verifiedCompletions", "completionEvidenceVerified", "evidenceVerified", "deliveryVerifiedRuns"),
        )
        mismatch_values = [
            strict_int(payload.get(name))
            for name in ("mismatches", "staleEvidenceAccepted", "unverifiedCompletions")
            if name in payload
        ]
        mismatches = max((value for value in mismatch_values if value is not None), default=None)
        malformed = 0
        if required is None or linked is None or mismatches is None:
            states.append("fail")
            reasons.append("completion-counts-malformed")
        if verified is None:
            states.append("watch")
            reasons.append("verified-evidence-count-missing")
        required, linked, verified, mismatches = required or 0, linked or 0, verified or 0, mismatches or 0
    declared = declared_pass(payload)
    declared_status = str(payload.get("status") or "").lower()
    if declared_status == "watch" and payload.get("ok") is False:
        states.append("watch")
        reasons.append("completion-source-declared-watch")
    elif declared is False:
        states.append("fail")
        reasons.append("completion-source-declared-failure")
    elif declared is None:
        states.append("watch")
        reasons.append("completion-source-status-missing")
    if required == 0:
        states.append("watch")
        reasons.append("completion-evidence-empty")
    if malformed:
        states.append("fail")
        reasons.append("completion-rows-malformed")
    if mismatches:
        states.append("fail")
        reasons.append("work-run-evidence-mismatch")
    if linked < required:
        states.append("fail")
        reasons.append("final-message-linkage-incomplete")
    if verified < required:
        states.append("fail")
        reasons.append("completion-evidence-incomplete")
    return gate(
        "completion-final-linkage", "Completion evidence and final linkage",
        source, states, reasons,
        {
            "required": required,
            "evidenceVerified": verified,
            "finalLinked": linked,
            "mismatches": mismatches,
            "malformed": malformed,
        },
        observed,
    )


def telegram_gate(data_dir: Path, now: dt.datetime) -> dict[str, Any]:
    source = "data/telegram-inbox-qa.json"
    payload, error = read_json(data_dir / "telegram-inbox-qa.json")
    count_names = ("renderedCards", "problemCards", "timingSamples", "minimumSamples", "p95Ms", "sloMs")
    if error or not isinstance(payload, dict):
        return source_error_gate("telegram-contract", "Telegram contract", source, error or "malformed", count_names)
    states: list[str] = []
    reasons: list[str] = []
    rolling = payload.get("rolling") if isinstance(payload.get("rolling"), dict) else {}
    contract = rolling.get("contractStress") if isinstance(rolling.get("contractStress"), dict) else {}
    lanes = payload.get("lanes") if isinstance(payload.get("lanes"), dict) else {}
    stress_lane = lanes.get("stress") if isinstance(lanes.get("stress"), dict) else {}
    sample = stress_lane.get("lastSample") if isinstance(stress_lane.get("lastSample"), dict) else {}
    stress = sample.get("stress") if isinstance(sample.get("stress"), dict) else {}
    stamp = first_nonempty(sample, ("checkedAt", "updatedAt")) or payload.get("updatedAt")
    fresh_state, fresh_reason, observed = freshness(stamp, now, TELEGRAM_MAX_AGE_MINUTES)
    states.append(fresh_state)
    if fresh_reason:
        reasons.append(fresh_reason)
    values = {
        "renderedCards": strict_int(stress.get("renderedCards")),
        "problemCards": strict_int(first_nonempty(stress, ("problemCount", "violations")) or sample.get("problemCount")),
        "timingSamples": strict_int(contract.get("samples")),
        "minimumSamples": strict_int(contract.get("minimumSamples")),
        "p95Ms": strict_number(contract.get("p95Ms")),
        "sloMs": strict_number(contract.get("sloMs")),
    }
    if any(value is None for value in values.values()):
        states.append("fail")
        reasons.append("telegram-contract-metrics-malformed")
    counts = {name: value or 0 for name, value in values.items()}
    if counts["renderedCards"] == 0:
        states.append("watch")
        reasons.append("telegram-render-evidence-missing")
    if counts["problemCards"] > 0:
        states.append("fail")
        reasons.append("telegram-contract-violations")
    if counts["timingSamples"] < max(counts["minimumSamples"], 1):
        states.append("watch")
        reasons.append("telegram-timing-sample-insufficient")
    if counts["sloMs"] > 0 and counts["p95Ms"] > counts["sloMs"]:
        states.append("fail")
        reasons.append("telegram-p95-over-slo")
    if str(contract.get("status") or "").lower() not in PASS_WORDS or sample.get("ok") is not True:
        states.append("fail")
        reasons.append("telegram-source-declared-failure")
    privacy = payload.get("privacy") if isinstance(payload.get("privacy"), dict) else {}
    if not (
        privacy.get("dashboardSafe") is True
        and privacy.get("messageIdsIncluded") is False
        and privacy.get("rawPromptsIncluded") is False
    ):
        states.append("fail")
        reasons.append("telegram-privacy-contract-unproven")
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    if coverage.get("recurringProductionWrites") is not False:
        states.append("fail")
        reasons.append("recurring-production-write-contract-unproven")
    return gate("telegram-contract", "Telegram contract", source, states, reasons, counts, observed)


def recovery_proof(data_dir: Path, scheduler: dict[str, Any], now: dt.datetime) -> tuple[list[str], list[str], dict[str, int], str | None]:
    states: list[str] = []
    reasons: list[str] = []
    counts = {"proofAvailable": 0, "proofPassed": 0, "cleanProbes": 0, "restartAttempts": 0}
    observed: str | None = None
    proof_payload: dict[str, Any] | None = None
    for filename in ("recovery-proof.json", "ecosystem-recovery-proof.json"):
        candidate, error = read_json(data_dir / filename)
        if error == "missing":
            continue
        if error or not isinstance(candidate, dict):
            return ["fail"], ["recovery-proof-malformed"], counts, None
        proof_payload = candidate
        break
    if proof_payload is None:
        jobs = scheduler.get("jobs") if isinstance(scheduler.get("jobs"), dict) else {}
        drill = jobs.get("weekly-recovery-drill") if isinstance(jobs.get("weekly-recovery-drill"), dict) else None
        if drill is not None:
            proof_payload = {
                "checkedAt": first_nonempty(drill, ("completedAt", "startedAt")),
                "ok": str(drill.get("status") or "").lower() == "ok" and drill.get("returncode") in (None, 0),
                "status": drill.get("status"),
            }
        else:
            benchmark, error = read_json(data_dir / "ecosystem-qa-benchmark.json")
            if not error and isinstance(benchmark, dict) and benchmark.get("mode") == "fault-injection":
                proof_payload = benchmark
    if proof_payload is None:
        return ["watch"], ["recovery-drill-evidence-missing"], counts, None
    counts["proofAvailable"] = 1
    stamp = first_nonempty(proof_payload, ("checkedAt", "completedAt", "updatedAt"))
    fresh_state, fresh_reason, observed = freshness(stamp, now, RECOVERY_PROOF_MAX_AGE_MINUTES)
    states.append(fresh_state)
    if fresh_reason:
        reasons.append(f"proof-{fresh_reason}")
    declared = declared_pass(proof_payload)
    if declared is not True:
        states.append("fail")
        reasons.append("recovery-proof-declared-failure")
    recovery_seconds = strict_number(first_nonempty(proof_payload, ("recoverySeconds", "serviceRecoverySeconds")))
    attempts = named_int(proof_payload, ("restartAttempts", "attempts"))
    clean = named_int(proof_payload, ("cleanProbes", "healthyProbesToClear"))
    checks_total = named_int(proof_payload, ("checksTotal", "total"))
    checks_passed = named_int(proof_payload, ("checksPassed", "passed"))
    metric_contract_present = recovery_seconds is not None or attempts is not None or clean is not None
    benchmark_contract_present = checks_total is not None and checks_passed is not None
    if metric_contract_present:
        if recovery_seconds is None or attempts is None or clean is None:
            states.append("fail")
            reasons.append("recovery-proof-metrics-malformed")
        else:
            counts["restartAttempts"] = attempts
            counts["cleanProbes"] = clean
            if recovery_seconds > RECOVERY_HARD_MAX_SECONDS:
                states.append("fail")
                reasons.append("recovery-time-over-slo")
            if attempts > RECOVERY_MAX_RESTARTS:
                states.append("fail")
                reasons.append("restart-attempt-limit-exceeded")
            if clean < RECOVERY_CLEAN_PROBES:
                states.append("fail")
                reasons.append("clean-probe-proof-incomplete")
    elif benchmark_contract_present:
        if checks_total == 0:
            states.append("watch")
            reasons.append("recovery-checks-empty")
        elif checks_passed != checks_total:
            states.append("fail")
            reasons.append("recovery-checks-failed")
    # A scheduler-owned weekly drill is allowed to prove execution by its fixed
    # command/return code; richer sidecars must provide metrics or check counts.
    elif "status" not in proof_payload:
        states.append("watch")
        reasons.append("recovery-proof-detail-missing")
    if worst_state(states) == "pass":
        counts["proofPassed"] = 1
    return states, reasons, counts, observed


def recovery_gate(data_dir: Path, now: dt.datetime) -> dict[str, Any]:
    source = "data/ecosystem-qa-scheduler.json + recovery proof"
    scheduler, error = read_json(data_dir / "ecosystem-qa-scheduler.json")
    count_names = ("probeHealthy", "failureStreak", "proofAvailable", "proofPassed", "cleanProbes", "restartAttempts")
    if error or not isinstance(scheduler, dict):
        return source_error_gate("recovery-proof", "Bounded recovery proof", source, error or "malformed", count_names)
    states: list[str] = []
    reasons: list[str] = []
    jobs = scheduler.get("jobs") if isinstance(scheduler.get("jobs"), dict) else {}
    probe = jobs.get("runtime-service-probe") if isinstance(jobs.get("runtime-service-probe"), dict) else None
    probe_healthy = 0
    failure_streak = 0
    observed: str | None = None
    if probe is None:
        states.append("watch")
        reasons.append("runtime-probe-missing")
    else:
        probe_state, probe_reason, observed = freshness(
            first_nonempty(probe, ("completedAt", "startedAt")), now, RECOVERY_PROBE_MAX_AGE_MINUTES
        )
        states.append(probe_state)
        if probe_reason:
            reasons.append(f"probe-{probe_reason}")
        parsed_streak = strict_int(probe.get("failureStreak"))
        if parsed_streak is None:
            states.append("fail")
            reasons.append("probe-failure-streak-malformed")
        else:
            failure_streak = parsed_streak
        probe_healthy = int(
            str(probe.get("status") or "").lower() == "ok"
            and probe.get("returncode") in (None, 0)
            and failure_streak == 0
        )
        if not probe_healthy:
            states.append("fail")
            reasons.append("runtime-probe-unhealthy")
    proof_states, proof_reasons, proof_counts, proof_observed = recovery_proof(data_dir, scheduler, now)
    states.extend(proof_states)
    reasons.extend(proof_reasons)
    counts = {
        "probeHealthy": probe_healthy,
        "failureStreak": failure_streak,
        **proof_counts,
    }
    return gate(
        "recovery-proof", "Bounded recovery proof", source, states, reasons,
        counts, proof_observed or observed,
    )


def run_contract_checks(root: Path, timeout_seconds: int) -> dict[str, Any]:
    test_file = root / "tests" / "test_reliability_scorecard.py"
    result = {
        "requested": True,
        "state": "fail",
        "tests": 0,
        "returnCode": None,
        "timedOut": False,
        "timeoutSeconds": timeout_seconds,
    }
    if not test_file.is_file():
        return result
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        process = subprocess.run(
            [sys.executable, str(test_file), "-q"],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        result["timedOut"] = True
        return result
    result["returnCode"] = process.returncode
    match = re.search(r"Ran\s+(\d+)\s+tests?", f"{process.stdout}\n{process.stderr}")
    result["tests"] = int(match.group(1)) if match else 0
    result["state"] = "pass" if process.returncode == 0 and result["tests"] > 0 else "fail"
    return result


def scorecard_semantics_gate(
    root: Path,
    data_dir: Path,
    now: dt.datetime,
    contract_checks: dict[str, Any],
) -> dict[str, Any]:
    source = "scripts/reliability_scorecard.py"
    checks: list[bool] = []
    states: list[str] = []
    reasons: list[str] = []
    try:
        module_path = root / "scripts" / "reliability_scorecard.py"
        spec = importlib.util.spec_from_file_location("reliability_scorecard_under_eval", module_path)
        if not spec or not spec.loader:
            raise ImportError("scorecard-spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        current = module.build_scorecard(data_dir, now)
        missing = module.build_scorecard(root / ".reliability-reuse-eval-missing", now)
        policy = current.get("policy") if isinstance(current.get("policy"), dict) else {}
        items = current.get("items") if isinstance(current.get("items"), list) else []
        item_ids = {row.get("id") for row in items if isinstance(row, dict)}
        statuses = {row.get("status") for row in items if isinstance(row, dict)}
        missing_items = missing.get("items") if isinstance(missing.get("items"), list) else []
        checks = [
            policy.get("compositeScore") is False,
            policy.get("dashboardSafe") is True,
            policy.get("rawPromptsOrSecrets") is False,
            policy.get("missingOrStaleEvidence") == "watch",
            item_ids == EXPECTED_SCORECARD_ITEMS and len(items) == len(EXPECTED_SCORECARD_ITEMS),
            statuses.issubset({"ready", "watch"}),
            bool(missing_items) and all(
                isinstance(row, dict) and row.get("status") == "watch" for row in missing_items
            ),
            "no composite score" in str(current.get("summary") or "").lower(),
        ]
        if not all(checks):
            states.append("fail")
            reasons.append("scorecard-semantic-contract-failed")
    except Exception:
        checks = [False] * 8
        states.append("fail")
        reasons.append("scorecard-semantic-check-unavailable")
    if contract_checks.get("requested") and contract_checks.get("state") != "pass":
        states.append("fail")
        reasons.append(
            "scorecard-contract-tests-timeout"
            if contract_checks.get("timedOut")
            else "scorecard-contract-tests-failed"
        )
    return gate(
        "scorecard-semantics", "Scorecard semantics", source, states, reasons,
        {
            "semanticChecks": len(checks),
            "semanticPassed": sum(1 for value in checks if value),
            "contractTests": int(contract_checks.get("tests") or 0),
            "contractTestsPassed": int(contract_checks.get("state") == "pass"),
        },
        iso(now),
    )


def not_run_contract_checks(timeout_seconds: int) -> dict[str, Any]:
    return {
        "requested": False,
        "state": "not-run",
        "tests": 0,
        "returnCode": None,
        "timedOut": False,
        "timeoutSeconds": timeout_seconds,
    }


def build_evaluation(
    data_dir: Path = DATA_DIR,
    *,
    root: Path = ROOT,
    now: dt.datetime | None = None,
    contract_checks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = (now or dt.datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    contract = contract_checks or not_run_contract_checks(15)
    gates = [
        memory_gate(data_dir, current),
        handoff_gate(data_dir, current),
        completion_gate(data_dir, current),
        telegram_gate(data_dir, current),
        recovery_gate(data_dir, current),
        scorecard_semantics_gate(root, data_dir, current, contract),
    ]
    state_counts = {
        state: sum(1 for row in gates if row["state"] == state)
        for state in ("pass", "watch", "fail")
    }
    status = worst_state(row["state"] for row in gates)
    return {
        "version": 1,
        "checkedAt": iso(current),
        "status": status,
        "ok": status == "pass",
        "summary": (
            f"{len(gates)} independent gates: {state_counts['pass']} pass, "
            f"{state_counts['watch']} watch, {state_counts['fail']} fail; no composite score."
        ),
        "checksPassed": state_counts["pass"],
        "checksTotal": len(gates),
        "stateCounts": state_counts,
        "policy": {
            "dashboardSafe": True,
            "compositeScore": False,
            "aggregation": "worst-state only",
            "missingOrStale": "watch",
            "malformedOrViolated": "fail",
            "rawOrPrivatePayloads": False,
            "defaultExecution": "local sidecar parsing only",
        },
        "contractChecks": contract,
        "gates": gates,
    }


def validate_output(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    gates = payload.get("gates") if isinstance(payload.get("gates"), list) else []
    ids = [row.get("id") for row in gates if isinstance(row, dict)]
    if tuple(ids) != GATE_IDS:
        problems.append("gate-identities")
    if any(not isinstance(row, dict) or row.get("state") not in STATE_RANK for row in gates):
        problems.append("gate-states")
    actual = {
        state: sum(1 for row in gates if isinstance(row, dict) and row.get("state") == state)
        for state in STATE_RANK
    }
    if payload.get("stateCounts") != actual:
        problems.append("state-counts")
    if payload.get("checksPassed") != actual["pass"] or payload.get("checksTotal") != len(GATE_IDS):
        problems.append("check-counts")
    policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else {}
    if policy.get("compositeScore") is not False or payload.get("status") != worst_state(
        row.get("state") for row in gates if isinstance(row, dict)
    ):
        problems.append("score-semantics")
    forbidden = {
        "objective", "detail", "prompt", "rawprompt", "memorycontent", "messagebody",
        "emailbody", "token", "password", "cookie", "oauth", "secret", "connectorpayload",
        "workid", "runid", "eventid", "messageid",
    }

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = "".join(character for character in str(key).lower() if character.isalnum())
                if normalized in forbidden:
                    problems.append("forbidden-output-field")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return sorted(set(problems))


def read_only_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "checkedAt": payload["checkedAt"],
        "status": payload["status"],
        "ok": payload["ok"],
        "checksPassed": payload["checksPassed"],
        "checksTotal": payload["checksTotal"],
        "stateCounts": payload["stateCounts"],
        "contractChecks": payload["contractChecks"],
        "gates": [
            {
                "id": row["id"],
                "state": row["state"],
                "reasonCodes": row["reasonCodes"],
                "counts": row["counts"],
            }
            for row in payload["gates"]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--as-of", default="", help="UTC/offset ISO timestamp for deterministic checks")
    parser.add_argument("--check", action="store_true", help="Read-only: print a safe summary without writing")
    parser.add_argument("--run-contract-checks", action="store_true", help="Run the fixed local scorecard unit-test artifact")
    parser.add_argument("--contract-timeout-seconds", type=int, default=15)
    parser.add_argument("--strict", action="store_true", help="Return nonzero unless every gate passes")
    args = parser.parse_args()
    if not 1 <= args.contract_timeout_seconds <= 60:
        parser.error("--contract-timeout-seconds must be between 1 and 60")
    if args.as_of:
        current = parse_time(args.as_of)
        if current is None:
            parser.error("--as-of must be an ISO-8601 timestamp")
    else:
        current = dt.datetime.now(UTC)
    contract = (
        run_contract_checks(ROOT, args.contract_timeout_seconds)
        if args.run_contract_checks
        else not_run_contract_checks(args.contract_timeout_seconds)
    )
    payload = build_evaluation(args.data_dir, root=ROOT, now=current, contract_checks=contract)
    problems = validate_output(payload)
    if problems:
        print(json.dumps({"ok": False, "status": "fail", "problems": problems}, indent=2))
        return 2
    if not args.check:
        atomic_write(args.output or (args.data_dir / OUTPUT_PATH.name), payload)
    print(json.dumps(read_only_summary(payload), indent=2, ensure_ascii=True))
    return 1 if args.strict and not payload["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
