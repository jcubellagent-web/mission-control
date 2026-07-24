from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "ecosystem_proposal_ledger.py"
SPEC = importlib.util.spec_from_file_location("ecosystem_proposal_ledger_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)

GATE_IDS = [
    "memory-privacy-reuse",
    "handoff-receipts",
    "completion-final-linkage",
    "telegram-contract",
    "recovery-proof",
    "scorecard-semantics",
]


def write_policy(tmp_path: Path, *, clean: bool = True, wip_limit: int = 3) -> tuple[Path, Path]:
    config = tmp_path / "continuous-maintenance.json"
    reliability = tmp_path / "reliability-reuse-eval.json"
    config.write_text(json.dumps({
        "wipLimit": wip_limit,
        "promotionGates": {"requiredReliabilityGates": GATE_IDS},
        "errorBudgetPolicy": {
            "freezeElectiveChangesOnGateFailure": True,
            "allowedWhileFrozen": ["security-fix", "reliability-repair", "rollback"],
        },
    }), encoding="utf-8")
    reliability.write_text(json.dumps({
        "ok": clean,
        "gates": [
            {"id": gate_id, "state": "pass" if clean else "fail"}
            for gate_id in GATE_IDS
        ],
    }), encoding="utf-8")
    return config, reliability


def test_current_rows_return_one_latest_event_per_proposal() -> None:
    rows = [
        {"id": "a", "status": "approved", "updatedAt": "2026-07-20T00:00:00Z"},
        {"id": "a", "status": "verifying", "updatedAt": "2026-07-21T00:00:00Z"},
        {"id": "b", "status": "proposed", "updatedAt": "2026-07-19T00:00:00Z"},
    ]

    current = {row["id"]: row for row in subject.current_rows(rows)}

    assert set(current) == {"a", "b"}
    assert current["a"]["status"] == "verifying"


def test_status_update_appends_a_transition_instead_of_rewriting_history(tmp_path: Path) -> None:
    ledger = tmp_path / "ecosystem-proposals.json"
    ledger.write_text(json.dumps({
        "version": 1,
        "proposals": [{
            "id": "proposal-a",
            "title": "Safe refactor",
            "summary": "Prepare a bounded change.",
            "owner": "joshex",
            "status": "approved",
            "risk": "low",
            "area": "Reliability",
            "createdAt": "2026-07-20T00:00:00Z",
            "updatedAt": "2026-07-20T00:00:00Z",
        }],
    }), encoding="utf-8")

    config, reliability = write_policy(tmp_path)
    with mock.patch.object(subject, "LEDGER", ledger), mock.patch.object(
        subject, "CONFIG_PATH", config
    ), mock.patch.object(subject, "RELIABILITY_PATH", reliability), mock.patch.object(
        sys, "argv", ["ecosystem_proposal_ledger.py", "--id", "proposal-a", "--status", "leased"]
    ):
        assert subject.main() == 0

    rows = json.loads(ledger.read_text(encoding="utf-8"))["proposals"]
    assert len(rows) == 2
    assert rows[0]["status"] == "approved"
    assert rows[1]["status"] == "leased"
    assert rows[1]["previousStatus"] == "approved"
    assert rows[1]["risk"] == "low"


def test_transition_policy_rejects_skips_and_frozen_elective_work(tmp_path: Path) -> None:
    config_path, reliability_path = write_policy(tmp_path, clean=False)
    current = {"id": "a", "status": "approved", "risk": "low"}
    base = {
        "risk": None,
        "change_class": None,
        "design_approved": False,
        "independent_review": False,
        "human_approved": False,
        "promotion_reviewed": False,
        "rollback_evidence": False,
    }
    args = type("Args", (), base)()
    config = subject.read_json(config_path, {})
    reliability = subject.read_json(reliability_path, {})

    assert "not allowed" in str(subject.validate_transition(current, "verifying", [current], config, reliability, args))
    assert "not clean" in str(subject.validate_transition(current, "leased", [current], config, reliability, args))
    args.change_class = "reliability-repair"
    assert subject.validate_transition(current, "leased", [current], config, reliability, args) is None


def test_wip_and_completion_evidence_are_enforced() -> None:
    config = {
        "wipLimit": 1,
        "promotionGates": {"requiredReliabilityGates": GATE_IDS},
        "errorBudgetPolicy": {
            "freezeElectiveChangesOnGateFailure": True,
            "allowedWhileFrozen": ["security-fix", "reliability-repair", "rollback"],
        },
    }
    reliability = {"ok": True, "gates": [{"id": gate_id, "state": "pass"} for gate_id in GATE_IDS]}
    args = type("Args", (), {
        "risk": None,
        "change_class": "reviewed-maintenance",
        "design_approved": False,
        "independent_review": False,
        "human_approved": False,
        "promotion_reviewed": False,
        "rollback_evidence": False,
    })()
    approved = {"id": "new", "status": "approved", "risk": "low"}
    active = {"id": "active", "status": "implementing", "risk": "low"}
    verifying = {"id": "done", "status": "verifying", "risk": "medium", "designApproved": True}

    assert "WIP limit" in str(subject.validate_transition(approved, "leased", [approved, active], config, reliability, args))
    assert "promotion-reviewed" in str(subject.validate_transition(verifying, "implemented", [verifying], config, reliability, args))
    args.promotion_reviewed = True
    args.rollback_evidence = True
    assert "independent-review" in str(subject.validate_transition(verifying, "implemented", [verifying], config, reliability, args))
    args.independent_review = True
    assert subject.validate_transition(verifying, "implemented", [verifying], config, reliability, args) is None

    frozen = {"ok": False, "gates": [{"id": gate_id, "state": "fail"} for gate_id in GATE_IDS]}
    args.change_class = "reviewed-maintenance"
    assert "not clean" in str(subject.validate_transition(verifying, "implemented", [verifying], config, frozen, args))
    args.change_class = "reliability-repair"
    assert subject.validate_transition(verifying, "implemented", [verifying], config, frozen, args) is None


def test_ledger_write_does_not_truncate_history(tmp_path: Path) -> None:
    ledger = tmp_path / "ecosystem-proposals.json"
    rows = [
        {"id": f"old-{index}", "status": "implemented", "updatedAt": f"2025-01-01T00:{index % 60:02d}:00Z"}
        for index in range(1001)
    ]
    ledger.write_text(json.dumps({"version": 1, "proposals": rows}), encoding="utf-8")

    with mock.patch.object(subject, "LEDGER", ledger), mock.patch.object(
        sys,
        "argv",
        ["ecosystem_proposal_ledger.py", "--title", "New", "--summary", "New proposal"],
    ):
        assert subject.main() == 0

    assert len(json.loads(ledger.read_text(encoding="utf-8"))["proposals"]) == 1002
