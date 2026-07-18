from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "ecosystem_qa_benchmark.py"
SPEC = importlib.util.spec_from_file_location("ecosystem_qa_benchmark_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"{}"


def test_http_probe_recovers_one_transient_failure() -> None:
    with patch.object(benchmark.urllib.request, "urlopen", side_effect=[OSError("brief restart"), Response(), Response()]), \
         patch.object(benchmark.time, "sleep"):
        result = benchmark.http_performance(samples=2, attempts_per_sample=2)
    assert result["ok"] is True
    assert result["errors"] == 0
    assert result["retryAttempts"] == 1


def health_payload() -> dict:
    return {
        "status": "attention",
        "agents": [{"ok": True, "stale": False} for _ in range(3)],
        "modelRoutesOk": True,
        "cronAttentionCount": 0,
        "operationalCronAttention": [],
        "qaMetaAttention": [],
        "blockingActionRequiredCount": 0,
        "nonBlockingActionRequiredCount": 0,
        "controlTowerAgeMinutes": 1.0,
    }


def test_medium_human_action_does_not_fail_operational_health(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "control-tower-live.json").write_text(
        json.dumps({"actionRequired": [{"priority": "medium", "title": "Human decision"}]}),
        encoding="utf-8",
    )
    with patch.object(benchmark, "ROOT", tmp_path), \
         patch.object(benchmark, "execute", return_value={"ok": False, "stdout": json.dumps(health_payload())}):
        result = benchmark.ecosystem_health_check()
    assert result["ok"] is True
    assert result["nonBlockingActionRequired"] == 1
    assert result["blockingActionRequired"] == 0


def test_high_priority_action_still_fails_operational_health(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "control-tower-live.json").write_text(
        json.dumps({"actionRequired": [{"priority": "high", "title": "Runtime outage"}]}),
        encoding="utf-8",
    )
    with patch.object(benchmark, "ROOT", tmp_path), \
         patch.object(benchmark, "execute", return_value={"ok": False, "stdout": json.dumps(health_payload())}):
        result = benchmark.ecosystem_health_check()
    assert result["ok"] is False
    assert result["blockingActionRequired"] == 1


def test_qa_meta_evidence_does_not_recursively_fail_operational_health(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "control-tower-live.json").write_text(json.dumps({
        "actionRequired": [{
            "priority": "high",
            "title": "1 scheduled job(s) missed: Deep release QA",
            "qaMetaOnly": True,
            "qaJobIds": ["nightly-control-tower-suite"],
        }],
    }))
    (data_dir / "ecosystem-qa-scheduler.json").write_text(json.dumps({
        "jobs": {"nightly-control-tower-suite": {"status": "running"}},
    }))
    payload = health_payload()
    payload["qaMetaAttention"] = [{
        "name": "Deep release QA",
        "status": "error",
        "qaJobId": "nightly-control-tower-suite",
        "qaMeta": True,
    }]
    with patch.object(benchmark, "ROOT", tmp_path), \
         patch.object(benchmark, "execute", return_value={"ok": False, "stdout": json.dumps(payload)}):
        result = benchmark.ecosystem_health_check()

    assert result["ok"] is True
    assert result["blockingActionRequired"] == 0
    assert result["qaMetaActionRequired"][0]["qaMetaOnly"] is True
    assert result["qaMetaAttention"][0]["qaJobId"] == "nightly-control-tower-suite"
    assert result["inFlightDeepReleaseQa"] is True


def test_operational_cron_attention_still_fails_benchmark_health(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "control-tower-live.json").write_text(json.dumps({"actionRequired": []}))
    payload = health_payload()
    payload["operationalCronAttention"] = [{"name": "Telegram delivery", "status": "error"}]
    with patch.object(benchmark, "ROOT", tmp_path), \
         patch.object(benchmark, "execute", return_value={"ok": False, "stdout": json.dumps(payload)}):
        result = benchmark.ecosystem_health_check()

    assert result["ok"] is False


def test_authoritative_health_blocker_survives_missing_live_projection(tmp_path) -> None:
    (tmp_path / "data").mkdir()
    payload = health_payload()
    payload["blockingActionRequiredCount"] = 1
    with patch.object(benchmark, "ROOT", tmp_path), \
         patch.object(benchmark, "execute", return_value={"ok": False, "stdout": json.dumps(payload)}):
        result = benchmark.ecosystem_health_check()

    assert result["ok"] is False
    assert result["blockingActionRequired"] == 1


def test_stable_qa_job_ids_classify_legacy_meta_action_without_title_matching(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "control-tower-live.json").write_text(json.dumps({
        "actionRequired": [{
            "priority": "high",
            "title": "Aggregate result",
            "qaJobIds": ["daily-qa-rollup"],
        }],
    }))
    with patch.object(benchmark, "ROOT", tmp_path), \
         patch.object(benchmark, "execute", return_value={"ok": False, "stdout": json.dumps(health_payload())}):
        result = benchmark.ecosystem_health_check()

    assert result["ok"] is True
    assert result["blockingActionRequired"] == 0
    assert result["qaMetaActionRequired"][0]["qaJobIds"] == ["daily-qa-rollup"]


def test_explicit_mixed_action_remains_blocking_even_with_only_aggregate_ids(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "control-tower-live.json").write_text(json.dumps({
        "actionRequired": [{
            "priority": "high",
            "title": "Aggregate and operational failures",
            "qaMetaOnly": False,
            "qaJobIds": ["daily-qa-rollup", "nightly-control-tower-suite"],
        }],
    }))
    with patch.object(benchmark, "ROOT", tmp_path), \
         patch.object(benchmark, "execute", return_value={"ok": False, "stdout": json.dumps(health_payload())}):
        result = benchmark.ecosystem_health_check()

    assert result["ok"] is False
    assert result["blockingActionRequired"] == 1
    assert result["qaMetaActionRequired"] == []


def test_zero_age_dashboard_is_fresh(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "control-tower-live.json").write_text(json.dumps({"actionRequired": []}))
    payload = health_payload()
    payload["controlTowerAgeMinutes"] = 0.0
    with patch.object(benchmark, "ROOT", tmp_path), \
         patch.object(benchmark, "execute", return_value={"ok": False, "stdout": json.dumps(payload)}):
        result = benchmark.ecosystem_health_check()

    assert result["ok"] is True
