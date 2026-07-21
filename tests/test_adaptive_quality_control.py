from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "adaptive_quality_control.py"
SPEC = importlib.util.spec_from_file_location("adaptive_quality_control_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
quality = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality)


def test_required_contract_failure_reduces_score_and_opens_attention(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "good.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    config = {
        "contracts": [
            {"id": "good", "path": "data/good.json", "field": "ok", "accepted": [True], "required": True, "weight": 50},
            {"id": "missing", "path": "data/missing.json", "field": "ok", "accepted": [True], "required": True, "weight": 50},
        ],
        "repository": {"roots": [], "extensions": [".py"]},
    }
    with patch.object(quality, "ROOT", tmp_path), patch.object(quality, "churn_counts", return_value={}):
        payload, _candidates = quality.build_payload(config, "snapshot")
    assert payload["status"] == "attention"
    assert payload["qualityScore"] == 50
    assert payload["contracts"][1]["state"] == "fail"


def test_large_file_candidate_is_ranked_but_never_authorized_for_mutation(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "large.py").write_text("\n".join(f"value_{index} = {index}" for index in range(30)), encoding="utf-8")
    config = {
        "contracts": [],
        "repository": {
            "roots": ["scripts"],
            "extensions": [".py"],
            "excludedParts": [],
            "largeFileLines": 20,
            "veryLargeFileLines": 40,
            "maximumCandidates": 5,
        },
        "riskPolicy": {"protectedPathFragments": [], "automaticSourceMutation": False},
    }
    with patch.object(quality, "ROOT", tmp_path), patch.object(quality, "churn_counts", return_value={}):
        metrics, candidates = quality.repository_analysis(config, deep=False)
    assert metrics["largeFiles"] == 1
    assert candidates[0]["kind"] == "large-file"
    assert candidates[0]["automaticMutationAllowed"] is False


def test_promoted_baseline_is_not_replaced_by_comparison(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"promotedAt": "2026-07-01T00:00:00Z", "qualityScore": 98, "metrics": {"sourceLines": 100}}), encoding="utf-8")
    config = {"baselinePolicy": {"maximumRegressionPoints": 3}}
    with patch.object(quality, "BASELINE_PATH", baseline):
        comparison = quality.compare_baseline({"sourceLines": 120}, 90, config)
    assert comparison["status"] == "attention"
    assert comparison["scoreDelta"] == -8
    assert json.loads(baseline.read_text())["qualityScore"] == 98


def test_snapshot_omits_unmeasured_deep_metric_from_baseline_delta(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({
        "promotedAt": "2026-07-01T00:00:00Z",
        "qualityScore": 100,
        "metrics": {"sourceLines": 100, "duplicateGroups": 24},
    }), encoding="utf-8")
    with patch.object(quality, "BASELINE_PATH", baseline):
        comparison = quality.compare_baseline({"sourceLines": 105, "duplicateGroups": None}, 100, {})
    assert comparison["metricDelta"] == {"sourceLines": 5}


def test_existing_baseline_requires_explicit_history_preserving_replacement(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    history = tmp_path / "history.json"
    baseline.write_text(json.dumps({"promotedAt": "2026-07-01T00:00:00Z", "qualityScore": 98}), encoding="utf-8")
    payload = {"status": "ready", "qualityScore": 100, "metrics": {}, "contracts": []}
    with patch.object(quality, "BASELINE_PATH", baseline), patch.object(quality, "BASELINE_HISTORY_PATH", history):
        try:
            quality.promote_baseline(payload)
        except RuntimeError as exc:
            assert "--replace-baseline" in str(exc)
        else:
            raise AssertionError("existing baseline was replaced without an explicit gate")
        replacement = quality.promote_baseline(payload, replace=True)
    archived = json.loads(history.read_text())["baselines"]
    assert replacement["qualityScore"] == 100
    assert archived[0]["baseline"]["qualityScore"] == 98
    assert len(archived[0]["sha256"]) == 64


def test_cloud_model_policy_keeps_codex_as_trusted_executor() -> None:
    config = json.loads((Path(__file__).parents[1] / "config" / "adaptive-quality-control.json").read_text())
    assert config["modelPolicy"]["analysis"] == "ollama/glm-5.2:cloud"
    assert config["modelPolicy"]["trustedExecutor"] == "openai/codex"
    assert config["modelPolicy"]["reviewRequiresExactDiffEvidence"] is True
    assert config["riskPolicy"]["automaticSourceMutation"] is False
    assert config["ownershipPolicy"]["requiredOwners"] == ["josh2", "jaimes"]
    assert config["ownershipPolicy"]["joshex"]["requiredForOperation"] is False


def test_joshex_oversight_is_independent_and_non_blocking(tmp_path: Path) -> None:
    config = json.loads((Path(__file__).parents[1] / "config" / "adaptive-quality-control.json").read_text())
    schedule = tmp_path / "schedule.json"
    schedule.write_text(json.dumps({"jobs": [
        {"id": "adaptive-quality-snapshot", "owner": "josh2"},
        {"id": "daily-refactor-discovery", "owner": "josh2"},
        {"id": "weekly-quality-baseline-review", "owner": "jaimes"},
    ]}), encoding="utf-8")
    payload = {
        "qualityScore": 100,
        "contracts": [{"required": True, "passed": True}],
        "baseline": {"status": "stable", "message": "Stable."},
        "refactorPortfolio": {"candidates": 3, "highRisk": 1, "mediumRisk": 1, "lowRisk": 1, "automaticSourceMutation": False},
        "modelRoute": {"reviewRequiresExactDiffEvidence": True},
    }
    with patch.object(quality, "QA_SCHEDULE_PATH", schedule):
        report = quality.build_oversight(payload, config)
    assert report["status"] == "pass"
    assert report["requiredForOperation"] is False
    assert report["actualRecurringOwners"] == {
        "adaptive-quality-snapshot": "josh2",
        "daily-refactor-discovery": "josh2",
        "weekly-quality-baseline-review": "jaimes",
    }
    assert "joshex" not in set(report["actualRecurringOwners"].values())
