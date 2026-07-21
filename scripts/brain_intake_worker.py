#!/usr/bin/env python3
"""Bounded private worker for durable Brain media intake jobs.

The OpenCLAW hook only persists and enqueues.  This process performs local
extraction and governance work later, commits one canonical Tier 3 terminal
outbox record, and leaves delivery exclusively to the Josh 2.0 gateway.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from brain_media_intake import (
    MAX_EXTRACTED_CHARS,
    MAX_SUBMISSION_BYTES,
    BrainAuthorizationError,
    BrainConfigurationError,
    BrainIntakeError,
    BrainSafetyError,
    BrainStore,
    brain_ingestion_enabled,
    clean_text,
    load_json,
    stable_id,
    utc_now,
)
from telegram_gateway_lifecycle import (
    GatewayLifecycle,
    LifecycleError,
    RolloutPolicy,
    StaleEventError,
    retry_delay_seconds,
)


WORKER_SCHEMA_VERSION = 1
DEFAULT_MAX_JOBS = 4
DEFAULT_LEASE_SECONDS = 1800
DEFAULT_TIME_BUDGET_SECONDS = 180
STAGES = (
    "stored", "extract", "classify", "deduplicate", "candidate",
    "review", "index", "finalize", "completed",
)
READY_STATES = ("queued", "retry_wait")
TERMINAL_JOB_STATES = ("completed", "dead_letter")


class BrainWorkerError(RuntimeError):
    pass


class LeaseLostError(BrainWorkerError):
    pass


class LifecycleBindingPending(BrainWorkerError):
    pass


class WorkerBudgetExceeded(BrainWorkerError):
    pass


def parse_utc(value: str) -> dt.datetime:
    text = str(value or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def utc_after(seconds: float) -> str:
    value = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=max(0.0, float(seconds)))
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def safe_error_class(exc: BaseException) -> str:
    if isinstance(exc, LeaseLostError):
        return "worker-lease-lost"
    if isinstance(exc, LifecycleBindingPending):
        return (
            "lifecycle-readiness-pending"
            if str(exc) == "lifecycle-readiness-pending"
            else "lifecycle-binding-pending"
        )
    if isinstance(exc, WorkerBudgetExceeded):
        return "worker-time-budget"
    if isinstance(exc, BrainSafetyError):
        return "brain-safety-rejected"
    if isinstance(exc, BrainAuthorizationError):
        return "brain-action-unauthorized"
    if isinstance(exc, BrainConfigurationError):
        return "brain-config-invalid"
    if isinstance(exc, BrainIntakeError):
        if str(exc) in {"worker-time-budget", "extraction-time-budget"}:
            return "worker-time-budget"
        return "brain-intake-error"
    if isinstance(exc, (LifecycleError, StaleEventError)):
        return "lifecycle-error"
    name = re.sub(r"[^a-z0-9]+", "-", exc.__class__.__name__.lower()).strip("-")
    return clean_text(name or "worker-error", 80)


class BrainIntakeWorker:
    def __init__(
        self,
        store_root: Path | str,
        *,
        lifecycle_root: Path | str,
        rollout_path: Path | str,
        config_path: Path | str | None = None,
        download_roots: Sequence[Path | str] | None = None,
        worker_id: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        retry_floor_seconds: float = 0.25,
    ) -> None:
        self.store = BrainStore(store_root, download_roots=download_roots)
        self.lifecycle_root = Path(lifecycle_root).expanduser().resolve()
        self.rollout_path = Path(rollout_path).expanduser().resolve()
        self.config_path = Path(config_path).expanduser().resolve() if config_path else None
        self.worker_id = clean_text(
            worker_id or stable_id("brain-worker", os.getpid(), uuid.uuid4().hex, length=24),
            80,
        )
        self.lease_seconds = max(30, min(int(lease_seconds), 3600))
        self.retry_floor_seconds = max(0.0, min(float(retry_floor_seconds), 30.0))

    @staticmethod
    def _check_deadline(deadline_monotonic: float | None) -> None:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise WorkerBudgetExceeded("worker-time-budget")

    def _policy(self) -> RolloutPolicy:
        return RolloutPolicy.load(self.rollout_path)

    def _gateway(self) -> GatewayLifecycle:
        # Reload every boundary so emergency fencing and rollback state are live.
        return GatewayLifecycle(
            self.lifecycle_root,
            rollout=self._policy(),
            owner="josh2",
        )

    def accepting_new(self) -> bool:
        if self.config_path is None:
            return False
        try:
            return brain_ingestion_enabled(load_json(self.config_path), self.rollout_path)
        except BrainIntakeError:
            return False

    def recover_expired_leases(self) -> int:
        now = utc_now()
        with self.store.connect() as db, self.store.transaction(db):
            recovered = db.execute(
                """UPDATE intake_jobs
                      SET state='queued',lease_owner='',lease_expires_at=NULL,
                          error_class='worker-lease-expired',available_at=?,updated_at=?
                    WHERE state='running' AND lease_expires_at IS NOT NULL
                      AND lease_expires_at<=? AND attempt_count<max_attempts""",
                (now, now, now),
            ).rowcount
            exhausted = db.execute(
                """UPDATE intake_jobs
                      SET state='exhausted',lease_owner='',lease_expires_at=NULL,
                          error_class='worker-lease-exhausted',available_at=?,updated_at=?
                    WHERE state='running' AND lease_expires_at IS NOT NULL
                      AND lease_expires_at<=? AND attempt_count>=max_attempts""",
                (now, now, now),
            ).rowcount
        return int(recovered) + int(exhausted)

    def _claim(self, byte_budget: int) -> dict[str, Any] | None:
        if byte_budget < 0:
            return None
        now = utc_now()
        lease_until = utc_after(self.lease_seconds)
        with self.store.connect() as db, self.store.transaction(db):
            meta = db.execute(
                "SELECT value FROM intake_worker_meta WHERE key='last-fairness-lane'",
            ).fetchone()
            last_lane = str(meta["value"]) if meta else ""
            row = db.execute(
                """SELECT j.*,
                          COALESCE(SUM(CASE WHEN a.digest IS NULL THEN 0 ELSE a.size_bytes END),0)
                            AS stored_bytes
                     FROM intake_jobs j
                     LEFT JOIN submission_artifacts sa ON sa.work_id=j.work_id
                     LEFT JOIN artifacts a ON a.digest=sa.digest
                    WHERE (
                          j.state='exhausted'
                          OR (j.state IN ('queued','retry_wait') AND j.available_at<=?
                              AND j.attempt_count<j.max_attempts)
                    )
                    GROUP BY j.work_id
                   HAVING stored_bytes<=?
                    ORDER BY CASE WHEN j.state='exhausted' THEN 0 ELSE 1 END,
                             CASE WHEN j.fairness_lane=? THEN 1 ELSE 0 END,
                             j.attempt_count ASC,j.available_at ASC,j.created_at ASC
                    LIMIT 1""",
                (now, max(0, int(byte_budget)), last_lane),
            ).fetchone()
            if not row:
                return None
            was_exhausted = str(row["state"]) == "exhausted"
            if was_exhausted:
                changed = db.execute(
                    """UPDATE intake_jobs SET state='running',lease_owner=?,lease_expires_at=?,updated_at=?
                         WHERE work_id=? AND state='exhausted' AND attempt_count>=max_attempts""",
                    (self.worker_id, lease_until, now, row["work_id"]),
                ).rowcount
            else:
                changed = db.execute(
                    """UPDATE intake_jobs
                          SET state='running',attempt_count=attempt_count+1,
                              lease_owner=?,lease_expires_at=?,error_class='',updated_at=?
                        WHERE work_id=? AND state IN ('queued','retry_wait') AND available_at<=?
                          AND attempt_count<max_attempts""",
                    (self.worker_id, lease_until, now, row["work_id"], now),
                ).rowcount
            if changed != 1:
                return None
            db.execute(
                """INSERT INTO intake_worker_meta(key,value,updated_at) VALUES('last-fairness-lane',?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                (row["fairness_lane"], now),
            )
            claimed = dict(row)
            claimed["state"] = "running"
            claimed["attempt_count"] = int(row["attempt_count"]) + (0 if was_exhausted else 1)
            claimed["exhausted"] = was_exhausted
            claimed["lease_owner"] = self.worker_id
            claimed["lease_expires_at"] = lease_until
            return claimed

    def _has_ready_over_budget(self, byte_budget: int) -> bool:
        now = utc_now()
        with self.store.connect() as db:
            row = db.execute(
                """SELECT 1 FROM (
                     SELECT j.work_id,
                            COALESCE(SUM(CASE WHEN a.digest IS NULL THEN 0 ELSE a.size_bytes END),0)
                              AS stored_bytes
                       FROM intake_jobs j
                       LEFT JOIN submission_artifacts sa ON sa.work_id=j.work_id
                       LEFT JOIN artifacts a ON a.digest=sa.digest
                      WHERE (
                            j.state='exhausted'
                            OR (j.state IN ('queued','retry_wait') AND j.available_at<=?
                                AND j.attempt_count<j.max_attempts)
                      )
                      GROUP BY j.work_id
                   ) WHERE stored_bytes>? LIMIT 1""",
                (now, max(0, int(byte_budget))),
            ).fetchone()
        return bool(row)

    def _set_stage(self, work_id: str, stage: str) -> None:
        if stage not in STAGES:
            raise BrainWorkerError("unknown-worker-stage")
        with self.store.connect() as db, self.store.transaction(db):
            row = db.execute(
                "SELECT stage FROM intake_jobs WHERE work_id=? AND state='running' AND lease_owner=?",
                (work_id, self.worker_id),
            ).fetchone()
            if not row:
                raise LeaseLostError("worker-lease-lost")
            if STAGES.index(stage) < STAGES.index(str(row["stage"])):
                return
            changed = db.execute(
                """UPDATE intake_jobs SET stage=?,lease_expires_at=?,updated_at=?
                     WHERE work_id=? AND state='running' AND lease_owner=?""",
                (stage, utc_after(self.lease_seconds), utc_now(), work_id, self.worker_id),
            ).rowcount
            if changed != 1:
                raise LeaseLostError("worker-lease-lost")

    def _cancelled(self, work_id: str) -> bool:
        with self.store.connect() as db:
            row = db.execute(
                """SELECT phase,cancel_requested,user_cancel_requested
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
        return (
            not row
            or bool(row["cancel_requested"])
            or bool(row["user_cancel_requested"])
            or row["phase"] == "forgotten"
        )

    def _cancel_kind(self, work_id: str) -> str:
        with self.store.connect() as db:
            row = db.execute(
                """SELECT phase,cancel_requested,user_cancel_requested
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
        if not row:
            return "forget"
        if row["phase"] == "forgotten" or row["cancel_requested"]:
            return "forget"
        if row["user_cancel_requested"]:
            return "user"
        return ""

    def _forget_complete(self, work_id: str) -> bool:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT phase FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()
        return bool(row and row["phase"] == "forgotten")

    def _binding(self, work_id: str) -> dict[str, Any]:
        binding = self.store.lifecycle_binding(work_id)
        if not binding:
            # Only the dual-gated predownload path may mint writer authority.
            # A worker must never legitimize an unbound row after the fact.
            raise LifecycleBindingPending("lifecycle-binding-pending")
        receipt = self._gateway().read_work(str(binding["lifecycle_work_id"]))
        if (
            not receipt
            or receipt.get("surfaceContract") != "brain-intake"
            or receipt.get("currentOwner") != "josh2"
            or int(receipt.get("deliveryTier") or 0) != 3
            or not bool(receipt.get("writerAuthorityAtStart"))
        ):
            raise BrainConfigurationError("brain-lifecycle-binding-invalid")
        if not bool(receipt.get("reactionDelivered")) or not bool(receipt.get("cardCreated")):
            # The gateway owns all Telegram-visible effects.  Extraction cannot
            # begin until its public lifecycle receipt proves both the immediate
            # acknowledgement and the durable live card were delivered.
            raise LifecycleBindingPending("lifecycle-readiness-pending")
        return binding

    def _advance_lifecycle(
        self,
        lifecycle_work_id: str,
        target: str,
        safe_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        path = ("received", "classified", "acknowledged", "working", "verifying")
        if target not in path:
            raise BrainWorkerError("invalid-lifecycle-target")
        for _ in range(10):
            gateway = self._gateway()
            receipt = gateway.read_work(lifecycle_work_id)
            if not receipt:
                raise LifecycleError("unknown-work")
            current = str(receipt["phase"])
            if current == "terminal" or path.index(current) >= path.index(target):
                return receipt
            next_phase = path[path.index(current) + 1]
            try:
                gateway.transition(
                    lifecycle_work_id,
                    next_phase,
                    expected_sequence=int(receipt["sequence"]),
                    fencing_epoch=int(receipt["fencingEpoch"]),
                    safe_payload=safe_payload,
                )
            except StaleEventError:
                continue
        raise LifecycleError("lifecycle-transition-contention")

    def _request_lifecycle_cancel(self, lifecycle_work_id: str) -> dict[str, Any]:
        for _ in range(8):
            gateway = self._gateway()
            receipt = gateway.read_work(lifecycle_work_id)
            if not receipt:
                raise LifecycleError("unknown-work")
            if receipt["phase"] == "terminal" or receipt.get("cancelRequested"):
                return receipt
            try:
                return gateway.request_cancel(
                    lifecycle_work_id,
                    expected_sequence=int(receipt["sequence"]),
                    fencing_epoch=int(receipt["fencingEpoch"]),
                )
            except StaleEventError:
                continue
        raise LifecycleError("lifecycle-cancel-contention")

    def _set_submission_phase(self, work_id: str, phase: str) -> None:
        with self.store.connect() as db, self.store.transaction(db):
            row = db.execute(
                "SELECT phase,cancel_requested,user_cancel_requested FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()
            if (
                not row
                or row["cancel_requested"]
                or row["user_cancel_requested"]
                or row["phase"] == "forgotten"
            ):
                raise BrainIntakeError("source-forget-in-progress")
            if row["phase"] != "quarantined":
                db.execute(
                    "UPDATE submissions SET phase=?,updated_at=? WHERE work_id=?",
                    (phase, utc_now(), work_id),
                )

    def _governance_summary(self, work_id: str) -> dict[str, Any]:
        with self.store.connect() as db:
            submission = db.execute(
                """SELECT phase,privacy_class,cancel_requested,user_cancel_requested
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
            artifacts = db.execute(
                """SELECT a.digest,a.media_class,a.ref_count,a.quarantine_reason
                     FROM submission_artifacts sa JOIN artifacts a ON a.digest=sa.digest
                    WHERE sa.work_id=? ORDER BY sa.attachment_id""",
                (work_id,),
            ).fetchall()
            extractions = db.execute(
                "SELECT status,coverage,prompt_injection,model_route FROM extractions WHERE work_id=?",
                (work_id,),
            ).fetchall()
            chunk_count = int(db.execute(
                "SELECT COUNT(*) FROM source_chunks WHERE work_id=?", (work_id,),
            ).fetchone()[0])
            vector_count = int(db.execute(
                """SELECT COUNT(*) FROM source_vectors WHERE chunk_id IN
                   (SELECT id FROM source_chunks WHERE work_id=?)""",
                (work_id,),
            ).fetchone()[0])
            candidates = db.execute(
                "SELECT status FROM candidates WHERE work_id=?", (work_id,),
            ).fetchall()
            attachment_failures = db.execute(
                """SELECT failure_reason FROM attachment_intents
                     WHERE work_id=? AND consumed_at IS NOT NULL AND failure_reason!=''""",
                (work_id,),
            ).fetchall()
        if not submission:
            raise BrainIntakeError("unknown-work")
        if (
            submission["cancel_requested"]
            or submission["user_cancel_requested"]
            or submission["phase"] == "forgotten"
        ):
            raise BrainIntakeError("source-forget-in-progress")
        local_counts: dict[str, int] = {}
        ref_counts: dict[str, int] = {}
        for row in artifacts:
            digest = str(row["digest"])
            local_counts[digest] = local_counts.get(digest, 0) + 1
            ref_counts[digest] = int(row["ref_count"])
        duplicates = sum(value - 1 for value in local_counts.values()) + sum(
            1 for digest, value in local_counts.items() if ref_counts[digest] > value
        )
        pending = sum(row["status"] in {"pending", "eligible"} for row in candidates)
        promoted = sum(row["status"] == "active" for row in candidates)
        indexed = sum(row["status"] == "indexed" for row in extractions)
        failure_reasons = sorted({str(row["failure_reason"]) for row in attachment_failures})
        unsupported = (
            sum(row["status"] == "unsupported" for row in extractions)
            + sum(reason in {"oversize", "download-unavailable", "corrupt"} for reason in failure_reasons)
        )
        injection = sum(bool(row["prompt_injection"]) for row in extractions)
        quarantined = any(row["quarantine_reason"] for row in artifacts)
        coverage = sorted({str(row["coverage"]) for row in extractions if row["coverage"]})
        media_classes = sorted({str(row["media_class"]) for row in artifacts})
        return {
            "privacyClass": str(submission["privacy_class"]),
            "mediaClass": media_classes[0] if len(media_classes) == 1 else "mixed" if media_classes else "unknown",
            "candidateCount": len(candidates),
            "reviewCount": pending,
            "promotionCount": promoted,
            "duplicateStatus": "duplicate" if duplicates else "unique",
            "sourceCoverage": coverage[0] if len(coverage) == 1 else "mixed" if coverage else "none",
            "indexedCount": indexed,
            "unsupportedCount": unsupported,
            "attachmentFailureCount": len(attachment_failures),
            "attachmentFailureReasons": failure_reasons or ["n/a"],
            "promptInjectionSignals": injection,
            "quarantined": quarantined,
            "extractionRoutes": sorted({
                str(row["model_route"]) for row in extractions if row["model_route"]
            }) or ["local-none"],
            "chunkIndexStatus": "available" if chunk_count and chunk_count == vector_count else "unavailable",
        }

    def _terminal_payload(
        self,
        work_id: str,
        binding: Mapping[str, Any],
        outcome: str,
        receipt: Mapping[str, Any],
        *,
        error_class: str = "",
    ) -> dict[str, Any]:
        return {
            "handoffSchemaVersion": 1,
            "surfaceContract": "brain-intake",
            "deliveryTier": 3,
            "owner": "josh2",
            "brainWorkRef": stable_id("brain-result-ref", work_id, length=32),
            "sourceRevision": int(binding["source_revision_at_start"]),
            "terminalStatus": outcome,
            "errorClass": clean_text(error_class, 80) or "n/a",
            "receipt": dict(receipt),
        }

    def _terminalize(
        self,
        work_id: str,
        binding: Mapping[str, Any],
        outcome: str,
        receipt: Mapping[str, Any],
        *,
        expected_attempt: int,
        error_class: str = "",
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        self._check_deadline(deadline_monotonic)
        lifecycle_work_id = str(binding["lifecycle_work_id"])
        owner_fence = stable_id("brain-lease-owner", self.worker_id, length=32)
        initial_payload = self._terminal_payload(
            work_id, binding, outcome, receipt, error_class=error_class,
        )
        initial_hash = payload_hash(initial_payload)
        # Persist the immutable handoff before crossing into the lifecycle DB.
        # A crash after the outbox commit can therefore replay byte-for-byte.
        with self.store.connect() as db, self.store.transaction(db):
            job = db.execute(
                "SELECT * FROM intake_jobs WHERE work_id=?", (work_id,),
            ).fetchone()
            if (
                not job
                or job["state"] != "running"
                or job["lease_owner"] != self.worker_id
                or int(job["attempt_count"]) != int(expected_attempt)
                or not job["lease_expires_at"]
                or parse_utc(str(job["lease_expires_at"])) <= dt.datetime.now(dt.timezone.utc)
            ):
                raise LeaseLostError("worker-lease-lost")
            prepared = db.execute(
                "SELECT * FROM intake_terminal_prepares WHERE work_id=?", (work_id,),
            ).fetchone()
            if prepared:
                encoded = str(prepared["private_payload_json"])
                try:
                    prepared_payload = json.loads(encoded)
                except json.JSONDecodeError as exc:
                    raise BrainWorkerError("terminal-prepare-corrupt") from exc
                if payload_hash(prepared_payload) != str(prepared["payload_hash"]):
                    raise BrainWorkerError("terminal-prepare-corrupt")
                lifecycle_receipt = self._gateway().read_work(lifecycle_work_id)
                if not lifecycle_receipt:
                    raise LifecycleError("unknown-work")
                if (
                    lifecycle_receipt["phase"] != "terminal"
                    and (
                        int(prepared["attempt_fence"]) != int(expected_attempt)
                        or str(prepared["lease_owner_hash"]) != owner_fence
                    )
                ):
                    db.execute(
                        """UPDATE intake_terminal_prepares SET outcome=?,payload_hash=?,
                                  private_payload_json=?,attempt_fence=?,lease_owner_hash=?
                            WHERE work_id=?""",
                        (
                            outcome, initial_hash,
                            json.dumps(initial_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
                            int(expected_attempt), owner_fence, work_id,
                        ),
                    )
            else:
                db.execute(
                    "INSERT INTO intake_terminal_prepares VALUES(?,?,?,?,?,?,?)",
                    (
                        work_id, outcome, initial_hash,
                        json.dumps(initial_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
                        int(expected_attempt), owner_fence,
                        utc_now(),
                    ),
                )
        self._check_deadline(deadline_monotonic)
        # BEGIN IMMEDIATE is the final cancellation fence.  Forget either sets
        # cancel_requested first (and wins), or waits until this terminal intent
        # is fully committed (and is ordered after it).  A changed intent is
        # committed in its own pass before any lifecycle write, preserving crash
        # replay identity.
        for _intent_pass in range(4):
            self._check_deadline(deadline_monotonic)
            with self.store.connect() as db, self.store.transaction(db):
                job = db.execute(
                    "SELECT * FROM intake_jobs WHERE work_id=?", (work_id,),
                ).fetchone()
                if (
                    not job
                    or job["state"] != "running"
                    or job["lease_owner"] != self.worker_id
                    or int(job["attempt_count"]) != int(expected_attempt)
                    or not job["lease_expires_at"]
                    or parse_utc(str(job["lease_expires_at"])) <= dt.datetime.now(dt.timezone.utc)
                ):
                    raise LeaseLostError("worker-lease-lost")
                submission = db.execute(
                    """SELECT phase,cancel_requested,user_cancel_requested
                         FROM submissions WHERE work_id=?""",
                    (work_id,),
                ).fetchone()
                if not submission:
                    raise BrainIntakeError("unknown-work")
                prepared = db.execute(
                    "SELECT * FROM intake_terminal_prepares WHERE work_id=?", (work_id,),
                ).fetchone()
                if not prepared:
                    raise BrainWorkerError("terminal-prepare-missing")
                try:
                    private_payload = json.loads(str(prepared["private_payload_json"]))
                except json.JSONDecodeError as exc:
                    raise BrainWorkerError("terminal-prepare-corrupt") from exc
                expected_hash = str(prepared["payload_hash"])
                if payload_hash(private_payload) != expected_hash:
                    raise BrainWorkerError("terminal-prepare-corrupt")
                outcome = str(prepared["outcome"])
                gateway = self._gateway()
                lifecycle_receipt = gateway.read_work(lifecycle_work_id)
                if not lifecycle_receipt:
                    raise LifecycleError("unknown-work")
                forget_won = bool(submission["cancel_requested"]) or submission["phase"] == "forgotten"
                user_cancel_won = bool(submission["user_cancel_requested"])
                cancellation_won = forget_won or user_cancel_won
                if (
                    forget_won
                    and submission["phase"] != "forgotten"
                    and lifecycle_receipt["phase"] != "terminal"
                ):
                    raise BrainIntakeError("forget-cleanup-pending")
                if (
                    cancellation_won
                    and lifecycle_receipt["phase"] != "terminal"
                    and outcome != "cancelled"
                ):
                    private_payload = self._terminal_payload(
                        work_id,
                        binding,
                        "cancelled",
                        self._cancel_receipt(work_id, forgotten=forget_won),
                    )
                    expected_hash = payload_hash(private_payload)
                    db.execute(
                        """UPDATE intake_terminal_prepares
                              SET outcome='cancelled',payload_hash=?,private_payload_json=?,
                                  attempt_fence=?,lease_owner_hash=?
                            WHERE work_id=?""",
                        (
                            expected_hash,
                            json.dumps(private_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
                            int(expected_attempt), owner_fence,
                            work_id,
                        ),
                    )
                    continue
                if outcome == "cancelled" and lifecycle_receipt["phase"] != "terminal":
                    self._check_deadline(deadline_monotonic)
                    self._request_lifecycle_cancel(lifecycle_work_id)
                self._check_deadline(deadline_monotonic)
                for _ in range(8):
                    gateway = self._gateway()
                    lifecycle_receipt = gateway.read_work(lifecycle_work_id)
                    if not lifecycle_receipt:
                        raise LifecycleError("unknown-work")
                    try:
                        terminal = gateway.commit_terminal(
                            lifecycle_work_id,
                            outcome,
                            expected_sequence=int(lifecycle_receipt["sequence"]),
                            fencing_epoch=int(lifecycle_receipt["fencingEpoch"]),
                            private_payload=private_payload,
                        )
                        break
                    except StaleEventError:
                        continue
                else:
                    raise LifecycleError("terminal-commit-contention")
                result_id = stable_id("brain-worker-result", work_id, length=32)
                existing = db.execute(
                    "SELECT * FROM intake_results WHERE work_id=?", (work_id,),
                ).fetchone()
                if existing:
                    if (
                        str(existing["payload_hash"]) != expected_hash
                        or str(existing["outcome"]) != outcome
                        or str(existing["terminal_event_id"]) != str(terminal["eventId"])
                    ):
                        raise BrainWorkerError("terminal-result-conflict")
                else:
                    db.execute(
                        "INSERT INTO intake_results VALUES(?,?,?,?,?,?,?,?)",
                        (
                            result_id, work_id, lifecycle_work_id, terminal["eventId"], outcome,
                            expected_hash,
                            json.dumps(private_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
                            utc_now(),
                        ),
                    )
                terminal_error = clean_text(private_payload.get("errorClass"), 80)
                changed = db.execute(
                    """UPDATE intake_jobs SET state='completed',stage='completed',lease_owner='',
                              lease_expires_at=NULL,error_class=?,completed_at=COALESCE(completed_at,?),updated_at=?
                         WHERE work_id=? AND (
                           (state='running' AND lease_owner=?) OR state='completed'
                         )""",
                    (
                        "" if terminal_error == "n/a" else terminal_error,
                        utc_now(), utc_now(), work_id, self.worker_id,
                    ),
                ).rowcount
                if changed != 1:
                    raise LeaseLostError("worker-lease-lost")
                return {"outcome": outcome, "duplicate": bool(terminal.get("duplicate"))}
        raise BrainWorkerError("terminal-intent-contention")

    def _cancel_receipt(self, work_id: str, *, forgotten: bool) -> dict[str, Any]:
        if not forgotten:
            receipt = self.store.final_receipt(work_id)
            receipt["Pending review"] = {"count": 0, "reasons": ["n/a"]}
            receipt["Approval needed"] = "n/a"
            receipt["Retention"] = "privately retained"
            return receipt
        return {
            "Stored": "No",
            "Extracted": {"types": ["n/a"], "coverage": ["none"], "routes": ["local-none"]},
            "Learned": {"count": 0, "types": ["n/a"]},
            "Source indexed": "No",
            "Pending review": {"count": 0, "reasons": ["n/a"]},
            "Duplicates": "n/a",
            "Unsupported": ["n/a"],
            "Privacy": "private",
            "Retention": "not retained",
            "How to correct": "n/a",
            "How to forget": "Already forgotten.",
            "Approval needed": "n/a",
        }

    def _finish_cancel_if_requested(
        self,
        work_id: str,
        binding: Mapping[str, Any],
        *,
        attempt: int,
        extracted_chars: int,
        deadline_monotonic: float | None = None,
    ) -> tuple[str, int] | None:
        cancel_kind = self._cancel_kind(work_id)
        if not cancel_kind:
            return None
        forgotten = cancel_kind == "forget"
        if forgotten and not self._forget_complete(work_id):
            raise BrainIntakeError("forget-cleanup-pending")
        self._check_deadline(deadline_monotonic)
        self._set_stage(work_id, "finalize")
        terminal = self._terminalize(
            work_id,
            binding,
            "cancelled",
            self._cancel_receipt(work_id, forgotten=forgotten),
            expected_attempt=attempt,
            deadline_monotonic=deadline_monotonic,
        )
        return str(terminal["outcome"]), extracted_chars

    def _process(
        self,
        job: Mapping[str, Any],
        *,
        max_extracted_chars: int,
        deadline_monotonic: float,
    ) -> tuple[str, int]:
        work_id = str(job["work_id"])
        attempt = int(job["attempt_count"])
        extracted_chars = 0
        self._check_deadline(deadline_monotonic)
        binding = self._binding(work_id)
        self._check_deadline(deadline_monotonic)
        lifecycle_work_id = str(binding["lifecycle_work_id"])
        cancelled = self._finish_cancel_if_requested(
            work_id, binding, attempt=attempt, extracted_chars=extracted_chars,
            deadline_monotonic=deadline_monotonic,
        )
        if cancelled:
            return cancelled
        with self.store.connect() as db:
            submission = db.execute(
                "SELECT phase FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()
        if not submission:
            raise BrainIntakeError("unknown-work")
        self._check_deadline(deadline_monotonic)
        if submission["phase"] == "quarantined":
            self._set_stage(work_id, "finalize")
            terminal = self._terminalize(
                work_id, binding, "failed", self.store.final_receipt(work_id),
                expected_attempt=attempt,
                error_class="brain-safety-rejected",
                deadline_monotonic=deadline_monotonic,
            )
            return str(terminal["outcome"]), extracted_chars
        self._advance_lifecycle(
            lifecycle_work_id,
            "working",
            {"status": "processing", "privacyClass": "private", "mediaClass": str(job["fairness_lane"])},
        )
        self._check_deadline(deadline_monotonic)
        stage_index = STAGES.index(str(job["stage"]))
        if stage_index <= STAGES.index("extract"):
            self._set_stage(work_id, "extract")
            extraction = self.store.extract_submission(
                work_id,
                max_extracted_chars=max_extracted_chars,
                deadline_monotonic=deadline_monotonic,
            )
            extracted_chars = int(extraction.get("extractedChars") or 0)
            self._check_deadline(deadline_monotonic)
        cancelled = self._finish_cancel_if_requested(
            work_id, binding, attempt=attempt, extracted_chars=extracted_chars,
            deadline_monotonic=deadline_monotonic,
        )
        if cancelled:
            return cancelled
        self._check_deadline(deadline_monotonic)
        self._set_stage(work_id, "classify")
        self._set_submission_phase(work_id, "classifying")
        summary = self._governance_summary(work_id)
        self._check_deadline(deadline_monotonic)
        self._set_stage(work_id, "deduplicate")
        self._set_submission_phase(work_id, "deduplicating")
        self._check_deadline(deadline_monotonic)
        self._set_stage(work_id, "candidate")
        self.store.synthesize_candidates(
            work_id, deadline_monotonic=deadline_monotonic,
        )
        self._check_deadline(deadline_monotonic)
        summary = self._governance_summary(work_id)
        if int(summary["candidateCount"]):
            self._set_submission_phase(work_id, "candidate_pending")
        cancelled = self._finish_cancel_if_requested(
            work_id, binding, attempt=attempt, extracted_chars=extracted_chars,
            deadline_monotonic=deadline_monotonic,
        )
        if cancelled:
            return cancelled
        self._check_deadline(deadline_monotonic)
        self._set_stage(work_id, "review")
        if int(summary["reviewCount"]):
            self._set_submission_phase(work_id, "reviewing")
        self.store.review_candidates(
            work_id, deadline_monotonic=deadline_monotonic,
        )
        self._check_deadline(deadline_monotonic)
        summary = self._governance_summary(work_id)
        self._set_stage(work_id, "index")
        if int(summary["reviewCount"]) == 0:
            final_phase = "unsupported" if int(summary["indexedCount"]) == 0 else "indexed"
            self._set_submission_phase(work_id, final_phase)
        self._check_deadline(deadline_monotonic)
        cancelled = self._finish_cancel_if_requested(
            work_id, binding, attempt=attempt, extracted_chars=extracted_chars,
            deadline_monotonic=deadline_monotonic,
        )
        if cancelled:
            return cancelled
        summary = self._governance_summary(work_id)
        self._check_deadline(deadline_monotonic)
        self._advance_lifecycle(lifecycle_work_id, "verifying", summary)
        self._check_deadline(deadline_monotonic)
        attachment_only_failure = (
            int(summary["attachmentFailureCount"]) > 0
            and int(summary["indexedCount"]) == 0
        )
        outcome = (
            "failed"
            if attachment_only_failure
            else "partial"
            if int(summary["unsupportedCount"]) or int(summary["reviewCount"])
            else "succeeded"
        )
        self._set_stage(work_id, "finalize")
        self._check_deadline(deadline_monotonic)
        terminal = self._terminalize(
            work_id, binding, outcome, self.store.final_receipt(work_id),
            expected_attempt=attempt,
            error_class="brain-attachment-unsupported" if attachment_only_failure else "",
            deadline_monotonic=deadline_monotonic,
        )
        return str(terminal["outcome"]), extracted_chars

    def _retry(self, job: Mapping[str, Any], error_class: str) -> bool:
        attempt = int(job["attempt_count"])
        maximum = int(job["max_attempts"])
        if attempt >= maximum:
            return False
        delay = max(
            self.retry_floor_seconds,
            retry_delay_seconds(attempt, seed=str(job["work_id"])),
        )
        with self.store.connect() as db, self.store.transaction(db):
            changed = db.execute(
                """UPDATE intake_jobs SET state='retry_wait',available_at=?,lease_owner='',
                          lease_expires_at=NULL,error_class=?,updated_at=?
                     WHERE work_id=? AND state='running' AND lease_owner=?""",
                (
                    utc_after(delay), clean_text(error_class, 80), utc_now(),
                    job["work_id"], self.worker_id,
                ),
            ).rowcount
        if changed != 1:
            raise LeaseLostError("worker-lease-lost")
        return True

    def _fail_terminal(self, job: Mapping[str, Any], error_class: str) -> None:
        work_id = str(job["work_id"])
        try:
            binding = self._binding(work_id)
            if self._cancel_kind(work_id) == "forget" and not self._forget_complete(work_id):
                raise BrainIntakeError("forget-cleanup-pending")
            cancel_kind = self._cancel_kind(work_id)
            receipt = (
                self._cancel_receipt(work_id, forgotten=cancel_kind == "forget")
                if cancel_kind
                else self.store.final_receipt(work_id)
            )
            outcome = "cancelled" if cancel_kind else "failed"
            self._terminalize(
                work_id, binding, outcome, receipt,
                expected_attempt=int(job["attempt_count"]),
                error_class="" if outcome == "cancelled" else error_class,
            )
        except Exception:
            with self.store.connect() as db, self.store.transaction(db):
                db.execute(
                    """UPDATE intake_jobs SET state='dead_letter',lease_owner='',lease_expires_at=NULL,
                              error_class=?,completed_at=?,updated_at=?
                         WHERE work_id=? AND state='running' AND lease_owner=?""",
                    (clean_text(error_class, 80), utc_now(), utc_now(), work_id, self.worker_id),
                )

    def run_once(
        self,
        *,
        max_jobs: int = DEFAULT_MAX_JOBS,
        max_bytes: int = MAX_SUBMISSION_BYTES,
        max_seconds: int = DEFAULT_TIME_BUDGET_SECONDS,
        max_extracted_chars: int = MAX_EXTRACTED_CHARS,
    ) -> dict[str, Any]:
        bounded_jobs = max(1, min(int(max_jobs), 32))
        bounded_bytes = max(0, min(int(max_bytes), MAX_SUBMISSION_BYTES))
        bounded_seconds = max(1, min(int(max_seconds), 3600))
        bounded_chars = max(1, min(int(max_extracted_chars), MAX_EXTRACTED_CHARS))
        recovered = self.recover_expired_leases()
        started = time.monotonic()
        deadline = started + bounded_seconds
        used_bytes = 0
        used_chars = 0
        counts = {
            "claimed": 0, "completed": 0, "succeeded": 0, "partial": 0,
            "failed": 0, "cancelled": 0, "retried": 0, "deadLetter": 0,
        }
        for _ in range(bounded_jobs):
            if time.monotonic() >= deadline or used_chars >= bounded_chars:
                break
            job = self._claim(bounded_bytes - used_bytes)
            if not job:
                break
            counts["claimed"] += 1
            used_bytes += int(job["stored_bytes"])
            if bool(job.get("exhausted")):
                self._fail_terminal(job, "worker-lease-exhausted")
                with self.store.connect() as db:
                    exhausted_state = db.execute(
                        "SELECT state FROM intake_jobs WHERE work_id=?", (job["work_id"],),
                    ).fetchone()
                if exhausted_state and exhausted_state["state"] == "completed":
                    counts["completed"] += 1
                    counts["failed"] += 1
                else:
                    counts["deadLetter"] += 1
                continue
            try:
                outcome, job_chars = self._process(
                    job,
                    max_extracted_chars=bounded_chars - used_chars,
                    deadline_monotonic=deadline,
                )
                used_chars += max(0, min(int(job_chars), bounded_chars - used_chars))
            except Exception as exc:
                error_class = safe_error_class(exc)
                if self._retry(job, error_class):
                    counts["retried"] += 1
                else:
                    self._fail_terminal(job, error_class)
                    with self.store.connect() as db:
                        state = db.execute(
                            "SELECT state FROM intake_jobs WHERE work_id=?", (job["work_id"],),
                        ).fetchone()
                    if state and state["state"] == "dead_letter":
                        counts["deadLetter"] += 1
                    else:
                        counts["completed"] += 1
                        counts["failed"] += 1
                # A failed extraction may already have persisted bounded
                # per-artifact progress. Stop this run so later jobs cannot
                # consume a second unaccounted character/time budget.
                break
            counts["completed"] += 1
            counts[outcome] += 1
        resource_deferred = int(self._has_ready_over_budget(bounded_bytes - used_bytes))
        return {
            "ok": counts["deadLetter"] == 0,
            "workerSchemaVersion": WORKER_SCHEMA_VERSION,
            "acceptingNew": self.accepting_new(),
            "recoveredLeases": recovered,
            "counts": counts,
            "resourceDeferred": resource_deferred,
            "limits": {
                "maxJobs": bounded_jobs,
                "maxBytes": bounded_bytes,
                "maxSeconds": bounded_seconds,
                "maxExtractedChars": bounded_chars,
            },
            "used": {"bytes": used_bytes, "extractedChars": used_chars},
            "privacy": {"rawContentIncluded": False, "identifiersIncluded": False, "countsOnly": True},
        }

    def status(self) -> dict[str, Any]:
        with self.store.connect() as db:
            states = {
                str(row["state"]): int(row["count"])
                for row in db.execute("SELECT state,COUNT(*) AS count FROM intake_jobs GROUP BY state")
            }
            stages = {
                str(row["stage"]): int(row["count"])
                for row in db.execute(
                    "SELECT stage,COUNT(*) AS count FROM intake_jobs WHERE state NOT IN ('completed','dead_letter') GROUP BY stage"
                )
            }
            results = int(db.execute("SELECT COUNT(*) FROM intake_results").fetchone()[0])
        return {
            "ok": int(states.get("dead_letter", 0)) == 0,
            "workerSchemaVersion": WORKER_SCHEMA_VERSION,
            "acceptingNew": self.accepting_new(),
            "states": states,
            "activeStages": stages,
            "terminalResults": results,
            "privacy": {"rawContentIncluded": False, "identifiersIncluded": False, "countsOnly": True},
        }


def parser() -> argparse.ArgumentParser:
    repo = Path(__file__).resolve().parents[1]
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--root",
        default=os.environ.get("BRAIN_INTAKE_ROOT", str(Path.home() / ".openclaw/private/brain-intake")),
    )
    result.add_argument(
        "--lifecycle-root",
        default=os.environ.get("TELEGRAM_LIFECYCLE_ROOT", str(Path.home() / ".openclaw/private/telegram-lifecycle")),
    )
    result.add_argument(
        "--rollout",
        default=os.environ.get("TELEGRAM_LIFECYCLE_ROLLOUT", str(repo / "config/telegram-lifecycle-rollout.json")),
    )
    result.add_argument(
        "--config",
        default=os.environ.get("TELEGRAM_INTAKE_LANES", str(repo / "config/telegram-intake-lanes.json")),
    )
    sub = result.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run-once")
    run.add_argument("--max-jobs", type=int, default=DEFAULT_MAX_JOBS)
    run.add_argument("--max-bytes", type=int, default=MAX_SUBMISSION_BYTES)
    run.add_argument("--max-seconds", type=int, default=DEFAULT_TIME_BUDGET_SECONDS)
    run.add_argument("--max-extracted-chars", type=int, default=MAX_EXTRACTED_CHARS)
    sub.add_parser("status")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        worker = BrainIntakeWorker(
            args.root,
            lifecycle_root=args.lifecycle_root,
            rollout_path=args.rollout,
            config_path=args.config,
        )
        if args.command == "status":
            result = worker.status()
        else:
            result = worker.run_once(
                max_jobs=args.max_jobs,
                max_bytes=args.max_bytes,
                max_seconds=args.max_seconds,
                max_extracted_chars=args.max_extracted_chars,
            )
    except Exception as exc:
        result = {
            "ok": False,
            "workerSchemaVersion": WORKER_SCHEMA_VERSION,
            "errorClass": safe_error_class(exc),
            "privacy": {"rawContentIncluded": False, "identifiersIncluded": False, "countsOnly": True},
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
