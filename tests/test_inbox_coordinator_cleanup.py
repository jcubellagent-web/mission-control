from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import stat
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "inbox_coordinator.py"


def load_subject(private_dir: Path):
    spec = importlib.util.spec_from_file_location("inbox_coordinator_cleanup_subject", MODULE_PATH)
    assert spec and spec.loader
    subject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(subject)
    subject.PRIVATE_DIR = private_dir
    subject.STATE_PATH = private_dir / "jobs.json"
    subject.LOCK_PATH = private_dir / "jobs.lock"
    return subject


def old_timestamp(days: int = 3) -> str:
    return (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def recent_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_state(subject, jobs: dict) -> None:
    subject.PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    subject.STATE_PATH.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_cleanup_scrubs_old_terminal_artifacts_but_retains_audit_tombstone(tmp_path: Path) -> None:
    subject = load_subject(tmp_path / "private")
    job_id = "terminal-job"
    prompt_path = subject.PRIVATE_DIR / f"{job_id}.prompt"
    result_path = subject.PRIVATE_DIR / f"{job_id}.result"
    jobs = {
        job_id: {
            "jobId": job_id,
            "createdAt": old_timestamp(4),
            "updatedAt": old_timestamp(3),
            "status": "done",
            "attempt": 1,
            "maxRetries": 1,
            "timeoutSeconds": 120,
            "promptPath": str(prompt_path),
            "resultPath": str(result_path),
            "promptSignature": "prompt-fingerprint",
            "dedupeKey": "dedupe-fingerprint",
            "origin": {"chatId": "chat", "threadId": "1", "messageId": "42"},
            "route": {"routeId": "luna", "model": "gpt-5.6-luna"},
            "actual": {"actualModel": "gpt-5.6-luna", "executionVerified": True},
            "latencyMs": 123,
            "delivered": True,
        }
    }
    write_state(subject, jobs)
    prompt_path.write_text("private prompt", encoding="utf-8")
    result_path.write_text("private result", encoding="utf-8")

    report = subject.cleanup(24 * 60 * 60)

    assert report["scrubbedJobs"] == 1
    assert report["removedArtifacts"] == 2
    assert not prompt_path.exists()
    assert not result_path.exists()
    saved = json.loads(subject.STATE_PATH.read_text(encoding="utf-8"))["jobs"][job_id]
    assert saved["jobId"] == job_id
    assert saved["status"] == "done"
    assert saved["origin"]["messageId"] == "42"
    assert saved["route"]["routeId"] == "luna"
    assert saved["actual"]["executionVerified"] is True
    assert saved["latencyMs"] == 123
    assert saved["delivered"] is True
    assert saved["auditTombstone"] is True
    assert saved["artifactsScrubbedAt"].endswith("Z")
    for private_field in ("promptPath", "resultPath", "promptSignature", "dedupeKey"):
        assert private_field not in saved
    assert mode(subject.STATE_PATH) == 0o600
    assert mode(subject.LOCK_PATH) == 0o600
    assert subject.job_status(job_id)["resultReady"] is False


def test_cleanup_preserves_active_and_young_jobs_and_hardens_private_files(tmp_path: Path) -> None:
    subject = load_subject(tmp_path / "private")
    queued_path = subject.PRIVATE_DIR / "queued-job.prompt"
    running_path = subject.PRIVATE_DIR / "running-job.result"
    young_path = subject.PRIVATE_DIR / "young-job.result"
    delivery_path = subject.PRIVATE_DIR / "delivery-job.result"
    pending_path = subject.PRIVATE_DIR / "pending-job.result"
    jobs = {
        "queued-job": {
            "jobId": "queued-job", "createdAt": old_timestamp(), "updatedAt": old_timestamp(),
            "status": "queued", "promptPath": str(queued_path), "resultPath": str(subject.PRIVATE_DIR / "queued-job.result"),
        },
        "running-job": {
            "jobId": "running-job", "createdAt": old_timestamp(), "updatedAt": old_timestamp(),
            "status": "running", "promptPath": "", "resultPath": str(running_path),
        },
        "young-job": {
            "jobId": "young-job", "createdAt": recent_timestamp(), "updatedAt": recent_timestamp(),
            "status": "done", "promptPath": "", "resultPath": str(young_path), "dedupeKey": "keep-young",
        },
        "delivery-job": {
            "jobId": "delivery-job", "createdAt": old_timestamp(), "updatedAt": old_timestamp(),
            "status": "failed", "promptPath": "", "resultPath": str(delivery_path),
            "deliveryRecoveryToken": "active-claim",
            "deliveryRecoveryStartedAt": recent_timestamp(),
            "deliveryRecoveryAttempts": 2,
        },
        "pending-job": {
            "jobId": "pending-job", "createdAt": old_timestamp(), "updatedAt": old_timestamp(),
            "status": "failed", "promptPath": "", "resultPath": str(pending_path),
            "delivered": False,
            "actual": {"executionVerified": True, "actualModel": "gpt-5.6-luna"},
            "origin": {"cardKey": "existing-card"},
            "deliveryRecoveryReferenceAt": recent_timestamp(),
        },
    }
    write_state(subject, jobs)
    for path in (queued_path, running_path, young_path, delivery_path, pending_path):
        path.write_text("keep", encoding="utf-8")
        path.chmod(0o644)
    subject.STATE_PATH.chmod(0o644)

    report = subject.cleanup(24 * 60 * 60)

    assert report["eligibleJobs"] == 0
    assert report["scrubbedJobs"] == 0
    assert report["preservedActiveJobs"] == 1
    assert report["preservedDeliveryPendingJobs"] == 1
    saved = json.loads(subject.STATE_PATH.read_text(encoding="utf-8"))["jobs"]
    assert saved == jobs
    for path in (queued_path, running_path, young_path, delivery_path, pending_path):
        assert path.read_text(encoding="utf-8") == "keep"
        assert mode(path) == 0o600
    assert mode(subject.STATE_PATH) == 0o600
    assert mode(subject.LOCK_PATH) == 0o600


def test_cleanup_dry_run_reports_without_mutating_state_artifacts_or_modes(tmp_path: Path) -> None:
    subject = load_subject(tmp_path / "private")
    job_id = "dry-run-job"
    result_path = subject.PRIVATE_DIR / f"{job_id}.result"
    jobs = {
        job_id: {
            "jobId": job_id,
            "createdAt": old_timestamp(),
            "updatedAt": old_timestamp(),
            "status": "failed",
            "promptPath": "",
            "resultPath": str(result_path),
            "dedupeKey": "keep-during-dry-run",
        }
    }
    write_state(subject, jobs)
    result_path.write_text("private result", encoding="utf-8")
    subject.STATE_PATH.chmod(0o644)
    result_path.chmod(0o644)
    subject.PRIVATE_DIR.chmod(0o755)
    assert not subject.LOCK_PATH.exists()
    before = subject.STATE_PATH.read_bytes()

    report = subject.cleanup(24 * 60 * 60, dry_run=True)

    assert report["dryRun"] is True
    assert report["wouldScrubJobs"] == 1
    assert report["scrubbedJobs"] == 0
    assert report["wouldRemoveArtifacts"] == 1
    assert report["removedArtifacts"] == 0
    assert subject.STATE_PATH.read_bytes() == before
    assert result_path.read_text(encoding="utf-8") == "private result"
    assert mode(subject.STATE_PATH) == 0o644
    assert mode(result_path) == 0o644
    assert mode(subject.PRIVATE_DIR) == 0o755
    assert not subject.LOCK_PATH.exists()
    assert report["wouldHardenDirectories"] == 1


def test_cleanup_never_unlinks_an_artifact_outside_private_dir(tmp_path: Path) -> None:
    subject = load_subject(tmp_path / "private")
    outside = tmp_path / "outside.result"
    outside.write_text("must stay", encoding="utf-8")
    jobs = {
        "unsafe-job": {
            "jobId": "unsafe-job",
            "createdAt": old_timestamp(),
            "updatedAt": old_timestamp(),
            "status": "failed",
            "promptPath": "",
            "resultPath": str(outside),
        }
    }
    write_state(subject, jobs)

    report = subject.cleanup(24 * 60 * 60)

    assert report["ok"] is False
    assert report["unsafeArtifactPaths"] == 1
    assert outside.read_text(encoding="utf-8") == "must stay"
    saved = json.loads(subject.STATE_PATH.read_text(encoding="utf-8"))["jobs"]["unsafe-job"]
    assert saved == jobs["unsafe-job"]


def test_include_queued_cancels_instead_of_leaving_an_unrunnable_queue_row(tmp_path: Path) -> None:
    subject = load_subject(tmp_path / "private")
    prompt_path = subject.PRIVATE_DIR / "queued-job.prompt"
    jobs = {
        "queued-job": {
            "jobId": "queued-job",
            "createdAt": old_timestamp(),
            "updatedAt": old_timestamp(),
            "status": "queued",
            "promptPath": str(prompt_path),
            "resultPath": str(subject.PRIVATE_DIR / "queued-job.result"),
        }
    }
    write_state(subject, jobs)
    prompt_path.write_text("stale private prompt", encoding="utf-8")

    report = subject.cleanup(24 * 60 * 60, include_queued=True)

    saved = json.loads(subject.STATE_PATH.read_text(encoding="utf-8"))["jobs"]["queued-job"]
    assert report["cancelledQueuedJobs"] == 1
    assert saved["previousStatus"] == "queued"
    assert saved["status"] == "cancelled"
    assert saved["auditTombstone"] is True
    assert not prompt_path.exists()


def test_delivery_pending_result_expires_after_finite_grace(tmp_path: Path) -> None:
    subject = load_subject(tmp_path / "private")
    result_path = subject.PRIVATE_DIR / "expired-delivery.result"
    jobs = {
        "expired-delivery": {
            "jobId": "expired-delivery",
            "createdAt": old_timestamp(4),
            "updatedAt": old_timestamp(3),
            "deliveryRecoveryReferenceAt": old_timestamp(3),
            "status": "failed",
            "resultPath": str(result_path),
            "delivered": False,
            "actual": {"executionVerified": True},
            "origin": {"cardKey": "existing-card"},
        }
    }
    write_state(subject, jobs)
    result_path.write_text("expired private result", encoding="utf-8")

    report = subject.cleanup(24 * 60 * 60)

    assert report["preservedDeliveryPendingJobs"] == 0
    assert report["scrubbedJobs"] == 1
    assert not result_path.exists()


def test_cleanup_fails_closed_on_corrupt_state_and_preserves_bytes(tmp_path: Path) -> None:
    subject = load_subject(tmp_path / "private")
    subject.PRIVATE_DIR.mkdir(parents=True)
    original = b'{"jobs": {broken json'
    subject.STATE_PATH.write_bytes(original)
    subject.STATE_PATH.chmod(0o644)

    report = subject.cleanup(24 * 60 * 60)

    assert report["ok"] is False
    assert "state-invalid-json" in report["errors"]
    assert subject.STATE_PATH.read_bytes() == original
    assert mode(subject.STATE_PATH) == 0o600


def test_cleanup_rejects_negative_retention_without_touching_state(tmp_path: Path) -> None:
    subject = load_subject(tmp_path / "private")
    jobs = {"young": {"jobId": "young", "updatedAt": recent_timestamp(), "status": "done"}}
    write_state(subject, jobs)
    before = subject.STATE_PATH.read_bytes()

    report = subject.cleanup(-1)

    assert report["ok"] is False
    assert report["errors"] == ["negative-max-age"]
    assert subject.STATE_PATH.read_bytes() == before
    assert not subject.LOCK_PATH.exists()


def test_cleanup_reports_unlink_failure_and_keeps_retryable_row(tmp_path: Path) -> None:
    subject = load_subject(tmp_path / "private")
    result_path = subject.PRIVATE_DIR / "unlink-failure.result"
    jobs = {
        "unlink-failure": {
            "jobId": "unlink-failure", "createdAt": old_timestamp(), "updatedAt": old_timestamp(),
            "status": "failed", "resultPath": str(result_path),
        }
    }
    write_state(subject, jobs)
    result_path.write_text("private result", encoding="utf-8")
    original_unlink = Path.unlink

    def fail_target_unlink(path, *args, **kwargs):
        if path == result_path:
            raise PermissionError("injected")
        return original_unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", fail_target_unlink):
        report = subject.cleanup(24 * 60 * 60)

    assert report["ok"] is False
    assert report["artifactRemovalFailures"] == 1
    assert report["scrubbedJobs"] == 0
    assert result_path.exists()
    saved = json.loads(subject.STATE_PATH.read_text(encoding="utf-8"))["jobs"]["unlink-failure"]
    assert saved == jobs["unlink-failure"]


def test_cleanup_reports_permission_failure_instead_of_false_hardening(tmp_path: Path) -> None:
    subject = load_subject(tmp_path / "private")
    prompt_path = subject.PRIVATE_DIR / "active.prompt"
    jobs = {
        "active": {
            "jobId": "active", "createdAt": old_timestamp(), "updatedAt": old_timestamp(),
            "status": "running", "promptPath": str(prompt_path),
        }
    }
    write_state(subject, jobs)
    prompt_path.write_text("active private prompt", encoding="utf-8")
    prompt_path.chmod(0o644)
    original_chmod = Path.chmod

    def fail_target_chmod(path, target_mode, *args, **kwargs):
        if path == prompt_path:
            raise PermissionError("injected")
        return original_chmod(path, target_mode, *args, **kwargs)

    with patch.object(Path, "chmod", fail_target_chmod):
        report = subject.cleanup(24 * 60 * 60)

    assert report["ok"] is False
    assert report["permissionFailures"] >= 1
    assert mode(prompt_path) == 0o644


def test_cleanup_removes_only_aged_unowned_job_artifacts(tmp_path: Path) -> None:
    subject = load_subject(tmp_path / "private")
    write_state(subject, {})
    old_orphan = subject.PRIVATE_DIR / "crashed-job.prompt"
    young_orphan = subject.PRIVATE_DIR / "recent-job.result"
    old_orphan.write_text("orphan private prompt", encoding="utf-8")
    young_orphan.write_text("recent private result", encoding="utf-8")
    old_epoch = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).timestamp()
    os.utime(old_orphan, (old_epoch, old_epoch))

    report = subject.cleanup(24 * 60 * 60)

    assert report["ok"] is True
    assert report["removedOrphanArtifacts"] == 1
    assert not old_orphan.exists()
    assert young_orphan.exists()
    assert mode(young_orphan) == 0o600


def test_cleanup_flags_unparseable_job_timestamp_without_scrubbing(tmp_path: Path) -> None:
    subject = load_subject(tmp_path / "private")
    result_path = subject.PRIVATE_DIR / "bad-time.result"
    jobs = {
        "bad-time": {
            "jobId": "bad-time", "updatedAt": "not-a-time", "status": "failed",
            "resultPath": str(result_path),
        }
    }
    write_state(subject, jobs)
    result_path.write_text("private result", encoding="utf-8")

    report = subject.cleanup(24 * 60 * 60)

    assert report["ok"] is False
    assert report["invalidTimestampJobs"] == 1
    assert result_path.exists()
    saved = json.loads(subject.STATE_PATH.read_text(encoding="utf-8"))["jobs"]["bad-time"]
    assert saved == jobs["bad-time"]
