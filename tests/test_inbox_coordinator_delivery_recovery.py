from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inbox_coordinator.py"
COMPLETE_OUTPUT = """Complete: Yes, the objective was completed.
What was done:
- Preserved the verified worker result.
- Reused the existing Inbox work card.
- Delivered the structured final response.
Issues:
- n/a
Appropriate next steps:
- No action needed.
Approval needed:
- n/a
"""


def load_coordinator(tmp_path: Path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        f"inbox_coordinator_delivery_recovery_{tmp_path.name}", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    private_dir = tmp_path / "private"
    monkeypatch.setattr(module, "PRIVATE_DIR", private_dir)
    monkeypatch.setattr(module, "STATE_PATH", private_dir / "jobs.json")
    monkeypatch.setattr(module, "LOCK_PATH", private_dir / "jobs.lock")
    monkeypatch.setattr(module, "TELEMETRY_PATH", tmp_path / "telemetry.jsonl")
    monkeypatch.setattr(module, "publish_control_tower", lambda *_args, **_kwargs: None)
    return module


def make_job(module, *, job_id: str = "job123", status: str = "failed", delivered: bool = False,
             execution_verified: bool = True, card_key: str = "inbox:1:42"):
    result_path = module.PRIVATE_DIR / f"{job_id}.result"
    module.ensure_private_dir()
    result_path.write_text(COMPLETE_OUTPUT, encoding="utf-8")
    job = {
        "jobId": job_id,
        "createdAt": module.utc_now(),
        "updatedAt": module.utc_now(),
        "status": status,
        "attempt": 1,
        "maxRetries": 1,
        "resultPath": str(result_path),
        "delivered": delivered,
        "origin": {
            "cardKey": card_key,
            "chatId": "-1003589561528",
            "threadId": "1",
            "messageId": "42",
        },
        "route": {
            "routeId": "luna",
            "worker": "josh2-codex-luna",
            "host": "josh2",
            "provider": "codex",
            "model": "gpt-5.6-luna",
            "routingReason": "fast Inbox coordination",
        },
        "actual": {
            "actualHost": "josh2",
            "actualWorker": "josh2-codex-luna",
            "actualProvider": "codex",
            "actualModel": "gpt-5.6-luna",
            "modelVerified": True,
            "executionVerified": execution_verified,
        },
        "lastError": "delivery failed",
    }
    module.save_json(module.STATE_PATH, {"jobs": {job_id: job}})
    return job_id, result_path


def read_job(module, job_id: str):
    return json.loads(module.STATE_PATH.read_text(encoding="utf-8"))["jobs"][job_id]


def test_recover_delivers_saved_verified_result_once_without_model_rerun(tmp_path, monkeypatch):
    module = load_coordinator(tmp_path, monkeypatch)
    job_id, result_path = make_job(module)
    deliveries = []

    def fake_delivery(received_id, snapshot, route, execution, output):
        deliveries.append((received_id, snapshot["origin"]["cardKey"], route["routeId"], output))
        return True

    monkeypatch.setattr(module, "deliver_result", fake_delivery)
    monkeypatch.setattr(
        module,
        "spawn_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model worker must not run")),
    )
    monkeypatch.setattr(
        module,
        "execute_route",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model executor must not run")),
    )

    first = module.recover()
    second = module.recover()

    assert first["ok"] is True
    assert first["deliveryRecovered"] == 1
    assert first["recovered"] == 0
    assert second["deliveryRecovered"] == 0
    assert len(deliveries) == 1
    assert deliveries[0][0:3] == (job_id, "inbox:1:42", "luna")
    assert deliveries[0][3] == COMPLETE_OUTPUT
    saved = read_job(module, job_id)
    assert saved["status"] == "done"
    assert saved["delivered"] is True
    assert saved["deliveryRecoveryAttempts"] == 1
    assert "deliveryRecoveryToken" not in saved
    assert result_path.exists()


