#!/usr/bin/env python3
"""Private, reply-bound Brain action adapter for the Josh Telegram gateway.

The OpenCLAW hook invokes this adapter before reply-chain storage or generic
agent dispatch.  Only an owner-authenticated reply to a private source, final,
or Forget-preview mapping can enter the action journal.  Telegram action
responses are lifecycle-scoped effects and unknown delivery results are never
retried.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import hmac
import html
import json
import math
import os
import re
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from brain_gateway_dispatcher import FINAL_RECEIPT_FIELDS, default_transport, private_message_ref
from brain_media_intake import (
    BrainConfigurationError,
    BrainStore,
    clean_text,
    load_json,
    load_private_receipt,
    private_brain_topic_receipt,
    resolved_authorized_sender,
    resolved_brain_topic,
    stable_id,
)
from telegram_gateway_lifecycle import (
    GatewayLifecycle,
    LifecycleError,
    RolloutPolicy,
    StaleEventError,
    UnauthorizedActionError,
)


ACTION_TTL_SECONDS = 600
OUTBOX_MAX_ATTEMPTS = 12
TELEGRAM_TIMEOUT_SECONDS = 8
HUMAN_CANARY_SCHEMA_VERSION = 2
HUMAN_CANARY_BASE_CLASSES = (
    "source_media", "ingestion_card", "ingestion_final",
)
HUMAN_CANARY_PRIVACY_CLASSES = (
    "privacy_command", "privacy_preview", "privacy_confirm", "privacy_final",
)
HUMAN_CANARY_FORGET_CLASSES = (
    "forget_command", "forget_preview", "forget_confirm", "forget_final",
)
HUMAN_CANARY_DELETE_ORDER = (
    "forget_final", "forget_confirm", "forget_preview", "forget_command",
    "privacy_final", "privacy_confirm", "privacy_preview", "privacy_command",
    "ingestion_final", "ingestion_card", "source_media",
)
HUMAN_CANARY_INBOUND_CLASS = {
    "privacy": "privacy_command",
    "privacy-confirm": "privacy_confirm",
    "forget-preview": "forget_command",
    "forget-confirm": "forget_confirm",
}
HUMAN_CANARY_RESPONSE_CLASS = {
    "privacy-preview": "privacy_preview",
    "privacy-final": "privacy_final",
    "forget-preview": "forget_preview",
    "forget-final": "forget_final",
}
#JAIMES: Human-canary Telegram refs stay in one owner-private, work-bound journal
# until Forget, four-agent retrieval cleanup, and newest-first deletion are proven.
INBOUND_STATES = frozenset({
    "reserved", "executing", "executed", "responding", "delivered",
    "response_pending", "deferred", "ignored", "indeterminate", "dead_letter",
})
PENDING_STATES = frozenset({"prepared", "pending", "consuming", "consumed", "indeterminate", "dead_letter"})
SAFE_ACTIONS = frozenset({
    "correct", "reference-only", "approve-memory", "reject-memory",
    "supersede-memory", "forget-preview", "forget-confirm", "cancel", "privacy",
    "privacy-confirm",
})
ACTION_STAGE_SPECS: dict[str, dict[str, str]] = {
    "accepted": {
        "status": "active",
        "detail": "A reply-bound Brain governance action was accepted.",
    },
    "completed": {
        "status": "done",
        "detail": "A reply-bound Brain governance action completed.",
    },
    "forget_completed": {
        "status": "done",
        "detail": "A reply-bound Brain Forget action completed with cleanup verification.",
    },
    "indeterminate": {
        "status": "error",
        "detail": "A Brain action or acknowledgement is indeterminate and fenced from retry.",
    },
    "dead_letter": {
        "status": "error",
        "detail": "A Brain governance action requires operator review.",
    },
}


class BrainActionError(RuntimeError):
    """Bounded, non-content-bearing action failure."""


class BrainHumanCanaryJournal:
    """One private, reply-bound Telegram deletion journal for one Brain work item."""

    def __init__(self, root: Path | str, work_id: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.work_id = str(work_id or "")
        if not self.work_id:
            raise BrainActionError("human-canary-work-missing")
        self.work_hash = hashlib.sha256(self.work_id.encode()).hexdigest()
        self.directory = self.root / self.work_hash
        self.db_path = self.directory / "journal.sqlite3"
        self.receipt_path = self.directory / "telegram-cleanup-receipt.json"
        self.receipt_temp_path = self.directory / ".telegram-cleanup-receipt.json.tmp"
        self.cleanup_lock_path = self.directory / ".telegram-cleanup.lock"

    @property
    def exists(self) -> bool:
        return self.db_path.is_file()

    @property
    def journal_paths(self) -> tuple[Path, Path, Path]:
        return (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        )

    def _fault(self, _point: str) -> None:
        """Fault-injection seam used by crash-recovery regressions."""

    @contextlib.contextmanager
    def _cleanup_lock(self) -> Iterator[None]:
        self._private_directory(self.root, create=True)
        if not self.directory.exists():
            self.directory.mkdir(mode=0o700)
        self._private_directory(self.directory)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.cleanup_lock_path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise BrainActionError("human-canary-cleanup-lock-invalid")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _load_receipt(self) -> dict[str, Any]:
        try:
            info = self.receipt_path.lstat()
        except OSError as exc:
            raise BrainActionError("human-canary-receipt-missing") from exc
        if (
            self.receipt_path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise BrainActionError("human-canary-receipt-invalid")
        try:
            receipt = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrainActionError("human-canary-receipt-invalid") from exc
        try:
            target_count = int(receipt.get("targetCount", -1)) if isinstance(receipt, dict) else -1
            unresolved = int(receipt.get("unresolved", -1)) if isinstance(receipt, dict) else -1
            schema_version = (
                int(receipt.get("journalSchemaVersion", -1))
                if isinstance(receipt, dict) else -1
            )
            receipt_version = (
                int(receipt.get("receiptVersion", -1))
                if isinstance(receipt, dict) else -1
            )
        except (TypeError, ValueError):
            raise BrainActionError("human-canary-receipt-invalid")
        privacy_path = bool(receipt.get("privacyPath")) if isinstance(receipt, dict) else False
        expected_classes = (
            *HUMAN_CANARY_BASE_CLASSES,
            *(HUMAN_CANARY_PRIVACY_CLASSES if privacy_path else ()),
            *HUMAN_CANARY_FORGET_CLASSES,
        )
        expected_class_counts = {target_class: 1 for target_class in expected_classes}
        if (
            not isinstance(receipt, dict)
            or receipt.get("cleanupConfirmed") is not True
            or receipt.get("postForgetVerified") is not True
            or not hmac.compare_digest(str(receipt.get("workIdHash") or ""), self.work_hash)
            or unresolved != 0
            or target_count != len(expected_classes)
            or schema_version != HUMAN_CANARY_SCHEMA_VERSION
            or receipt_version != 1
            or receipt.get("classCounts") != expected_class_counts
        ):
            raise BrainActionError("human-canary-receipt-invalid")
        return receipt

    @staticmethod
    def _private_directory(path: Path, *, create: bool = False) -> Path:
        if create:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)
        try:
            info = path.lstat()
        except OSError as exc:
            raise BrainActionError("human-canary-journal-directory-missing") from exc
        if (
            path.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise BrainActionError("human-canary-journal-directory-invalid")
        return path.resolve()

    @contextlib.contextmanager
    def connect(self, *, create: bool = False) -> Iterator[sqlite3.Connection]:
        if create:
            self._private_directory(self.root, create=True)
            if not self.directory.exists():
                self.directory.mkdir(mode=0o700)
            self._private_directory(self.directory)
            if not self.db_path.exists():
                flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(self.db_path, flags, 0o600)
                os.close(descriptor)
        if not self.db_path.is_file() or self.db_path.is_symlink():
            raise BrainActionError("human-canary-journal-missing")
        info = self.db_path.lstat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise BrainActionError("human-canary-journal-permissions-invalid")
        db = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()
            for private_path in (
                self.db_path, Path(f"{self.db_path}-wal"), Path(f"{self.db_path}-shm"),
            ):
                if private_path.exists():
                    os.chmod(private_path, 0o600)

    @staticmethod
    @contextlib.contextmanager
    def transaction(db: sqlite3.Connection) -> Iterator[None]:
        db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            db.rollback()
            raise
        else:
            db.commit()

    def _schema(self, db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
              singleton INTEGER PRIMARY KEY CHECK(singleton=1),
              schema_version INTEGER NOT NULL,
              work_id_private TEXT NOT NULL,
              work_id_hash TEXT NOT NULL UNIQUE,
              chat_ref TEXT NOT NULL,
              topic_ref TEXT NOT NULL,
              state TEXT NOT NULL,
              privacy_path INTEGER NOT NULL DEFAULT 0,
              post_forget_verified INTEGER NOT NULL DEFAULT 0,
              activated_at TEXT NOT NULL,
              sealed_at TEXT NOT NULL DEFAULT '',
              bindings_scrubbed_at TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS targets (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              work_id_hash TEXT NOT NULL,
              chat_ref TEXT NOT NULL,
              topic_ref TEXT NOT NULL,
              message_ref TEXT NOT NULL DEFAULT '',
              class TEXT NOT NULL UNIQUE,
              direction TEXT NOT NULL,
              required INTEGER NOT NULL DEFAULT 1,
              delivery_state TEXT NOT NULL,
              cleanup_state TEXT NOT NULL DEFAULT 'known',
              attempts INTEGER NOT NULL DEFAULT 0,
              error_class TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_human_canary_message
              ON targets(chat_ref,topic_ref,message_ref) WHERE message_ref!='';
            CREATE TABLE IF NOT EXISTS cleanup_evidence (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL,
              private_value TEXT NOT NULL,
              expected_digest TEXT NOT NULL DEFAULT '',
              expected_ref_count INTEGER NOT NULL DEFAULT 0,
              expected_work_ref_count INTEGER NOT NULL DEFAULT 0,
              require_path_absent INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              UNIQUE(kind,private_value)
            );
            """
        )
        metadata_columns = {
            str(row["name"]) for row in db.execute("PRAGMA table_info(metadata)")
        }
        if "post_forget_verified" not in metadata_columns:
            db.execute(
                "ALTER TABLE metadata ADD COLUMN post_forget_verified INTEGER NOT NULL DEFAULT 0"
            )
        evidence_columns = {
            str(row["name"]) for row in db.execute("PRAGMA table_info(cleanup_evidence)")
        }
        evidence_additions = {
            "expected_ref_count": "INTEGER NOT NULL DEFAULT 0",
            "expected_work_ref_count": "INTEGER NOT NULL DEFAULT 0",
            "require_path_absent": "INTEGER NOT NULL DEFAULT 1",
        }
        for column, definition in evidence_additions.items():
            if column not in evidence_columns:
                db.execute(f"ALTER TABLE cleanup_evidence ADD COLUMN {column} {definition}")

    def _metadata(self, db: sqlite3.Connection) -> sqlite3.Row:
        row = db.execute("SELECT * FROM metadata WHERE singleton=1").fetchone()
        if (
            not row
            or int(row["schema_version"]) != HUMAN_CANARY_SCHEMA_VERSION
            or not hmac.compare_digest(str(row["work_id_private"]), self.work_id)
            or not hmac.compare_digest(str(row["work_id_hash"]), self.work_hash)
        ):
            raise BrainActionError("human-canary-journal-binding-invalid")
        return row

    def activate(
        self,
        *,
        chat_ref: str,
        topic_ref: str,
        source_message_ref: str,
        card_message_ref: str,
        final_message_ref: str,
    ) -> dict[str, Any]:
        if self.receipt_path.exists():
            raise BrainActionError("human-canary-journal-already-finalized")
        if not all(str(value).isdigit() for value in (
            source_message_ref, card_message_ref, final_message_ref,
        )) or len({source_message_ref, card_message_ref, final_message_ref}) != 3:
            raise BrainActionError("human-canary-base-surfaces-invalid")
        now = utc_now()
        with self.connect(create=True) as db, self.transaction(db):
            self._schema(db)
            existing = db.execute("SELECT * FROM metadata WHERE singleton=1").fetchone()
            if existing:
                self._metadata(db)
                if (
                    not hmac.compare_digest(str(existing["chat_ref"]), chat_ref)
                    or not hmac.compare_digest(str(existing["topic_ref"]), topic_ref)
                ):
                    raise BrainActionError("human-canary-journal-route-conflict")
            else:
                db.execute(
                    """INSERT INTO metadata(
                         singleton,schema_version,work_id_private,work_id_hash,chat_ref,
                         topic_ref,state,activated_at,updated_at
                       ) VALUES(1,?,?,?,?,?,'active',?,?)""",
                    (
                        HUMAN_CANARY_SCHEMA_VERSION, self.work_id, self.work_hash,
                        chat_ref, topic_ref, now, now,
                    ),
                )
            for target_class, direction, message_ref, delivery_state in (
                ("source_media", "inbound", source_message_ref, "received"),
                ("ingestion_card", "outbound", card_message_ref, "delivered"),
                ("ingestion_final", "outbound", final_message_ref, "delivered"),
            ):
                db.execute(
                    """INSERT OR IGNORE INTO targets(
                         work_id_hash,chat_ref,topic_ref,message_ref,class,direction,
                         delivery_state,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        self.work_hash, chat_ref, topic_ref, message_ref, target_class,
                        direction, delivery_state, now, now,
                    ),
                )
            rows = db.execute(
                "SELECT class,message_ref FROM targets ORDER BY class",
            ).fetchall()
            expected = {
                "source_media": source_message_ref,
                "ingestion_card": card_message_ref,
                "ingestion_final": final_message_ref,
            }
            if any(expected.get(str(row["class"])) != str(row["message_ref"]) for row in rows):
                raise BrainActionError("human-canary-base-surfaces-conflict")
        return {
            "ok": True, "activated": True, "targetCount": 3,
            "privacy": {"countsOnly": True, "identifiersIncluded": False},
        }

    def record_cleanup_evidence(
        self,
        evidence: list[tuple[str, str, str, int, int, bool]],
    ) -> None:
        allowed = {"artifact_path", "extraction_path", "download_path", "chunk_ref"}
        now = utc_now()
        with self.connect() as db, self.transaction(db):
            metadata = self._metadata(db)
            if str(metadata["state"]) != "active":
                raise BrainActionError("human-canary-journal-sealed")
            for (
                kind, private_value, expected_digest, expected_ref_count,
                expected_work_ref_count, require_path_absent,
            ) in evidence:
                if kind not in allowed or not str(private_value):
                    raise BrainActionError("human-canary-cleanup-evidence-invalid")
                digest = str(expected_digest or "")
                if digest and not re.fullmatch(r"[a-f0-9]{64}", digest):
                    raise BrainActionError("human-canary-cleanup-evidence-invalid")
                ref_count = int(expected_ref_count)
                work_ref_count = int(expected_work_ref_count)
                if (
                    ref_count < 0
                    or work_ref_count < 0
                    or work_ref_count > ref_count
                    or (kind != "artifact_path" and (ref_count or work_ref_count))
                    or (kind == "artifact_path" and (not digest or work_ref_count < 1))
                    or (kind == "artifact_path" and bool(require_path_absent) != (
                        ref_count == work_ref_count
                    ))
                ):
                    raise BrainActionError("human-canary-cleanup-evidence-invalid")
                db.execute(
                    """INSERT OR IGNORE INTO cleanup_evidence(
                         kind,private_value,expected_digest,expected_ref_count,
                         expected_work_ref_count,require_path_absent,created_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        kind, str(private_value), digest, ref_count, work_ref_count,
                        int(bool(require_path_absent)), now,
                    ),
                )

    def record(
        self,
        *,
        target_class: str,
        direction: str,
        chat_ref: str,
        topic_ref: str,
        message_ref: str,
        delivery_state: str,
    ) -> None:
        if target_class not in set(HUMAN_CANARY_DELETE_ORDER):
            raise BrainActionError("human-canary-target-class-invalid")
        if direction not in {"inbound", "outbound"}:
            raise BrainActionError("human-canary-target-direction-invalid")
        if delivery_state not in {"received", "delivered", "indeterminate", "dead_letter"}:
            raise BrainActionError("human-canary-delivery-state-invalid")
        if delivery_state in {"received", "delivered"} and not str(message_ref).isdigit():
            raise BrainActionError("human-canary-message-ref-missing")
        now = utc_now()
        with self.connect() as db, self.transaction(db):
            metadata = self._metadata(db)
            if (
                str(metadata["state"]) not in {"active", "sealed", "ready"}
                or not hmac.compare_digest(str(metadata["chat_ref"]), chat_ref)
                or not hmac.compare_digest(str(metadata["topic_ref"]), topic_ref)
            ):
                raise BrainActionError("human-canary-journal-route-conflict")
            existing = db.execute(
                "SELECT * FROM targets WHERE class=?", (target_class,),
            ).fetchone()
            if existing:
                existing_ref = str(existing["message_ref"])
                if (
                    str(existing["direction"]) != direction
                    or (existing_ref and message_ref and not hmac.compare_digest(existing_ref, message_ref))
                ):
                    raise BrainActionError("human-canary-target-conflict")
                db.execute(
                    """UPDATE targets SET message_ref=?,delivery_state=?,updated_at=?
                         WHERE class=?""",
                    (message_ref or existing_ref, delivery_state, now, target_class),
                )
            else:
                if str(metadata["state"]) != "active":
                    raise BrainActionError("human-canary-journal-sealed")
                db.execute(
                    """INSERT INTO targets(
                         work_id_hash,chat_ref,topic_ref,message_ref,class,direction,
                         delivery_state,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        self.work_hash, chat_ref, topic_ref, message_ref, target_class,
                        direction, delivery_state, now, now,
                    ),
                )
            if target_class.startswith("privacy_"):
                db.execute(
                    "UPDATE metadata SET privacy_path=1,updated_at=? WHERE singleton=1",
                    (now,),
                )

    def _required_classes(self, db: sqlite3.Connection) -> tuple[str, ...]:
        metadata = self._metadata(db)
        return (
            *HUMAN_CANARY_BASE_CLASSES,
            *(HUMAN_CANARY_PRIVACY_CLASSES if bool(metadata["privacy_path"]) else ()),
            *HUMAN_CANARY_FORGET_CLASSES,
        )

    def status(self) -> dict[str, Any]:
        if not self.db_path.exists():
            if self.receipt_path.is_file():
                if any(path.exists() or path.is_symlink() for path in self.journal_paths[1:]):
                    raise BrainActionError("human-canary-journal-remnants")
                receipt = self._load_receipt()
                return {
                    "ok": bool(receipt.get("cleanupConfirmed")),
                    "state": "complete",
                    "targetCount": int(receipt.get("targetCount") or 0),
                    "unresolved": int(receipt.get("unresolved") or 0),
                    "privacyPath": bool(receipt.get("privacyPath")),
                    "postForgetVerified": bool(receipt.get("postForgetVerified")),
                    "journalRemoved": True,
                    "receiptPresent": True,
                    "privacy": {"countsOnly": True, "identifiersIncluded": False},
                }
            raise BrainActionError("human-canary-journal-missing")
        with self.connect() as db:
            metadata = self._metadata(db)
            required = set(self._required_classes(db))
            rows = db.execute(
                "SELECT class,message_ref,delivery_state,cleanup_state FROM targets",
            ).fetchall()
        seen = {str(row["class"]) for row in rows}
        missing = sorted(required - seen)
        invalid = sum(
            1 for row in rows
            if str(row["class"]) in required
            and (
                not str(row["message_ref"]).isdigit()
                or str(row["delivery_state"]) not in {"received", "delivered"}
            )
        )
        unresolved = sum(
            1 for row in rows
            if str(row["cleanup_state"]) not in {"deleted", "already_absent"}
        )
        return {
            "ok": not missing and invalid == 0,
            "state": str(metadata["state"]),
            "privacyPath": bool(metadata["privacy_path"]),
            "targetCount": len(rows),
            "requiredCount": len(required),
            "missingClasses": missing,
            "invalidTargets": invalid,
            "unresolved": unresolved,
            "privacy": {"countsOnly": True, "identifiersIncluded": False},
        }

    def seal(self) -> None:
        status = self.status()
        if not status.get("ok"):
            raise BrainActionError("human-canary-journal-incomplete")
        now = utc_now()
        with self.connect() as db, self.transaction(db):
            metadata = self._metadata(db)
            if str(metadata["state"]) not in {"active", "sealed"}:
                raise BrainActionError("human-canary-journal-state-invalid")
            db.execute(
                """UPDATE metadata SET state='sealed',sealed_at=CASE WHEN sealed_at='' THEN ? ELSE sealed_at END,
                          updated_at=? WHERE singleton=1""",
                (now, now),
            )

    def mark_bindings_scrubbed(self) -> None:
        now = utc_now()
        with self.connect() as db, self.transaction(db):
            metadata = self._metadata(db)
            if str(metadata["state"]) not in {"sealed", "ready"}:
                raise BrainActionError("human-canary-journal-not-sealed")
            db.execute(
                """UPDATE metadata SET state='ready',
                          bindings_scrubbed_at=CASE WHEN bindings_scrubbed_at='' THEN ? ELSE bindings_scrubbed_at END,
                          updated_at=? WHERE singleton=1""",
                (now, now),
            )

    def mark_post_forget_verified(self) -> None:
        with self.connect() as db, self.transaction(db):
            metadata = self._metadata(db)
            if str(metadata["state"]) != "ready":
                raise BrainActionError("human-canary-cleanup-not-ready")
            db.execute(
                "UPDATE metadata SET post_forget_verified=1,updated_at=? WHERE singleton=1",
                (utc_now(),),
            )

    @staticmethod
    def _cleanup_state(result: Mapping[str, Any]) -> tuple[str, str]:
        if result.get("ok"):
            return ("already_absent" if result.get("alreadyAbsent") else "deleted"), ""
        state = clean_text(result.get("state"), 40)
        if state not in {"indeterminate", "dead_letter"}:
            state = "indeterminate"
        error_class = clean_text(result.get("errorClass"), 80) or "telegram-delete-unknown"
        return state, error_class

    def _receipt_payload(self, db: sqlite3.Connection) -> dict[str, Any]:
        metadata = self._metadata(db)
        rows = db.execute(
            """SELECT class,direction,message_ref,delivery_state,cleanup_state,attempts
                 FROM targets ORDER BY id""",
        ).fetchall()
        snapshot = [dict(row) for row in rows]
        unresolved = sum(
            1 for row in rows if str(row["cleanup_state"]) not in {"deleted", "already_absent"}
        )
        if unresolved:
            raise BrainActionError("human-canary-cleanup-unresolved")
        digest = hashlib.sha256(json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        return {
            "receiptVersion": 1,
            "journalSchemaVersion": HUMAN_CANARY_SCHEMA_VERSION,
            "workIdHash": self.work_hash,
            "cleanupConfirmed": True,
            "privacyPath": bool(metadata["privacy_path"]),
            "postForgetVerified": bool(metadata["post_forget_verified"]),
            "targetCount": len(rows),
            "classCounts": {
                target_class: sum(1 for row in rows if row["class"] == target_class)
                for target_class in HUMAN_CANARY_DELETE_ORDER
                if any(row["class"] == target_class for row in rows)
            },
            "journalDigest": digest,
            "unresolved": 0,
            "activatedAt": str(metadata["activated_at"]),
            "completedAt": utc_now(),
        }

    def _write_receipt(self, payload: Mapping[str, Any]) -> None:
        self._private_directory(self.directory)
        encoded = (json.dumps(dict(payload), indent=2, sort_keys=True) + "\n").encode()
        if self.receipt_path.exists() or self.receipt_path.is_symlink():
            existing = self._load_receipt()
            stable_existing = {
                key: value for key, value in existing.items() if key != "completedAt"
            }
            stable_payload = {
                key: value for key, value in dict(payload).items() if key != "completedAt"
            }
            if stable_existing != stable_payload:
                raise BrainActionError("human-canary-receipt-conflict")
            return
        if self.receipt_temp_path.exists() or self.receipt_temp_path.is_symlink():
            info = self.receipt_temp_path.lstat()
            if (
                self.receipt_temp_path.is_symlink()
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise BrainActionError("human-canary-receipt-temp-invalid")
            self.receipt_temp_path.unlink()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.receipt_temp_path, flags, 0o600)
        try:
            offset = 0
            while offset < len(encoded):
                offset += os.write(descriptor, encoded[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fault("receipt_temp_fsynced")
        os.replace(self.receipt_temp_path, self.receipt_path)
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_fd = os.open(self.directory, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        self._fault("receipt_renamed")

    def _remove_journal_files(self) -> None:
        # Sidecars go first.  Therefore a crash after the main DB disappears
        # can never leave a WAL/SHM that a counts-only receipt masks.
        ordered = (
            (Path(f"{self.db_path}-wal"), "journal_wal_unlinked"),
            (Path(f"{self.db_path}-shm"), "journal_shm_unlinked"),
            (self.db_path, "journal_db_unlinked"),
        )
        for private_path, fault_point in ordered:
            try:
                private_path.unlink()
            except FileNotFoundError:
                pass
            self._fault(fault_point)
        if any(path.exists() or path.is_symlink() for path in self.journal_paths):
            raise BrainActionError("human-canary-journal-removal-failed")
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_fd = os.open(self.directory, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def cleanup(
        self,
        transport: Callable[[str, Mapping[str, Any], int], dict[str, Any]],
        *,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        with self._cleanup_lock():
            return self._cleanup_locked(transport, max_attempts=max_attempts)

    def _cleanup_locked(
        self,
        transport: Callable[[str, Mapping[str, Any], int], dict[str, Any]],
        *,
        max_attempts: int,
    ) -> dict[str, Any]:
        if not self.db_path.exists() and self.receipt_path.is_file():
            self._remove_journal_files()
            return self.status()
        bounded_attempts = max(1, min(int(max_attempts), 5))
        with self.connect() as db:
            metadata = self._metadata(db)
            if str(metadata["state"]) != "ready":
                raise BrainActionError("human-canary-cleanup-not-ready")
            required = set(self._required_classes(db))
            rows = db.execute(
                "SELECT * FROM targets",
            ).fetchall()
            if {str(row["class"]) for row in rows} != required:
                raise BrainActionError("human-canary-journal-incomplete")
        for target_class in HUMAN_CANARY_DELETE_ORDER:
            with self.connect() as db:
                row = db.execute(
                    "SELECT * FROM targets WHERE class=?", (target_class,),
                ).fetchone()
            if not row or str(row["cleanup_state"]) in {"deleted", "already_absent"}:
                continue
            for _ in range(bounded_attempts):
                with self.connect() as db, self.transaction(db):
                    db.execute(
                        """UPDATE targets SET cleanup_state='deleting',attempts=attempts+1,
                                  error_class='',updated_at=? WHERE class=?""",
                        (utc_now(), target_class),
                    )
                result = transport(
                    "deleteMessage",
                    {"chat_id": str(row["chat_ref"]), "message_id": int(str(row["message_ref"]))},
                    TELEGRAM_TIMEOUT_SECONDS,
                )
                cleanup_state, error_class = self._cleanup_state(result)
                with self.connect() as db, self.transaction(db):
                    db.execute(
                        """UPDATE targets SET cleanup_state=?,error_class=?,updated_at=?
                             WHERE class=?""",
                        (cleanup_state, error_class, utc_now(), target_class),
                    )
                if cleanup_state in {"deleted", "already_absent"}:
                    break
        with self.connect() as db:
            rows = db.execute("SELECT cleanup_state FROM targets").fetchall()
            unresolved = sum(
                1 for row in rows if str(row["cleanup_state"]) not in {"deleted", "already_absent"}
            )
            deleted = sum(1 for row in rows if str(row["cleanup_state"]) == "deleted")
            already_absent = sum(
                1 for row in rows if str(row["cleanup_state"]) == "already_absent"
            )
            target_count = len(rows)
            receipt = self._receipt_payload(db) if unresolved == 0 else None
        if receipt is not None:
            self._write_receipt(receipt)
            self._remove_journal_files()
        return {
            "ok": unresolved == 0 and receipt is not None and not self.db_path.exists(),
            "state": "complete" if unresolved == 0 else "cleanup-pending",
            "targetCount": target_count,
            "deleted": deleted,
            "alreadyAbsent": already_absent,
            "unresolved": unresolved,
            "journalRemoved": unresolved == 0 and not self.db_path.exists(),
            "receiptPresent": unresolved == 0 and self.receipt_path.is_file(),
            "privacy": {"countsOnly": True, "identifiersIncluded": False},
        }


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_after(seconds: int) -> str:
    value = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=max(1, int(seconds)))
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_ref(prefix: str, *parts: Any, length: int = 32) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    return f"{prefix}-{hashlib.sha256(material.encode()).hexdigest()[:length]}"


def safe_error_class(exc: BaseException) -> str:
    if isinstance(exc, UnauthorizedActionError):
        return "action-authorization-failed"
    if isinstance(exc, StaleEventError):
        return "action-lifecycle-race"
    if isinstance(exc, LifecycleError):
        return "action-lifecycle-failed"
    if isinstance(exc, BrainActionError):
        value = clean_text(exc, 80)
        return value if value and value.replace("-", "").isalnum() else "brain-action-failed"
    code = clean_text(getattr(exc, "code", ""), 80)
    return code if code and code.replace("-", "").isalnum() else "brain-action-failed"


def default_action_publisher(event: Mapping[str, Any]) -> bool:
    """Append one stable, dashboard-safe action event to the owning ledger."""
    spec = ACTION_STAGE_SPECS.get(str(event.get("stage") or ""))
    script = Path(__file__).resolve().parent / "agent_publish.py"
    if not spec or not script.exists():
        return False
    phase = clean_text(event.get("phase"), 40)
    if phase not in {
        "received", "classified", "acknowledged", "working",
        "awaiting_input", "verifying", "terminal",
    }:
        return False
    status = clean_text(event.get("status"), 20)
    work_event = clean_text(event.get("workEvent"), 20)
    if status not in {"active", "done", "error"} or work_event not in {"heartbeat", "terminal"}:
        return False
    detail = spec["detail"]
    route_verified = bool(event.get("routeVerified"))
    route_class = clean_text(event.get("routeClass"), 40)
    if route_verified and route_class:
        detail = f"{detail} Preserved route: {route_class}."
    command = [
        sys.executable, str(script),
        "--agent", "josh2",
        "--type", "status",
        "--status", status,
        "--title", "Brain governed action",
        "--tool", "Josh 2.0 Brain gateway",
        "--detail", detail,
        "--privacy", "dashboard-safe",
        "--brain-feed",
        "--work-event", work_event,
        "--work-id", str(event["workId"]),
        "--run-id", str(event["runId"]),
        "--phase", phase,
        "--origin-claim-hash", str(event["originClaimHash"]),
        "--event-id", str(event["eventId"]),
    ]
    if route_verified and route_class:
        command.append("--route-verified")
    try:
        result = subprocess.run(
            command,
            cwd=script.parents[1],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        payload = json.loads(result.stdout or "{}") if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return False
    ledger = payload.get("workLedger") if isinstance(payload, dict) else None
    return bool(
        payload.get("ok") is True
        and isinstance(ledger, dict)
        and ledger.get("accepted") is True
    )


class BrainGatewayActions:
    def __init__(
        self,
        store_root: Path | str,
        *,
        lifecycle_root: Path | str,
        rollout_path: Path | str,
        config_path: Path | str,
        topic_receipt_path: Path | str,
        authorized_sender_receipt_path: Path | str,
        dispatcher_state_root: Path | str,
        state_root: Path | str,
        transport: Callable[[str, Mapping[str, Any], int], dict[str, Any]] | None = None,
        action_publisher: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> None:
        # Keep construction side-effect free.  Bot/unauthorized events are
        # rejected before any adapter, BrainStore, or lifecycle DB is opened.
        self.store_root = Path(store_root).expanduser().resolve()
        self.lifecycle_root = Path(lifecycle_root).expanduser().resolve()
        self.rollout_path = Path(rollout_path).expanduser().resolve()
        self.config_path = Path(config_path).expanduser().resolve()
        self.topic_receipt_path = Path(topic_receipt_path).expanduser().resolve()
        self.authorized_sender_receipt_path = Path(authorized_sender_receipt_path).expanduser().resolve()
        self.dispatcher_db = Path(dispatcher_state_root).expanduser().resolve() / "dispatcher.sqlite3"
        self.state_root = Path(state_root).expanduser().resolve()
        self.human_canary_root = self.state_root / "human-canary"
        self.db_path = self.state_root / "actions.sqlite3"
        self.transport = transport or default_transport
        self.action_publisher = action_publisher or default_action_publisher

    def _ensure_schema(self) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_root, 0o700)
        if not self.db_path.exists():
            descriptor = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS inbound_events (
                  event_key TEXT PRIMARY KEY,
                  telegram_message_ref TEXT NOT NULL,
                  reply_message_ref TEXT NOT NULL,
                  work_id TEXT NOT NULL,
                  action_ref TEXT NOT NULL UNIQUE,
                  action TEXT NOT NULL,
                  state TEXT NOT NULL,
                  response_effect_key TEXT NOT NULL DEFAULT '',
                  response_message_ref TEXT NOT NULL DEFAULT '',
                  error_class TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending_actions (
                  action_ref TEXT PRIMARY KEY,
                  work_id TEXT NOT NULL,
                  action TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  lifecycle_token TEXT NOT NULL DEFAULT '',
                  brain_token TEXT NOT NULL DEFAULT '',
                  preview_message_ref TEXT NOT NULL DEFAULT '',
                  state TEXT NOT NULL,
                  expires_at TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS message_mappings (
                  chat_ref TEXT NOT NULL,
                  topic_ref TEXT NOT NULL,
                  message_ref TEXT NOT NULL,
                  work_id TEXT NOT NULL,
                  mapping_kind TEXT NOT NULL,
                  action_ref TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY(chat_ref,topic_ref,message_ref)
                );
                CREATE TABLE IF NOT EXISTS response_attempts (
                  effect_key TEXT PRIMARY KEY,
                  action_ref TEXT NOT NULL UNIQUE,
                  stage TEXT NOT NULL,
                  telegram_message_ref TEXT NOT NULL DEFAULT '',
                  error_class TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending_responses (
                  action_ref TEXT PRIMARY KEY,
                  event_key TEXT NOT NULL UNIQUE,
                  work_id TEXT NOT NULL,
                  authorized_user TEXT NOT NULL,
                  chat_ref TEXT NOT NULL,
                  topic_ref TEXT NOT NULL,
                  reply_ref TEXT NOT NULL,
                  response_kind TEXT NOT NULL,
                  text_private TEXT NOT NULL,
                  result_private_json TEXT NOT NULL DEFAULT '{}',
                  state TEXT NOT NULL DEFAULT 'pending',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS control_outbox (
                  event_id TEXT PRIMARY KEY,
                  work_id TEXT NOT NULL,
                  run_id TEXT NOT NULL,
                  origin_claim_hash TEXT NOT NULL,
                  action_class TEXT NOT NULL,
                  stage TEXT NOT NULL,
                  phase TEXT NOT NULL,
                  work_event TEXT NOT NULL DEFAULT 'heartbeat',
                  terminal_status TEXT NOT NULL DEFAULT 'active',
                  route_verified INTEGER NOT NULL DEFAULT 0,
                  route_class TEXT NOT NULL DEFAULT '',
                  state TEXT NOT NULL,
                  attempts INTEGER NOT NULL DEFAULT 0,
                  available_at TEXT NOT NULL,
                  error_class TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(work_id,event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_brain_action_outbox
                  ON control_outbox(state,available_at,attempts);
                CREATE TABLE IF NOT EXISTS deletion_receipts (
                  deletion_id TEXT PRIMARY KEY,
                  work_id_hash TEXT NOT NULL,
                  adapter_rows INTEGER NOT NULL,
                  dispatcher_rows INTEGER NOT NULL,
                  lifecycle_rows INTEGER NOT NULL,
                  completed_at TEXT NOT NULL
                );
                """
            )
            outbox_columns = {
                str(row["name"]) for row in db.execute("PRAGMA table_info(control_outbox)")
            }
            additions = {
                "work_event": "TEXT NOT NULL DEFAULT 'heartbeat'",
                "terminal_status": "TEXT NOT NULL DEFAULT 'active'",
                "route_verified": "INTEGER NOT NULL DEFAULT 0",
                "route_class": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in additions.items():
                if column not in outbox_columns:
                    db.execute(f"ALTER TABLE control_outbox ADD COLUMN {column} {definition}")
        os.chmod(self.db_path, 0o600)

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()
            for private_path in (
                self.db_path,
                Path(f"{self.db_path}-wal"),
                Path(f"{self.db_path}-shm"),
            ):
                if private_path.exists():
                    os.chmod(private_path, stat.S_IRUSR | stat.S_IWUSR)

    @staticmethod
    @contextlib.contextmanager
    def transaction(db: sqlite3.Connection) -> Iterator[None]:
        db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            db.rollback()
            raise
        else:
            db.commit()

    @staticmethod
    def _readonly(path: Path) -> sqlite3.Connection:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def _store(self) -> BrainStore:
        return BrainStore(
            self.store_root,
            authorized_sender_receipt=self.authorized_sender_receipt_path,
        )

    def _gateway(self) -> GatewayLifecycle:
        return GatewayLifecycle(
            self.lifecycle_root,
            rollout=RolloutPolicy.load(self.rollout_path),
            owner="josh2",
        )

    def _human_canary(self, work_id: str) -> BrainHumanCanaryJournal:
        return BrainHumanCanaryJournal(self.human_canary_root, work_id)

    def _human_canary_fault(self, _point: str) -> None:
        """Fault-injection seam for durable binding-scrub recovery tests."""

    def _recover_human_canary_bindings(self, work_id: str) -> None:
        journal = self._human_canary(work_id)
        if not journal.exists:
            return
        state = str(journal.status().get("state") or "")
        if state == "sealed":
            # Replaying every stage is safe: each delete is work-bound, each
            # transaction is idempotent, and the final receipt is one-per-work.
            self._scrub_forget_bindings(work_id)

    def human_canary_preflight(self) -> dict[str, Any]:
        """Verify live deletion authority without exposing the bot or topic identity."""
        config = load_json(self.config_path)
        chat_ref, _topic_ref = resolved_brain_topic(config, self.topic_receipt_path)
        identity = self.transport("getMe", {}, TELEGRAM_TIMEOUT_SECONDS)
        identity_result = identity.get("result") if isinstance(identity.get("result"), dict) else {}
        bot_ref = str(identity_result.get("id") or "")
        topic_receipt = load_private_receipt(
            self.topic_receipt_path,
            error_prefix="topic-receipt",
        )
        receipt_bot_ref = clean_text(topic_receipt.get("botId"), 120)
        identity_verified = bool(
            identity.get("ok")
            and bot_ref.isdigit()
            and receipt_bot_ref.isdigit()
            and hmac.compare_digest(bot_ref, receipt_bot_ref)
        )
        if not identity_verified:
            return {
                "ok": False,
                "identityVerified": False,
                "creatorOrAdmin": False,
                "canManageTopics": False,
                "canDeleteMessages": False,
                "errorClass": clean_text(identity.get("errorClass"), 80) or "telegram-bot-identity-unverified",
                "privacy": {"countsOnly": True, "identifiersIncluded": False},
            }
        membership = self.transport(
            "getChatMember",
            {"chat_id": chat_ref, "user_id": int(bot_ref)},
            TELEGRAM_TIMEOUT_SECONDS,
        )
        member = membership.get("result") if isinstance(membership.get("result"), dict) else {}
        status = clean_text(member.get("status"), 40)
        creator = status in {"creator", "owner"}
        creator_or_admin = creator or status == "administrator"
        can_manage_topics = bool(creator or member.get("can_manage_topics") is True)
        can_delete_messages = bool(creator or member.get("can_delete_messages") is True)
        ok = bool(
            membership.get("ok")
            and creator_or_admin
            and can_manage_topics
            and can_delete_messages
        )
        return {
            "ok": ok,
            "identityVerified": True,
            "creatorOrAdmin": creator_or_admin,
            "canManageTopics": can_manage_topics,
            "canDeleteMessages": can_delete_messages,
            "errorClass": "" if ok else (
                clean_text(membership.get("errorClass"), 80)
                or "telegram-delete-permission-missing"
            ),
            "privacy": {"countsOnly": True, "identifiersIncluded": False},
        }

    def activate_human_canary(self, work_id: str) -> dict[str, Any]:
        permission = self.human_canary_preflight()
        if not permission.get("ok") or not permission.get("canDeleteMessages"):
            raise BrainActionError("human-canary-delete-permission-missing")
        receipt = self._bound_work_receipt(work_id)
        if receipt.get("phase") != "terminal" or not receipt.get("finalDelivered"):
            raise BrainActionError("human-canary-ingestion-not-delivered")
        config = load_json(self.config_path)
        expected_chat, expected_topic = resolved_brain_topic(config, self.topic_receipt_path)
        if not self.dispatcher_db.exists():
            raise BrainActionError("human-canary-dispatcher-missing")
        try:
            with contextlib.closing(self._readonly(self.dispatcher_db)) as db:
                rows = db.execute(
                    """SELECT chat_ref,topic_ref,source_message_ref,card_message_ref,final_message_ref
                         FROM surfaces WHERE work_id=?""",
                    (work_id,),
                ).fetchall()
        except (OSError, sqlite3.Error) as exc:
            raise BrainActionError("human-canary-dispatcher-unreadable") from exc
        if len(rows) != 1:
            raise BrainActionError("human-canary-surface-binding-invalid")
        row = rows[0]
        if (
            not hmac.compare_digest(str(row["chat_ref"]), expected_chat)
            or not hmac.compare_digest(str(row["topic_ref"]), expected_topic)
        ):
            raise BrainActionError("human-canary-surface-route-invalid")
        refs = (
            str(row["source_message_ref"]),
            str(row["card_message_ref"]),
            str(row["final_message_ref"]),
        )
        for ref in refs:
            mapping = self._mapping(expected_chat, expected_topic, ref)
            if not mapping or not hmac.compare_digest(str(mapping.get("workId") or ""), work_id):
                raise BrainActionError("human-canary-surface-binding-invalid")
        journal = self._human_canary(work_id)
        activated = journal.activate(
            chat_ref=expected_chat,
            topic_ref=expected_topic,
            source_message_ref=refs[0],
            card_message_ref=refs[1],
            final_message_ref=refs[2],
        )
        evidence: list[tuple[str, str, str, int, int, bool]] = []
        with self._store().connect() as db:
            for stored_path, digest, ref_count, work_ref_count in db.execute(
                """SELECT a.stored_path,a.digest,a.ref_count,COUNT(*) AS work_ref_count
                     FROM submission_artifacts sa JOIN artifacts a ON a.digest=sa.digest
                     WHERE sa.work_id=? GROUP BY a.stored_path,a.digest,a.ref_count""",
                (work_id,),
            ).fetchall():
                if stored_path:
                    evidence.append((
                        "artifact_path", str(stored_path), str(digest), int(ref_count),
                        int(work_ref_count), int(ref_count) == int(work_ref_count),
                    ))
            for private_path, text_hash in db.execute(
                "SELECT private_path,text_hash FROM extractions WHERE work_id=?",
                (work_id,),
            ).fetchall():
                if private_path:
                    evidence.append((
                        "extraction_path", str(private_path), str(text_hash), 0, 0, True,
                    ))
            for cleanup_path, fingerprint in db.execute(
                """SELECT source_cleanup_path,source_cleanup_fingerprint
                     FROM attachment_intents WHERE work_id=? AND source_cleanup_path!=''""",
                (work_id,),
            ).fetchall():
                digest = str(fingerprint) if re.fullmatch(r"[a-f0-9]{64}", str(fingerprint)) else ""
                evidence.append(("download_path", str(cleanup_path), digest, 0, 0, True))
            for chunk_ref, text_hash in db.execute(
                "SELECT id,text_hash FROM source_chunks WHERE work_id=?",
                (work_id,),
            ).fetchall():
                evidence.append(("chunk_ref", str(chunk_ref), str(text_hash), 0, 0, True))
        journal.record_cleanup_evidence(evidence)
        return {
            **activated,
            "deletePermissionVerified": True,
            "surfaceBindingsVerified": True,
        }

    def _record_human_canary_inbound(
        self,
        work_id: str,
        *,
        action: str,
        chat_ref: str,
        topic_ref: str,
        message_ref: str,
    ) -> None:
        journal = self._human_canary(work_id)
        if not journal.exists:
            return
        target_class = HUMAN_CANARY_INBOUND_CLASS.get(action)
        if target_class:
            journal.record(
                target_class=target_class,
                direction="inbound",
                chat_ref=chat_ref,
                topic_ref=topic_ref,
                message_ref=message_ref,
                delivery_state="received",
            )

    def _record_human_canary_response(
        self,
        work_id: str,
        *,
        response_kind: str,
        chat_ref: str,
        topic_ref: str,
        message_ref: str,
        delivery_state: str,
    ) -> None:
        journal = self._human_canary(work_id)
        if not journal.exists:
            return
        target_class = HUMAN_CANARY_RESPONSE_CLASS.get(response_kind)
        if target_class:
            journal.record(
                target_class=target_class,
                direction="outbound",
                chat_ref=chat_ref,
                topic_ref=topic_ref,
                message_ref=message_ref,
                delivery_state=delivery_state,
            )

    @staticmethod
    def _canonical_payload_hash(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _review_only_partial_terminal_verified(self, work_id: str) -> bool:
        """Accept a partial terminal only when immutable evidence proves clean review."""
        try:
            store = self._store()
            with store.connect() as db:
                results = db.execute(
                    """SELECT outcome,payload_hash,private_payload_json
                         FROM intake_results WHERE work_id=?""",
                    (work_id,),
                ).fetchall()
                candidates = db.execute(
                    "SELECT candidate_type FROM candidates WHERE work_id=?",
                    (work_id,),
                ).fetchall()
                extractions = db.execute(
                    "SELECT status,prompt_injection FROM extractions WHERE work_id=?",
                    (work_id,),
                ).fetchall()
                attachments = db.execute(
                    "SELECT state,failure_reason FROM attachment_intents WHERE work_id=?",
                    (work_id,),
                ).fetchall()
                artifacts = db.execute(
                    """SELECT a.quarantine_reason
                         FROM artifacts a
                         JOIN submission_artifacts sa ON sa.digest=a.digest
                        WHERE sa.work_id=?""",
                    (work_id,),
                ).fetchall()
            lifecycle_path = self.lifecycle_root / "lifecycle.sqlite3"
            with contextlib.closing(self._readonly(lifecycle_path)) as db:
                outboxes = db.execute(
                    """SELECT outcome,state,payload_hash,payload_json
                         FROM terminal_outbox WHERE work_id=?""",
                    (work_id,),
                ).fetchall()
                verifying_events = db.execute(
                    """SELECT safe_payload_json FROM lifecycle_events
                         WHERE work_id=? AND event_type='verifying'
                         ORDER BY sequence""",
                    (work_id,),
                ).fetchall()
            if len(results) != 1 or len(outboxes) != 1 or len(verifying_events) != 1:
                return False
            result = results[0]
            outbox = outboxes[0]
            result_payload = json.loads(str(result["private_payload_json"]))
            outbox_payload = json.loads(str(outbox["payload_json"]))
            verifying = json.loads(str(verifying_events[0]["safe_payload_json"]))
            if not all(isinstance(value, dict) for value in (
                result_payload, outbox_payload, verifying,
            )):
                return False
            result_hash = str(result["payload_hash"])
            outbox_hash = str(outbox["payload_hash"])
            if not (
                hmac.compare_digest(result_hash, self._canonical_payload_hash(result_payload))
                and hmac.compare_digest(outbox_hash, self._canonical_payload_hash(outbox_payload))
                and hmac.compare_digest(result_hash, outbox_hash)
                and result_payload == outbox_payload
            ):
                return False
            receipt = result_payload.get("receipt")
            if not isinstance(receipt, dict):
                return False
            pending = receipt.get("Pending review")
            learned = receipt.get("Learned")
            if not isinstance(pending, dict) or not isinstance(learned, dict):
                return False
            reasons = pending.get("reasons")
            if not (
                isinstance(reasons, list)
                and len(reasons) == 1
                and reasons[0] == "manual-review-required"
            ):
                return False
            return bool(
                str(result["outcome"]) == "partial"
                and str(outbox["outcome"]) == "partial"
                and str(outbox["state"]) == "delivered"
                and result_payload.get("terminalStatus") == "partial"
                and result_payload.get("errorClass") == "n/a"
                and result_payload.get("surfaceContract") == "brain-intake"
                and result_payload.get("owner") == "josh2"
                and int(result_payload.get("deliveryTier") or 0) == 3
                and receipt.get("Stored") == "Yes"
                and receipt.get("Extracted") == {
                    "types": ["text"],
                    "coverage": ["full"],
                    "routes": ["local-deterministic"],
                }
                and receipt.get("Source indexed") == "Yes"
                and receipt.get("Unsupported") == ["n/a"]
                and int(pending.get("count") or 0) == 1
                and int(learned.get("count") or 0) == 0
                and learned.get("types") == ["n/a"]
                and receipt.get("Duplicates") == "n/a"
                and receipt.get("Privacy") == "private"
                and receipt.get("Retention") == "privately retained"
                and receipt.get("Approval needed") == "memory review"
                and int(verifying.get("candidateCount") or 0) == 1
                and int(verifying.get("reviewCount") or 0) == 1
                and len(candidates) == 1
                and str(candidates[0]["candidate_type"]) == "fact"
                and bool(extractions)
                and all(str(row["status"]) == "indexed" for row in extractions)
                and sum(max(0, int(row["prompt_injection"] or 0)) for row in extractions) == 0
                and len(attachments) == 1
                and str(attachments[0]["state"]) == "stored"
                and not str(attachments[0]["failure_reason"] or "")
                and bool(artifacts)
                and all(not str(row["quarantine_reason"] or "") for row in artifacts)
            )
        except (OSError, sqlite3.Error, json.JSONDecodeError, TypeError, ValueError):
            return False

    def _human_canary_pre_forget_assertions(
        self,
        work_id: str,
        *,
        retrieval_query: str,
    ) -> dict[str, Any]:
        """Verify the live source using private reads and return only redacted facts."""
        checks = {
            "routeReceiptVerified": False,
            "sourceUncaptioned": False,
            "objectiveSafe": False,
            "sourceSurfacesDistinct": False,
            "lifecycleTier3Delivered": False,
            "dispatcherExactlyOnce": False,
            "controlTowerAccepted": False,
            "artifactCount": 0,
            "extractionCount": 0,
            "sourceIndexCount": 0,
            "chunkCount": 0,
            "chunkFtsCount": 0,
            "chunkIndexIdentityVerified": False,
            "vectorCount": 0,
            "candidateCount": 0,
            "candidateGovernanceReady": False,
            "privateFilesVerified": False,
            "localRouteVerified": False,
            "promptInjectionSignals": -1,
            "nonDeliveredAttemptCount": -1,
            "finalReceiptFieldCount": 0,
            "visibilityStageCount": 0,
            "verifiedVisibilityStageCount": 0,
            "earlyVisibilityStagesClean": False,
            "retrievalAgentCount": 4,
            "retrievalAgentsWithProvenance": 0,
            "retrievalRowCount": 0,
            "retrievalAllRowsValid": False,
        }
        try:
            query = clean_text(retrieval_query, 800)
            if not query:
                raise BrainActionError("human-canary-retrieval-query-required")
            journal = self._human_canary(work_id)
            with journal.connect() as canary_db:
                metadata = journal._metadata(canary_db)
                base_targets = {
                    str(row["class"]): str(row["message_ref"])
                    for row in canary_db.execute(
                        "SELECT class,message_ref FROM targets WHERE class IN (?,?,?)",
                        HUMAN_CANARY_BASE_CLASSES,
                    ).fetchall()
                }
            expected_chat, expected_topic = resolved_brain_topic(
                load_json(self.config_path), self.topic_receipt_path,
            )
            expected_sender = resolved_authorized_sender(
                self.authorized_sender_receipt_path,
                chat_id=expected_chat,
                topic_id=expected_topic,
            )
            store = self._store()
            with store.connect() as db:
                submission = db.execute(
                    "SELECT * FROM submissions WHERE work_id=?", (work_id,),
                ).fetchone()
                artifacts = db.execute(
                    """SELECT a.stored_path,a.digest FROM submission_artifacts sa
                         JOIN artifacts a ON a.digest=sa.digest WHERE sa.work_id=?""",
                    (work_id,),
                ).fetchall()
                extractions = db.execute(
                    """SELECT private_path,text_hash,model_route,status,prompt_injection
                         FROM extractions WHERE work_id=?""",
                    (work_id,),
                ).fetchall()
                source_index_count = int(db.execute(
                    "SELECT COUNT(*) FROM source_fts WHERE work_id=?", (work_id,),
                ).fetchone()[0])
                chunks = db.execute(
                    "SELECT id,provenance_ref FROM source_chunks WHERE work_id=?",
                    (work_id,),
                ).fetchall()
                chunk_fts_rows = db.execute(
                    "SELECT chunk_id FROM source_chunk_fts WHERE work_id=?", (work_id,),
                ).fetchall()
                chunk_fts_count = len(chunk_fts_rows)
                vector_count = int(db.execute(
                    """SELECT COUNT(*) FROM source_vectors v JOIN source_chunks c ON c.id=v.chunk_id
                         WHERE c.work_id=?""",
                    (work_id,),
                ).fetchone()[0])
                candidates = db.execute(
                    """SELECT candidate_type,subject,predicate,value_private,status,
                              privacy_class,confidence,provenance_ref,
                              registry_candidate_id,registry_memory_id
                         FROM candidates WHERE work_id=?""",
                    (work_id,),
                ).fetchall()
            if not submission:
                raise BrainActionError("human-canary-source-missing")
            try:
                private_source = json.loads(str(submission["source_private_json"]))
            except json.JSONDecodeError as exc:
                raise BrainActionError("human-canary-source-receipt-invalid") from exc
            source_ref = str(private_source.get("messageRef") or "")
            checks["routeReceiptVerified"] = bool(
                hmac.compare_digest(str(private_source.get("chatRef") or ""), expected_chat)
                and hmac.compare_digest(str(private_source.get("topicRef") or ""), expected_topic)
                and hmac.compare_digest(str(private_source.get("senderRef") or ""), expected_sender)
                and hmac.compare_digest(str(metadata["chat_ref"]), expected_chat)
                and hmac.compare_digest(str(metadata["topic_ref"]), expected_topic)
                and hmac.compare_digest(base_targets.get("source_media", ""), source_ref)
            )
            checks["sourceUncaptioned"] = bool(
                not submission["caption_present"] and not str(submission["caption_private"])
            )
            objective = str(submission["objective_private"])
            checks["objectiveSafe"] = bool(
                objective.casefold().startswith(
                    "govern verified text evidence about jcu10 human canary"
                )
                and "content pending extraction" not in objective.casefold()
                and "telegram" not in objective.casefold()
                and expected_chat not in objective
                and expected_topic not in objective
                and source_ref not in objective
            )
            base_values = [base_targets.get(key, "") for key in HUMAN_CANARY_BASE_CLASSES]
            checks["sourceSurfacesDistinct"] = bool(
                all(value.isdigit() for value in base_values) and len(set(base_values)) == 3
            )
            checks["artifactCount"] = len(artifacts)
            checks["extractionCount"] = len(extractions)
            checks["sourceIndexCount"] = source_index_count
            checks["chunkCount"] = len(chunks)
            checks["chunkFtsCount"] = chunk_fts_count
            checks["vectorCount"] = vector_count
            chunk_ids = [str(row["id"]) for row in chunks]
            chunk_fts_ids = [str(row["chunk_id"]) for row in chunk_fts_rows]
            chunk_provenance_refs = {
                str(row["provenance_ref"]) for row in chunks if str(row["provenance_ref"])
            }
            checks["chunkIndexIdentityVerified"] = bool(
                chunk_ids
                and len(chunk_ids) == len(set(chunk_ids))
                and len(chunk_fts_ids) == len(set(chunk_fts_ids))
                and set(chunk_ids) == set(chunk_fts_ids)
                and len(chunk_provenance_refs) == len(chunk_ids)
            )
            checks["candidateCount"] = len(candidates)
            import memory_registry
            registry = memory_registry.connect()
            try:
                governed_candidates = [
                    row for row in candidates
                    if str(row["status"]) in {"active", "pending", "eligible"}
                ]
                candidate_links_valid = []
                source_binding = f"brain-source:{work_id}"
                expected_provenance = stable_id("source-evidence", work_id, length=28)
                for row in governed_candidates:
                    candidate_ref = str(row["registry_candidate_id"])
                    memory_ref = str(row["registry_memory_id"])
                    registry_candidate = registry.execute(
                        """SELECT memory_type,subject,predicate,object_text,owner,
                                  visibility,privacy,source_path,source_ref,evidence,
                                  confidence,status,content_hash,source_kind,
                                  extraction_version,governance_eligible,
                                  injection_status,source_state
                             FROM memory_candidates WHERE id=?""",
                        (candidate_ref,),
                    ).fetchone() if candidate_ref else None
                    registry_memory = registry.execute(
                        """SELECT memory_type,subject,predicate,object_text,owner,
                                  visibility,privacy,source_path,source_ref,evidence,
                                  confidence,status,content_hash
                             FROM memory_records WHERE id=?""",
                        (memory_ref,),
                    ).fetchone() if memory_ref else None
                    local_status = str(row["status"])
                    local_type = str(row["candidate_type"])
                    registry_type = (
                        local_type if local_type in memory_registry.ALLOWED_TYPES else "procedure"
                    )
                    local_subject = str(row["subject"])
                    local_predicate = str(row["predicate"])
                    local_value = str(row["value_private"])
                    local_privacy = str(row["privacy_class"])
                    local_provenance = str(row["provenance_ref"])
                    try:
                        local_confidence = float(row["confidence"])
                    except (TypeError, ValueError):
                        local_confidence = math.nan
                    expected_content_hash = memory_registry.stable_hash(
                        registry_type, local_subject, local_predicate, local_value,
                        "josh2", source_binding,
                    )
                    candidate_semantics_valid = bool(
                        registry_candidate
                        and str(registry_candidate["memory_type"]) == registry_type
                        and str(registry_candidate["subject"]) == local_subject
                        and str(registry_candidate["predicate"]) == local_predicate
                        and str(registry_candidate["object_text"]) == local_value
                        and str(registry_candidate["owner"]) == "josh2"
                        and str(registry_candidate["visibility"]) == "ecosystem"
                        and str(registry_candidate["privacy"]) == local_privacy
                        and str(registry_candidate["source_path"]) == source_binding
                        and str(registry_candidate["source_ref"]) == source_binding
                        and str(registry_candidate["evidence"]) == local_provenance
                        and math.isfinite(local_confidence)
                        and math.isclose(
                            float(registry_candidate["confidence"]),
                            local_confidence,
                            rel_tol=0.0,
                            abs_tol=1e-12,
                        )
                        and hmac.compare_digest(
                            str(registry_candidate["content_hash"]), expected_content_hash,
                        )
                        and str(registry_candidate["source_kind"]) == "brain-source"
                        and bool(str(registry_candidate["extraction_version"]))
                    )
                    memory_semantics_valid = bool(
                        registry_memory
                        and str(registry_memory["memory_type"]) == registry_type
                        and str(registry_memory["subject"]) == local_subject
                        and str(registry_memory["predicate"]) == local_predicate
                        and str(registry_memory["object_text"]) == local_value
                        and str(registry_memory["owner"]) == "josh2"
                        and str(registry_memory["visibility"]) == "ecosystem"
                        and str(registry_memory["privacy"]) == local_privacy
                        and str(registry_memory["source_path"]) == source_binding
                        and str(registry_memory["source_ref"]) == source_binding
                        and str(registry_memory["evidence"]) == local_provenance
                        and math.isclose(
                            float(registry_memory["confidence"]),
                            local_confidence,
                            rel_tol=0.0,
                            abs_tol=1e-12,
                        )
                        and hmac.compare_digest(
                            str(registry_memory["content_hash"]), expected_content_hash,
                        )
                    )
                    candidate_status_valid = bool(
                        candidate_semantics_valid
                        and str(registry_candidate["injection_status"]) == "clear"
                        and str(registry_candidate["source_state"]) == "active"
                        and (
                            (
                                local_status == "active"
                                and str(registry_candidate["status"]) == "active"
                                and bool(registry_candidate["governance_eligible"])
                                and memory_semantics_valid
                                and str(registry_memory["status"]) == "active"
                            )
                            or (
                                local_status in {"pending", "eligible"}
                                and str(registry_candidate["status"]) in {"candidate", "disputed"}
                                and not memory_ref
                                and (
                                    local_status != "eligible"
                                    or bool(registry_candidate["governance_eligible"])
                                )
                            )
                        )
                    )
                    candidate_links_valid.append(bool(
                        hmac.compare_digest(local_provenance, expected_provenance)
                        and local_privacy in {
                            "private", "internal", "dashboard-safe",
                        }
                        and 0.0 <= local_confidence <= 1.0
                        and candidate_status_valid
                    ))
            finally:
                registry.close()
            checks["candidateGovernanceReady"] = bool(
                governed_candidates and all(candidate_links_valid)
            )
            private_files_verified = True
            for row in (*artifacts, *extractions):
                path_key = "stored_path" if "stored_path" in row.keys() else "private_path"
                digest_key = "digest" if "digest" in row.keys() else "text_hash"
                verified = store._verified_private_artifact(
                    Path(str(row[path_key])), str(row[digest_key]),
                )
                private_files_verified = bool(
                    private_files_verified
                    and stat.S_IMODE(verified.lstat().st_mode) == 0o600
                )
            checks["privateFilesVerified"] = bool(
                private_files_verified and artifacts and extractions
            )
            checks["localRouteVerified"] = bool(
                extractions
                and all(
                    str(row["model_route"]) in {"local-none", "local-deterministic", "local-tool"}
                    and str(row["status"]) in {"indexed", "unsupported"}
                    for row in extractions
                )
            )
            checks["promptInjectionSignals"] = sum(
                max(0, int(row["prompt_injection"] or 0)) for row in extractions
            )
            final_receipt = store.final_receipt(work_id)
            checks["finalReceiptFieldCount"] = sum(
                1 for field in FINAL_RECEIPT_FIELDS if field in final_receipt
            )
            lifecycle = self._bound_work_receipt(work_id)
            terminal_outcome = str(lifecycle.get("outcome") or "")
            terminal_outcome_verified = bool(
                terminal_outcome == "succeeded"
                or (
                    terminal_outcome == "partial"
                    and self._review_only_partial_terminal_verified(work_id)
                )
            )
            checks["lifecycleTier3Delivered"] = bool(
                int(lifecycle.get("deliveryTier") or 0) == 3
                and lifecycle.get("surfaceContract") == "brain-intake"
                and lifecycle.get("currentOwner") == "josh2"
                and lifecycle.get("phase") == "terminal"
                and terminal_outcome_verified
                and lifecycle.get("reactionDelivered")
                and lifecycle.get("cardCreated")
                and lifecycle.get("finalDelivered")
                and lifecycle.get("deliveryState") == "delivered"
            )
            with contextlib.closing(self._readonly(self.dispatcher_db)) as db:
                surface = db.execute(
                    """SELECT reaction_state,card_state,close_state,final_state,
                              source_message_ref,card_message_ref,final_message_ref
                         FROM surfaces WHERE work_id=?""",
                    (work_id,),
                ).fetchone()
                delivered_attempts = {
                    str(row["kind"]): int(row["count"])
                    for row in db.execute(
                        """SELECT kind,COUNT(*) AS count FROM attempts
                             WHERE work_id=? AND stage='delivered' GROUP BY kind""",
                        (work_id,),
                    ).fetchall()
                }
                total_attempts = {
                    str(row["kind"]): int(row["count"])
                    for row in db.execute(
                        """SELECT kind,COUNT(*) AS count FROM attempts
                             WHERE work_id=? GROUP BY kind""",
                        (work_id,),
                    ).fetchall()
                }
                non_delivered_attempts = int(db.execute(
                    """SELECT COUNT(*) FROM attempts
                         WHERE work_id=? AND stage!='delivered'""",
                    (work_id,),
                ).fetchone()[0])
                checks["nonDeliveredAttemptCount"] = non_delivered_attempts
                visibility = db.execute(
                    """SELECT stage,state,route_verified,route_class FROM visibility_outbox
                         WHERE lifecycle_work_id=? AND stage IN (
                           'receipt_ready','processing','verifying','terminal_committed','delivered'
                         )""",
                    (work_id,),
                ).fetchall()
            checks["dispatcherExactlyOnce"] = bool(
                surface
                and all(str(surface[key]) == "delivered" for key in (
                    "reaction_state", "card_state", "close_state", "final_state",
                ))
                and all(delivered_attempts.get(kind, 0) == 1 for kind in (
                    "reaction", "card", "final",
                ))
                and all(total_attempts.get(kind, 0) == 1 for kind in (
                    "reaction", "card", "final",
                ))
                and non_delivered_attempts == 0
                and all(
                    hmac.compare_digest(str(surface[key]), base_targets[target_class])
                    for key, target_class in (
                        ("source_message_ref", "source_media"),
                        ("card_message_ref", "ingestion_card"),
                        ("final_message_ref", "ingestion_final"),
                    )
                )
            )
            required_visibility_stages = {
                "receipt_ready", "processing", "verifying", "terminal_committed", "delivered",
            }
            visibility_by_stage = {
                stage: [row for row in visibility if str(row["stage"]) == stage]
                for stage in required_visibility_stages
            }
            accepted_visibility_stages = {
                str(row["stage"]) for row in visibility
                if str(row["state"]) == "accepted"
            }
            verified_visibility_stages = {
                str(row["stage"]) for row in visibility
                if str(row["stage"]) in {"verifying", "terminal_committed", "delivered"}
                and str(row["state"]) == "accepted"
                and bool(row["route_verified"])
                and str(row["route_class"]) in {
                    "local-none", "local-deterministic", "local-tool", "mixed-local",
                }
            }
            checks["visibilityStageCount"] = len(accepted_visibility_stages)
            checks["verifiedVisibilityStageCount"] = len(verified_visibility_stages)
            checks["earlyVisibilityStagesClean"] = all(
                len(visibility_by_stage[stage]) == 1
                and str(visibility_by_stage[stage][0]["state"]) == "accepted"
                and int(visibility_by_stage[stage][0]["route_verified"]) == 0
                and str(visibility_by_stage[stage][0]["route_class"]) == ""
                for stage in ("receipt_ready", "processing")
            )
            checks["controlTowerAccepted"] = bool(
                accepted_visibility_stages == required_visibility_stages
                and all(len(visibility_by_stage[stage]) == 1 for stage in required_visibility_stages)
                and checks["earlyVisibilityStagesClean"]
                and verified_visibility_stages == {
                    "verifying", "terminal_committed", "delivered",
                }
            )
            retrieval_agents = 0
            retrieval_rows = 0
            retrieval_all_valid = True
            expected_retrieval_source_ref = stable_id(
                "source-evidence", work_id, length=28,
            )
            for agent in ("josh2", "jaimes", "jain", "joshex"):
                retrieval = store.search_source(query=query, agent=agent, limit=10)
                results = retrieval.get("results") if isinstance(retrieval.get("results"), list) else []
                rows_valid = bool(results) and all(
                    isinstance(row, dict)
                    and hmac.compare_digest(str(row.get("workId") or ""), work_id)
                    and hmac.compare_digest(
                        str(row.get("sourceRef") or ""), expected_retrieval_source_ref,
                    )
                    and str(row.get("chunkRef") or "") in chunk_provenance_refs
                    and row.get("resultType") == "source_evidence"
                    and row.get("privacy") == "dashboard-safe"
                    and not isinstance(row.get("confidence"), bool)
                    and isinstance(row.get("confidence"), (int, float))
                    and math.isfinite(float(row["confidence"]))
                    and 0.0 <= float(row["confidence"]) <= 1.0
                    for row in results
                )
                retrieval_rows += len(results)
                retrieval_all_valid = bool(retrieval_all_valid and rows_valid)
                retrieval_agents += int(rows_valid)
            checks["retrievalAgentsWithProvenance"] = retrieval_agents
            checks["retrievalRowCount"] = retrieval_rows
            checks["retrievalAllRowsValid"] = retrieval_all_valid
            required_true = (
                "routeReceiptVerified", "sourceUncaptioned", "objectiveSafe",
                "sourceSurfacesDistinct", "lifecycleTier3Delivered",
                "dispatcherExactlyOnce", "controlTowerAccepted",
                "candidateGovernanceReady", "chunkIndexIdentityVerified",
                "privateFilesVerified", "localRouteVerified",
            )
            ok = bool(
                all(checks[key] for key in required_true)
                and all(int(checks[key]) >= 1 for key in (
                    "artifactCount", "extractionCount", "sourceIndexCount",
                    "chunkCount", "chunkFtsCount", "vectorCount", "candidateCount",
                ))
                and checks["chunkCount"] == checks["chunkFtsCount"] == checks["vectorCount"]
                and checks["finalReceiptFieldCount"] == len(FINAL_RECEIPT_FIELDS)
                and checks["promptInjectionSignals"] == 0
                and checks["retrievalAgentsWithProvenance"] == 4
                and checks["retrievalAllRowsValid"]
            )
            if not ok:
                raise BrainActionError("human-canary-pre-forget-assertion-failed")
        except Exception as exc:
            return {"ok": False, **checks, "errorClass": safe_error_class(exc)}
        return {"ok": True, **checks, "errorClass": ""}

    def human_canary_status(
        self,
        work_id: str,
        *,
        stage: str = "journal",
        retrieval_query: str = "",
    ) -> dict[str, Any]:
        if stage not in {"journal", "pre-forget", "post-forget"}:
            raise BrainActionError("human-canary-status-stage-invalid")
        journal = self._human_canary(work_id)
        result = journal.status()
        result["stage"] = stage
        if stage == "pre-forget":
            missing = set(result.get("missingClasses") or [])
            assertions = self._human_canary_pre_forget_assertions(
                work_id, retrieval_query=retrieval_query,
            )
            result["preForget"] = assertions
            result["ok"] = bool(
                result.get("state") == "active"
                and result.get("privacyPath") is True
                and int(result.get("invalidTargets") or 0) == 0
                and missing.issubset(set(HUMAN_CANARY_FORGET_CLASSES))
                and int(result.get("targetCount") or 0) == (
                    len(HUMAN_CANARY_BASE_CLASSES) + len(HUMAN_CANARY_PRIVACY_CLASSES)
                )
                and assertions.get("ok")
            )
        elif stage == "post-forget":
            if result.get("state") == "complete":
                source_search_zero = 0
                try:
                    query = clean_text(retrieval_query, 800)
                    if not query:
                        raise BrainActionError("human-canary-retrieval-query-required")
                    self._verify_forget_cleanup(work_id)
                    self._verify_binding_scrub(work_id)
                    store = self._store()
                    for agent in ("josh2", "jaimes", "jain", "joshex"):
                        search = store.search_source(query=query, agent=agent, limit=10)
                        source_search_zero += int(int(search.get("count") or 0) == 0)
                    complete_ok = bool(
                        result.get("postForgetVerified") and source_search_zero == 4
                    )
                except Exception:
                    complete_ok = False
                assertions = {
                    "ok": complete_ok,
                    "receiptVerified": bool(result.get("postForgetVerified")),
                    "sourceSearchAgentsZero": source_search_zero,
                    "errorClass": "" if complete_ok else "human-canary-post-forget-unverified",
                }
            else:
                assertions = self._human_canary_post_forget_assertions(
                    work_id, retrieval_query=retrieval_query,
                )
            result["postForget"] = assertions
            result["telegramCleanupPending"] = bool(
                result.get("state") == "ready" and int(result.get("unresolved") or 0) > 0
            )
            result["ok"] = bool(
                result.get("ok")
                and result.get("state") in {"ready", "complete"}
                and result.get("privacyPath") is True
                and int(result.get("targetCount") or 0) == len(HUMAN_CANARY_DELETE_ORDER)
                and (
                    result.get("state") == "ready"
                    or int(result.get("unresolved") or 0) == 0
                )
                and assertions.get("ok")
            )
        return result

    def _human_canary_post_forget_assertions(
        self,
        work_id: str,
        *,
        retrieval_query: str,
    ) -> dict[str, Any]:
        """Recheck private Forget and binding cleanup using counts/booleans only."""
        try:
            query = clean_text(retrieval_query, 800)
            if not query:
                raise BrainActionError("human-canary-retrieval-query-required")
            self._verify_forget_cleanup(work_id)
            self._verify_binding_scrub(work_id)
            work_hash = hashlib.sha256(work_id.encode()).hexdigest()
            journal = self._human_canary(work_id)
            with journal.connect() as canary_db:
                evidence = canary_db.execute(
                    """SELECT kind,private_value,expected_digest,expected_ref_count,
                              expected_work_ref_count,require_path_absent
                         FROM cleanup_evidence ORDER BY id""",
                ).fetchall()
            non_artifact_path_rows = [
                row for row in evidence
                if str(row["kind"]) in {"extraction_path", "download_path"}
            ]
            artifact_rows = [row for row in evidence if str(row["kind"]) == "artifact_path"]
            chunk_refs = [
                str(row["private_value"]) for row in evidence if str(row["kind"]) == "chunk_ref"
            ]
            non_artifact_paths_absent = bool(
                non_artifact_path_rows
                and all(
                    not Path(str(row["private_value"])).exists()
                    and not Path(str(row["private_value"])).is_symlink()
                    for row in non_artifact_path_rows
                )
            )
            store = self._store()
            shared_artifacts_retained = 0
            artifact_cleanup_verified = bool(artifact_rows)
            with store.connect() as db:
                source_receipts = int(db.execute(
                    "SELECT COUNT(*) FROM deletion_receipts WHERE work_id_hash=?",
                    (work_hash,),
                ).fetchone()[0])
                submission = db.execute(
                    """SELECT phase,cancel_requested,caption_present,caption_private,
                              objective_private,media_group_ref,source_private_json
                         FROM submissions WHERE work_id=?""",
                    (work_id,),
                ).fetchone()
                vector_remnants = 0
                if chunk_refs:
                    placeholders = ",".join("?" for _ in chunk_refs)
                    vector_remnants = int(db.execute(
                        f"SELECT COUNT(*) FROM source_vectors WHERE chunk_id IN ({placeholders})",
                        chunk_refs,
                    ).fetchone()[0])
                orphan_vectors = int(db.execute(
                    """SELECT COUNT(*) FROM source_vectors v
                         LEFT JOIN source_chunks c ON c.id=v.chunk_id WHERE c.id IS NULL""",
                ).fetchone()[0])
                for row in artifact_rows:
                    digest = str(row["expected_digest"])
                    private_path = Path(str(row["private_value"]))
                    association_count = int(db.execute(
                        """SELECT COUNT(*) FROM submission_artifacts
                             WHERE work_id=? AND digest=?""",
                        (work_id, digest),
                    ).fetchone()[0])
                    artifact = db.execute(
                        "SELECT stored_path,ref_count FROM artifacts WHERE digest=?",
                        (digest,),
                    ).fetchone()
                    if bool(row["require_path_absent"]):
                        verified = bool(
                            association_count == 0
                            and artifact is None
                            and not private_path.exists()
                            and not private_path.is_symlink()
                        )
                    else:
                        expected_remaining = (
                            int(row["expected_ref_count"])
                            - int(row["expected_work_ref_count"])
                        )
                        total_associations = int(db.execute(
                            "SELECT COUNT(*) FROM submission_artifacts WHERE digest=?",
                            (digest,),
                        ).fetchone()[0])
                        verified = bool(
                            association_count == 0
                            and artifact
                            and expected_remaining >= 1
                            and int(artifact["ref_count"]) == expected_remaining
                            and total_associations == expected_remaining
                            and hmac.compare_digest(
                                str(artifact["stored_path"]), str(private_path),
                            )
                        )
                        if verified:
                            try:
                                store._verified_private_artifact(private_path, digest)
                            except Exception:
                                verified = False
                        shared_artifacts_retained += int(verified)
                    artifact_cleanup_verified = bool(artifact_cleanup_verified and verified)
            private_path_cleanup_verified = bool(
                non_artifact_paths_absent and artifact_cleanup_verified
            )
            private_paths_absent = bool(
                non_artifact_paths_absent
                and all(
                    not Path(str(row["private_value"])).exists()
                    and not Path(str(row["private_value"])).is_symlink()
                    for row in artifact_rows
                )
            )
            with self.connect() as db:
                action_receipts = int(db.execute(
                    "SELECT COUNT(*) FROM deletion_receipts WHERE work_id_hash=?",
                    (work_hash,),
                ).fetchone()[0])
            if not submission:
                raise BrainActionError("human-canary-post-forget-source-missing")
            submission_scrubbed = bool(
                submission["phase"] == "forgotten"
                and bool(submission["cancel_requested"])
                and not bool(submission["caption_present"])
                and all(str(submission[key]) in {"", "{}"} for key in (
                    "caption_private", "objective_private", "media_group_ref", "source_private_json",
                ))
            )
            if (
                not submission_scrubbed
                or source_receipts != 1
                or action_receipts != 1
                or not private_path_cleanup_verified
                or not chunk_refs
                or vector_remnants
                or orphan_vectors
            ):
                raise BrainActionError("human-canary-post-forget-receipt-invalid")
            import memory_registry
            registry = memory_registry.connect()
            try:
                source_ref = f"brain-source:{work_id}"
                record_rows = registry.execute(
                    """SELECT owner,visibility,privacy,status,subject,predicate,object_text,
                              COALESCE(evidence,'') AS evidence
                         FROM memory_records WHERE source_path=? OR source_ref=?""",
                    (source_ref, source_ref),
                ).fetchall()
                candidate_rows = registry.execute(
                    """SELECT owner,visibility,privacy,status,source_state,subject,predicate,
                              object_text,COALESCE(evidence,'') AS evidence
                         FROM memory_candidates WHERE source_path=? OR source_ref=?""",
                    (source_ref, source_ref),
                ).fetchall()
                agent_hits: dict[str, int] = {}
                for agent in ("josh2", "jaimes", "jain", "joshex"):
                    active_records = sum(
                        1 for row in record_rows
                        if memory_registry.visibility_allowed(
                            agent, str(row["visibility"]), str(row["privacy"]), str(row["owner"]),
                        )
                        and (
                            str(row["status"]) != "forgotten"
                            or any(str(row[key]) for key in ("subject", "predicate", "object_text", "evidence"))
                        )
                    )
                    active_candidates = sum(
                        1 for row in candidate_rows
                        if memory_registry.visibility_allowed(
                            agent, str(row["visibility"]), str(row["privacy"]), str(row["owner"]),
                        )
                        and (
                            str(row["status"]) != "forgotten"
                            or str(row["source_state"]) != "forgotten"
                            or any(str(row[key]) for key in ("subject", "predicate", "object_text", "evidence"))
                        )
                    )
                    agent_hits[agent] = active_records + active_candidates
            finally:
                registry.close()
            retrieval_zero = all(value == 0 for value in agent_hits.values())
            store = self._store()
            source_search_zero = 0
            for agent in ("josh2", "jaimes", "jain", "joshex"):
                result = store.search_source(query=query, agent=agent, limit=10)
                source_search_zero += int(int(result.get("count") or 0) == 0)
            if not retrieval_zero or source_search_zero != 4:
                raise BrainActionError("human-canary-retrieval-remnants")
        except Exception as exc:
            return {
                "ok": False,
                "submissionScrubbed": False,
                "sourceDeletionReceiptCount": 0,
                "actionDeletionReceiptCount": 0,
                "bindingsScrubbed": False,
                "privatePathsAbsent": False,
                "privatePathCleanupVerified": False,
                "retainedSharedArtifactCount": 0,
                "vectorRemnants": -1,
                "orphanVectorCount": -1,
                "retrievalAgentCount": 4,
                "retrievalAgentsZero": 0,
                "sourceSearchAgentsZero": 0,
                "errorClass": safe_error_class(exc),
            }
        return {
            "ok": True,
            "submissionScrubbed": True,
            "sourceDeletionReceiptCount": source_receipts,
            "actionDeletionReceiptCount": action_receipts,
            "bindingsScrubbed": True,
            "privatePathsAbsent": private_paths_absent,
            "privatePathCleanupVerified": True,
            "retainedSharedArtifactCount": shared_artifacts_retained,
            "vectorRemnants": 0,
            "orphanVectorCount": 0,
            "retrievalAgentCount": 4,
            "retrievalAgentsZero": 4,
            "sourceSearchAgentsZero": 4,
            "errorClass": "",
        }

    def cleanup_human_canary_telegram(
        self,
        work_id: str,
        *,
        max_attempts: int = 3,
        retrieval_query: str = "",
    ) -> dict[str, Any]:
        permission = self.human_canary_preflight()
        if not permission.get("ok") or not permission.get("canDeleteMessages"):
            raise BrainActionError("human-canary-delete-permission-missing")
        # This mutating recovery remains behind the same explicit production
        # confirmation as Telegram deletion.  Status commands are read-only.
        self._recover_human_canary_bindings(work_id)
        journal = self._human_canary(work_id)
        status = self.human_canary_status(
            work_id, stage="post-forget", retrieval_query=retrieval_query,
        )
        if status.get("state") not in {"ready", "complete"} or not status.get("ok"):
            raise BrainActionError("human-canary-post-forget-not-ready")
        if status.get("state") == "ready":
            journal.mark_post_forget_verified()
        result = journal.cleanup(self.transport, max_attempts=max_attempts)
        return {
            **result,
            "deletePermissionVerified": True,
            "journalRemoved": not journal.db_path.exists(),
            "receiptPresent": journal.receipt_path.is_file(),
        }

    def _route_guard(self, envelope: Mapping[str, Any]) -> tuple[bool, bool, str, str, str]:
        # Resolve the private route first.  Tracked configuration and the
        # authorized-sender receipt are Brain-only dependencies; loading them
        # before the route comparison would let an unavailable Brain control
        # file consume otherwise unrelated Telegram traffic.
        expected_chat, expected_topic = private_brain_topic_receipt(self.topic_receipt_path)
        chat_ref = clean_text(envelope.get("chatId"), 80)
        topic_ref = clean_text(envelope.get("threadId"), 80)
        sender_ref = clean_text(envelope.get("senderId"), 120)
        on_brain = bool(
            hmac.compare_digest(chat_ref, expected_chat)
            and hmac.compare_digest(topic_ref, expected_topic)
        )
        if not on_brain:
            return False, False, expected_chat, expected_topic, ""

        config = load_json(self.config_path)
        configured_chat, configured_topic = resolved_brain_topic(
            config,
            self.topic_receipt_path,
        )
        if not (
            hmac.compare_digest(configured_chat, expected_chat)
            and hmac.compare_digest(configured_topic, expected_topic)
        ):
            raise BrainConfigurationError("brain-action-route-binding-mismatch")
        authorized_sender = resolved_authorized_sender(
            self.authorized_sender_receipt_path,
            chat_id=expected_chat,
            topic_id=expected_topic,
        )
        authorized = bool(
            envelope.get("senderIsBot") is False
            and sender_ref
            and hmac.compare_digest(sender_ref, authorized_sender)
        )
        return on_brain, authorized, expected_chat, expected_topic, authorized_sender

    def _mapping(self, chat_ref: str, topic_ref: str, reply_ref: str) -> dict[str, str] | None:
        if self.db_path.exists():
            try:
                with contextlib.closing(self._readonly(self.db_path)) as db:
                    row = db.execute(
                        """SELECT work_id,mapping_kind,action_ref FROM message_mappings
                             WHERE chat_ref=? AND topic_ref=? AND message_ref=?""",
                        (chat_ref, topic_ref, reply_ref),
                    ).fetchone()
                if row:
                    return {
                        "workId": str(row["work_id"]),
                        "kind": str(row["mapping_kind"]),
                        "actionRef": str(row["action_ref"]),
                    }
            except (OSError, sqlite3.Error):
                raise BrainActionError("action-mapping-unreadable")
        if not self.dispatcher_db.exists():
            return None
        try:
            with contextlib.closing(self._readonly(self.dispatcher_db)) as db:
                rows = db.execute(
                    """SELECT work_id,source_message_ref,card_message_ref,final_message_ref FROM surfaces
                         WHERE chat_ref=? AND topic_ref=?
                           AND (source_message_ref=? OR card_message_ref=? OR final_message_ref=?)""",
                    (chat_ref, topic_ref, reply_ref, reply_ref, reply_ref),
                ).fetchall()
        except (OSError, sqlite3.Error) as exc:
            raise BrainActionError("surface-mapping-unreadable") from exc
        if not rows:
            return None
        if len(rows) != 1:
            return {"workId": "", "kind": "ambiguous", "actionRef": ""}
        row = rows[0]
        if hmac.compare_digest(str(row["final_message_ref"]), reply_ref):
            kind = "final"
        elif hmac.compare_digest(str(row["card_message_ref"]), reply_ref):
            kind = "card"
        else:
            kind = "source"
        return {"workId": str(row["work_id"]), "kind": kind, "actionRef": ""}

    @staticmethod
    def _parse_action(text: str) -> tuple[str, dict[str, str]] | None:
        normalized = clean_text(text, 2000)
        if normalized.casefold() in {"/cancel", "cancel this brain intake"}:
            return "cancel", {}
        if normalized.casefold() == "reference only":
            return "reference-only", {}
        if normalized.casefold() == "forget":
            return "forget-preview", {}
        correction = re.fullmatch(
            r"Correct:\s*([^|]{1,240})\s*\|\s*([^|]{1,120})\s*\|\s*(.{1,1200})",
            normalized,
            flags=re.IGNORECASE,
        )
        if correction:
            return "correct", {
                "subject": correction.group(1).strip(),
                "predicate": correction.group(2).strip(),
                "value": correction.group(3).strip(),
            }
        approve = re.fullmatch(r"Approve candidate:\s*([A-Za-z0-9_.:-]{1,128})", normalized, re.IGNORECASE)
        if approve:
            return "approve-memory", {"candidateId": approve.group(1)}
        reject = re.fullmatch(
            r"Reject candidate:\s*([A-Za-z0-9_.:-]{1,128})(?:\s*\|\s*(incorrect|unsupported|outdated))?",
            normalized,
            re.IGNORECASE,
        )
        if reject:
            return "reject-memory", {
                "candidateId": reject.group(1),
                "reason": (reject.group(2) or "incorrect").lower(),
            }
        supersede = re.fullmatch(
            r"Supersede memory:\s*([A-Za-z0-9_.:-]{1,128})\s*\|\s*with candidate:\s*([A-Za-z0-9_.:-]{1,128})",
            normalized,
            re.IGNORECASE,
        )
        if supersede:
            return "supersede-memory", {
                "obsoleteMemoryId": supersede.group(1),
                "candidateId": supersede.group(2),
            }
        privacy = re.fullmatch(
            r"Privacy:\s*(private|internal|dashboard-safe)",
            normalized,
            re.IGNORECASE,
        )
        if privacy:
            return "privacy", {"privacy": privacy.group(1).lower()}
        return None

    @staticmethod
    def _looks_like_action(text: str) -> bool:
        normalized = clean_text(text, 240).casefold()
        return normalized.startswith((
            "/cancel", "cancel this brain intake", "correct:",
            "reference only", "approve candidate:", "reject candidate:",
            "supersede memory:", "forget", "confirm forget",
            "privacy:", "confirm privacy",
        ))

    def _recover_duplicate(self, event_key: str) -> str:
        """Resume only a provable pre-effect reservation; fence everything else."""
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM inbound_events WHERE event_key=?", (event_key,),
            ).fetchone()
            if not row:
                return "indeterminate"
            pending = db.execute(
                "SELECT state,lifecycle_token,action_ref FROM pending_actions WHERE action_ref=?",
                (row["action_ref"],),
            ).fetchone()
            if not pending:
                pending = db.execute(
                    """SELECT p.state,p.lifecycle_token,p.action_ref
                         FROM message_mappings m JOIN pending_actions p ON p.action_ref=m.action_ref
                         WHERE m.work_id=? AND m.message_ref=?
                           AND m.mapping_kind IN ('forget-preview','privacy-preview')""",
                    (row["work_id"], row["reply_message_ref"]),
                ).fetchone()
            response_plan = db.execute(
                "SELECT state FROM pending_responses WHERE event_key=?", (event_key,),
            ).fetchone()
        state = str(row["state"])
        if state == "reserved":
            if not pending:
                return "resume"
            pending_state = str(pending["state"])
            pending_action = str(row["action"])
            if pending_action in {"forget-confirm", "privacy-confirm"} and pending_state == "pending":
                return "resume"
            if pending_state == "prepared" and not str(pending["lifecycle_token"] or ""):
                return "resume"
        if state == "deferred":
            return "resume"
        if state == "response_pending":
            return "response_pending"
        if state == "executed" and response_plan:
            self._update_inbound(
                event_key,
                "response_pending",
                error_class="response-resume-pending",
            )
            return "response_pending"
        if state in {"executing", "executed", "responding", "reserved"}:
            effect_key = str(row["response_effect_key"] or "")
            if state == "responding" and effect_key:
                with self.connect() as db, self.transaction(db):
                    db.execute(
                        """UPDATE response_attempts SET stage='indeterminate',
                                  error_class='telegram-result-unknown',updated_at=?
                             WHERE effect_key=? AND stage='attempting'""",
                        (utc_now(), effect_key),
                    )
                try:
                    self._gateway().finish_effect(
                        effect_key,
                        state="indeterminate",
                        error_class="telegram-result-unknown",
                    )
                except LifecycleError:
                    pass
            self._update_inbound(
                event_key,
                "indeterminate",
                error_class=(
                    "telegram-result-unknown" if state == "responding"
                    else "action-result-unknown"
                ),
            )
            action = str(row["action"])
            if action in SAFE_ACTIONS:
                self._enqueue_control(
                    str(row["work_id"]), str(row["action_ref"]), action, "indeterminate",
                )
            return "indeterminate"
        return state

    def _reserve_inbound(
        self,
        *,
        event_key: str,
        message_ref: str,
        reply_ref: str,
        work_id: str,
        action_ref: str,
        action: str,
    ) -> bool:
        self._ensure_schema()
        now = utc_now()
        with self.connect() as db, self.transaction(db):
            changed = db.execute(
                """INSERT OR IGNORE INTO inbound_events(
                     event_key,telegram_message_ref,reply_message_ref,work_id,
                     action_ref,action,state,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,'reserved',?,?)""",
                (event_key, message_ref, reply_ref, work_id, action_ref, action, now, now),
            ).rowcount
        return changed == 1

    def _update_inbound(self, event_key: str, state: str, **values: Any) -> None:
        if state not in INBOUND_STATES:
            raise BrainActionError("action-state-invalid")
        allowed = {"response_effect_key", "response_message_ref", "error_class"}
        if not set(values).issubset(allowed):
            raise BrainActionError("action-state-update-invalid")
        fields = ["state=?"] + [f"{key}=?" for key in values] + ["updated_at=?"]
        params = [state] + [clean_text(values[key], 180) for key in values] + [utc_now(), event_key]
        with self.connect() as db, self.transaction(db):
            changed = db.execute(
                f"UPDATE inbound_events SET {','.join(fields)} WHERE event_key=?",
                params,
            ).rowcount
        if changed != 1:
            raise BrainActionError("action-event-missing")

    def _create_pending(
        self,
        *,
        action_ref: str,
        work_id: str,
        action: str,
        payload: Mapping[str, Any],
    ) -> None:
        now = utc_now()
        expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ACTION_TTL_SECONDS)).replace(
            microsecond=0,
        ).isoformat().replace("+00:00", "Z")
        with self.connect() as db, self.transaction(db):
            db.execute(
                """INSERT OR IGNORE INTO pending_actions(
                     action_ref,work_id,action,payload_json,state,expires_at,created_at,updated_at
                   ) VALUES(?,?,?,?, 'prepared',?,?,?)""",
                (
                    action_ref, work_id, action,
                    json.dumps(dict(payload), sort_keys=True, separators=(",", ":")),
                    expires, now, now,
                ),
            )
            existing = db.execute(
                "SELECT work_id,action,payload_json,state FROM pending_actions WHERE action_ref=?",
                (action_ref,),
            ).fetchone()
        expected_payload = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
        if (
            not existing
            or str(existing["work_id"]) != work_id
            or str(existing["action"]) != action
            or str(existing["payload_json"]) != expected_payload
            or str(existing["state"]) != "prepared"
        ):
            raise BrainActionError("pending-action-conflict")

    def _set_pending(self, action_ref: str, state: str, **values: Any) -> None:
        if state not in PENDING_STATES:
            raise BrainActionError("pending-action-state-invalid")
        allowed = {
            "payload_json", "lifecycle_token", "brain_token",
            "preview_message_ref", "expires_at",
        }
        if not set(values).issubset(allowed):
            raise BrainActionError("pending-action-update-invalid")
        fields = ["state=?"] + [f"{key}=?" for key in values] + ["updated_at=?"]
        params = [state] + [str(values[key]) for key in values] + [utc_now(), action_ref]
        with self.connect() as db, self.transaction(db):
            changed = db.execute(
                f"UPDATE pending_actions SET {','.join(fields)} WHERE action_ref=?",
                params,
            ).rowcount
        if changed != 1:
            raise BrainActionError("pending-action-missing")

    def _bound_work_receipt(self, work_id: str) -> dict[str, Any]:
        receipt = self._gateway().read_work(work_id)
        if (
            not receipt
            or receipt.get("surfaceContract") != "brain-intake"
            or receipt.get("currentOwner") != "josh2"
        ):
            raise BrainActionError("action-lifecycle-binding-invalid")
        return receipt

    def _work_receipt(self, work_id: str) -> dict[str, Any]:
        receipt = self._bound_work_receipt(work_id)
        if not receipt.get("writerEnabled"):
            raise LifecycleError("lifecycle-writer-disabled")
        return receipt

    def _writer_ready(self, work_id: str) -> bool:
        try:
            return bool(self._bound_work_receipt(work_id).get("writerEnabled"))
        except (BrainActionError, LifecycleError):
            return False

    def _queue_response(
        self,
        *,
        action_ref: str,
        event_key: str,
        work_id: str,
        authorized_user: str,
        chat_ref: str,
        topic_ref: str,
        reply_ref: str,
        response_kind: str,
        text: str,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        if response_kind not in {
            "ordinary", "forget-preview", "forget-final",
            "privacy-preview", "privacy-final",
        }:
            raise BrainActionError("action-response-kind-invalid")
        bounded_text = str(text or "")[:4000]
        if not bounded_text:
            raise BrainActionError("action-response-empty")
        encoded = json.dumps(dict(result or {}), sort_keys=True, separators=(",", ":"))
        now = utc_now()
        with self.connect() as db, self.transaction(db):
            db.execute(
                """INSERT OR IGNORE INTO pending_responses(
                     action_ref,event_key,work_id,authorized_user,chat_ref,topic_ref,
                     reply_ref,response_kind,text_private,result_private_json,state,
                     created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                (
                    action_ref, event_key, work_id, authorized_user, chat_ref,
                    topic_ref, reply_ref, response_kind, bounded_text, encoded,
                    now, now,
                ),
            )
            existing = db.execute(
                "SELECT * FROM pending_responses WHERE action_ref=?", (action_ref,),
            ).fetchone()
        if not existing or any(
            str(existing[key]) != value
            for key, value in {
                "event_key": event_key,
                "work_id": work_id,
                "authorized_user": authorized_user,
                "chat_ref": chat_ref,
                "topic_ref": topic_ref,
                "reply_ref": reply_ref,
                "response_kind": response_kind,
                "text_private": bounded_text,
                "result_private_json": encoded,
            }.items()
        ):
            raise BrainActionError("action-response-plan-conflict")

    def _mint_and_consume(
        self,
        *,
        work_id: str,
        action_ref: str,
        action: str,
        authorized_user: str,
        chat_ref: str,
        topic_ref: str,
        message_ref: str,
    ) -> None:
        gateway = self._gateway()
        receipt = self._work_receipt(work_id)
        token = gateway.create_action(
            work_id=work_id,
            lifecycle_revision=int(receipt["sequence"]),
            authorized_user=authorized_user,
            chat_ref=chat_ref,
            topic_ref=topic_ref,
            message_ref=message_ref,
            artifact_ref=action_ref,
            action=action,
            ttl_seconds=ACTION_TTL_SECONDS,
        )
        self._set_pending(action_ref, "prepared", lifecycle_token=token)
        consumed = gateway.consume_action(
            token,
            authorized_user=authorized_user,
            chat_ref=chat_ref,
            topic_ref=topic_ref,
            message_ref=message_ref,
            artifact_ref=action_ref,
        )
        if consumed.get("action") != action or consumed.get("workId") != work_id:
            raise BrainActionError("action-consume-conflict")
        self._set_pending(action_ref, "consuming", lifecycle_token="")

    def _enqueue_control(self, work_id: str, action_ref: str, action: str, stage: str) -> None:
        if stage not in ACTION_STAGE_SPECS or action not in SAFE_ACTIONS:
            raise BrainActionError("action-visibility-invalid")
        receipt = self._work_receipt(work_id)
        run_id = clean_text(receipt.get("runId"), 160)
        phase = clean_text(receipt.get("phase"), 40)
        delivery_state = clean_text(receipt.get("deliveryState"), 40)
        outcome = clean_text(receipt.get("outcome"), 40)
        if phase == "terminal":
            work_event = "terminal"
            terminal_status = (
                "error"
                if delivery_state in {"indeterminate", "dead_letter"}
                or outcome in {"failed", "expired"}
                else "done"
            )
        else:
            work_event = "heartbeat"
            terminal_status = ACTION_STAGE_SPECS[stage]["status"]
        route_verified, route_class = self._route_evidence(work_id)
        event_id = stable_ref("brain-action-event", work_id, action_ref, stage)
        origin_hash = hashlib.sha256(f"brain-action|{work_id}|{run_id}".encode()).hexdigest()
        now = utc_now()
        with self.connect() as db, self.transaction(db):
            db.execute(
                """INSERT OR IGNORE INTO control_outbox(
                     event_id,work_id,run_id,origin_claim_hash,action_class,
                     stage,phase,work_event,terminal_status,route_verified,route_class,
                     state,available_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)""",
                (
                    event_id, work_id, run_id, origin_hash, action, stage, phase,
                    work_event, terminal_status, int(route_verified), route_class,
                    now, now, now,
                ),
            )
        self.drain_outbox(max_events=4)

    def _route_evidence(self, work_id: str) -> tuple[bool, str]:
        if not self.dispatcher_db.exists():
            return False, ""
        try:
            with contextlib.closing(self._readonly(self.dispatcher_db)) as db:
                row = db.execute(
                    """SELECT route_verified,route_class FROM visibility_outbox
                         WHERE lifecycle_work_id=? AND route_verified=1
                         ORDER BY CASE stage
                           WHEN 'delivered' THEN 0 WHEN 'terminal_committed' THEN 1
                           WHEN 'verifying' THEN 2 ELSE 3 END
                         LIMIT 1""",
                    (work_id,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            return False, ""
        route_class = clean_text(row["route_class"], 40) if row else ""
        if route_class not in {"local-none", "local-deterministic", "local-tool", "mixed-local"}:
            return False, ""
        return True, route_class

    def drain_outbox(self, *, max_events: int = 16) -> dict[str, Any]:
        if not self.db_path.exists():
            return {"ok": True, "counts": {"accepted": 0, "pending": 0, "deadLetter": 0}}
        accepted = dead = 0
        for _ in range(max(1, min(int(max_events), 64))):
            now = utc_now()
            with self.connect() as db, self.transaction(db):
                row = db.execute(
                    """SELECT * FROM control_outbox
                         WHERE state IN ('pending','sending') AND available_at<=?
                         ORDER BY created_at,event_id LIMIT 1""",
                    (now,),
                ).fetchone()
                if not row:
                    break
                changed = db.execute(
                    """UPDATE control_outbox SET state='sending',attempts=attempts+1,updated_at=?
                         WHERE event_id=? AND state IN ('pending','sending')""",
                    (now, row["event_id"]),
                ).rowcount
                if changed != 1:
                    continue
                attempt = int(row["attempts"]) + 1
            event = {
                "eventId": str(row["event_id"]),
                "workId": str(row["work_id"]),
                "runId": str(row["run_id"]),
                "originClaimHash": str(row["origin_claim_hash"]),
                "actionClass": str(row["action_class"]),
                "stage": str(row["stage"]),
                "phase": str(row["phase"]),
                "workEvent": str(row["work_event"]),
                "status": str(row["terminal_status"]),
                "routeVerified": bool(row["route_verified"]),
                "routeClass": str(row["route_class"]),
            }
            try:
                published = bool(self.action_publisher(event))
            except Exception:
                published = False
            with self.connect() as db, self.transaction(db):
                if published:
                    db.execute(
                        "UPDATE control_outbox SET state='accepted',error_class='',updated_at=? WHERE event_id=?",
                        (utc_now(), row["event_id"]),
                    )
                    accepted += 1
                else:
                    exhausted = attempt >= OUTBOX_MAX_ATTEMPTS
                    db.execute(
                        """UPDATE control_outbox SET state=?,available_at=?,error_class=?,updated_at=?
                             WHERE event_id=?""",
                        (
                            "dead_letter" if exhausted else "pending",
                            utc_after(min(60, 2 ** min(attempt, 6))),
                            "control-tower-unavailable", utc_now(), row["event_id"],
                        ),
                    )
                    dead += int(exhausted)
        stale_before = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.connect() as db:
            pending = int(db.execute(
                "SELECT COUNT(*) FROM control_outbox WHERE state IN ('pending','sending')",
            ).fetchone()[0])
            indeterminate = int(db.execute(
                "SELECT COUNT(*) FROM inbound_events WHERE state='indeterminate'",
            ).fetchone()[0])
            dead_total = int(db.execute(
                """SELECT
                     (SELECT COUNT(*) FROM inbound_events WHERE state='dead_letter') +
                     (SELECT COUNT(*) FROM control_outbox WHERE state='dead_letter')""",
            ).fetchone()[0])
            aged = int(db.execute(
                """SELECT COUNT(*) FROM control_outbox
                     WHERE state IN ('pending','sending') AND created_at<?""",
                (stale_before,),
            ).fetchone()[0])
        return {
            "ok": indeterminate == 0 and dead_total == 0 and aged == 0,
            "counts": {
                "accepted": accepted, "pending": pending,
                "deadLetter": dead_total, "indeterminate": indeterminate,
                "agedOutbox": aged,
            },
        }

    def _apply_action(
        self,
        *,
        work_id: str,
        action: str,
        payload: Mapping[str, str],
        authorized_user: str,
    ) -> tuple[dict[str, Any], str]:
        store = self._store()
        if action == "correct":
            result = store.correct(
                work_id,
                subject=payload["subject"], predicate=payload["predicate"],
                value=payload["value"], authorized_user=authorized_user,
                privacy="private",
            )
            return result, "Correction recorded for governed review."
        if action == "reference-only":
            result = store.mark_reference_only(work_id, authorized_user=authorized_user)
            return result, "Source set to reference-only; governed promotion is blocked."
        if action == "approve-memory":
            result = store.approve_candidate(
                work_id, candidate_id=payload["candidateId"], authorized_user=authorized_user,
            )
            return result, "Eligible candidate approved."
        if action == "reject-memory":
            result = store.reject_candidate(
                work_id, candidate_id=payload["candidateId"],
                authorized_user=authorized_user, reason=payload["reason"],
            )
            return result, "Candidate rejected."
        if action == "supersede-memory":
            result = store.supersede_memory(
                work_id,
                candidate_id=payload["candidateId"],
                obsolete_memory_id=payload["obsoleteMemoryId"],
                authorized_user=authorized_user,
            )
            return result, "Verified correction superseded the governed memory."
        if action == "cancel":
            result = store.cancel_submission(work_id, authorized_user=authorized_user)
            if result.get("tooLate"):
                return result, "Brain intake was already terminal; no cancellation was applied."
            self._request_cancel_if_open(work_id)
            return result, "Brain intake cancellation accepted. The private source was retained."
        if action == "privacy":
            target = clean_text(payload.get("privacy"), 40)
            if target not in {"private", "internal", "dashboard-safe"}:
                raise BrainActionError("privacy-class-invalid")
            preview = store.privacy_change_preview(
                work_id,
                authorized_user=authorized_user,
                privacy=target,
            )
            if preview.get("confirmationRequired"):
                return preview, (
                    "<b>Privacy change preview</b>\n"
                    f"Source classification: <b>{html.escape(str(preview.get('currentPrivacy') or 'private'))}</b> "
                    f"→ <b>{html.escape(target)}</b>.\n"
                    "Existing governed candidates and memories will not be silently broadened.\n\n"
                    "Reply exactly <b>CONFIRM PRIVACY</b> to this preview within 10 minutes."
                )
            result = store.change_privacy(
                work_id,
                authorized_user=authorized_user,
                privacy=target,
            )
            revoked = max(0, int(result.get("revokedPending") or 0))
            return result, (
                f"Source privacy set to <b>{html.escape(target)}</b>. "
                f"Revoked governed items: {revoked}."
            )
        if action == "forget-preview":
            result = store.forget_preview(work_id, authorized_user=authorized_user)
            impact = result.get("impact") if isinstance(result.get("impact"), dict) else {}
            lines = [
                "<b>Forget impact preview</b>",
                f"• Stored artifacts: {max(0, int(impact.get('artifacts') or 0))}",
                f"• Extractions: {max(0, int(impact.get('extractions') or 0))}",
                f"• Governed candidates: {max(0, int(impact.get('candidates') or 0))}",
                f"• Active memories: {max(0, int(impact.get('activeMemories') or 0))}",
                "",
                "Reply exactly <b>CONFIRM FORGET</b> to this preview within 10 minutes.",
            ]
            return result, "\n".join(lines)
        raise BrainActionError("action-not-supported")

    @staticmethod
    def _response_payload(chat_ref: str, topic_ref: str, reply_ref: str, text: str) -> dict[str, Any]:
        return {
            "chat_id": chat_ref,
            "message_thread_id": int(topic_ref),
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": True,
            "disable_web_page_preview": True,
            "reply_parameters": {
                "message_id": int(reply_ref),
                "allow_sending_without_reply": False,
            },
        }

    def _respond(
        self,
        *,
        event_key: str,
        action_ref: str,
        work_id: str,
        chat_ref: str,
        topic_ref: str,
        reply_ref: str,
        text: str,
    ) -> tuple[str, str]:
        gateway = self._gateway()
        receipt = self._work_receipt(work_id)
        claim = gateway.claim_effect(
            work_id,
            "callback_ack",
            sequence=int(receipt["sequence"]),
            fencing_epoch=int(receipt["fencingEpoch"]),
            scope_ref=action_ref,
        )
        effect_key = clean_text(claim.get("idempotencyKey"), 100)
        if not effect_key:
            raise BrainActionError("action-response-effect-missing")
        with self.connect() as db, self.transaction(db):
            attempt = db.execute(
                "SELECT * FROM response_attempts WHERE effect_key=?", (effect_key,),
            ).fetchone()
            if not attempt:
                if not claim.get("allowed") and claim.get("state") != "sending":
                    return str(claim.get("state") or "dead_letter"), ""
                db.execute(
                    """INSERT INTO response_attempts(
                         effect_key,action_ref,stage,created_at,updated_at
                       ) VALUES(?,?,'reserved',?,?)""",
                    (effect_key, action_ref, utc_now(), utc_now()),
                )
                attempt = db.execute(
                    "SELECT * FROM response_attempts WHERE effect_key=?", (effect_key,),
                ).fetchone()
        stage = str(attempt["stage"])
        if stage == "delivered":
            gateway.finish_effect(effect_key, state="delivered", private_receipt="telegram-confirmed")
            return "delivered", str(attempt["telegram_message_ref"])
        if stage in {"indeterminate", "dead_letter"}:
            gateway.finish_effect(
                effect_key, state=stage, error_class=str(attempt["error_class"]),
            )
            return stage, ""
        if stage == "attempting":
            with self.connect() as db, self.transaction(db):
                db.execute(
                    """UPDATE response_attempts SET stage='indeterminate',
                              error_class='telegram-result-unknown',updated_at=? WHERE effect_key=?""",
                    (utc_now(), effect_key),
                )
            gateway.finish_effect(
                effect_key, state="indeterminate", error_class="telegram-result-unknown",
            )
            return "indeterminate", ""
        with self.connect() as db, self.transaction(db):
            db.execute(
                "UPDATE response_attempts SET stage='attempting',updated_at=? WHERE effect_key=? AND stage='reserved'",
                (utc_now(), effect_key),
            )
        self._update_inbound(event_key, "responding", response_effect_key=effect_key)
        result = self.transport(
            "sendMessage",
            self._response_payload(chat_ref, topic_ref, reply_ref, text),
            TELEGRAM_TIMEOUT_SECONDS,
        )
        state = "delivered" if result.get("ok") else clean_text(result.get("state"), 40) or "indeterminate"
        if state not in {"delivered", "indeterminate", "dead_letter"}:
            state = "indeterminate"
        message_ref = private_message_ref(result)
        if state == "delivered" and not message_ref:
            state = "indeterminate"
        error_class = "" if state == "delivered" else clean_text(
            result.get("errorClass"), 80,
        ) or "telegram-result-unknown"
        with self.connect() as db, self.transaction(db):
            db.execute(
                """UPDATE response_attempts SET stage=?,telegram_message_ref=?,error_class=?,updated_at=?
                     WHERE effect_key=? AND stage='attempting'""",
                (state, message_ref, error_class, utc_now(), effect_key),
            )
        gateway.finish_effect(
            effect_key,
            state=state,
            private_receipt=f"telegram-message:{message_ref}" if state == "delivered" else "",
            error_class=error_class,
        )
        self._update_inbound(
            event_key,
            state,
            response_effect_key=effect_key,
            response_message_ref=message_ref,
            error_class=error_class,
        )
        return state, message_ref

    def _deliver_response_plan(
        self,
        action_ref: str,
        *,
        recover: bool = False,
    ) -> dict[str, Any]:
        with self.connect() as db:
            plan = db.execute(
                "SELECT * FROM pending_responses WHERE action_ref=?", (action_ref,),
            ).fetchone()
            pending = db.execute(
                "SELECT action FROM pending_actions WHERE action_ref=?", (action_ref,),
            ).fetchone()
        if not plan or not pending:
            raise BrainActionError("action-response-plan-missing")
        event_key = str(plan["event_key"])
        work_id = str(plan["work_id"])
        action = str(pending["action"])
        if not self._writer_ready(work_id):
            self._update_inbound(
                event_key,
                "response_pending",
                error_class="brain-writer-disabled",
            )
            return {
                "ok": False,
                "handled": True,
                "silentDrop": True,
                "actionState": "deferred",
                "privacy": {"identifiersIncluded": False, "rawContentIncluded": False},
            }
        recovery_cutoff = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.connect() as db, self.transaction(db):
            current = db.execute(
                "SELECT state,updated_at FROM pending_responses WHERE action_ref=?",
                (action_ref,),
            ).fetchone()
            claimable = bool(
                current
                and (
                    current["state"] == "pending"
                    or (
                        recover
                        and current["state"] == "delivering"
                        and str(current["updated_at"]) <= recovery_cutoff
                    )
                )
            )
            if claimable:
                claimed = db.execute(
                    """UPDATE pending_responses SET state='delivering',updated_at=?
                         WHERE action_ref=? AND state=? AND updated_at=?""",
                    (utc_now(), action_ref, current["state"], current["updated_at"]),
                ).rowcount
            else:
                claimed = 0
        if claimed != 1:
            return {
                "ok": True,
                "handled": True,
                "silentDrop": True,
                "actionState": "response-busy",
                "privacy": {"identifiersIncluded": False, "rawContentIncluded": False},
            }
        response_kind = str(plan["response_kind"])
        try:
            delivery, response_ref = self._respond(
                event_key=event_key,
                action_ref=action_ref,
                work_id=work_id,
                chat_ref=str(plan["chat_ref"]),
                topic_ref=str(plan["topic_ref"]),
                reply_ref=str(plan["reply_ref"]),
                text=str(plan["text_private"]),
            )
            self._record_human_canary_response(
                work_id,
                response_kind=response_kind,
                chat_ref=str(plan["chat_ref"]),
                topic_ref=str(plan["topic_ref"]),
                message_ref=response_ref,
                delivery_state=delivery,
            )
        except LifecycleError as exc:
            if "writer-disabled" not in str(exc):
                raise
            with self.connect() as db, self.transaction(db):
                db.execute(
                    "UPDATE pending_responses SET state='pending',updated_at=? WHERE action_ref=? AND state='delivering'",
                    (utc_now(), action_ref),
                )
            self._update_inbound(
                event_key,
                "response_pending",
                error_class="brain-writer-disabled",
            )
            return {
                "ok": False,
                "handled": True,
                "silentDrop": True,
                "actionState": "deferred",
                "privacy": {"identifiersIncluded": False, "rawContentIncluded": False},
            }
        try:
            result = json.loads(str(plan["result_private_json"]))
        except json.JSONDecodeError as exc:
            raise BrainActionError("action-response-plan-invalid") from exc
        if not isinstance(result, dict):
            raise BrainActionError("action-response-plan-invalid")
        if response_kind == "forget-preview":
            brain_token = str(result.get("confirmationToken") or "")
            impact = result.get("impact") if isinstance(result.get("impact"), dict) else {}
            if delivery == "delivered" and response_ref and brain_token:
                self._arm_forget_confirm(
                    parent_action_ref=action_ref,
                    work_id=work_id,
                    authorized_user=str(plan["authorized_user"]),
                    chat_ref=str(plan["chat_ref"]),
                    topic_ref=str(plan["topic_ref"]),
                    preview_message_ref=response_ref,
                    brain_token=brain_token,
                    impact=impact,
                )
            else:
                self._set_pending(
                    action_ref,
                    "indeterminate" if delivery == "indeterminate" else "dead_letter",
                    brain_token="",
                    payload_json="{}",
                )
        elif response_kind == "privacy-preview":
            brain_token = str(result.get("confirmationToken") or "")
            target = clean_text(result.get("targetPrivacy"), 40)
            if delivery == "delivered" and response_ref and brain_token:
                self._arm_privacy_confirm(
                    parent_action_ref=action_ref,
                    work_id=work_id,
                    authorized_user=str(plan["authorized_user"]),
                    chat_ref=str(plan["chat_ref"]),
                    topic_ref=str(plan["topic_ref"]),
                    preview_message_ref=response_ref,
                    brain_token=brain_token,
                    target_privacy=target,
                )
            else:
                self._set_pending(
                    action_ref,
                    "indeterminate" if delivery == "indeterminate" else "dead_letter",
                    brain_token="",
                    payload_json="{}",
                )
        elif response_kind != "forget-final":
            self._set_pending(
                action_ref,
                "consumed" if delivery == "delivered" else delivery,
                payload_json="{}",
                lifecycle_token="",
                brain_token="",
            )
        if delivery in {"indeterminate", "dead_letter"}:
            self._enqueue_control(work_id, action_ref, action, delivery)
        with self.connect() as db, self.transaction(db):
            db.execute(
                """UPDATE pending_responses SET state=?,text_private='',result_private_json='{}',updated_at=?
                     WHERE action_ref=? AND state='delivering'""",
                (delivery, utc_now(), action_ref),
            )
        if response_kind == "forget-final":
            # Forget reply/action identifiers are needed until the final
            # acknowledgement is delivered or permanently fenced.
            self._scrub_forget_bindings(work_id)
        else:
            with self.connect() as db, self.transaction(db):
                db.execute("DELETE FROM pending_responses WHERE action_ref=?", (action_ref,))
        return {
            "ok": delivery == "delivered",
            "handled": True,
            "silentDrop": False,
            "actionState": delivery,
            "privacy": {"identifiersIncluded": False, "rawContentIncluded": False},
        }

    def _arm_forget_confirm(
        self,
        *,
        parent_action_ref: str,
        work_id: str,
        authorized_user: str,
        chat_ref: str,
        topic_ref: str,
        preview_message_ref: str,
        brain_token: str,
        impact: Mapping[str, Any],
    ) -> None:
        confirm_ref = stable_ref("brain-forget-confirm", parent_action_ref, preview_message_ref)
        with self.connect() as db:
            existing = db.execute(
                "SELECT work_id,action,preview_message_ref FROM pending_actions WHERE action_ref=?",
                (confirm_ref,),
            ).fetchone()
        if existing:
            if (
                str(existing["work_id"]) == work_id
                and str(existing["action"]) == "forget-confirm"
                and str(existing["preview_message_ref"]) == preview_message_ref
            ):
                return
            raise BrainActionError("forget-confirm-binding-conflict")
        receipt = self._work_receipt(work_id)
        lifecycle_token = self._gateway().create_action(
            work_id=work_id,
            lifecycle_revision=int(receipt["sequence"]),
            authorized_user=authorized_user,
            chat_ref=chat_ref,
            topic_ref=topic_ref,
            message_ref=preview_message_ref,
            artifact_ref=confirm_ref,
            action="forget-confirm",
            ttl_seconds=ACTION_TTL_SECONDS,
        )
        now = utc_now()
        expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ACTION_TTL_SECONDS)).replace(
            microsecond=0,
        ).isoformat().replace("+00:00", "Z")
        safe_impact = {
            key: max(0, int(impact.get(key) or 0))
            for key in ("artifacts", "extractions", "candidates", "activeMemories")
        }
        with self.connect() as db, self.transaction(db):
            db.execute(
                """INSERT INTO pending_actions(
                     action_ref,work_id,action,payload_json,lifecycle_token,brain_token,
                     preview_message_ref,state,expires_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,'pending',?,?,?)""",
                (
                    confirm_ref, work_id, "forget-confirm",
                    json.dumps(safe_impact, sort_keys=True, separators=(",", ":")),
                    lifecycle_token, brain_token, preview_message_ref,
                    expires, now, now,
                ),
            )
            db.execute(
                """INSERT INTO message_mappings(
                     chat_ref,topic_ref,message_ref,work_id,mapping_kind,action_ref,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (chat_ref, topic_ref, preview_message_ref, work_id, "forget-preview", confirm_ref, now),
            )
            db.execute(
                """UPDATE pending_actions SET brain_token='',payload_json='{}',state='consumed',updated_at=?
                     WHERE action_ref=?""",
                (now, parent_action_ref),
            )

    def _arm_privacy_confirm(
        self,
        *,
        parent_action_ref: str,
        work_id: str,
        authorized_user: str,
        chat_ref: str,
        topic_ref: str,
        preview_message_ref: str,
        brain_token: str,
        target_privacy: str,
    ) -> None:
        if target_privacy not in {"private", "internal", "dashboard-safe"}:
            raise BrainActionError("privacy-class-invalid")
        confirm_ref = stable_ref("brain-privacy-confirm", parent_action_ref, preview_message_ref)
        with self.connect() as db:
            existing = db.execute(
                "SELECT work_id,action,preview_message_ref,payload_json FROM pending_actions WHERE action_ref=?",
                (confirm_ref,),
            ).fetchone()
        expected_payload = json.dumps(
            {"privacy": target_privacy}, sort_keys=True, separators=(",", ":"),
        )
        if existing:
            if (
                str(existing["work_id"]) == work_id
                and str(existing["action"]) == "privacy-confirm"
                and str(existing["preview_message_ref"]) == preview_message_ref
                and str(existing["payload_json"]) == expected_payload
            ):
                return
            raise BrainActionError("privacy-confirm-binding-conflict")
        receipt = self._work_receipt(work_id)
        lifecycle_token = self._gateway().create_action(
            work_id=work_id,
            lifecycle_revision=int(receipt["sequence"]),
            authorized_user=authorized_user,
            chat_ref=chat_ref,
            topic_ref=topic_ref,
            message_ref=preview_message_ref,
            artifact_ref=confirm_ref,
            action="privacy",
            ttl_seconds=ACTION_TTL_SECONDS,
        )
        now = utc_now()
        expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ACTION_TTL_SECONDS)).replace(
            microsecond=0,
        ).isoformat().replace("+00:00", "Z")
        payload_json = expected_payload
        with self.connect() as db, self.transaction(db):
            db.execute(
                """INSERT OR IGNORE INTO pending_actions(
                     action_ref,work_id,action,payload_json,lifecycle_token,brain_token,
                     preview_message_ref,state,expires_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,'pending',?,?,?)""",
                (
                    confirm_ref, work_id, "privacy-confirm", payload_json,
                    lifecycle_token, brain_token, preview_message_ref, expires, now, now,
                ),
            )
            existing = db.execute(
                "SELECT * FROM pending_actions WHERE action_ref=?", (confirm_ref,),
            ).fetchone()
            if not existing or any(
                str(existing[key]) != value
                for key, value in {
                    "work_id": work_id,
                    "action": "privacy-confirm",
                    "payload_json": payload_json,
                    "preview_message_ref": preview_message_ref,
                }.items()
            ):
                raise BrainActionError("privacy-confirm-binding-conflict")
            db.execute(
                """INSERT OR IGNORE INTO message_mappings(
                     chat_ref,topic_ref,message_ref,work_id,mapping_kind,action_ref,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    chat_ref, topic_ref, preview_message_ref, work_id,
                    "privacy-preview", confirm_ref, now,
                ),
            )
            db.execute(
                """UPDATE pending_actions SET brain_token='',payload_json='{}',state='consumed',updated_at=?
                     WHERE action_ref=?""",
                (now, parent_action_ref),
            )

    def _request_cancel_if_open(self, work_id: str) -> None:
        for _ in range(8):
            gateway = self._gateway()
            receipt = gateway.read_work(work_id)
            if not receipt:
                raise BrainActionError("action-lifecycle-missing")
            if not receipt.get("writerEnabled"):
                raise LifecycleError("lifecycle-writer-disabled")
            if receipt.get("phase") == "terminal" or receipt.get("cancelRequested"):
                return
            try:
                gateway.request_cancel(
                    work_id,
                    expected_sequence=int(receipt["sequence"]),
                    fencing_epoch=int(receipt["fencingEpoch"]),
                )
                return
            except StaleEventError:
                continue
        raise BrainActionError("action-cancel-contention")

    def _verify_forget_cleanup(self, work_id: str) -> None:
        self._verify_forget_cleanup_with_chunks(work_id, ())

    def _verify_forget_cleanup_with_chunks(
        self,
        work_id: str,
        expected_chunk_ids: tuple[str, ...],
    ) -> None:
        store = self._store()
        with store.connect() as db:
            submission = db.execute(
                """SELECT phase,cancel_requested,caption_private,objective_private,
                          media_group_ref,source_private_json
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
            if not submission:
                raise BrainActionError("forget-verification-source-missing")
            counts = {
                "artifacts": int(db.execute(
                    "SELECT COUNT(*) FROM submission_artifacts WHERE work_id=?", (work_id,),
                ).fetchone()[0]),
                "extractions": int(db.execute(
                    "SELECT COUNT(*) FROM extractions WHERE work_id=?", (work_id,),
                ).fetchone()[0]),
                "sourceFts": int(db.execute(
                    "SELECT COUNT(*) FROM source_fts WHERE work_id=?", (work_id,),
                ).fetchone()[0]),
                "chunks": int(db.execute(
                    "SELECT COUNT(*) FROM source_chunks WHERE work_id=?", (work_id,),
                ).fetchone()[0]),
                "chunkFts": int(db.execute(
                    "SELECT COUNT(*) FROM source_chunk_fts WHERE work_id=?", (work_id,),
                ).fetchone()[0]),
                "candidateContent": int(db.execute(
                    """SELECT COUNT(*) FROM candidates WHERE work_id=? AND (
                         status!='forgotten' OR subject!='' OR predicate!='' OR value_private!=''
                         OR provenance_ref!='' OR registry_candidate_id!='' OR registry_memory_id!=''
                       )""",
                    (work_id,),
                ).fetchone()[0]),
                "intentContent": int(db.execute(
                    """SELECT COUNT(*) FROM attachment_intents WHERE work_id=? AND (
                         source_message_ref!='' OR file_ref!='' OR declared_mime!='' OR declared_size!=0
                       )""",
                    (work_id,),
                ).fetchone()[0]),
                "revisionContent": int(db.execute(
                    "SELECT COUNT(*) FROM source_revision_events WHERE work_id=?", (work_id,),
                ).fetchone()[0]),
            }
            if expected_chunk_ids:
                placeholders = ",".join("?" for _ in expected_chunk_ids)
                counts["vectors"] = int(db.execute(
                    f"SELECT COUNT(*) FROM source_vectors WHERE chunk_id IN ({placeholders})",
                    expected_chunk_ids,
                ).fetchone()[0])
        if (
            submission["phase"] != "forgotten"
            or not bool(submission["cancel_requested"])
            or any(str(submission[key]) not in {"", "{}"} for key in (
                "caption_private", "objective_private", "media_group_ref", "source_private_json",
            ))
            or any(counts.values())
        ):
            raise BrainActionError("forget-cleanup-verification-failed")
        try:
            import memory_registry
            registry = memory_registry.connect()
            try:
                source_ref = f"brain-source:{work_id}"
                record_ids = [
                    str(row["id"])
                    for row in registry.execute(
                        "SELECT id FROM memory_records WHERE source_path=? OR source_ref=?",
                        (source_ref, source_ref),
                    ).fetchall()
                ]
                memory_content = int(registry.execute(
                    """SELECT COUNT(*) FROM memory_records
                         WHERE (source_path=? OR source_ref=?) AND (
                           status!='forgotten' OR subject!='' OR predicate!=''
                           OR object_text!='' OR COALESCE(evidence,'')!=''
                         )""",
                    (source_ref, source_ref),
                ).fetchone()[0])
                candidate_content = int(registry.execute(
                    """SELECT COUNT(*) FROM memory_candidates
                         WHERE (source_path=? OR source_ref=?) AND (
                           status!='forgotten' OR source_state!='forgotten'
                           OR subject!='' OR predicate!='' OR object_text!=''
                           OR COALESCE(evidence,'')!=''
                         )""",
                    (source_ref, source_ref),
                ).fetchone()[0])
                fts = 0
                if record_ids:
                    placeholders = ",".join("?" for _ in record_ids)
                    fts = int(registry.execute(
                        f"SELECT COUNT(*) FROM memory_fts WHERE id IN ({placeholders})",
                        record_ids,
                    ).fetchone()[0])
            finally:
                registry.close()
        except (ImportError, sqlite3.Error, AttributeError) as exc:
            raise BrainActionError("forget-registry-verification-failed") from exc
        if memory_content or candidate_content or fts:
            raise BrainActionError("forget-registry-remnants-detected")

    def _scrub_forget_bindings(self, work_id: str) -> None:
        """Remove all reply/action identifiers after the final ack is fenced."""
        human_canary = self._human_canary(work_id)
        if human_canary.exists:
            # The private deletion journal must be complete before the source,
            # surface, action, and final-response bindings disappear.
            human_canary.seal()
            self._human_canary_fault("scrub_after_seal")
        dispatcher_rows = lifecycle_rows = adapter_rows = 0
        if self.dispatcher_db.exists():
            dispatcher = sqlite3.connect(self.dispatcher_db, timeout=15, isolation_level=None)
            dispatcher.row_factory = sqlite3.Row
            try:
                dispatcher.execute("BEGIN IMMEDIATE")
                dispatcher_rows = int(dispatcher.execute(
                    """SELECT
                         (SELECT COUNT(*) FROM surfaces WHERE work_id=?) +
                         (SELECT COUNT(*) FROM attempts WHERE work_id=?)""",
                    (work_id, work_id),
                ).fetchone()[0])
                dispatcher.execute("DELETE FROM attempts WHERE work_id=?", (work_id,))
                dispatcher.execute("DELETE FROM surfaces WHERE work_id=?", (work_id,))
                dispatcher.commit()
            except Exception:
                dispatcher.rollback()
                raise
            finally:
                dispatcher.close()
        self._human_canary_fault("scrub_after_dispatcher_commit")
        gateway = self._gateway()
        with gateway.connect() as lifecycle, gateway.transaction(lifecycle):
            lifecycle_rows = int(lifecycle.execute(
                """SELECT
                     (SELECT COUNT(*) FROM actions WHERE work_id=?) +
                     (SELECT COUNT(*) FROM effects WHERE work_id=? AND kind='callback_ack')""",
                (work_id, work_id),
            ).fetchone()[0])
            lifecycle.execute("DELETE FROM actions WHERE work_id=?", (work_id,))
            lifecycle.execute(
                "DELETE FROM effects WHERE work_id=? AND kind='callback_ack'", (work_id,),
            )
            lifecycle.execute(
                "UPDATE effects SET private_receipt='' WHERE work_id=?", (work_id,),
            )
        self._human_canary_fault("scrub_after_lifecycle_commit")
        with self.connect() as db, self.transaction(db):
            adapter_rows = int(db.execute(
                """SELECT
                     (SELECT COUNT(*) FROM inbound_events WHERE work_id=?) +
                     (SELECT COUNT(*) FROM pending_actions WHERE work_id=?) +
                     (SELECT COUNT(*) FROM message_mappings WHERE work_id=?) +
                     (SELECT COUNT(*) FROM pending_responses WHERE work_id=?) +
                     (SELECT COUNT(*) FROM response_attempts WHERE action_ref IN (
                        SELECT action_ref FROM pending_actions WHERE work_id=?
                        UNION SELECT action_ref FROM inbound_events WHERE work_id=?
                     ))""",
                (work_id, work_id, work_id, work_id, work_id, work_id),
            ).fetchone()[0])
            db.execute(
                """DELETE FROM response_attempts WHERE action_ref IN (
                     SELECT action_ref FROM pending_actions WHERE work_id=?
                     UNION SELECT action_ref FROM inbound_events WHERE work_id=?
                   )""",
                (work_id, work_id),
            )
            db.execute("DELETE FROM pending_responses WHERE work_id=?", (work_id,))
            db.execute("DELETE FROM message_mappings WHERE work_id=?", (work_id,))
            db.execute("DELETE FROM pending_actions WHERE work_id=?", (work_id,))
            db.execute("DELETE FROM inbound_events WHERE work_id=?", (work_id,))
            work_hash = hashlib.sha256(work_id.encode()).hexdigest()
            existing_receipts = int(db.execute(
                "SELECT COUNT(*) FROM deletion_receipts WHERE work_id_hash=?",
                (work_hash,),
            ).fetchone()[0])
            if existing_receipts > 1:
                raise BrainActionError("forget-binding-deletion-receipt-conflict")
            if existing_receipts == 0:
                db.execute(
                    "INSERT INTO deletion_receipts VALUES(?,?,?,?,?,?)",
                    (
                        stable_ref("brain-action-delete", work_id), work_hash,
                        adapter_rows, dispatcher_rows, lifecycle_rows, utc_now(),
                    ),
                )
        self._human_canary_fault("scrub_after_adapter_commit")
        self._verify_binding_scrub(work_id)
        self._verify_binding_deletion_receipt(work_id)
        if human_canary.exists:
            self._human_canary_fault("scrub_before_mark_bindings_scrubbed")
            human_canary.mark_bindings_scrubbed()

    def _verify_binding_deletion_receipt(self, work_id: str) -> None:
        work_hash = hashlib.sha256(work_id.encode()).hexdigest()
        with self.connect() as db:
            count = int(db.execute(
                "SELECT COUNT(*) FROM deletion_receipts WHERE work_id_hash=?",
                (work_hash,),
            ).fetchone()[0])
        if count != 1:
            raise BrainActionError("forget-binding-deletion-receipt-invalid")

    def _verify_binding_scrub(self, work_id: str) -> None:
        with self.connect() as db:
            adapter_remaining = int(db.execute(
                """SELECT
                     (SELECT COUNT(*) FROM inbound_events WHERE work_id=?) +
                     (SELECT COUNT(*) FROM pending_actions WHERE work_id=?) +
                     (SELECT COUNT(*) FROM message_mappings WHERE work_id=?) +
                     (SELECT COUNT(*) FROM pending_responses WHERE work_id=?)""",
                (work_id, work_id, work_id, work_id),
            ).fetchone()[0])
        dispatcher_remaining = 0
        if self.dispatcher_db.exists():
            with contextlib.closing(self._readonly(self.dispatcher_db)) as db:
                dispatcher_remaining = int(db.execute(
                    """SELECT
                         (SELECT COUNT(*) FROM surfaces WHERE work_id=?) +
                         (SELECT COUNT(*) FROM attempts WHERE work_id=?)""",
                    (work_id, work_id),
                ).fetchone()[0])
        with self._gateway().connect() as lifecycle:
            lifecycle_remaining = int(lifecycle.execute(
                """SELECT
                     (SELECT COUNT(*) FROM actions WHERE work_id=?) +
                     (SELECT COUNT(*) FROM effects WHERE work_id=? AND kind='callback_ack') +
                     (SELECT COUNT(*) FROM effects WHERE work_id=? AND private_receipt!='')""",
                (work_id, work_id, work_id),
            ).fetchone()[0])
        if adapter_remaining or dispatcher_remaining or lifecycle_remaining:
            raise BrainActionError("forget-binding-scrub-failed")

    def _execute_immediate(
        self,
        *,
        event_key: str,
        action_ref: str,
        work_id: str,
        action: str,
        payload: Mapping[str, str],
        authorized_user: str,
        chat_ref: str,
        topic_ref: str,
        message_ref: str,
        reply_ref: str,
    ) -> dict[str, Any]:
        self._create_pending(
            action_ref=action_ref, work_id=work_id, action=action, payload=payload,
        )
        self._mint_and_consume(
            work_id=work_id, action_ref=action_ref, action=action,
            authorized_user=authorized_user, chat_ref=chat_ref,
            topic_ref=topic_ref, message_ref=reply_ref,
        )
        self._enqueue_control(work_id, action_ref, action, "accepted")
        self._update_inbound(event_key, "executing")
        result, response_text = self._apply_action(
            work_id=work_id, action=action, payload=payload,
            authorized_user=authorized_user,
        )
        confirmation_required = bool(result.get("confirmationRequired"))
        preview_action = action == "forget-preview" or (
            action == "privacy" and confirmation_required
        )
        brain_token = str(result.get("confirmationToken") or "") if preview_action else ""
        if preview_action and not brain_token:
            raise BrainActionError("action-preview-token-missing")
        self._set_pending(
            action_ref,
            "consuming",
            payload_json="{}",
            brain_token=brain_token,
        )
        self._update_inbound(event_key, "executed")
        self._enqueue_control(work_id, action_ref, action, "completed")
        if action == "forget-preview":
            response_kind = "forget-preview"
            plan_result = {
                "confirmationToken": brain_token,
                "impact": result.get("impact") if isinstance(result.get("impact"), dict) else {},
            }
        elif action == "privacy" and confirmation_required:
            response_kind = "privacy-preview"
            plan_result = {
                "confirmationToken": brain_token,
                "currentPrivacy": clean_text(result.get("currentPrivacy"), 40),
                "targetPrivacy": clean_text(result.get("targetPrivacy"), 40),
            }
        else:
            response_kind = "ordinary"
            plan_result = {}
        self._queue_response(
            action_ref=action_ref,
            event_key=event_key,
            work_id=work_id,
            authorized_user=authorized_user,
            chat_ref=chat_ref,
            topic_ref=topic_ref,
            reply_ref=message_ref,
            response_kind=response_kind,
            text=response_text,
            result=plan_result,
        )
        return self._deliver_response_plan(action_ref)

    def _execute_forget_confirm(
        self,
        *,
        event_key: str,
        action_ref: str,
        work_id: str,
        authorized_user: str,
        chat_ref: str,
        topic_ref: str,
        message_ref: str,
        preview_ref: str,
    ) -> dict[str, Any]:
        with self.connect() as db, self.transaction(db):
            pending = db.execute(
                "SELECT * FROM pending_actions WHERE action_ref=?", (action_ref,),
            ).fetchone()
            changed = 0
            if pending and pending["action"] == "forget-confirm":
                changed = db.execute(
                    """UPDATE pending_actions SET state='consuming',updated_at=?
                         WHERE action_ref=? AND state='pending' AND expires_at>=?""",
                    (utc_now(), action_ref, utc_now()),
                ).rowcount
        if not pending or pending["action"] != "forget-confirm":
            self._update_inbound(event_key, "ignored")
            return {"ok": True, "handled": True, "silentDrop": True, "actionState": "ignored"}
        if changed != 1:
            self._update_inbound(event_key, "ignored")
            return {"ok": True, "handled": True, "silentDrop": True, "actionState": "ignored"}
        lifecycle_token = str(pending["lifecycle_token"])
        brain_token = str(pending["brain_token"])
        if not lifecycle_token or not brain_token:
            raise BrainActionError("forget-confirm-token-missing")
        consumed = self._gateway().consume_action(
            lifecycle_token,
            authorized_user=authorized_user,
            chat_ref=chat_ref,
            topic_ref=topic_ref,
            message_ref=preview_ref,
            artifact_ref=action_ref,
        )
        if consumed.get("action") != "forget-confirm" or consumed.get("workId") != work_id:
            raise BrainActionError("forget-confirm-binding-conflict")
        self._enqueue_control(work_id, action_ref, "forget-confirm", "accepted")
        self._update_inbound(event_key, "executing")
        with self._store().connect() as brain_db:
            expected_chunk_ids = tuple(
                str(row["id"])
                for row in brain_db.execute(
                    "SELECT id FROM source_chunks WHERE work_id=?", (work_id,),
                ).fetchall()
            )
        result = self._store().forget(
            work_id,
            authorized_user=authorized_user,
            confirmation_token=brain_token,
        )
        if result.get("ok") is not True or result.get("forgotten") is not True:
            raise BrainActionError("forget-cleanup-incomplete")
        self._request_cancel_if_open(work_id)
        self._verify_forget_cleanup_with_chunks(work_id, expected_chunk_ids)
        self._set_pending(
            action_ref,
            "consumed",
            lifecycle_token="", brain_token="", payload_json="{}",
        )
        self._update_inbound(event_key, "executed")
        self._enqueue_control(work_id, action_ref, "forget-confirm", "forget_completed")
        self._queue_response(
            action_ref=action_ref,
            event_key=event_key,
            work_id=work_id,
            authorized_user=authorized_user,
            chat_ref=chat_ref,
            topic_ref=topic_ref,
            reply_ref=message_ref,
            response_kind="forget-final",
            text=(
                "<b>Forget complete</b>\n"
                "Source content, extraction indexes, governed candidates, memory, and retrieval data were cleared."
            ),
        )
        return self._deliver_response_plan(action_ref)

    def _execute_privacy_confirm(
        self,
        *,
        event_key: str,
        action_ref: str,
        work_id: str,
        authorized_user: str,
        chat_ref: str,
        topic_ref: str,
        message_ref: str,
        preview_ref: str,
    ) -> dict[str, Any]:
        with self.connect() as db, self.transaction(db):
            pending = db.execute(
                "SELECT * FROM pending_actions WHERE action_ref=?", (action_ref,),
            ).fetchone()
            changed = 0
            if pending and pending["action"] == "privacy-confirm":
                changed = db.execute(
                    """UPDATE pending_actions SET state='consuming',updated_at=?
                         WHERE action_ref=? AND state='pending' AND expires_at>=?""",
                    (utc_now(), action_ref, utc_now()),
                ).rowcount
        if not pending or pending["action"] != "privacy-confirm" or changed != 1:
            self._update_inbound(event_key, "ignored")
            return {"ok": True, "handled": True, "silentDrop": True, "actionState": "ignored"}
        try:
            payload = json.loads(str(pending["payload_json"]))
        except json.JSONDecodeError as exc:
            raise BrainActionError("privacy-confirm-payload-invalid") from exc
        target = clean_text(payload.get("privacy") if isinstance(payload, dict) else "", 40)
        lifecycle_token = str(pending["lifecycle_token"])
        brain_token = str(pending["brain_token"])
        if target not in {"private", "internal", "dashboard-safe"} or not lifecycle_token or not brain_token:
            raise BrainActionError("privacy-confirm-token-missing")
        consumed = self._gateway().consume_action(
            lifecycle_token,
            authorized_user=authorized_user,
            chat_ref=chat_ref,
            topic_ref=topic_ref,
            message_ref=preview_ref,
            artifact_ref=action_ref,
        )
        if consumed.get("action") != "privacy" or consumed.get("workId") != work_id:
            raise BrainActionError("privacy-confirm-binding-conflict")
        self._enqueue_control(work_id, action_ref, "privacy-confirm", "accepted")
        self._update_inbound(event_key, "executing")
        result = self._store().change_privacy(
            work_id,
            authorized_user=authorized_user,
            privacy=target,
            confirmation_token=brain_token,
        )
        if result.get("ok") is not True or result.get("broadened") is not True:
            raise BrainActionError("privacy-confirmation-incomplete")
        self._set_pending(
            action_ref,
            "consumed",
            lifecycle_token="",
            brain_token="",
            payload_json="{}",
        )
        self._update_inbound(event_key, "executed")
        self._enqueue_control(work_id, action_ref, "privacy-confirm", "completed")
        response_text = (
            f"Source privacy set to <b>{html.escape(target)}</b>. "
            "Existing governed candidates and memories kept their prior scope."
        )
        self._queue_response(
            action_ref=action_ref,
            event_key=event_key,
            work_id=work_id,
            authorized_user=authorized_user,
            chat_ref=chat_ref,
            topic_ref=topic_ref,
            reply_ref=message_ref,
            response_kind="privacy-final",
            text=response_text,
        )
        return self._deliver_response_plan(action_ref)

    def handle_event(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        try:
            on_brain, authorized, expected_chat, expected_topic, authorized_user = self._route_guard(envelope)
        except BrainConfigurationError as exc:
            # Do not mark a routing/configuration failure as handled.  The
            # ingress hook will leave the durable message replayable instead
            # of adopting it as a silent drop.
            return {
                "ok": False,
                "brainAction": False,
                "handled": False,
                "silentDrop": False,
                "routingUnavailable": True,
                "errorClass": safe_error_class(exc),
                "privacy": {"identifiersIncluded": False, "rawContentIncluded": False},
            }
        if not on_brain:
            return {"ok": True, "brainAction": False, "handled": False, "silentDrop": False}
        if not authorized:
            return {"ok": True, "brainAction": True, "handled": True, "silentDrop": True}
        message_ref = clean_text(envelope.get("messageId"), 80)
        reply_ref = clean_text(envelope.get("replyToMessageId"), 80)
        text = str(envelope.get("text") or "")
        action_looking = self._looks_like_action(text)
        if not message_ref.isdigit() or not reply_ref.isdigit() or not text.strip():
            if action_looking:
                return {"ok": True, "brainAction": True, "handled": True, "silentDrop": True}
            return {"ok": True, "brainAction": False, "handled": False, "silentDrop": False}
        mapping = self._mapping(expected_chat, expected_topic, reply_ref)
        if not mapping:
            if action_looking:
                return {"ok": True, "brainAction": True, "handled": True, "silentDrop": True}
            return {"ok": True, "brainAction": False, "handled": False, "silentDrop": False}
        if mapping["kind"] == "ambiguous" or not mapping["workId"]:
            return {"ok": True, "brainAction": True, "handled": True, "silentDrop": True}
        work_id = mapping["workId"]
        if mapping["kind"] in {"forget-preview", "privacy-preview"}:
            expected_confirmation = (
                "CONFIRM FORGET" if mapping["kind"] == "forget-preview" else "CONFIRM PRIVACY"
            )
            confirmation_action = (
                "forget-confirm" if mapping["kind"] == "forget-preview" else "privacy-confirm"
            )
            parsed: tuple[str, dict[str, str]] | None = (
                (confirmation_action, {})
                if clean_text(text, 80) == expected_confirmation
                else None
            )
            action_ref = mapping["actionRef"]
        else:
            parsed = self._parse_action(text)
            action_ref = stable_ref(
                "brain-action", expected_chat, expected_topic, message_ref, reply_ref,
            )
        action = parsed[0] if parsed else "invalid"
        if envelope.get("edited") is True:
            return {
                "ok": True, "brainAction": True, "handled": True,
                "silentDrop": True, "actionState": "edited-action-rejected",
            }
        if mapping["kind"] == "card" and action != "cancel":
            parsed = None
            action = "invalid"
        elif mapping["kind"] == "card" and action == "cancel":
            try:
                active_card = self._bound_work_receipt(work_id).get("phase") != "terminal"
            except (BrainActionError, LifecycleError):
                active_card = False
            if not active_card:
                parsed = None
                action = "invalid"
        if parsed and action in HUMAN_CANARY_INBOUND_CLASS:
            # Journal the exact, already-bound inbound ID before any governed
            # action or confirmation is allowed to execute.
            self._record_human_canary_inbound(
                work_id,
                action=action,
                chat_ref=expected_chat,
                topic_ref=expected_topic,
                message_ref=message_ref,
            )
        event_key = stable_ref("brain-action-inbound", expected_chat, expected_topic, message_ref)
        reserved = self._reserve_inbound(
            event_key=event_key,
            message_ref=message_ref,
            reply_ref=reply_ref,
            work_id=work_id,
            action_ref=(
                stable_ref("brain-confirm-inbound", action_ref, message_ref)
                if mapping["kind"] in {"forget-preview", "privacy-preview"}
                else action_ref
            ),
            action=action,
        )
        if not reserved:
            recovery = self._recover_duplicate(event_key)
            if recovery != "resume":
                return {
                    "ok": recovery not in {"indeterminate", "dead_letter"},
                    "brainAction": True, "handled": True,
                    "silentDrop": True, "duplicate": True,
                    "actionState": recovery,
                }
        if not parsed:
            self._update_inbound(event_key, "ignored")
            return {
                "ok": True, "brainAction": True, "handled": True,
                "silentDrop": True, "actionState": "ignored",
            }
        if not self._writer_ready(work_id):
            if action not in {"forget-confirm", "privacy-confirm"}:
                self._create_pending(
                    action_ref=action_ref,
                    work_id=work_id,
                    action=action,
                    payload=parsed[1],
                )
            self._update_inbound(
                event_key,
                "deferred",
                error_class="brain-writer-disabled",
            )
            return {
                "ok": False,
                "brainAction": True,
                "handled": True,
                "silentDrop": True,
                "actionState": "deferred",
                "privacy": {"identifiersIncluded": False, "rawContentIncluded": False},
            }
        try:
            if action == "forget-confirm":
                result = self._execute_forget_confirm(
                    event_key=event_key,
                    action_ref=action_ref,
                    work_id=work_id,
                    authorized_user=authorized_user,
                    chat_ref=expected_chat,
                    topic_ref=expected_topic,
                    message_ref=message_ref,
                    preview_ref=reply_ref,
                )
            elif action == "privacy-confirm":
                result = self._execute_privacy_confirm(
                    event_key=event_key,
                    action_ref=action_ref,
                    work_id=work_id,
                    authorized_user=authorized_user,
                    chat_ref=expected_chat,
                    topic_ref=expected_topic,
                    message_ref=message_ref,
                    preview_ref=reply_ref,
                )
            else:
                result = self._execute_immediate(
                    event_key=event_key,
                    action_ref=action_ref,
                    work_id=work_id,
                    action=action,
                    payload=parsed[1],
                    authorized_user=authorized_user,
                    chat_ref=expected_chat,
                    topic_ref=expected_topic,
                    message_ref=message_ref,
                    reply_ref=reply_ref,
                )
            return {"brainAction": True, **result}
        except Exception as exc:
            error_class = safe_error_class(exc)
            try:
                with self.connect() as db:
                    row = db.execute(
                        "SELECT state FROM inbound_events WHERE event_key=?", (event_key,),
                    ).fetchone()
                uncertain = bool(row and row["state"] in {"executing", "responding"})
                state = "indeterminate" if uncertain else "dead_letter"
                self._update_inbound(event_key, state, error_class=error_class)
                if action in SAFE_ACTIONS:
                    self._enqueue_control(work_id, action_ref, action, state)
            except Exception:
                state = "indeterminate"
            return {
                "ok": False,
                "brainAction": True,
                "handled": True,
                "silentDrop": True,
                "actionState": state,
                "errorClass": error_class,
                "privacy": {"identifiersIncluded": False, "rawContentIncluded": False},
            }

    def _resume_deferred(self, row: sqlite3.Row) -> dict[str, Any]:
        work_id = str(row["work_id"])
        if not self._writer_ready(work_id):
            return {"ok": False, "actionState": "deferred"}
        config = load_json(self.config_path)
        chat_ref, topic_ref = resolved_brain_topic(config, self.topic_receipt_path)
        authorized_user = resolved_authorized_sender(
            self.authorized_sender_receipt_path,
            chat_id=chat_ref,
            topic_id=topic_ref,
        )
        reply_ref = str(row["reply_message_ref"])
        mapping = self._mapping(chat_ref, topic_ref, reply_ref)
        if not mapping or mapping.get("workId") != work_id:
            self._update_inbound(
                str(row["event_key"]),
                "dead_letter",
                error_class="action-mapping-unavailable",
            )
            return {"ok": False, "actionState": "dead_letter"}
        action = str(row["action"])
        action_ref = (
            str(mapping.get("actionRef") or "")
            if action in {"forget-confirm", "privacy-confirm"}
            else str(row["action_ref"])
        )
        if not action_ref:
            self._update_inbound(
                str(row["event_key"]),
                "dead_letter",
                error_class="action-binding-unavailable",
            )
            return {"ok": False, "actionState": "dead_letter"}
        with self.connect() as db, self.transaction(db):
            claimed = db.execute(
                """UPDATE inbound_events SET state='reserved',error_class='',updated_at=?
                     WHERE event_key=? AND state='deferred'""",
                (utc_now(), row["event_key"]),
            ).rowcount
        if claimed != 1:
            return {"ok": True, "actionState": "duplicate"}
        try:
            if action == "forget-confirm":
                return self._execute_forget_confirm(
                    event_key=str(row["event_key"]),
                    action_ref=action_ref,
                    work_id=work_id,
                    authorized_user=authorized_user,
                    chat_ref=chat_ref,
                    topic_ref=topic_ref,
                    message_ref=str(row["telegram_message_ref"]),
                    preview_ref=reply_ref,
                )
            if action == "privacy-confirm":
                return self._execute_privacy_confirm(
                    event_key=str(row["event_key"]),
                    action_ref=action_ref,
                    work_id=work_id,
                    authorized_user=authorized_user,
                    chat_ref=chat_ref,
                    topic_ref=topic_ref,
                    message_ref=str(row["telegram_message_ref"]),
                    preview_ref=reply_ref,
                )
            with self.connect() as db:
                pending = db.execute(
                    "SELECT payload_json,state FROM pending_actions WHERE action_ref=?",
                    (action_ref,),
                ).fetchone()
            if not pending or pending["state"] != "prepared":
                raise BrainActionError("deferred-action-journal-invalid")
            payload = json.loads(str(pending["payload_json"]))
            if not isinstance(payload, dict):
                raise BrainActionError("deferred-action-journal-invalid")
            return self._execute_immediate(
                event_key=str(row["event_key"]),
                action_ref=action_ref,
                work_id=work_id,
                action=action,
                payload={str(key): str(value) for key, value in payload.items()},
                authorized_user=authorized_user,
                chat_ref=chat_ref,
                topic_ref=topic_ref,
                message_ref=str(row["telegram_message_ref"]),
                reply_ref=reply_ref,
            )
        except Exception as exc:
            with self.connect() as db:
                current = db.execute(
                    "SELECT state FROM inbound_events WHERE event_key=?", (row["event_key"],),
                ).fetchone()
            uncertain = bool(current and current["state"] in {"executing", "executed", "responding"})
            state = "indeterminate" if uncertain else "dead_letter"
            self._update_inbound(
                str(row["event_key"]), state, error_class=safe_error_class(exc),
            )
            if action in SAFE_ACTIONS:
                self._enqueue_control(work_id, action_ref, action, state)
            return {"ok": False, "actionState": state}

    def drain_pending(self, *, max_actions: int = 16) -> dict[str, Any]:
        if not self.db_path.exists():
            return {
                "ok": True,
                "counts": {"resumed": 0, "deferred": 0, "responsePending": 0},
            }
        resumed = 0
        for _ in range(max(1, min(int(max_actions), 64))):
            with self.connect() as db:
                row = db.execute(
                    """SELECT * FROM inbound_events WHERE state='deferred'
                         ORDER BY created_at,event_key LIMIT 1""",
                ).fetchone()
            if not row or not self._writer_ready(str(row["work_id"])):
                break
            result = self._resume_deferred(row)
            resumed += int(result.get("actionState") != "deferred")
        for _ in range(max(1, min(int(max_actions), 64))):
            recovery_cutoff = (
                dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            with self.connect() as db:
                row = db.execute(
                    """SELECT pr.action_ref,pr.work_id FROM pending_responses pr
                         JOIN inbound_events ie ON ie.event_key=pr.event_key
                         WHERE pr.state='pending'
                            OR (pr.state='delivering' AND pr.updated_at<=?)
                         ORDER BY pr.created_at,pr.action_ref LIMIT 1""",
                    (recovery_cutoff,),
                ).fetchone()
            if not row or not self._writer_ready(str(row["work_id"])):
                break
            result = self._deliver_response_plan(str(row["action_ref"]), recover=True)
            resumed += int(result.get("actionState") != "deferred")
        with self.connect() as db:
            deferred = int(db.execute(
                "SELECT COUNT(*) FROM inbound_events WHERE state='deferred'",
            ).fetchone()[0])
            response_pending = int(db.execute(
                "SELECT COUNT(*) FROM inbound_events WHERE state='response_pending'",
            ).fetchone()[0])
        return {
            "ok": True,
            "counts": {
                "resumed": resumed,
                "deferred": deferred,
                "responsePending": response_pending,
            },
        }

    def drain(self, *, max_actions: int = 16) -> dict[str, Any]:
        pending = self.drain_pending(max_actions=max_actions)
        outbox = self.drain_outbox(max_events=max_actions)
        return {
            "ok": bool(pending.get("ok")) and bool(outbox.get("ok")),
            "pending": pending.get("counts") or {},
            "outbox": outbox.get("counts") or {},
        }

    def status(self) -> dict[str, Any]:
        if not self.db_path.exists():
            return {
                "ok": True,
                "counts": {
                    "inbound": 0, "pendingConfirmations": 0,
                    "deferred": 0, "responsePending": 0,
                    "indeterminate": 0, "deadLetter": 0,
                    "outboxPending": 0, "agedOutbox": 0,
                },
                "privacy": {"countsOnly": True, "identifiersIncluded": False},
            }
        stale_before = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.connect() as db:
            counts = {
                "inbound": int(db.execute("SELECT COUNT(*) FROM inbound_events").fetchone()[0]),
                "pendingConfirmations": int(db.execute(
                    """SELECT COUNT(*) FROM pending_actions
                         WHERE action IN ('forget-confirm','privacy-confirm') AND state='pending'""",
                ).fetchone()[0]),
                "deferred": int(db.execute(
                    "SELECT COUNT(*) FROM inbound_events WHERE state='deferred'",
                ).fetchone()[0]),
                "responsePending": int(db.execute(
                    "SELECT COUNT(*) FROM inbound_events WHERE state='response_pending'",
                ).fetchone()[0]),
                "indeterminate": int(db.execute(
                    "SELECT COUNT(*) FROM inbound_events WHERE state='indeterminate'",
                ).fetchone()[0]),
                "deadLetter": int(db.execute(
                    """SELECT
                         (SELECT COUNT(*) FROM inbound_events WHERE state='dead_letter') +
                         (SELECT COUNT(*) FROM control_outbox WHERE state='dead_letter')""",
                ).fetchone()[0]),
                "outboxPending": int(db.execute(
                    "SELECT COUNT(*) FROM control_outbox WHERE state IN ('pending','sending')",
                ).fetchone()[0]),
                "agedOutbox": int(db.execute(
                    """SELECT COUNT(*) FROM control_outbox
                         WHERE state IN ('pending','sending') AND created_at<?""",
                    (stale_before,),
                ).fetchone()[0]),
            }
        return {
            "ok": not any(counts[key] for key in ("indeterminate", "deadLetter", "agedOutbox")),
            "counts": counts,
            "privacy": {"countsOnly": True, "identifiersIncluded": False},
        }


def private_envelope(args: argparse.Namespace) -> dict[str, Any]:
    if not getattr(args, "private_stdin", False):
        raise BrainActionError("private-stdin-required")
    try:
        value = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise BrainActionError("private-envelope-invalid") from exc
    if not isinstance(value, dict):
        raise BrainActionError("private-envelope-invalid")
    return value


def parser() -> argparse.ArgumentParser:
    private = Path.home() / ".openclaw/private"
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", default=str(private / "brain-intake"))
    result.add_argument("--lifecycle-root", default=str(private / "telegram-lifecycle"))
    result.add_argument("--rollout", default=str(Path.home() / ".openclaw/workspace/mission-control/config/telegram-lifecycle-rollout.json"))
    result.add_argument("--config", default=str(Path.home() / ".openclaw/workspace/mission-control/config/telegram-intake-lanes.json"))
    result.add_argument("--topic-receipt", default=str(private / "telegram-topic-control/brain-topic-creation.json"))
    result.add_argument("--authorized-sender-receipt", default=str(private / "telegram-topic-control/brain-authorized-sender.json"))
    result.add_argument("--dispatcher-state-root", default=str(private / "brain-gateway-dispatcher"))
    result.add_argument("--state-root", default=str(private / "brain-gateway-actions"))
    sub = result.add_subparsers(dest="command", required=True)
    process = sub.add_parser("process-event")
    process.add_argument("--private-stdin", action="store_true")
    drain = sub.add_parser("drain-outbox")
    drain.add_argument("--max-events", type=int, default=16)
    drain_pending = sub.add_parser("drain-pending")
    drain_pending.add_argument("--max-actions", type=int, default=16)
    drain_all = sub.add_parser("drain")
    drain_all.add_argument("--max-actions", type=int, default=16)
    sub.add_parser("human-canary-preflight")
    activate_canary = sub.add_parser("human-canary-activate")
    activate_canary.add_argument("--private-stdin", action="store_true")
    activate_canary.add_argument("--confirm-production-canary", action="store_true")
    canary_status = sub.add_parser("human-canary-status")
    canary_status.add_argument("--private-stdin", action="store_true")
    canary_status.add_argument(
        "--stage", choices=("journal", "pre-forget", "post-forget"), default="journal",
    )
    cleanup_canary = sub.add_parser("human-canary-cleanup-telegram")
    cleanup_canary.add_argument("--private-stdin", action="store_true")
    cleanup_canary.add_argument("--confirm-production-canary", action="store_true")
    cleanup_canary.add_argument("--max-attempts", type=int, default=3)
    sub.add_parser("status")
    return result


def main() -> int:
    args = parser().parse_args()
    adapter = BrainGatewayActions(
        args.root,
        lifecycle_root=args.lifecycle_root,
        rollout_path=args.rollout,
        config_path=args.config,
        topic_receipt_path=args.topic_receipt,
        authorized_sender_receipt_path=args.authorized_sender_receipt,
        dispatcher_state_root=args.dispatcher_state_root,
        state_root=args.state_root,
    )
    try:
        if args.command == "process-event":
            result = adapter.handle_event(private_envelope(args))
        elif args.command == "drain-outbox":
            result = adapter.drain_outbox(max_events=args.max_events)
        elif args.command == "drain-pending":
            result = adapter.drain_pending(max_actions=args.max_actions)
        elif args.command == "drain":
            result = adapter.drain(max_actions=args.max_actions)
        elif args.command == "human-canary-preflight":
            result = adapter.human_canary_preflight()
        elif args.command == "human-canary-activate":
            if not args.confirm_production_canary:
                raise BrainActionError("production-canary-confirmation-required")
            payload = private_envelope(args)
            result = adapter.activate_human_canary(clean_text(payload.get("workId"), 180))
        elif args.command == "human-canary-status":
            payload = private_envelope(args)
            result = adapter.human_canary_status(
                clean_text(payload.get("workId"), 180),
                stage=args.stage,
                retrieval_query=clean_text(payload.get("retrievalQuery"), 800),
            )
        elif args.command == "human-canary-cleanup-telegram":
            if not args.confirm_production_canary:
                raise BrainActionError("production-canary-confirmation-required")
            payload = private_envelope(args)
            result = adapter.cleanup_human_canary_telegram(
                clean_text(payload.get("workId"), 180),
                max_attempts=args.max_attempts,
                retrieval_query=clean_text(payload.get("retrievalQuery"), 800),
            )
        else:
            result = adapter.status()
    except Exception as exc:
        result = {
            "ok": False,
            "handled": True,
            "silentDrop": True,
            "errorClass": safe_error_class(exc),
            "privacy": {"identifiersIncluded": False, "rawContentIncluded": False},
        }
    print(json.dumps(result, sort_keys=True))
    # Handled failures return success to the ingress hook so no generic agent
    # can reinterpret the governed action or duplicate an unknown write.
    if args.command == "process-event":
        return 0 if result.get("handled") or result.get("ok") else 1
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
