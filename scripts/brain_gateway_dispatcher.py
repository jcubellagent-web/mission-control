#!/usr/bin/env python3
"""Gateway-owned Telegram surfaces for governed Brain intake.

The media hook persists and downloads, and the Brain worker extracts and
commits a terminal outbox record.  Neither process is allowed to call
Telegram.  This Josh 2.0 sidecar is the sole Brain surface writer: it consumes
the private binding, reserves lifecycle effects, and produces one reaction,
one editable card, and one separate terminal receipt.

Only aggregate counts and bounded error classes are written to stdout.  Chat,
topic, message, file, caption, and extraction identifiers stay in private
SQLite stores.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import html
import json
import os
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from brain_media_intake import BrainStore, clean_text, load_json, resolved_brain_topic
from telegram_gateway_lifecycle import (
    GatewayLifecycle,
    LifecycleError,
    RolloutPolicy,
    StaleEventError,
)


DISPATCHER_SCHEMA_VERSION = 1
DEFAULT_MAX_WORK = 8
DEFAULT_MAX_SECONDS = 10
DEFAULT_LEASE_SECONDS = 45
VISIBILITY_MAX_ATTEMPTS = 12
TELEGRAM_TIMEOUT_SECONDS = 8
TELEGRAM_ATTEMPTS = 3
SAFE_LOCAL_ROUTES = frozenset({"local-none", "local-deterministic", "local-tool"})
SAFE_INGESTION_PHASES = frozenset({
    "receipt_pending", "downloading", "stored", "scanning", "extracting",
    "classifying", "deduplicating", "candidate_pending", "reviewing",
    "indexed", "unsupported", "quarantined", "forgotten",
})
PROGRESS_BY_PHASE = {
    "receipt_pending": 10, "downloading": 20, "stored": 30,
    "scanning": 38, "extracting": 46, "classifying": 58,
    "deduplicating": 68, "candidate_pending": 76, "reviewing": 82,
    "indexed": 92, "unsupported": 92, "quarantined": 92, "forgotten": 100,
}
DISPLAY_PHASE = {
    "receipt_pending": "Receipt secured", "downloading": "Storing privately",
    "stored": "Stored", "scanning": "Safety scanning", "extracting": "Extracting",
    "classifying": "Classifying", "deduplicating": "Deduplicating",
    "candidate_pending": "Preparing governed candidates", "reviewing": "Reviewing",
    "indexed": "Source indexed", "unsupported": "Extraction limited",
    "quarantined": "Quarantined", "forgotten": "Forgotten",
}
ATTEMPT_STATES = frozenset({"reserved", "attempting", "delivered", "indeterminate", "dead_letter"})
FINAL_RECEIPT_FIELDS = (
    "Stored", "Extracted", "Learned", "Source indexed", "Pending review",
    "Duplicates", "Unsupported", "Privacy", "Retention", "How to correct",
    "How to forget", "Approval needed",
)
VISIBILITY_SPECS: dict[str, dict[str, str]] = {
    "receipt_ready": {
        "status": "active", "phase": "acknowledged", "workEvent": "start",
        "detail": "Private receipt secured; acknowledgement and live card are ready.",
    },
    "processing": {
        "status": "active", "phase": "working", "workEvent": "heartbeat",
        "detail": "Private Brain extraction and governance are in progress.",
    },
    "verifying": {
        "status": "active", "phase": "verifying", "workEvent": "heartbeat",
        "detail": "Private extraction results and governance receipts are being verified.",
    },
    "terminal_committed": {
        "status": "active", "phase": "terminal", "workEvent": "heartbeat",
        "detail": "Terminal Brain receipt is durably committed; final delivery is pending.",
    },
    "delivered": {
        "status": "done", "phase": "terminal", "workEvent": "terminal",
        "detail": "Brain ingestion receipt was delivered through the gateway.",
    },
    "indeterminate": {
        "status": "error", "phase": "terminal", "workEvent": "terminal",
        "detail": "Brain final delivery is indeterminate and remains fenced from retry.",
    },
    "dead_letter": {
        "status": "error", "phase": "terminal", "workEvent": "terminal",
        "detail": "Brain final delivery failed and requires operator review.",
    },
}


class BrainGatewayError(RuntimeError):
    """Bounded fail-closed dispatcher error."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_after(seconds: int) -> str:
    value = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=max(1, int(seconds)))
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> dt.datetime:
    text = str(value or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def safe_error_class(exc: BaseException) -> str:
    if isinstance(exc, StaleEventError):
        return "lifecycle-race"
    if isinstance(exc, LifecycleError):
        return "lifecycle-error"
    if isinstance(exc, BrainGatewayError):
        value = clean_text(str(exc), 80)
        return value if value and value.replace("-", "").isalnum() else "brain-gateway-error"
    return "brain-gateway-error"


def bounded_html(value: Any, limit: int = 240) -> str:
    return html.escape(clean_text(value, limit), quote=False)


def private_message_ref(result: Mapping[str, Any]) -> str:
    value = (result.get("result") or {}).get("message_id") if isinstance(result.get("result"), dict) else ""
    text = str(value or "")
    return text if text.isdigit() else ""


def _retry_after_seconds(payload: Mapping[str, Any]) -> float:
    try:
        value = float((payload.get("parameters") or {}).get("retry_after") or 0)
    except (AttributeError, TypeError, ValueError):
        return 0.0
    return max(0.0, min(value, 5.0))


def default_transport(method: str, payload: Mapping[str, Any], timeout: int) -> dict[str, Any]:
    """Call the private Josh bot helper without returning Telegram text."""
    workspace_scripts = Path(__file__).resolve().parents[2] / "scripts"
    if str(workspace_scripts) not in sys.path:
        sys.path.insert(0, str(workspace_scripts))
    try:
        from send_josh_reply import API_BASE  # type: ignore
    except Exception:
        API_BASE = ""
    if not API_BASE:
        return {"ok": False, "state": "dead_letter", "errorClass": "telegram-helper-unavailable"}

    for attempt in range(TELEGRAM_ATTEMPTS):
        request = urllib.request.Request(
            f"{API_BASE}/{method}",
            data=json.dumps(dict(payload), separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                try:
                    parsed = json.loads(response.read())
                except json.JSONDecodeError:
                    return {"ok": False, "state": "indeterminate", "errorClass": "telegram-response-invalid"}
        except urllib.error.HTTPError as exc:
            try:
                parsed = json.loads(exc.read().decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                parsed = {}
            description = str(parsed.get("description") or "").lower()
            if (
                method == "deleteMessage"
                and exc.code == 400
                and "message to delete not found" in description
            ):
                return {
                    "ok": True, "state": "delivered", "alreadyAbsent": True, "result": {},
                }
            if 500 <= exc.code <= 599:
                return {
                    "ok": False, "state": "indeterminate",
                    "errorClass": "telegram-result-unknown",
                }
            retryable = exc.code == 429
            if retryable and attempt + 1 < TELEGRAM_ATTEMPTS:
                time.sleep(_retry_after_seconds(parsed) or min(1.0, 0.2 * (2 ** attempt)))
                continue
            return {
                "ok": False,
                "state": "dead_letter",
                "errorClass": "telegram-rate-limit" if exc.code == 429 else f"telegram-http-{exc.code}",
            }
        except (TimeoutError, socket.timeout, urllib.error.URLError, OSError):
            # A request may have reached Telegram before the response was lost.
            return {"ok": False, "state": "indeterminate", "errorClass": "telegram-result-unknown"}

        if parsed.get("ok") is True:
            return {"ok": True, "state": "delivered", "result": parsed.get("result") or {}}
        try:
            code = int(parsed.get("error_code") or 0)
        except (TypeError, ValueError):
            code = 0
        description = str(parsed.get("description") or "").lower()
        if (
            method == "deleteMessage"
            and code == 400
            and "message to delete not found" in description
        ):
            return {
                "ok": True, "state": "delivered", "alreadyAbsent": True, "result": {},
            }
        if "message is not modified" in description:
            return {"ok": True, "state": "delivered", "notModified": True, "result": {}}
        if 500 <= code <= 599:
            return {
                "ok": False, "state": "indeterminate",
                "errorClass": "telegram-result-unknown",
            }
        retryable = code == 429
        if retryable and attempt + 1 < TELEGRAM_ATTEMPTS:
            time.sleep(_retry_after_seconds(parsed) or min(1.0, 0.2 * (2 ** attempt)))
            continue
        return {
            "ok": False,
            "state": "dead_letter",
            "errorClass": "telegram-rate-limit" if code == 429 else "telegram-api-rejected",
        }
    return {"ok": False, "state": "dead_letter", "errorClass": "telegram-attempts-exhausted"}


def default_visibility_publisher(event: Mapping[str, Any]) -> bool:
    """Require the canonical local work-ledger acceptance receipt.

    This is deliberately not a kiosk or HTTP health check.  ``agent_publish``
    commits locally on the Control Tower owner and projects the same accepted
    event to Brain Feed.  The caller keeps a private durable retry outbox when
    that acceptance cannot be proven.
    """
    script = Path(__file__).resolve().parent / "agent_publish.py"
    if not script.exists():
        return False
    spec = VISIBILITY_SPECS.get(str(event.get("stage") or ""))
    if not spec:
        return False
    route_class = clean_text(event.get("routeClass"), 80)
    route_verified = bool(event.get("routeVerified"))
    detail = spec["detail"]
    if route_verified and route_class:
        detail = f"{detail} Actual route: {route_class}."
    command = [
        sys.executable,
        str(script),
        "--agent", "josh2",
        "--type", "status",
        "--status", spec["status"],
        "--title", "Brain media intake",
        "--tool", "Josh 2.0 Brain gateway",
        "--detail", detail,
        "--privacy", "dashboard-safe",
        "--brain-feed",
        "--work-event", spec["workEvent"],
        "--work-id", str(event["workId"]),
        "--run-id", str(event["runId"]),
        "--phase", spec["phase"],
        "--origin-claim-hash", str(event["originClaimHash"]),
        "--event-id", str(event["eventId"]),
    ]
    if route_verified:
        if not route_class:
            return False
        command.extend([
            "--model-family", "local",
            "--model-id", route_class,
            "--route-verified",
        ])
    else:
        command.append("--route-unverified")
    try:
        result = subprocess.run(
            command,
            cwd=script.parents[1],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        if result.returncode != 0:
            return False
        payload = json.loads(result.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return False
    ledger = payload.get("workLedger") if isinstance(payload, dict) else None
    return bool(
        isinstance(payload, dict)
        and payload.get("ok") is True
        and isinstance(ledger, dict)
        and ledger.get("accepted") is True
    )


class BrainGatewayDispatcher:
    def __init__(
        self,
        store_root: Path | str,
        *,
        lifecycle_root: Path | str,
        rollout_path: Path | str,
        config_path: Path | str,
        topic_receipt_path: Path | str,
        state_root: Path | str,
        transport: Callable[[str, Mapping[str, Any], int], dict[str, Any]] | None = None,
        visibility_publisher: Callable[[Mapping[str, Any]], bool] | None = None,
        dispatcher_id: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.store = BrainStore(store_root)
        self.lifecycle_root = Path(lifecycle_root).expanduser().resolve()
        self.rollout_path = Path(rollout_path).expanduser().resolve()
        self.config_path = Path(config_path).expanduser().resolve()
        self.topic_receipt_path = Path(topic_receipt_path).expanduser().resolve()
        self.state_root = Path(state_root).expanduser().resolve()
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_root, 0o700)
        self.db_path = self.state_root / "dispatcher.sqlite3"
        self.transport = transport or default_transport
        self.visibility_publisher = visibility_publisher or default_visibility_publisher
        self.dispatcher_id = clean_text(
            dispatcher_id or f"brain-gateway-{os.getpid()}-{uuid.uuid4().hex[:12]}", 80,
        )
        self.lease_seconds = max(20, min(int(lease_seconds), 300))
        with self.connect() as db:
            self._schema(db)

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
            if self.db_path.exists():
                os.chmod(self.db_path, stat.S_IRUSR | stat.S_IWUSR)

    @contextlib.contextmanager
    def transaction(self, db: sqlite3.Connection) -> Iterator[None]:
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
            CREATE TABLE IF NOT EXISTS surfaces (
              work_id TEXT PRIMARY KEY, lifecycle_work_id TEXT NOT NULL UNIQUE,
              chat_ref TEXT NOT NULL, topic_ref TEXT NOT NULL,
              source_message_ref TEXT NOT NULL,
              reaction_state TEXT NOT NULL DEFAULT 'pending',
              card_state TEXT NOT NULL DEFAULT 'pending', card_message_ref TEXT NOT NULL DEFAULT '',
              final_state TEXT NOT NULL DEFAULT 'pending', final_message_ref TEXT NOT NULL DEFAULT '',
              close_state TEXT NOT NULL DEFAULT 'pending',
              last_render_hash TEXT NOT NULL DEFAULT '', last_ingestion_phase TEXT NOT NULL DEFAULT '',
              last_card_sequence INTEGER NOT NULL DEFAULT 0,
              error_class TEXT NOT NULL DEFAULT '', lease_owner TEXT NOT NULL DEFAULT '',
              lease_expires_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
              idempotency_key TEXT PRIMARY KEY, work_id TEXT NOT NULL,
              kind TEXT NOT NULL, lifecycle_sequence INTEGER NOT NULL,
              fencing_epoch INTEGER NOT NULL, stage TEXT NOT NULL,
              telegram_message_ref TEXT NOT NULL DEFAULT '', error_class TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY(work_id) REFERENCES surfaces(work_id)
            );
            CREATE INDEX IF NOT EXISTS idx_attempts_work ON attempts(work_id,kind,lifecycle_sequence);
            CREATE TABLE IF NOT EXISTS visibility_outbox (
              event_key TEXT PRIMARY KEY, lifecycle_work_id TEXT NOT NULL,
              lifecycle_run_id TEXT NOT NULL, origin_claim_hash TEXT NOT NULL,
              stage TEXT NOT NULL, route_verified INTEGER NOT NULL DEFAULT 0,
              route_class TEXT NOT NULL DEFAULT '',
              state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              available_at TEXT NOT NULL, error_class TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              UNIQUE(lifecycle_work_id,stage)
            );
            CREATE INDEX IF NOT EXISTS idx_visibility_ready
              ON visibility_outbox(state,available_at,attempts);
            """
        )
        visibility_columns = {
            str(row["name"]) for row in db.execute("PRAGMA table_info(visibility_outbox)")
        }
        if "route_verified" not in visibility_columns:
            db.execute(
                "ALTER TABLE visibility_outbox ADD COLUMN route_verified INTEGER NOT NULL DEFAULT 0"
            )
        if "route_class" not in visibility_columns:
            db.execute(
                "ALTER TABLE visibility_outbox ADD COLUMN route_class TEXT NOT NULL DEFAULT ''"
            )

    def _gateway(self) -> GatewayLifecycle:
        return GatewayLifecycle(
            self.lifecycle_root,
            rollout=RolloutPolicy.load(self.rollout_path),
            owner="josh2",
        )

    def _source(self, work_id: str) -> dict[str, Any]:
        with self.store.connect() as db:
            row = db.execute(
                """SELECT b.lifecycle_work_id,b.lifecycle_run_id,b.source_revision_at_start,
                          s.phase,s.caption_present,s.objective_private,
                          s.created_at,s.updated_at,s.source_private_json,
                          COALESCE(j.state,'') AS job_state,COALESCE(j.stage,'') AS job_stage
                     FROM lifecycle_bindings b JOIN submissions s ON s.work_id=b.work_id
                     LEFT JOIN intake_jobs j ON j.work_id=b.work_id WHERE b.work_id=?""",
                (work_id,),
            ).fetchone()
            media_rows = db.execute(
                """SELECT DISTINCT a.media_class FROM submission_artifacts sa
                     JOIN artifacts a ON a.digest=sa.digest
                     WHERE sa.work_id=? ORDER BY a.media_class""",
                (work_id,),
            ).fetchall()
        if not row:
            raise BrainGatewayError("brain-source-missing")
        if str(row["lifecycle_work_id"]) != str(work_id):
            raise BrainGatewayError("brain-work-identity-mismatch")
        try:
            private_source = json.loads(str(row["source_private_json"]))
        except json.JSONDecodeError as exc:
            raise BrainGatewayError("brain-source-corrupt") from exc
        config = load_json(self.config_path)
        expected_chat, expected_topic = resolved_brain_topic(config, self.topic_receipt_path)
        chat_ref = str(private_source.get("chatRef") or "")
        topic_ref = str(private_source.get("topicRef") or "")
        message_ref = str(private_source.get("messageRef") or "")
        if (
            chat_ref != expected_chat
            or topic_ref != expected_topic
            or not message_ref.isdigit()
            or not topic_ref
        ):
            raise BrainGatewayError("brain-source-binding-mismatch")
        phase = clean_text(row["phase"], 80)
        if phase not in SAFE_INGESTION_PHASES:
            raise BrainGatewayError("brain-ingestion-phase-invalid")
        media_classes = tuple(
            clean_text(item["media_class"], 40).lower()
            for item in media_rows
            if clean_text(item["media_class"], 40)
            and re.fullmatch(r"[a-z][a-z0-9-]{1,39}", clean_text(item["media_class"], 40).lower())
        )
        objective = clean_text(row["objective_private"], 240)
        if not objective.startswith((
            "Govern a captioned verified ",
            "Govern verified ",
        )):
            media_label = media_classes[0] if len(media_classes) == 1 else "mixed media" if media_classes else "media"
            objective = (
                f"Govern a captioned verified {media_label} Brain submission"
                if bool(row["caption_present"])
                else f"Govern verified {media_label} evidence about content pending extraction"
            )
        return {
            "work_id": work_id,
            "lifecycle_work_id": str(row["lifecycle_work_id"]),
            "chat_ref": chat_ref,
            "topic_ref": topic_ref,
            "message_ref": message_ref,
            "phase": phase,
            "caption_present": bool(row["caption_present"]),
            "objective": objective,
            "media_classes": media_classes,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "job_state": clean_text(row["job_state"], 40),
            "job_stage": clean_text(row["job_stage"], 40),
        }

    def _candidate_work(self, limit: int) -> list[str]:
        bounded_limit = max(1, min(int(limit), 64))
        page_size = max(64, bounded_limit * 4)
        selected: list[str] = []
        offset = 0
        while len(selected) < bounded_limit:
            with self.store.connect() as db:
                rows = db.execute(
                    """SELECT b.work_id FROM lifecycle_bindings b
                         JOIN submissions s ON s.work_id=b.work_id
                         LEFT JOIN intake_jobs j ON j.work_id=b.work_id
                         WHERE s.phase!='forgotten'
                         ORDER BY CASE WHEN COALESCE(j.state,'')='completed' THEN 1 ELSE 0 END,
                                  s.updated_at ASC,b.work_id ASC LIMIT ? OFFSET ?""",
                    (page_size, offset),
                ).fetchall()
            if not rows:
                break
            page = [str(row["work_id"]) for row in rows]
            placeholders = ",".join("?" for _ in page)
            with self.connect() as db:
                blocked = {
                    str(row["work_id"])
                    for row in db.execute(
                        f"""SELECT s.work_id FROM surfaces s
                              WHERE s.work_id IN ({placeholders}) AND (
                                s.final_state IN ('delivered','indeterminate','dead_letter')
                                OR s.close_state IN ('indeterminate','dead_letter')
                                OR s.reaction_state IN ('indeterminate','dead_letter')
                                OR s.card_state IN ('indeterminate','dead_letter')
                                OR s.error_class!=''
                                OR EXISTS (
                                  SELECT 1 FROM visibility_outbox v
                                   WHERE v.lifecycle_work_id=s.lifecycle_work_id
                                     AND v.state='dead_letter'
                                )
                              )""",
                        page,
                    )
                }
            selected.extend(work_id for work_id in page if work_id not in blocked)
            offset += len(page)
            if len(rows) < page_size:
                break
        return selected[:bounded_limit]

    def _bind_surface(self, source: Mapping[str, Any]) -> None:
        now = utc_now()
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM surfaces WHERE work_id=?", (source["work_id"],)).fetchone()
            expected = (
                str(source["lifecycle_work_id"]), str(source["chat_ref"]),
                str(source["topic_ref"]), str(source["message_ref"]),
            )
            if row:
                actual = (
                    str(row["lifecycle_work_id"]), str(row["chat_ref"]),
                    str(row["topic_ref"]), str(row["source_message_ref"]),
                )
                if actual != expected:
                    raise BrainGatewayError("brain-surface-binding-conflict")
                return
            db.execute(
                """INSERT INTO surfaces(
                     work_id,lifecycle_work_id,chat_ref,topic_ref,source_message_ref,
                     created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (source["work_id"], *expected, now, now),
            )

    def _claim_lease(self, work_id: str) -> bool:
        now = utc_now()
        with self.connect() as db, self.transaction(db):
            changed = db.execute(
                """UPDATE surfaces SET lease_owner=?,lease_expires_at=?,updated_at=?
                     WHERE work_id=? AND (
                       lease_owner='' OR lease_owner=? OR lease_expires_at IS NULL OR lease_expires_at<=?
                     )""",
                (self.dispatcher_id, utc_after(self.lease_seconds), now, work_id, self.dispatcher_id, now),
            ).rowcount
        return changed == 1

    def _renew_lease(self, work_id: str) -> None:
        with self.connect() as db, self.transaction(db):
            changed = db.execute(
                """UPDATE surfaces SET lease_expires_at=?,updated_at=?
                     WHERE work_id=? AND lease_owner=?""",
                (utc_after(self.lease_seconds), utc_now(), work_id, self.dispatcher_id),
            ).rowcount
        if changed != 1:
            raise BrainGatewayError("brain-surface-lease-lost")

    def _release_lease(self, work_id: str) -> None:
        with self.connect() as db, self.transaction(db):
            db.execute(
                """UPDATE surfaces SET lease_owner='',lease_expires_at=NULL,updated_at=?
                     WHERE work_id=? AND lease_owner=?""",
                (utc_now(), work_id, self.dispatcher_id),
            )

    def _surface(self, work_id: str) -> sqlite3.Row:
        with self.connect() as db:
            row = db.execute("SELECT * FROM surfaces WHERE work_id=?", (work_id,)).fetchone()
        if not row:
            raise BrainGatewayError("brain-surface-missing")
        return row

    def _set_surface(self, work_id: str, **fields: Any) -> None:
        allowed = {
            "reaction_state", "card_state", "card_message_ref", "final_state",
            "final_message_ref", "close_state", "last_render_hash",
            "last_ingestion_phase", "last_card_sequence", "error_class",
        }
        if not fields or not set(fields).issubset(allowed):
            raise BrainGatewayError("brain-surface-update-invalid")
        assignments = [f"{key}=?" for key in fields]
        values = [fields[key] for key in fields]
        with self.connect() as db, self.transaction(db):
            changed = db.execute(
                f"UPDATE surfaces SET {','.join(assignments)},updated_at=? WHERE work_id=? AND lease_owner=?",
                (*values, utc_now(), work_id, self.dispatcher_id),
            ).rowcount
        if changed != 1:
            raise BrainGatewayError("brain-surface-lease-lost")

    def _actual_route_class(self, work_id: str) -> tuple[bool, str]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT DISTINCT model_route,tool_version FROM extractions WHERE work_id=?",
                (work_id,),
            ).fetchall()
        if not rows:
            return False, ""
        routes = sorted({clean_text(row["model_route"], 40) for row in rows})
        if not routes or not set(routes).issubset(SAFE_LOCAL_ROUTES):
            return False, ""
        # The extraction row itself is the executed evidence. Tool versions
        # stay private; only this fixed route class can enter shared status.
        return True, routes[0] if len(routes) == 1 else "mixed-local"

    def _visibility_event(self, receipt: Mapping[str, Any], stage: str) -> dict[str, Any]:
        if stage not in VISIBILITY_SPECS:
            raise BrainGatewayError("brain-visibility-stage-invalid")
        work_id = str(receipt.get("workId") or "")
        run_id = str(receipt.get("runId") or "")
        if not work_id.startswith("work-telegram-") or not run_id:
            raise BrainGatewayError("brain-visibility-identity-invalid")
        origin_hash = hashlib.sha256(
            f"brain-visibility|{work_id}|{run_id}".encode("utf-8")
        ).hexdigest()
        route_verified, route_class = (
            self._actual_route_class(work_id)
            if stage in {"verifying", "terminal_committed", "delivered", "indeterminate", "dead_letter"}
            else (False, "")
        )
        return {
            "stage": stage,
            "workId": work_id,
            "runId": run_id,
            "originClaimHash": origin_hash,
            "routeVerified": route_verified,
            "routeClass": route_class,
        }

    def _ensure_visibility(self, receipt: Mapping[str, Any], stage: str) -> bool:
        """Persist first, then require canonical local ledger acceptance."""
        event = self._visibility_event(receipt, stage)
        event_key = "brain-visibility-" + hashlib.sha256(
            f"{event['workId']}|{stage}".encode("utf-8")
        ).hexdigest()[:32]
        event["eventId"] = event_key
        now = utc_now()
        with self.connect() as db, self.transaction(db):
            db.execute(
                """INSERT OR IGNORE INTO visibility_outbox(
                     event_key,lifecycle_work_id,lifecycle_run_id,origin_claim_hash,
                     stage,route_verified,route_class,state,attempts,available_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,'pending',0,?,?,?)""",
                (
                    event_key, event["workId"], event["runId"],
                    event["originClaimHash"], stage, int(bool(event["routeVerified"])),
                    event["routeClass"], now, now, now,
                ),
            )
            row = db.execute(
                "SELECT * FROM visibility_outbox WHERE event_key=?", (event_key,),
            ).fetchone()
            if not row:
                raise BrainGatewayError("brain-visibility-outbox-missing")
            event["routeVerified"] = bool(row["route_verified"])
            event["routeClass"] = str(row["route_class"])
            if row["state"] == "accepted":
                return True
            if row["state"] == "dead_letter" or str(row["available_at"]) > now:
                return False
            # A local publisher uses a stable work/stage identity, so replaying
            # a stale sending row is safe and lets us recover a process crash.
            changed = db.execute(
                """UPDATE visibility_outbox SET state='sending',attempts=attempts+1,updated_at=?
                     WHERE event_key=? AND state IN ('pending','sending') AND available_at<=?""",
                (now, event_key, now),
            ).rowcount
            if changed != 1:
                return False
            attempt = int(row["attempts"]) + 1

        accepted = False
        try:
            accepted = bool(self.visibility_publisher(event))
        except Exception:
            accepted = False
        with self.connect() as db, self.transaction(db):
            if accepted:
                db.execute(
                    """UPDATE visibility_outbox SET state='accepted',error_class='',updated_at=?
                         WHERE event_key=? AND state='sending'""",
                    (utc_now(), event_key),
                )
                return True
            terminal = attempt >= VISIBILITY_MAX_ATTEMPTS
            delay = min(60, 2 ** min(attempt, 6))
            db.execute(
                """UPDATE visibility_outbox SET state=?,available_at=?,error_class=?,updated_at=?
                     WHERE event_key=? AND state='sending'""",
                (
                    "dead_letter" if terminal else "pending",
                    utc_after(delay), "control-tower-unavailable", utc_now(), event_key,
                ),
            )
        return False

    def _advance_to_acknowledged(self, lifecycle_work_id: str) -> dict[str, Any]:
        path = ("received", "classified", "acknowledged")
        for _ in range(8):
            gateway = self._gateway()
            receipt = gateway.read_work(lifecycle_work_id)
            if not receipt:
                raise BrainGatewayError("brain-lifecycle-missing")
            phase = str(receipt["phase"])
            if phase == "terminal" or phase not in path or phase == "acknowledged":
                return receipt
            try:
                gateway.transition(
                    lifecycle_work_id,
                    path[path.index(phase) + 1],
                    expected_sequence=int(receipt["sequence"]),
                    fencing_epoch=int(receipt["fencingEpoch"]),
                    safe_payload={"status": "phase_change", "surfaceContract": "brain-intake"},
                )
            except StaleEventError:
                continue
        raise BrainGatewayError("brain-lifecycle-contention")

    def _record_phase_change(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        for _ in range(6):
            gateway = self._gateway()
            current = gateway.read_work(str(receipt["workId"]))
            if not current:
                raise BrainGatewayError("brain-lifecycle-missing")
            if current["phase"] == "terminal":
                return current
            try:
                return gateway.record_progress(
                    str(current["workId"]),
                    expected_sequence=int(current["sequence"]),
                    fencing_epoch=int(current["fencingEpoch"]),
                    status="phase_change",
                )
            except StaleEventError:
                continue
        raise BrainGatewayError("brain-lifecycle-contention")

    def _attempt(self, key: str) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute("SELECT * FROM attempts WHERE idempotency_key=?", (key,)).fetchone()

    def _reserve_effect(self, work_id: str, receipt: Mapping[str, Any], kind: str) -> sqlite3.Row:
        gateway = self._gateway()
        claim = gateway.claim_effect(
            str(receipt["workId"]), kind,
            sequence=int(receipt["sequence"]),
            fencing_epoch=int(receipt["fencingEpoch"]),
        )
        key = str(claim.get("idempotencyKey") or "")
        if not key:
            raise BrainGatewayError("brain-effect-key-missing")
        existing = self._attempt(key)
        if existing:
            return existing
        state = str(claim.get("state") or "")
        if not claim.get("allowed") and state != "sending":
            raise BrainGatewayError(f"brain-effect-{state or 'fenced'}")
        # The dispatcher always persists `reserved` before setting `attempting`
        # and before the API call.  Therefore an ownerless sending intent with
        # no local attempt is a provable crash-before-send and is safe to adopt.
        with self.connect() as db, self.transaction(db):
            db.execute(
                """INSERT OR IGNORE INTO attempts(
                     idempotency_key,work_id,kind,lifecycle_sequence,fencing_epoch,
                     stage,created_at,updated_at
                   ) VALUES(?,?,?,?,?,'reserved',?,?)""",
                (
                    key, work_id, kind, int(receipt["sequence"]),
                    int(receipt["fencingEpoch"]), utc_now(), utc_now(),
                ),
            )
        attempt = self._attempt(key)
        if not attempt:
            raise BrainGatewayError("brain-effect-reservation-missing")
        return attempt

    def _update_attempt(
        self,
        key: str,
        stage: str,
        *,
        message_ref: str = "",
        error_class: str = "",
    ) -> None:
        if stage not in ATTEMPT_STATES:
            raise BrainGatewayError("brain-effect-stage-invalid")
        with self.connect() as db, self.transaction(db):
            changed = db.execute(
                """UPDATE attempts SET stage=?,telegram_message_ref=?,error_class=?,updated_at=?
                     WHERE idempotency_key=?""",
                (stage, message_ref, clean_text(error_class, 80), utc_now(), key),
            ).rowcount
        if changed != 1:
            raise BrainGatewayError("brain-effect-attempt-missing")

    def _deliver_effect(
        self,
        source: Mapping[str, Any],
        receipt: Mapping[str, Any],
        kind: str,
        method: str,
        payload: Mapping[str, Any],
        *,
        message_required: bool,
    ) -> dict[str, Any]:
        self._renew_lease(str(source["work_id"]))
        attempt = self._reserve_effect(str(source["work_id"]), receipt, kind)
        key = str(attempt["idempotency_key"])
        stage = str(attempt["stage"])
        gateway = self._gateway()
        if stage == "delivered":
            gateway.finish_effect(
                key, state="delivered",
                private_receipt=(f"telegram-message:{attempt['telegram_message_ref']}" if attempt["telegram_message_ref"] else "telegram-confirmed"),
            )
            return {"state": "delivered", "messageRef": str(attempt["telegram_message_ref"])}
        if stage in {"indeterminate", "dead_letter"}:
            gateway.finish_effect(key, state=stage, error_class=str(attempt["error_class"]))
            return {"state": stage, "messageRef": ""}
        if stage == "attempting":
            # No API receipt was persisted.  Telegram may have accepted it.
            self._update_attempt(key, "indeterminate", error_class="telegram-result-unknown")
            gateway.finish_effect(key, state="indeterminate", error_class="telegram-result-unknown")
            return {"state": "indeterminate", "messageRef": ""}

        current = gateway.read_work(str(receipt["workId"]))
        if (
            not current
            or not current.get("writerEnabled")
            or int(current["sequence"]) != int(receipt["sequence"])
            or int(current["fencingEpoch"]) != int(receipt["fencingEpoch"])
            or (current["phase"] == "terminal" and kind not in {"final", "card_edit"})
        ):
            self._update_attempt(key, "dead_letter", error_class="lifecycle-race-fenced")
            gateway.finish_effect(key, state="dead_letter", error_class="lifecycle-race-fenced")
            return {"state": "dead_letter", "messageRef": ""}

        # This durable state is the ambiguity boundary.  A restart after it may
        # not retry because the API call could already have been accepted.
        self._update_attempt(key, "attempting")
        result = self.transport(method, payload, TELEGRAM_TIMEOUT_SECONDS)
        state = "delivered" if result.get("ok") else str(result.get("state") or "indeterminate")
        if state not in {"delivered", "indeterminate", "dead_letter"}:
            state = "indeterminate"
        message_ref = private_message_ref(result)
        if state == "delivered" and message_required and not message_ref:
            state = "indeterminate"
        error_class = "" if state == "delivered" else clean_text(result.get("errorClass"), 80) or "telegram-delivery-failed"
        self._update_attempt(key, state, message_ref=message_ref, error_class=error_class)
        gateway.finish_effect(
            key,
            state=state,
            private_receipt=f"telegram-message:{message_ref}" if message_ref else "telegram-confirmed" if state == "delivered" else "",
            error_class=error_class,
        )
        return {"state": state, "messageRef": message_ref}

    @staticmethod
    def _reaction_payload(source: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "chat_id": source["chat_ref"],
            "message_id": int(str(source["message_ref"])),
            "reaction": [{"type": "emoji", "emoji": "👀"}],
        }

    @staticmethod
    def _card_payload(source: Mapping[str, Any], text: str) -> dict[str, Any]:
        return {
            "chat_id": source["chat_ref"],
            "message_thread_id": int(str(source["topic_ref"])),
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": True,
            "disable_web_page_preview": True,
        }

    @staticmethod
    def _edit_payload(source: Mapping[str, Any], message_ref: str, text: str) -> dict[str, Any]:
        payload = BrainGatewayDispatcher._card_payload(source, text)
        payload.pop("message_thread_id", None)
        payload["message_id"] = int(message_ref)
        return payload

    @staticmethod
    def _route_line(*, verified: bool, route_class: str) -> str:
        route_class = clean_text(route_class, 40)
        if not verified or route_class not in SAFE_LOCAL_ROUTES | {"mixed-local"}:
            return "Model: Pending verification · Route: Pending/unverified"
        model = "Not invoked" if route_class == "local-none" else "No external model"
        return f"Model: {model} · Route: Verified {route_class}"

    @classmethod
    def _terminal_route_evidence(cls, payload: Mapping[str, Any]) -> tuple[bool, str]:
        """Read only the immutable, hash-verified worker receipt route claim."""
        receipt = payload.get("receipt")
        extracted = receipt.get("Extracted") if isinstance(receipt, Mapping) else None
        raw_routes = extracted.get("routes") if isinstance(extracted, Mapping) else None
        if not isinstance(raw_routes, list):
            return False, ""
        routes = sorted({clean_text(route, 40) for route in raw_routes if clean_text(route, 40)})
        if not routes or not set(routes).issubset(SAFE_LOCAL_ROUTES):
            return False, ""
        return True, routes[0] if len(routes) == 1 else "mixed-local"

    @classmethod
    def render_card(
        cls,
        source: Mapping[str, Any],
        receipt: Mapping[str, Any],
        *,
        terminal: bool = False,
        route_verified: bool = False,
        route_class: str = "",
    ) -> str:
        ingestion = str(source["phase"])
        progress = 100 if terminal else PROGRESS_BY_PHASE.get(ingestion, 10)
        phase = "Delivered" if terminal else DISPLAY_PHASE.get(ingestion, "Processing")
        objective = clean_text(source.get("objective"), 240)
        if not objective.startswith(("Govern a captioned verified ", "Govern verified ")):
            objective = "Govern verified media evidence about content pending extraction"
        try:
            elapsed = max(0, int((dt.datetime.now(dt.timezone.utc) - parse_utc(str(source["created_at"]))).total_seconds()))
        except (TypeError, ValueError):
            elapsed = 0
        elapsed_text = f"{elapsed // 60}m {elapsed % 60}s" if elapsed >= 60 else f"{elapsed}s"
        worker = "Local intake worker · complete" if terminal else "Local intake worker · bounded private processing"
        return (
            "<b>Brain intake</b>\n"
            f"<b>Objective</b>\n{bounded_html(objective)}\n\n"
            f"<b>Phase</b>\n{bounded_html(phase)} · {progress}%\n\n"
            "<b>Owner and route</b>\n"
            "Josh 2.0 · private Brain intake\n"
            f"{bounded_html(cls._route_line(verified=route_verified, route_class=route_class))}\n\n"
            f"<b>Active work</b>\n• {bounded_html(worker)}\n\n"
            f"Elapsed: {bounded_html(elapsed_text)} · updated {bounded_html(dt.datetime.now().astimezone().strftime('%H:%M %Z'))}"
        )

    @staticmethod
    def _receipt_value(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(clean_text(item, 120) for item in value[:8]) or "n/a"
        if isinstance(value, dict):
            parts: list[str] = []
            if isinstance(value.get("count"), int):
                parts.append(str(max(0, int(value["count"]))))
            for key in ("types", "coverage", "routes", "reasons", "categories"):
                items = value.get(key)
                if isinstance(items, list):
                    safe_items = [clean_text(item, 80) for item in items[:8] if clean_text(item, 80)]
                    if safe_items:
                        parts.append(f"{key}: {', '.join(safe_items)}")
            return " · ".join(parts) or "n/a"
        if isinstance(value, (str, int, float, bool)):
            return clean_text(value, 300) or "n/a"
        return "n/a"

    @classmethod
    def render_final(cls, payload: Mapping[str, Any]) -> str:
        outcome = clean_text(payload.get("terminalStatus"), 40)
        receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
        complete = "Yes" if outcome in {"succeeded", "partial"} else "No"
        error_class = clean_text(payload.get("errorClass"), 80)
        issues = "n/a" if error_class in {"", "n/a"} else error_class
        route_verified, route_class = cls._terminal_route_evidence(payload)
        route_line = cls._route_line(verified=route_verified, route_class=route_class).replace(" · ", " | ")
        why = "Frozen worker receipt" if route_verified else "No verified worker route evidence"
        rows = [
            f"{route_line} | Why: {why}",
            f"<b>Complete:</b> {bounded_html(complete)} · {bounded_html(outcome or 'failed')}",
            "<b>What was done:</b>",
        ]
        for field in FINAL_RECEIPT_FIELDS:
            rows.append(f"<b>{bounded_html(field)}:</b> {bounded_html(cls._receipt_value(receipt.get(field)), 500)}")
        rows.extend([
            "",
            f"<b>Issues:</b> {bounded_html(issues)}",
            "<b>Appropriate next steps:</b> Use the reply-bound correction or Forget instruction if needed.",
        ])
        return "\n".join(rows)

    def _terminal_payload(self, work_id: str) -> dict[str, Any]:
        with self.store.connect() as db:
            row = db.execute(
                """SELECT private_payload_json,payload_hash FROM intake_results WHERE work_id=?
                   UNION ALL
                   SELECT private_payload_json,payload_hash FROM intake_terminal_prepares WHERE work_id=?
                   LIMIT 1""",
                (work_id, work_id),
            ).fetchone()
        if not row:
            raise BrainGatewayError("brain-terminal-payload-missing")
        try:
            payload = json.loads(str(row["private_payload_json"]))
        except json.JSONDecodeError as exc:
            raise BrainGatewayError("brain-terminal-payload-corrupt") from exc
        actual = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        if actual != str(row["payload_hash"]):
            raise BrainGatewayError("brain-terminal-payload-corrupt")
        if payload.get("surfaceContract") != "brain-intake" or payload.get("owner") != "josh2":
            raise BrainGatewayError("brain-terminal-payload-invalid")
        return payload

    def _bootstrap(self, source: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
        work_id = str(source["work_id"])
        surface = self._surface(work_id)
        if not receipt.get("reactionDelivered"):
            result = self._deliver_effect(
                source, receipt, "reaction", "setMessageReaction",
                self._reaction_payload(source), message_required=False,
            )
            self._set_surface(work_id, reaction_state=result["state"], error_class="" if result["state"] == "delivered" else result["state"])
            if result["state"] != "delivered":
                return receipt
        receipt = self._advance_to_acknowledged(str(receipt["workId"]))
        if receipt["phase"] == "terminal":
            return receipt
        if not receipt.get("cardCreated"):
            text = self.render_card(source, receipt)
            result = self._deliver_effect(
                source, receipt, "card", "sendMessage",
                self._card_payload(source, text), message_required=True,
            )
            fields: dict[str, Any] = {
                "card_state": result["state"],
                "error_class": "" if result["state"] == "delivered" else result["state"],
            }
            if result["state"] == "delivered":
                fields.update({
                    "card_message_ref": result["messageRef"],
                    "last_render_hash": hashlib.sha256(text.encode()).hexdigest(),
                    "last_ingestion_phase": source["phase"],
                    "last_card_sequence": int(receipt["sequence"]),
                })
            self._set_surface(work_id, **fields)
        return self._gateway().read_work(str(receipt["workId"])) or receipt

    def _update_card(self, source: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
        visibility_stage = (
            "verifying"
            if receipt.get("phase") == "verifying" or source["phase"] in {"candidate_pending", "reviewing", "indexed", "unsupported", "quarantined"}
            else "processing"
        )
        self._ensure_visibility(receipt, visibility_stage)
        surface = self._surface(str(source["work_id"]))
        if not surface["card_message_ref"] or surface["card_state"] != "delivered":
            return receipt
        if source["phase"] == surface["last_ingestion_phase"]:
            return receipt
        receipt = self._record_phase_change(receipt)
        if receipt["phase"] == "terminal":
            return receipt
        text = self.render_card(source, receipt)
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest == surface["last_render_hash"]:
            self._set_surface(str(source["work_id"]), last_ingestion_phase=source["phase"])
            return receipt
        result = self._deliver_effect(
            source, receipt, "card_edit", "editMessageText",
            self._edit_payload(source, str(surface["card_message_ref"]), text),
            message_required=False,
        )
        if result["state"] == "delivered":
            self._set_surface(
                str(source["work_id"]), last_render_hash=digest,
                last_ingestion_phase=source["phase"], last_card_sequence=int(receipt["sequence"]),
            )
        else:
            self._set_surface(str(source["work_id"]), error_class=result["state"])
        return self._gateway().read_work(str(receipt["workId"])) or receipt

    def _finish_terminal(self, source: Mapping[str, Any], receipt: Mapping[str, Any]) -> str:
        work_id = str(source["work_id"])
        # The committed payload is immutable and hash-checked.  It is the only
        # source allowed to turn the visible card from pending to a route claim.
        payload = self._terminal_payload(work_id)
        route_verified, route_class = self._terminal_route_evidence(payload)
        # Final delivery is forbidden until the terminal lifecycle is durably
        # accepted by the local Control Tower work ledger / Brain Feed path.
        for visibility_stage in (
            "receipt_ready", "processing", "verifying", "terminal_committed",
        ):
            if not self._ensure_visibility(receipt, visibility_stage):
                return "visibility_pending"
        surface = self._surface(work_id)
        if surface["card_message_ref"] and surface["close_state"] == "pending":
            text = self.render_card(
                source,
                receipt,
                terminal=True,
                route_verified=route_verified,
                route_class=route_class,
            )
            close = self._deliver_effect(
                source, receipt, "card_edit", "editMessageText",
                self._edit_payload(source, str(surface["card_message_ref"]), text),
                message_required=False,
            )
            self._set_surface(work_id, close_state=close["state"], error_class="" if close["state"] == "delivered" else close["state"])
            if close["state"] != "delivered":
                return "incident"
            surface = self._surface(work_id)
        if surface["close_state"] != "delivered":
            # A separate final would falsely imply the managed card closed.
            # Unknown/failed close outcomes require operator reconciliation;
            # they never permit a duplicate edit or a final send.
            return "incident"

        current = self._gateway().read_work(str(receipt["workId"]))
        if not current:
            raise BrainGatewayError("brain-lifecycle-missing")
        state = str(current["deliveryState"])
        if state in {"delivered", "indeterminate", "dead_letter"}:
            self._ensure_visibility(current, state)
            return state
        gateway = self._gateway()
        claim = gateway.claim_terminal_delivery(str(receipt["workId"]))
        if not claim.get("allowed") and claim.get("state") not in {"sending"}:
            return str(claim.get("state") or "dead_letter")
        if claim.get("allowed"):
            claimed_payload = claim.get("payload")
            if not isinstance(claimed_payload, dict) or claimed_payload != payload:
                gateway.finish_terminal_delivery(str(receipt["workId"]), "dead_letter")
                raise BrainGatewayError("brain-terminal-payload-conflict")
        current = gateway.read_work(str(receipt["workId"])) or receipt
        final = self._deliver_effect(
            source, current, "final", "sendMessage",
            self._card_payload(source, self.render_final(payload)),
            message_required=True,
        )
        self._set_surface(
            work_id, final_state=final["state"],
            final_message_ref=final["messageRef"] if final["state"] == "delivered" else "",
            error_class="" if final["state"] == "delivered" else final["state"],
        )
        gateway.finish_terminal_delivery(str(receipt["workId"]), final["state"])
        self._ensure_visibility(gateway.read_work(str(receipt["workId"])) or receipt, final["state"])
        return final["state"]

    def process_work(self, work_id: str) -> str:
        source = self._source(work_id)
        self._bind_surface(source)
        if not self._claim_lease(work_id):
            return "leased"
        try:
            gateway = self._gateway()
            receipt = gateway.read_work(str(source["lifecycle_work_id"]))
            if not receipt or receipt.get("surfaceContract") != "brain-intake" or receipt.get("currentOwner") != "josh2":
                raise BrainGatewayError("brain-lifecycle-binding-invalid")
            if not receipt.get("writerEnabled"):
                return "killed"
            if receipt["phase"] != "terminal":
                receipt = self._bootstrap(source, receipt)
                if not receipt.get("reactionDelivered") or not receipt.get("cardCreated"):
                    return "surface_pending"
                self._ensure_visibility(receipt, "receipt_ready")
                source = self._source(work_id)
                receipt = self._update_card(source, receipt)
            if receipt["phase"] == "terminal":
                if not receipt.get("reactionDelivered") or not receipt.get("cardCreated"):
                    self._set_surface(work_id, error_class="terminal-before-surface")
                    return "incident"
                source = self._source(work_id)
                return self._finish_terminal(source, receipt)
            return "active"
        finally:
            self._release_lease(work_id)

    def run_once(self, *, max_work: int = DEFAULT_MAX_WORK, max_seconds: int = DEFAULT_MAX_SECONDS) -> dict[str, Any]:
        bounded_work = max(1, min(int(max_work), 64))
        bounded_seconds = max(1, min(int(max_seconds), 60))
        counts: dict[str, int] = {
            "examined": 0, "active": 0, "delivered": 0, "surfacePending": 0,
            "indeterminate": 0, "deadLetter": 0, "killed": 0, "leased": 0,
            "incident": 0, "visibilityPending": 0, "errors": 0,
        }
        started = time.monotonic()
        for work_id in self._candidate_work(bounded_work):
            if time.monotonic() - started >= bounded_seconds:
                break
            counts["examined"] += 1
            try:
                state = self.process_work(work_id)
            except Exception as exc:
                counts["errors"] += 1
                try:
                    source = self._source(work_id)
                    self._bind_surface(source)
                    if self._claim_lease(work_id):
                        try:
                            self._set_surface(work_id, error_class=safe_error_class(exc))
                        finally:
                            self._release_lease(work_id)
                except Exception:
                    pass
                continue
            key = {
                "active": "active", "delivered": "delivered", "surface_pending": "surfacePending",
                "indeterminate": "indeterminate", "dead_letter": "deadLetter", "killed": "killed",
                "leased": "leased", "incident": "incident",
                "visibility_pending": "visibilityPending",
            }.get(state)
            if key:
                counts[key] += 1
        return {
            "ok": counts["errors"] == 0 and counts["incident"] == 0,
            "dispatcherSchemaVersion": DISPATCHER_SCHEMA_VERSION,
            "counts": counts,
            "privacy": {"rawContentIncluded": False, "identifiersIncluded": False, "countsOnly": True},
        }

    def status(self) -> dict[str, Any]:
        with self.connect() as db:
            surfaces = int(db.execute("SELECT COUNT(*) FROM surfaces").fetchone()[0])
            attempts = {
                str(row["stage"]): int(row["count"])
                for row in db.execute("SELECT stage,COUNT(*) AS count FROM attempts GROUP BY stage")
            }
            incidents = int(db.execute(
                "SELECT COUNT(*) FROM surfaces WHERE error_class!=''",
            ).fetchone()[0])
            visibility = {
                str(row["state"]): int(row["count"])
                for row in db.execute("SELECT state,COUNT(*) AS count FROM visibility_outbox GROUP BY state")
            }
        return {
            "ok": (
                incidents == 0
                and int(attempts.get("indeterminate", 0)) == 0
                and int(visibility.get("dead_letter", 0)) == 0
            ),
            "dispatcherSchemaVersion": DISPATCHER_SCHEMA_VERSION,
            "surfaceCount": surfaces,
            "attemptStates": attempts,
            "visibilityStates": visibility,
            "incidentCount": incidents,
            "privacy": {"rawContentIncluded": False, "identifiersIncluded": False, "countsOnly": True},
        }


def parser() -> argparse.ArgumentParser:
    repo = Path(__file__).resolve().parents[1]
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", default=os.environ.get(
        "BRAIN_INTAKE_ROOT", str(Path.home() / ".openclaw/private/brain-intake"),
    ))
    result.add_argument("--lifecycle-root", default=os.environ.get(
        "TELEGRAM_LIFECYCLE_ROOT", str(Path.home() / ".openclaw/private/telegram-lifecycle"),
    ))
    result.add_argument("--rollout", default=os.environ.get(
        "TELEGRAM_LIFECYCLE_ROLLOUT", str(repo / "config/telegram-lifecycle-rollout.json"),
    ))
    result.add_argument("--config", default=os.environ.get(
        "TELEGRAM_INTAKE_LANES", str(repo / "config/telegram-intake-lanes.json"),
    ))
    result.add_argument("--topic-receipt", default=os.environ.get(
        "BRAIN_TOPIC_RECEIPT",
        str(Path.home() / ".openclaw/private/telegram-topic-control/brain-topic-creation.json"),
    ))
    result.add_argument("--state-root", default=os.environ.get(
        "BRAIN_GATEWAY_STATE_ROOT", str(Path.home() / ".openclaw/private/brain-gateway-dispatcher"),
    ))
    sub = result.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run-once")
    run.add_argument("--max-work", type=int, default=DEFAULT_MAX_WORK)
    run.add_argument("--max-seconds", type=int, default=DEFAULT_MAX_SECONDS)
    sub.add_parser("status")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        dispatcher = BrainGatewayDispatcher(
            args.root,
            lifecycle_root=args.lifecycle_root,
            rollout_path=args.rollout,
            config_path=args.config,
            topic_receipt_path=args.topic_receipt,
            state_root=args.state_root,
        )
        result = dispatcher.status() if args.command == "status" else dispatcher.run_once(
            max_work=args.max_work, max_seconds=args.max_seconds,
        )
    except Exception as exc:
        result = {
            "ok": False,
            "dispatcherSchemaVersion": DISPATCHER_SCHEMA_VERSION,
            "errorClass": safe_error_class(exc),
            "privacy": {"rawContentIncluded": False, "identifiersIncluded": False, "countsOnly": True},
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
