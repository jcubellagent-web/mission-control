from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "continuous_maintenance.py"
SPEC = importlib.util.spec_from_file_location("continuous_maintenance_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)

NOW = dt.datetime(2026, 7, 24, 12, 0, tzinfo=dt.timezone.utc)


def config() -> dict:
    return {
        "mode": "proposal-first",
        "wipLimit": 1,
        "proposalMaxAgeDays": 30,
        "reliabilityMaxAgeMinutes": 120,
        "requiredConsecutiveCleanRuns": 7,
        "activeStages": ["leased", "implementing", "verifying"],
        "promotionGates": {
            "requiredReliabilityGates": list(subject.RELIABILITY_GATE_IDS),
            "automaticSourceMutation": False,
            "reviewedPromotionRequired": True,
            "rollbackEvidenceRequired": True,
        },
        "errorBudgetPolicy": {
            "freezeElectiveChangesOnGateFailure": True,
            "allowedWhileFrozen": ["security-fix", "reliability-repair", "rollback"],
        },
        "riskTiers": {"low": {"automaticPreparation": ["prepare-sandbox"], "reviewRequired": ["promote-source"]}},
        "dependencyPolicy": {"majorUpdates": "individual-reviewed-proposal"},
    }


def reliability(*, clean: bool, checked_at: str = "2026-07-24T11:00:00Z") -> dict:
    return {
        "checkedAt": checked_at,
        "ok": clean,
        "gates": [
            {"id": gate_id, "state": "pass" if clean else ("fail" if gate_id == "handoff-receipts" else "pass")}
            for gate_id in subject.RELIABILITY_GATE_IDS
        ],
    }


def test_current_projection_deduplicates_history_and_preserves_event_count() -> None:
    rows = [
        {"id": "proposal-a", "title": "First", "status": "approved", "updatedAt": "2026-07-20T00:00:00Z"},
        {"id": "proposal-a", "title": "First", "status": "implementing", "risk": "low", "updatedAt": "2026-07-21T00:00:00Z"},
        {"id": "proposal-b", "title": "Second", "status": "implemented", "updatedAt": "2026-07-22T00:00:00Z"},
    ]

    current = subject.current_proposals(rows)

    assert len(current) == 2
    first = next(row for row in current if row["id"] == "proposal-a")
    assert first["stage"] == "implementing"
    assert first["risk"] == "low"
    assert first["historyEvents"] == 2


def test_seventh_clean_run_earns_reviewed_promotion_but_never_auto_mutation() -> None:
    history = {
        "runs": [
            {"checkedAt": f"2026-07-{day:02d}T11:00:00Z", "clean": True, "gatesPassed": 6, "gatesRequired": 6}
            for day in range(18, 24)
        ]
    }
    proposals = {
        "proposals": [
            {
                "id": "proposal-a",
                "title": "Bounded refactor",
                "status": "approved",
                "risk": "low",
                "sourceCandidateId": "candidate-linked",
                "createdAt": "2026-07-23T00:00:00Z",
                "updatedAt": "2026-07-23T00:00:00Z",
            }
        ]
    }
    candidates = {
        "candidates": [
            {"id": "candidate-linked", "title": "Already accepted", "risk": "low", "score": 90},
            {"id": "candidate-open", "title": "Still discovered", "risk": "medium", "score": 80},
        ]
    }

    portfolio, updated_history = subject.build_portfolio(
        config(), proposals, candidates, reliability(clean=True), history, now=NOW
    )

    assert portfolio["status"] == "ready"
    assert portfolio["readiness"]["consecutiveCleanRuns"] == 7
    assert portfolio["readiness"]["promotionReady"] is True
    assert portfolio["policy"]["automaticSourceMutation"] is False
    assert portfolio["policy"]["reviewedPromotionRequired"] is True
    assert portfolio["changePolicy"]["electiveChangesFrozen"] is False
    assert [row["id"] for row in portfolio["discoveries"]] == ["candidate-open"]
    assert len(updated_history["runs"]) == 7


def test_failed_gate_resets_readiness_and_wip_limit_fails_closed() -> None:
    proposals = {
        "proposals": [
            {"id": "one", "title": "One", "status": "implementing", "updatedAt": "2026-07-24T10:00:00Z"},
            {"id": "two", "title": "Two", "status": "verifying", "updatedAt": "2026-07-24T10:01:00Z"},
        ]
    }
    history = {"runs": [{"checkedAt": "2026-07-23T11:00:00Z", "clean": True, "gatesPassed": 6, "gatesRequired": 6}]}

    portfolio, _updated_history = subject.build_portfolio(
        config(), proposals, {"candidates": []}, reliability(clean=False), history, now=NOW
    )

    assert portfolio["status"] == "watch"
    assert portfolio["readiness"]["consecutiveCleanRuns"] == 0
    assert portfolio["readiness"]["promotionReady"] is False
    assert portfolio["changePolicy"]["electiveChangesFrozen"] is True
    assert "handoff-receipts" in portfolio["changePolicy"]["reasonGateIds"]
    assert portfolio["wip"]["withinLimit"] is False
    assert portfolio["counts"]["activeWip"] == 2


def test_missing_or_replayed_current_snapshot_never_inherits_prior_readiness() -> None:
    history = {
        "runs": [
            {"checkedAt": f"2026-07-{day:02d}T11:00:00Z", "clean": True, "gatesPassed": 6, "gatesRequired": 6}
            for day in range(18, 25)
        ]
    }

    missing, _ = subject.build_portfolio(config(), {"proposals": []}, {}, {}, history, now=NOW)
    replayed, _ = subject.build_portfolio(
        config(),
        {"proposals": []},
        {},
        reliability(clean=True, checked_at="2026-07-18T11:00:00Z"),
        history,
        now=NOW,
    )

    for portfolio in (missing, replayed):
        assert portfolio["status"] == "watch"
        assert portfolio["readiness"]["promotionReady"] is False
        assert portfolio["readiness"]["snapshotFresh"] is False
        assert portfolio["changePolicy"]["electiveChangesFrozen"] is True


def test_old_open_proposals_are_flagged_without_deleting_history() -> None:
    proposals = {
        "proposals": [{
            "id": "aging",
            "title": "Aging proposal",
            "status": "approved",
            "createdAt": "2026-05-01T00:00:00Z",
            "updatedAt": "2026-05-01T00:00:00Z",
        }]
    }

    portfolio, _history = subject.build_portfolio(
        config(), proposals, {"candidates": []}, reliability(clean=False), {}, now=NOW
    )

    assert portfolio["counts"]["aging"] == 1
    assert portfolio["wip"]["agingProposalIds"] == ["aging"]
    assert portfolio["currentProposals"][0]["historyEvents"] == 1