def test_recover_failed_delivery_remains_retryable_with_same_card_key(tmp_path, monkeypatch):
    module = load_coordinator(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "DELIVERY_RECOVERY_BACKOFF_SECONDS", 0)
    job_id, result_path = make_job(module)
    card_keys = []
    outcomes = iter([False, True])

    def fake_delivery(_job_id, snapshot, _route, _execution, _output):
        card_keys.append(snapshot["origin"]["cardKey"])
        return next(outcomes)

    monkeypatch.setattr(module, "deliver_result", fake_delivery)
    monkeypatch.setattr(
        module,
        "spawn_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model worker must not run")),
    )

    first = module.recover()
    pending = read_job(module, job_id)
    second = module.recover()

    assert first["ok"] is False
    assert first["deliveryRetryFailed"] == 1
    assert pending["status"] == "failed"
    assert pending["delivered"] is False
    assert pending["lastError"] == "delivery_recovery_failed"
    assert result_path.exists()
    assert second["ok"] is True
    assert second["deliveryRecovered"] == 1
    assert card_keys == ["inbox:1:42", "inbox:1:42"]
    assert read_job(module, job_id)["deliveryRecoveryAttempts"] == 2


def test_automatic_delivery_recovery_has_backoff_and_attempt_cap(tmp_path, monkeypatch):
    module = load_coordinator(tmp_path, monkeypatch)
    job_id, _result_path = make_job(module)
    deliveries = []
    monkeypatch.setattr(
        module,
        "deliver_result",
        lambda *_args, **_kwargs: deliveries.append("attempt") or False,
    )

    first = module.recover()
    immediate = module.recover()

    assert first["deliveryRetryFailed"] == 1
    assert immediate["deliveryDeferredBackoff"] == 1
    assert deliveries == ["attempt"]

    # Advance only the stored attempt time; the immutable delivery-failure
    # reference remains fresh and cannot be extended by these edits.
    for expected_attempts in (2, 3):
        state = json.loads(module.STATE_PATH.read_text(encoding="utf-8"))
        state["jobs"][job_id]["deliveryRecoveryLastAttemptAt"] = "2000-01-01T00:00:00Z"
        module.save_json(module.STATE_PATH, state)
        retry = module.recover()
        assert retry["deliveryRetryFailed"] == 1
        assert len(deliveries) == expected_attempts

    state = json.loads(module.STATE_PATH.read_text(encoding="utf-8"))
    state["jobs"][job_id]["deliveryRecoveryLastAttemptAt"] = "2000-01-01T00:00:00Z"
    module.save_json(module.STATE_PATH, state)
    exhausted = module.recover()

    assert exhausted["deliveryAttemptsExhausted"] == 1
    assert exhausted["deliveryRetryFailed"] == 0
    assert len(deliveries) == 3
    saved = read_job(module, job_id)
    assert saved["deliveryRecoveryAutomaticAttempts"] == 3
    assert saved["delivered"] is False


def test_recover_never_creates_replacement_card_when_original_key_is_missing(tmp_path, monkeypatch):
    module = load_coordinator(tmp_path, monkeypatch)
    job_id, _result_path = make_job(module, card_key="")
    monkeypatch.setattr(
        module,
        "deliver_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("delivery must require original card key")),
    )
    monkeypatch.setattr(
        module,
        "spawn_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model worker must not run")),
    )

    result = module.recover()

    assert result["deliveryNotRecoverable"] == 1
    saved = read_job(module, job_id)
    assert saved["status"] == "failed"
    assert saved["delivered"] is False
    assert saved.get("deliveryRecoveryAttempts") is None


