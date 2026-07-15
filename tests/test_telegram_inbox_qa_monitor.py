from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "telegram_inbox_qa_monitor.py"
SPEC = importlib.util.spec_from_file_location("telegram_inbox_qa_monitor_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
monitor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = monitor
SPEC.loader.exec_module(monitor)


def payload(*, ok: bool = True, cleanup_failed: int = 0, final_ms: float = 2_000.0) -> dict:
    return {
        "role": "josh2",
        "ok": ok,
        "problems": [] if ok else ["transport failed"],
        "stress": {"ok": True, "iterations": 100, "renderedCards": 900, "problems": []},
        "transport": {
            "ok": ok,
            "renderer": "rich",
            "timing": {
                "setupMs": 400,
                "cumulativeMs": {"eyes": 400, "header": 900, "liveCard": 1_400, "final": final_ms},
                "checks": {"terminalLiveCard100Percent": True},
            },
            "milestoneEdits": 4,
            "final": {"exactlyOne": True, "messageIds": [999999]},
            "cleanup": {
                "attempted": 4,
                "deleted": 4 - cleanup_failed,
                "failedIds": [888888] if cleanup_failed else [],
                "indeterminateIds": [],
                "indeterminateStages": [],
                "records": [{"messageId": 999999}],
            },
            "failures": [] if ok else ["transport failed"],
            "elapsedMs": 2_500,
        },
    }


def test_sanitize_result_whitelists_metrics_without_message_ids_or_raw_records() -> None:
    sample = monitor.sanitize_result(payload(), "live", checked_at="2026-07-15T12:00:00Z")
    encoded = json.dumps(sample)

    assert sample["transport"]["latencyMs"]["final"] == 2_000.0
    assert sample["transport"]["cleanup"] == {
        "attempted": 4,
        "deleted": 4,
        "failedCount": 0,
        "indeterminateCount": 0,
    }
    assert "999999" not in encoded
    assert "888888" not in encoded
    assert "records" not in encoded


def test_final_stage_is_slo_gated_and_scope_stays_synthetic() -> None:
    sample = monitor.sanitize_result(payload(final_ms=61_000), "live")
    violations = monitor.sample_violations(sample, {"eyes": 2_000, "header": 5_000, "liveCard": 8_000, "final": 60_000})

    assert violations == ["final latency exceeded 60000 ms"]
    assert "synthetic" in sample["transport"]["scope"]


def test_repeated_failure_debounces_then_recovery_clears(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(monitor, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(monitor, "LOCK_PATH", tmp_path / "state.lock")
    monkeypatch.setattr(monitor, "slo_thresholds", lambda: {"eyes": 2_000, "header": 5_000, "liveCard": 8_000, "final": 60_000})

    first = monitor.sanitize_result(payload(ok=False), "live")
    first_state, first_actionable = monitor.update_state(
        first, alert_after_failures=2, history_limit=100, rolling_window=30, minimum_rolling_samples=20
    )
    second = monitor.sanitize_result(payload(ok=False), "live")
    second_state, second_actionable = monitor.update_state(
        second, alert_after_failures=2, history_limit=100, rolling_window=30, minimum_rolling_samples=20
    )
    recovered = monitor.sanitize_result(payload(ok=True), "live")
    recovered_state, recovered_actionable = monitor.update_state(
        recovered, alert_after_failures=2, history_limit=100, rolling_window=30, minimum_rolling_samples=20
    )

    assert first_actionable is False
    assert first_state["status"] == "degraded"
    assert second_actionable is True
    assert second_state["status"] == "attention"
    assert recovered_actionable is False
    assert recovered_state["status"] == "ok"
    assert recovered_state["lanes"]["live"]["consecutiveFailures"] == 0


def test_incomplete_cleanup_is_actionable_on_first_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(monitor, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(monitor, "LOCK_PATH", tmp_path / "state.lock")
    monkeypatch.setattr(monitor, "slo_thresholds", lambda: {"eyes": 2_000, "header": 5_000, "liveCard": 8_000, "final": 60_000})
    sample = monitor.sanitize_result(payload(ok=False, cleanup_failed=1), "live")

    state, actionable = monitor.update_state(
        sample, alert_after_failures=2, history_limit=100, rolling_window=30, minimum_rolling_samples=20
    )

    assert actionable is True
    assert state["lanes"]["live"]["alertActive"] is True
    assert "cleanup" in " ".join(sample["violations"])


def test_busy_skip_preserves_an_existing_alert(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(monitor, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(monitor, "LOCK_PATH", tmp_path / "state.lock")
    monitor.atomic_write(
        monitor.STATE_PATH,
        {
            "version": 1,
            "lanes": {"live": {"consecutiveFailures": 2, "alertActive": True}},
            "history": [],
        },
    )
    sample = {
        "checkedAt": monitor.iso(),
        "mode": "live",
        "role": "josh2",
        "ok": True,
        "status": "skipped_busy",
        "stress": {"ok": True, "iterations": 0, "renderedCards": 0, "problemCount": 0},
    }

    state, actionable = monitor.update_state(
        sample, alert_after_failures=2, history_limit=100, rolling_window=30, minimum_rolling_samples=20
    )

    assert actionable is True
    assert state["lanes"]["live"]["consecutiveFailures"] == 2
    assert state["lanes"]["live"]["alertActive"] is True


def test_history_has_separate_time_and_count_retention() -> None:
    now = dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc)
    rows = [
        {"checkedAt": "2026-07-23T00:00:00Z", "mode": "stress"},
        {"checkedAt": "2026-07-24T00:00:01Z", "mode": "stress"},
        {"checkedAt": "2026-06-30T00:00:00Z", "mode": "live"},
        {"checkedAt": "2026-07-01T00:00:01Z", "mode": "live"},
    ]

    kept = monitor.prune_history(rows, now=now)

    assert [row["checkedAt"] for row in kept] == ["2026-07-01T00:00:01Z", "2026-07-24T00:00:01Z"]


def test_rolling_p95_uses_warmup_and_nearest_rank() -> None:
    rows = []
    for index in range(20):
        rows.append({
            "checkedAt": f"2026-07-{index + 1:02d}T00:00:00Z",
            "mode": "live",
            "transport": {"latencyMs": {"eyes": 500 if index < 19 else 2_500}},
        })
    rolling = monitor.rolling_latency(rows, 30, {"eyes": 2_000})

    assert rolling["stages"]["eyes"]["p95Ms"] == 500.0
    assert monitor.rolling_violations(rolling, 21) == []
    assert monitor.rolling_violations(rolling, 20) == []

    rows[-2]["transport"]["latencyMs"]["eyes"] = 2_400
    rolling = monitor.rolling_latency(rows, 30, {"eyes": 2_000})
    assert rolling["stages"]["eyes"]["p95Ms"] == 2_400.0
    assert monitor.rolling_violations(rolling, 20) == ["rolling synthetic eyes p95 exceeded 2000 ms"]


def test_private_cleanup_recovery_blocks_unknown_stage(monkeypatch, tmp_path) -> None:
    ledger = tmp_path / "private" / "pending.json"
    monkeypatch.setattr(monitor, "PRIVATE_CLEANUP_PATH", ledger)
    monitor.atomic_write_private(
        ledger,
        {
            "version": 1,
            "messageIds": [],
            "indeterminateStages": ["structured-final"],
            "chatId": monitor.PRODUCTION_CHAT_ID,
            "threadId": monitor.PRODUCTION_THREAD_ID,
        },
    )

    ok, result = monitor.retry_private_cleanup()

    assert ok is False
    assert result["unknown"] == 1
    assert ledger.exists()


def test_private_cleanup_recovery_fails_closed_on_corrupt_ledger(monkeypatch, tmp_path) -> None:
    ledger = tmp_path / "private" / "pending.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(monitor, "PRIVATE_CLEANUP_PATH", ledger)

    ok, result = monitor.retry_private_cleanup()

    assert ok is False
    assert result["invalid"] == 1
    assert ledger.read_text(encoding="utf-8") == "{not-json"


def test_host_opt_in_is_exactly_scoped_to_production_topic(monkeypatch, tmp_path) -> None:
    approval = tmp_path / "approval.json"
    monkeypatch.delenv("JOSH_TELEGRAM_RECURRING_CANARY", raising=False)
    monkeypatch.setattr(monitor, "OPT_IN_PATH", approval)
    monitor.atomic_write(approval, {"enabled": True, "chatId": monitor.PRODUCTION_CHAT_ID, "threadId": "17"})
    assert monitor.recurring_canary_enabled() is False

    monitor.atomic_write(approval, {"enabled": True, "chatId": monitor.PRODUCTION_CHAT_ID, "threadId": "1"})
    assert monitor.recurring_canary_enabled() is True


def test_runner_passes_private_journal_only_for_live_mode(monkeypatch, tmp_path) -> None:
    captured: list[dict] = []
    monkeypatch.delenv("TELEGRAM_CANARY_CLEANUP_JOURNAL", raising=False)

    class Result:
        returncode = 0
        stdout = json.dumps({"role": "josh2", "ok": True, "stress": {"ok": True}, "transport": None})

    def fake_run(*_args, **kwargs):
        captured.append(kwargs)
        return Result()

    monkeypatch.setattr(monitor.subprocess, "run", fake_run)
    monkeypatch.setattr(monitor, "PRIVATE_CLEANUP_PATH", tmp_path / "private" / "pending.json")

    monitor.run_harness("stress", 1, 30)
    monitor.run_harness("live", 1, 30)

    assert "TELEGRAM_CANARY_CLEANUP_JOURNAL" not in captured[0]["env"]
    assert captured[1]["env"]["TELEGRAM_CANARY_CLEANUP_JOURNAL"].endswith("pending.json")


def test_loaded_history_is_rewhitelisted_before_persistence(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(monitor, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(monitor, "LOCK_PATH", tmp_path / "state.lock")
    monitor.atomic_write(monitor.STATE_PATH, {
        "version": 1,
        "lanes": {"stress": {"prompt": "private", "consecutiveFailures": 0}},
        "history": [{
            "checkedAt": monitor.iso(),
            "mode": "stress",
            "ok": True,
            "messageIds": [123],
            "rawPrompt": "private",
            "stress": {"ok": True, "iterations": 1, "renderedCards": 9},
        }],
    })
    sample = monitor.sanitize_result({
        "role": "josh2",
        "ok": True,
        "stress": {"ok": True, "iterations": 1, "renderedCards": 9, "problems": []},
        "monitorDurationMs": 10,
    }, "stress")

    state, _ = monitor.update_state(
        sample, alert_after_failures=1, history_limit=100, rolling_window=30, minimum_rolling_samples=20
    )
    encoded = json.dumps(state)

    assert '"messageIds":' not in encoded
    assert '"rawPrompt":' not in encoded
    assert "private" not in encoded
    assert state["coverage"]["recurringProductionWrites"] is False


def test_safe_output_separates_run_health_from_paging_actionability() -> None:
    sample = monitor.sanitize_result(payload(ok=False), "live")
    state = {"summary": "degraded", "lanes": {"live": {"consecutiveFailures": 1, "alertAfterFailures": 2}}}

    output = monitor.safe_output(state, sample, actionable=False)

    assert output["ok"] is False
    assert output["alertActionable"] is False
    assert output["status"] == "failed"


def test_contract_stress_rolling_reports_warmup_and_p95(monkeypatch) -> None:
    monkeypatch.setattr(monitor, "inbox_slo_config", lambda: {
        "contractStressP95Ms": 50,
        "contractStressMinimumSamples": 3,
    })
    rows = [
        {"mode": "stress", "stress": {"durationMs": value}}
        for value in (10, 20, 80)
    ]

    rolling = monitor.rolling_contract_stress(rows[:2], 30, 20)
    assert rolling["status"] == "warming_up"
    rolling = monitor.rolling_contract_stress(rows, 30, 20)
    assert rolling["p95Ms"] == 80.0
    assert rolling["status"] == "attention"
