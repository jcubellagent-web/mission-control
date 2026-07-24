#!/usr/bin/env python3
"""History-preserving ledger for dashboard-safe ecosystem change proposals."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "ecosystem-proposals.json"
CONFIG_PATH = ROOT / "config" / "continuous-maintenance.json"
RELIABILITY_PATH = ROOT / "data" / "reliability-reuse-eval.json"
VALID = {
    "proposed", "approved", "leased", "implementing", "verifying",
    "implemented", "deferred", "rejected", "superseded",
}
RISK_LEVELS = {"low", "medium", "high", "unclassified"}
ACTIVE_STATUSES = {"leased", "implementing", "verifying"}
TERMINAL_STATUSES = {"implemented", "deferred", "rejected", "superseded"}
ALLOWED_TRANSITIONS = {
    "proposed": {"approved", "deferred", "rejected", "superseded"},
    "approved": {"leased", "deferred", "rejected", "superseded"},
    "leased": {"implementing", "deferred"},
    "implementing": {"verifying", "deferred"},
    "verifying": {"implemented", "implementing", "deferred"},
}


def iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
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


def proposal_id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:52]
    return f"proposal-{dt.datetime.now(dt.timezone.utc):%Y%m%d}-{slug}-{uuid.uuid4().hex[:6]}"


def current_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """Return one latest event per proposal without rewriting ledger history."""
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, row in enumerate(rows):
        if isinstance(row, dict) and row.get("id"):
            grouped.setdefault(str(row["id"]), []).append((index, row))
    latest: list[dict[str, Any]] = []
    for events in grouped.values():
        _index, row = max(events, key=lambda item: (str(item[1].get("updatedAt") or ""), item[0]))
        latest.append(row)
    return latest


def reliability_clean(payload: Any, config: dict[str, Any]) -> bool:
    promotion = config.get("promotionGates") if isinstance(config.get("promotionGates"), dict) else {}
    required = [str(value) for value in promotion.get("requiredReliabilityGates", []) if value]
    gates = payload.get("gates") if isinstance(payload, dict) else []
    by_id = {
        str(row.get("id")): row for row in gates
        if isinstance(gates, list) and isinstance(row, dict) and row.get("id")
    }
    return bool(
        isinstance(payload, dict)
        and payload.get("ok") is True
        and required
        and all(str((by_id.get(gate_id) or {}).get("state") or "").lower() == "pass" for gate_id in required)
    )


def validate_transition(
    current: dict[str, Any],
    target: str,
    all_current: list[dict[str, Any]],
    config: dict[str, Any],
    reliability: Any,
    args: argparse.Namespace,
) -> str | None:
    source = str(current.get("status") or "proposed")
    if target == source:
        return f"proposal is already {target}"
    if target not in ALLOWED_TRANSITIONS.get(source, set()):
        return f"transition not allowed: {source} -> {target}"

    risk = str(args.risk or current.get("risk") or "unclassified").lower()
    if target in ACTIVE_STATUSES and risk == "unclassified":
        return "--risk is required before work can enter an active state"

    if target in ACTIVE_STATUSES:
        limit = max(1, int(config.get("wipLimit") or 3))
        active_others = sum(
            str(row.get("status") or "") in ACTIVE_STATUSES and row.get("id") != current.get("id")
            for row in all_current
        )
        if active_others + 1 > limit:
            return f"active maintenance WIP limit exceeded ({active_others + 1}/{limit})"

        error_budget = config.get("errorBudgetPolicy") if isinstance(config.get("errorBudgetPolicy"), dict) else {}
        allowed_while_frozen = {str(value) for value in error_budget.get("allowedWhileFrozen", [])}
        if (
            error_budget.get("freezeElectiveChangesOnGateFailure", True)
            and not reliability_clean(reliability, config)
            and str(args.change_class or "") not in allowed_while_frozen
        ):
            return "reliability gates are not clean; use an allowed --change-class for repair, security, or rollback work"

    evidence = {
        "designApproved": bool(args.design_approved or current.get("designApproved")),
        "independentReview": bool(args.independent_review or current.get("independentReview")),
        "humanApproved": bool(args.human_approved or current.get("humanApproved")),
        "promotionReviewed": bool(args.promotion_reviewed or current.get("promotionReviewed")),
        "rollbackEvidence": bool(args.rollback_evidence or current.get("rollbackEvidence")),
    }
    if target == "leased" and risk in {"medium", "high"} and not evidence["designApproved"]:
        return "medium/high-risk work requires --design-approved before leasing"
    if target == "leased" and risk == "high" and not evidence["humanApproved"]:
        return "high-risk work requires --human-approved before leasing"
    if target == "implemented":
        if not evidence["promotionReviewed"] or not evidence["rollbackEvidence"]:
            return "implementation requires --promotion-reviewed and --rollback-evidence"
        if risk in {"medium", "high"} and not evidence["independentReview"]:
            return "medium/high-risk implementation requires --independent-review"
    return None


def publish_summary(document: dict[str, Any]) -> None:
    open_rows = [
        row for row in current_rows(document.get("proposals", []))
        if row.get("status") in {"proposed", "approved", "leased", "implementing", "verifying"}
    ]
    command = [
        sys.executable, str(ROOT / "scripts" / "agent_publish.py"),
        "--agent", "josh2", "--type", "status", "--status", "info",
        "--title", "Ecosystem proposal ledger updated",
        "--tool", "proposal ledger",
        "--detail", f"{len(open_rows)} current proposal(s) remain open; publication never implies approval.",
        "--privacy", "dashboard-safe", "--brain-feed",
    ]
    subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title")
    parser.add_argument("--summary")
    parser.add_argument("--owner", default="josh2")
    parser.add_argument("--status", choices=sorted(VALID), default="proposed")
    parser.add_argument("--id")
    parser.add_argument("--risk", choices=sorted(RISK_LEVELS))
    parser.add_argument("--area")
    parser.add_argument("--source-candidate-id")
    parser.add_argument("--change-class", choices=["reviewed-maintenance", "security-fix", "reliability-repair", "rollback"])
    parser.add_argument("--design-approved", action="store_true")
    parser.add_argument("--independent-review", action="store_true")
    parser.add_argument("--human-approved", action="store_true")
    parser.add_argument("--promotion-reviewed", action="store_true")
    parser.add_argument("--rollback-evidence", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    document = read_json(LEDGER, {"version": 1, "proposals": []})
    proposals = [row for row in document.get("proposals", []) if isinstance(row, dict)]
    if args.title:
        if not args.summary:
            parser.error("--summary is required with --title")
        timestamp = iso()
        row = {
            "id": args.id or proposal_id(args.title),
            "title": args.title,
            "summary": args.summary,
            "owner": args.owner,
            "status": args.status,
            "risk": args.risk or "unclassified",
            "area": args.area or "Reliability",
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "privacy": "dashboard-safe",
            "approvalImpliedByPublication": False,
        }
        if args.source_candidate_id:
            row["sourceCandidateId"] = args.source_candidate_id
        proposals.append(row)
    elif args.id:
        matches = [row for row in proposals if row.get("id") == args.id]
        if not matches:
            parser.error(f"proposal not found: {args.id}")
        current = max(matches, key=lambda row: str(row.get("updatedAt") or ""))
        config = read_json(CONFIG_PATH, {})
        problem = validate_transition(
            current,
            args.status,
            current_rows(proposals),
            config,
            read_json(RELIABILITY_PATH, {}),
            args,
        )
        if problem:
            parser.error(problem)
        transition = dict(current)
        transition["previousStatus"] = current.get("status")
        transition["status"] = args.status
        transition["updatedAt"] = iso()
        if args.summary:
            transition["summary"] = args.summary
        if args.risk:
            transition["risk"] = args.risk
        if args.area:
            transition["area"] = args.area
        if args.source_candidate_id:
            transition["sourceCandidateId"] = args.source_candidate_id
        if args.change_class:
            transition["changeClass"] = args.change_class
        for field, supplied in (
            ("designApproved", args.design_approved),
            ("independentReview", args.independent_review),
            ("humanApproved", args.human_approved),
            ("promotionReviewed", args.promotion_reviewed),
            ("rollbackEvidence", args.rollback_evidence),
        ):
            if supplied:
                transition[field] = True
        proposals.append(transition)
    document.update({"version": 1, "updatedAt": iso(), "proposals": proposals})
    atomic_write(LEDGER, document)
    if args.publish:
        publish_summary(document)
    print(json.dumps(document, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