def test_routine_recover_defers_historical_saved_results_until_exact_job_is_selected(tmp_path, monkeypatch):
    module = load_coordinator(tmp_path, monkeypatch)
    job_id, result_path = make_job(module)
    state = json.loads(module.STATE_PATH.read_text(encoding="utf-8"))
    state["jobs"][job_id]["createdAt"] = "2020-01-01T00:00:00Z"
    state["jobs"][job_id]["updatedAt"] = "2020-01-01T00:05:00Z"
    module.save_json(module.STATE_PATH, state)
    deliveries = []
    monkeypatch.setattr(
        module,
        "deliver_result",
        lambda received_id, *_args: deliveries.append(received_id) or True,
    )
    monkeypatch.setattr(
        module,
        "spawn_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model worker must not run")),
    )

    automatic = module.recover()
    automatic_again = module.recover()

    assert automatic["deliveryDeferredHistorical"] == 1
    assert automatic_again["deliveryDeferredHistorical"] == 1
    assert automatic["deliveryRecovered"] == 0
    assert deliveries == []
    deferred = read_job(module, job_id)
    assert deferred["status"] == "failed"
    assert deferred["delivered"] is False
    assert deferred["deliveryRecoveryReferenceAt"] == "2020-01-01T00:05:00Z"
    assert result_path.exists()

    explicit = module.recover(job_id)

    assert explicit["requestedJobId"] == job_id
    assert explicit["requestedJobFound"] is True
    assert explicit["deliveryRecovered"] == 1
    assert deliveries == [job_id]


def test_take_result_exposes_delivery_pending_output_without_consuming_it(tmp_path, monkeypatch):
    module = load_coordinator(tmp_path, monkeypatch)
    job_id, result_path = make_job(module)

    first = module.take_result(job_id)
    second = module.take_result(job_id)

    assert first["ok"] is True
    assert first["output"] == COMPLETE_OUTPUT
    assert first["deliveryPending"] is True
    assert first["deliveryRecoveryEligible"] is True
    assert first["resultRetained"] is True
    assert second["ok"] is True
    assert second["output"] == COMPLETE_OUTPUT
    assert result_path.exists()
    assert read_job(module, job_id)["resultInspectedAt"] == first["job"]["resultInspectedAt"]


def test_inspecting_historical_result_does_not_refresh_automatic_delivery_window(tmp_path, monkeypatch):
    module = load_coordinator(tmp_path, monkeypatch)
    job_id, _result_path = make_job(module)
    state = json.loads(module.STATE_PATH.read_text(encoding="utf-8"))
    state["jobs"][job_id]["createdAt"] = "2020-01-01T00:00:00Z"
    state["jobs"][job_id]["updatedAt"] = "2020-01-01T00:05:00Z"
    module.save_json(module.STATE_PATH, state)
    monkeypatch.setattr(
        module,
        "deliver_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("historical result must stay inert")),
    )

    inspected = module.take_result(job_id)
    recovered = module.recover()

    assert inspected["ok"] is True
    assert recovered["deliveryDeferredHistorical"] == 1
    assert recovered["deliveryRecovered"] == 0
    saved = read_job(module, job_id)
    assert saved["deliveryRecoveryReferenceAt"] == "2020-01-01T00:05:00Z"


def test_take_result_rejects_unverified_failed_output(tmp_path, monkeypatch):
    module = load_coordinator(tmp_path, monkeypatch)
    job_id, result_path = make_job(module, execution_verified=False)

    result = module.take_result(job_id)

    assert result == {"ok": False, "error": "execution-unverified"}
    assert result_path.exists()


def test_queued_job_with_verified_result_uses_delivery_only_recovery(tmp_path, monkeypatch):
    module = load_coordinator(tmp_path, monkeypatch)
    job_id, _result_path = make_job(module, status="queued")
    calls = []
    monkeypatch.setattr(module, "deliver_result", lambda *_args: calls.append("delivery") or True)
    monkeypatch.setattr(
        module,
        "spawn_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("saved output must not be rerun")),
    )

    result = module.recover()

    assert result["deliveryRecovered"] == 1
    assert result["recovered"] == 0
    assert calls == ["delivery"]
    assert read_job(module, job_id)["status"] == "done"


def test_verified_result_reader_rejects_symlink_even_when_state_uses_expected_name(tmp_path, monkeypatch):
    module = load_coordinator(tmp_path, monkeypatch)
    job_id, result_path = make_job(module)
    outside = tmp_path / "outside.txt"
    outside.write_text("private outside content", encoding="utf-8")
    result_path.unlink()
    result_path.symlink_to(outside)

    result = module.take_result(job_id)

    assert result["ok"] is False
    assert result["error"] == "result-file-unavailable"
    assert outside.read_text(encoding="utf-8") == "private outside content"
