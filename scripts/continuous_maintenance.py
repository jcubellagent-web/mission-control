#!/usr/bin/env python3
"""Build the dashboard-safe, proposal-first continuous-maintenance portfolio.

The projection preserves proposal history, emits one current row per proposal,
tracks bounded refactor discoveries, and records whether the six reliability
gates have stayed clean long enough to permit a reviewed source promotion.  It
never edits source, creates branches, merges changes, or weakens approval gates.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "continuous-maintenance.json"
DATA_DIR = ROOT / "data"
PROPOSALS_PATH = DATA_DIR / "ecosystem-proposals.json"
CANDIDATES_PATH = DATA_DIR / "adaptive-refactor-candidates.json"
RELIABILITY_PATH = DATA_DIR / "reliability-reuse-eval.json"
OUTPUT_PATH = DATA_DIR / "maintenance-portfolio.json"
HISTORY_PATH = DATA_DIR / "maintenance-readiness-history.json"
UTC = dt.timezone.utc
STAGE_BY_STATUS = {
    "proposed": "discovered",
    "approved": "accepted",
    "leased": "leased",
    "implementing": "implementing",
    "verifying": "verifying",
    "implemented": "completed",
    "rejected": "deferred",
    "superseded": "deferred",
    "deferred": "deferred",
}
RISK_LEVELS = {"low", "medium", "high", "unclassified"}
MAX_HISTORY_ROWS = 400
RELIABILITY_GATE_IDS = (
    "memory-privacy-reuse",
    "handoff-receipts",
    "completion-final-linkage",
    "telegram-contract",
    "recovery-proof",
    "scorecard-semantics",
)


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


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


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


def current_proposals(rows: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            continue
        grouped.setdefault(str(row["id"]), []).append((index, row))

    current: list[dict[str, Any]] = []
    for proposal_id, events in grouped.items():
        latest_index, latest = max(
            events,
            key=lambda item: (parse_time(item[1].get("updatedAt")) or dt.datetime.min.replace(tzinfo=UTC), item[0]),
        )
        del latest_index
        created_values = [parse_time(row.get("createdAt")) for _index, row in events]
        created_at = min((value for value in created_values if value), default=None)
        status = str(latest.get("status") or "proposed").lower()
        risk = str(latest.get("risk") or "unclassified").lower()
        if risk not in RISK_LEVELS:
            risk = "unclassified"
        current.append({
            "id": proposal_id,
            "title": str(latest.get("title") or "Untitled maintenance proposal"),
            "owner": str(latest.get("owner") or "josh2"),
            "status": status,
            "stage": STAGE_BY_STATUS.get(status, "deferred"),
            "risk": risk,
            "area": str(latest.get("area") or "Reliability"),
            "sourceCandidateId": latest.get("sourceCandidateId"),
            "createdAt": iso(created_at) if created_at else latest.get("createdAt"),
            "updatedAt": latest.get("updatedAt"),
            "historyEvents": len(events),
        })
    return sorted(current, key=lambda row: (str(row.get("updatedAt") or ""), row["id"]), reverse=True)


def reliability_snapshot(payload: Any, required_ids: list[str]) -> dict[str, Any]:
    gates = payload.get("gates") if isinstance(payload, dict) else []
    gates = [row for row in gates if isinstance(row, dict)] if isinstance(gates, list) else []
    by_id = {str(row.get("id")): row for row in gates if row.get("id")}
    required = [gate_id for gate_id in required_ids if gate_id]
    passed = sum(str((by_id.get(gate_id) or {}).get("state") or "").lower() == "pass" for gate_id in required)
    clean = bool(
        isinstance(payload, dict)
        and payload.get("ok") is True
        and required
        and passed == len(required)
    )
    checked_at = payload.get("checkedAt") if isinstance(payload, dict) else None
    return {
        "checkedAt": checked_at,
        "clean": clean,
        "gatesPassed": passed,
        "gatesRequired": len(required),
        "failedGateIds": [
            gate_id for gate_id in required
            if str((by_id.get(gate_id) or {}).get("state") or "").lower() != "pass"
        ],
    }


def readiness_history(existing: Any, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = existing.get("runs") if isinstance(existing, dict) else []
    rows = [row for row in rows if isinstance(row, dict) and row.get("checkedAt")] if isinstance(rows, list) else []
    checked_at = snapshot.get("checkedAt")
    if checked_at and not any(row.get("checkedAt") == checked_at for row in rows):
        rows.append({
            "checkedAt": checked_at,
            "clean": snapshot["clean"],
            "gatesPassed": snapshot["gatesPassed"],
            "gatesRequired": snapshot["gatesRequired"],
        })
    return sorted(rows, key=lambda row: str(row.get("checkedAt") or ""))[-MAX_HISTORY_ROWS:]


def consecutive_clean_runs(rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in reversed(rows):
        if row.get("clean") is not True:
            break
        count += 1
    return count


def candidate_rows(payload: Any, linked_ids: set[str]) -> list[dict[str, Any]]:
    rows = payload.get("candidates") if isinstance(payload, dict) else []
    result: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("id") or str(row.get("id")) in linked_ids:
            continue
        risk = str(row.get("risk") or "unclassified").lower()
        result.append({
            "id": str(row["id"]),
            "title": str(row.get("title") or "Refactor candidate"),
            "path": row.get("path"),
            "paths": row.get("paths"),
            "risk": risk if risk in RISK_LEVELS else "unclassified",
            "score": row.get("score"),
            "stage": "discovered",
            "automaticMutationAllowed": False,
        })
    return result


def build_portfolio(
    config: dict[str, Any],
    proposals_payload: Any,
    candidates_payload: Any,
    reliability_payload: Any,
    history_payload: Any,
    *,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = (now or dt.datetime.now(UTC)).astimezone(UTC)
    proposals = current_proposals(
        proposals_payload.get("proposals", []) if isinstance(proposals_payload, dict) else []
    )
    active_stages = {str(value) for value in config.get("activeStages", [])}
    wip = [row for row in proposals if row["stage"] in active_stages]
    wip_limit = max(1, int(config.get("wipLimit") or 3))
    maximum_age = max(1, int(config.get("proposalMaxAgeDays") or 30))
    aging: list[str] = []
    for row in proposals:
        if row["stage"] not in {"discovered", "accepted"}:
            continue
        stamp = parse_time(row.get("updatedAt") or row.get("createdAt"))
        if stamp and current - stamp > dt.timedelta(days=maximum_age):
            aging.append(row["id"])

    promotion = config.get("promotionGates") if isinstance(config.get("promotionGates"), dict) else {}
    required_gate_ids = [str(value) for value in promotion.get("requiredReliabilityGates", [])]
    snapshot = reliability_snapshot(reliability_payload, required_gate_ids)
    history_rows = readiness_history(history_payload, snapshot)
    clean_runs = consecutive_clean_runs(history_rows)
    required_clean_runs = max(1, int(config.get("requiredConsecutiveCleanRuns") or 7))
    linked_ids = {
        str(row.get("sourceCandidateId")) for row in proposals if row.get("sourceCandidateId")
    }
    discoveries = candidate_rows(candidates_payload, linked_ids)
    error_budget = config.get("errorBudgetPolicy") if isinstance(config.get("errorBudgetPolicy"), dict) else {}
    elective_frozen = bool(error_budget.get("freezeElectiveChangesOnGateFailure", True) and not snapshot["clean"])
    status = "ready" if clean_runs >= required_clean_runs and len(wip) <= wip_limit else "watch"
    payload = {
        "schemaVersion": 1,
        "generatedAt": iso(current),
        "status": status,
        "mode": str(config.get("mode") or "proposal-first"),
        "summary": (
            f"{len(proposals)} current proposal(s), {len(discoveries)} unaccepted discovery candidate(s), "
            f"{len(wip)}/{wip_limit} active WIP; promotion readiness {clean_runs}/{required_clean_runs} clean run(s)."
        ),
        "policy": {
            "wipLimit": wip_limit,
            "proposalMaxAgeDays": maximum_age,
            "requiredConsecutiveCleanRuns": required_clean_runs,
            "automaticSourceMutation": False,
            "reviewedPromotionRequired": True,
            "rollbackEvidenceRequired": promotion.get("rollbackEvidenceRequired") is True,
            "riskTiers": config.get("riskTiers", {}),
            "dependencyPolicy": config.get("dependencyPolicy", {}),
        },
        "readiness": {
            **snapshot,
            "consecutiveCleanRuns": clean_runs,
            "requiredConsecutiveCleanRuns": required_clean_runs,
            "promotionReady": clean_runs >= required_clean_runs and len(wip) <= wip_limit,
        },
        "changePolicy": {
            "electiveChangesFrozen": elective_frozen,
            "reasonGateIds": snapshot["failedGateIds"] if elective_frozen else [],
            "allowedChangeClasses": (
                [str(value) for value in error_budget.get("allowedWhileFrozen", [])]
                if elective_frozen else ["reviewed-maintenance"]
            ),
        },
        "counts": {
            "historyEvents": sum(int(row["historyEvents"]) for row in proposals),
            "currentProposals": len(proposals),
            "discoveries": len(discoveries),
            "activeWip": len(wip),
            "aging": len(aging),
            "completed": sum(row["stage"] == "completed" for row in proposals),
            "deferred": sum(row["stage"] == "deferred" for row in proposals),
        },
        "wip": {
            "withinLimit": len(wip) <= wip_limit,
            "activeProposalIds": [row["id"] for row in wip],
            "agingProposalIds": aging,
        },
        "currentProposals": proposals[:100],
        "discoveries": discoveries[:50],
        "privacy": {
            "dashboardSafe": True,
            "sourceContentIncluded": False,
            "rawPromptsIncluded": False,
            "secretsIncluded": False,
        },
    }
    history = {"schemaVersion": 1, "updatedAt": iso(current), "runs": history_rows}
    return payload, history


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    data_dir = args.data_dir
    output = args.output or data_dir / OUTPUT_PATH.name
    history_path = args.history or data_dir / HISTORY_PATH.name
    payload, history = build_portfolio(
        read_json(args.config, {}),
        read_json(data_dir / PROPOSALS_PATH.name, {}),
        read_json(data_dir / CANDIDATES_PATH.name, {}),
        read_json(data_dir / RELIABILITY_PATH.name, {}),
        read_json(history_path, {}),
    )
    if not args.no_write:
        atomic_write(output, payload)
        atomic_write(history_path, history)
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0 if payload["readiness"]["gatesRequired"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
