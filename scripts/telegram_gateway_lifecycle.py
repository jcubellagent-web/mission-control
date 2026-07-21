#!/usr/bin/env python3
"""Private, versioned lifecycle boundary for Telegram gateway work.

This module deliberately contains no Telegram transport credentials and never
publishes raw user text.  Josh 2.0 and JAIMES use it as the one durable
coordination boundary before their trusted gateway adapters perform a visible
Telegram effect.  Existing v2 receipts remain readable while new v3 writers
are controlled by the canonical rollout file.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


SCHEMA_VERSION = 3
LIFECYCLE_VERSION = 3
RENDERER_VERSION = "telegram-html-v3"
CLASSIFIER_VERSION = "delivery-tier-v3"
SUPPORTED_READER_VERSIONS = frozenset({2, 3})
ALLOWED_OWNERS = frozenset({"josh2", "jaimes"})
WORK_ID_RE = re.compile(r"^work-telegram-[0-9a-f]{24}$")

PHASES = frozenset({
    "received", "classified", "acknowledged", "working", "awaiting_input",
    "verifying", "terminal",
})
OUTCOMES = frozenset({
    "succeeded", "partial", "failed", "cancelled", "superseded", "expired",
})
DELIVERY_STATES = frozenset({
    "pending", "sending", "delivered", "indeterminate", "dead_letter",
})
EFFECT_KINDS = frozenset({"reaction", "card", "card_edit", "final", "callback_ack", "topic_create"})
SINGLETON_EFFECT_KINDS = frozenset({"reaction", "card", "final", "topic_create"})
SURFACE_CONTRACTS = frozenset({"telegram", "brain-intake", "native-desktop", "native-web"})
ACTION_ALLOWLIST = frozenset({
    "cancel", "retry-safe", "approve-memory", "reject-memory", "reference-only",
    "correct", "privacy", "forget-preview", "forget-confirm", "supersede-memory",
    "handoff",
})

LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "received": frozenset({"classified", "terminal"}),
    "classified": frozenset({"acknowledged", "terminal"}),
    "acknowledged": frozenset({"working", "awaiting_input", "terminal"}),
    "working": frozenset({"awaiting_input", "verifying", "terminal"}),
    "awaiting_input": frozenset({"working", "verifying", "terminal"}),
    "verifying": frozenset({"working", "awaiting_input", "terminal"}),
    "terminal": frozenset(),
}

SAFE_REASON_CODES = frozenset({
    "brain-media", "mutation", "tool-use", "delegation", "approval", "multi-step",
    "long-running", "uncertain", "quick-answer", "conversation", "promotion",
})

TELEMETRY_FIELDS = frozenset({
    "schemaVersion", "lifecycleVersion", "rendererVersion", "classifierVersion",
    "deliveryTier", "classifierReason", "workId", "runId", "generation", "sequence",
    "fencingEpoch", "intakeAgent", "currentOwner", "phase", "outcome",
    "deliveryState", "surfaceContract", "workerRoute", "mediaClass", "privacyClass",
    "candidateCount", "promotionCount", "reviewCount", "duplicateStatus",
    "latencyBucket", "errorClass", "sourceCoverage", "status",
})

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
COMPLEX_TASK_RE = re.compile(
    r"\b(?:change|create|delete|deploy|restart|send|update|edit|install|configure|"
    r"build|implement|debug|investigate|research|compare|analy[sz]e|verify|test|"
    r"upload|download|approve|connect|login|account|email|calendar|linear|github|"
    r"delegate|handoff|monitor|wait|run|execute|fix|memory|remember|forget)\b",
    re.I,
)
CONVERSATION_RE = re.compile(
    r"^\s*(?:hi|hello|hey|thanks|thank you|good (?:morning|afternoon|evening)|"
    r"how are you|ok(?:ay)?|cool|great|nice)[!.?\s]*$",
    re.I,
)
QUICK_QUESTION_RE = re.compile(
    r"^\s*(?:what|who|when|where|why|how|is|are|can|could|does|do|did|will|would)\b",
    re.I,
)


class LifecycleError(RuntimeError):
    """Base class for fail-closed lifecycle errors."""


class UnknownSchemaError(LifecycleError):
    pass


class IllegalTransitionError(LifecycleError):
    pass


class StaleEventError(LifecycleError):
    pass


class UnauthorizedActionError(LifecycleError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> dt.datetime:
    text = str(value or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def stable_id(prefix: str, *parts: Any, length: int = 24) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    return f"{prefix}-{hashlib.sha256(material.encode()).hexdigest()[:length]}"


def canonical_work_id(key: str, run_id: str) -> str:
    """Match the identity already emitted by both canonical fast-ack owners."""
    material = f"{key}|{run_id}".encode("utf-8")
    return f"work-telegram-{hashlib.sha256(material).hexdigest()[:24]}"


def clean_plain_text(value: Any, limit: int = 1200) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = CONTROL_CHARS.sub("", text)
    text = " ".join(text.split())
    return text[:limit]


def safe_html_text(value: Any, limit: int = 1200) -> str:
    # Truncate before escaping so an entity can never be split.
    return html.escape(clean_plain_text(value, limit), quote=False)


def payload_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def classify_delivery_tier(
    text: str,
    *,
    has_media: bool = False,
    brain: bool = False,
    tool_expected: bool = False,
    mutation_expected: bool = False,
    delegated: bool = False,
    approval_required: bool = False,
) -> tuple[int, str]:
    """Return a conservative delivery tier and a dashboard-safe reason code."""
    compact = clean_plain_text(text, 4000)
    if brain or has_media:
        return 3, "brain-media"
    if mutation_expected:
        return 3, "mutation"
    if tool_expected:
        return 3, "tool-use"
    if delegated:
        return 3, "delegation"
    if approval_required:
        return 3, "approval"
    if CONVERSATION_RE.fullmatch(compact):
        return 1, "conversation"
    if COMPLEX_TASK_RE.search(compact):
        return 3, "multi-step"
    if QUICK_QUESTION_RE.search(compact) and len(compact) <= 220 and compact.count("\n") <= 1:
        return 2, "quick-answer"
    return 3, "uncertain"


@dataclass(frozen=True)
class RolloutPolicy:
    master_state: str = "off"
    global_kill_switch: bool = False
    brain_kill_switch: bool = True
    host_enabled: Mapping[str, bool] | None = None
    writer_version: int = LIFECYCLE_VERSION
    reader_versions: Sequence[int] = (2, 3)
    shadow_min_samples: int = 20
    brain_fixture_minimum: int = 20

    @classmethod
    def load(cls, path: Path | str | None) -> "RolloutPolicy":
        if not path:
            return cls()
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError):
            return cls(global_kill_switch=True)
        return cls(
            master_state=str(data.get("masterState") or "off"),
            global_kill_switch=bool(data.get("globalKillSwitch", False)),
            brain_kill_switch=bool(data.get("brainKillSwitch", True)),
            host_enabled={str(k): bool(v) for k, v in (data.get("hosts") or {}).items()},
            writer_version=int(data.get("writerLifecycleVersion") or LIFECYCLE_VERSION),
            reader_versions=tuple(int(v) for v in data.get("readerLifecycleVersions") or (2, 3)),
            shadow_min_samples=max(20, int(data.get("shadowMinimumPerOwner") or 20)),
            brain_fixture_minimum=max(20, int(data.get("brainFixtureMinimum") or 20)),
        )

    def validate(self) -> None:
        if self.master_state not in {"off", "shadow", "josh2", "jaimes", "all"}:
            raise LifecycleError("unknown-rollout-state")
        if self.writer_version not in SUPPORTED_READER_VERSIONS:
            raise UnknownSchemaError("writer-version-not-readable")
        if not {2, 3}.issubset(set(self.reader_versions)):
            raise UnknownSchemaError("n-and-n-minus-one-readers-required")
        if self.shadow_min_samples < 20 or self.brain_fixture_minimum < 20:
            raise LifecycleError("rollout-evidence-minimum-below-floor")
        if not set((self.host_enabled or {}).keys()).issubset(ALLOWED_OWNERS):
            raise LifecycleError("unknown-rollout-owner")

    def writer_enabled(self, owner: str) -> bool:
        self.validate()
        if owner not in ALLOWED_OWNERS:
            return False
        # A v2 pin hands visible writes back to the legacy N-1 implementation;
        # this v3 journal remains readable but must not pretend to be that writer.
        if self.writer_version != LIFECYCLE_VERSION:
            return False
        if self.global_kill_switch or not (self.host_enabled or {}).get(owner, True):
            return False
        return self.master_state == "all" or self.master_state == owner

    def shadow_enabled(self, owner: str) -> bool:
        self.validate()
        return (
            owner in ALLOWED_OWNERS
            and self.writer_version == LIFECYCLE_VERSION
            and not self.global_kill_switch
            and (self.host_enabled or {}).get(owner, True)
            and self.master_state == "shadow"
        )

    def brain_enabled(self, owner: str = "josh2") -> bool:
        return self.writer_enabled(owner) and not self.brain_kill_switch


class GatewayLifecycle:
    """SQLite-backed, effective-once lifecycle and action journal."""

    def __init__(
        self,
        root: Path | str,
        *,
        rollout: RolloutPolicy | None = None,
        owner: str = "josh2",
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.db_path = self.root / "lifecycle.sqlite3"
        self.owner = owner
        self.rollout = rollout or RolloutPolicy()
        self.rollout.validate()
        with self.connect() as db:
            self._init_schema(db)

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()
            if self.db_path.exists():
                os.chmod(self.db_path, stat.S_IRUSR | stat.S_IWUSR)

    def _init_schema(self, db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS work_receipts (
              work_id TEXT PRIMARY KEY,
              origin_key TEXT NOT NULL UNIQUE,
              run_id TEXT NOT NULL,
              generation INTEGER NOT NULL,
              schema_version INTEGER NOT NULL,
              lifecycle_version INTEGER NOT NULL,
              renderer_version TEXT NOT NULL,
              classifier_version TEXT NOT NULL,
              delivery_tier INTEGER NOT NULL,
              classifier_reason TEXT NOT NULL,
              sequence INTEGER NOT NULL,
              fencing_epoch INTEGER NOT NULL,
              intake_agent TEXT NOT NULL,
              current_owner TEXT NOT NULL,
              worker_route TEXT NOT NULL,
              phase TEXT NOT NULL,
              outcome TEXT,
              delivery_state TEXT NOT NULL,
              surface_contract TEXT NOT NULL,
              source_revision INTEGER NOT NULL,
              card_created INTEGER NOT NULL DEFAULT 0,
              reaction_delivered INTEGER NOT NULL DEFAULT 0,
              final_delivered INTEGER NOT NULL DEFAULT 0,
              cancel_requested INTEGER NOT NULL DEFAULT 0,
              render_hash TEXT NOT NULL DEFAULT '',
              shadow_only INTEGER NOT NULL DEFAULT 0,
              writer_authority_at_start INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              terminal_at TEXT
            );
            CREATE TABLE IF NOT EXISTS lifecycle_events (
              event_id TEXT PRIMARY KEY,
              work_id TEXT NOT NULL,
              sequence INTEGER NOT NULL,
              fencing_epoch INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              safe_payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(work_id, sequence),
              FOREIGN KEY(work_id) REFERENCES work_receipts(work_id)
            );
            CREATE TABLE IF NOT EXISTS effects (
              idempotency_key TEXT PRIMARY KEY,
              work_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              scope_ref TEXT NOT NULL DEFAULT '',
              sequence INTEGER NOT NULL,
              fencing_epoch INTEGER NOT NULL,
              state TEXT NOT NULL,
              private_receipt TEXT NOT NULL DEFAULT '',
              error_class TEXT NOT NULL DEFAULT '',
              attempts INTEGER NOT NULL DEFAULT 0,
              intent_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(work_id, kind, sequence, scope_ref),
              FOREIGN KEY(work_id) REFERENCES work_receipts(work_id)
            );
            CREATE TABLE IF NOT EXISTS terminal_outbox (
              event_id TEXT PRIMARY KEY,
              work_id TEXT NOT NULL UNIQUE,
              outcome TEXT NOT NULL,
              state TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              payload_hash TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(work_id) REFERENCES work_receipts(work_id)
            );
            CREATE TABLE IF NOT EXISTS actions (
              token_hash TEXT PRIMARY KEY,
              nonce_hash TEXT NOT NULL UNIQUE,
              work_id TEXT NOT NULL,
              lifecycle_revision INTEGER NOT NULL,
              authorized_owner TEXT NOT NULL,
              authorized_user TEXT NOT NULL,
              chat_ref TEXT NOT NULL,
              topic_ref TEXT NOT NULL,
              message_ref TEXT NOT NULL,
              artifact_ref TEXT NOT NULL,
              action TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              consumed_at TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(work_id) REFERENCES work_receipts(work_id)
            );
            CREATE TABLE IF NOT EXISTS shadow_samples (
              id TEXT PRIMARY KEY,
              owner TEXT NOT NULL,
              work_id TEXT NOT NULL,
              tier INTEGER NOT NULL,
              reason TEXT NOT NULL,
              legacy_contract TEXT NOT NULL,
              matched INTEGER NOT NULL,
              terminal_observed INTEGER NOT NULL DEFAULT 0,
              terminal_delivered INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              UNIQUE(owner, work_id)
            );
            """
        )
        # Fail closed when opening a database created by the short-lived v3
        # prototype: pre-owner-bound callback tokens remain unreadable instead
        # of being silently rebound to whichever owner happens to be current.
        action_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(actions)")}
        if "authorized_owner" not in action_columns:
            db.execute("ALTER TABLE actions ADD COLUMN authorized_owner TEXT NOT NULL DEFAULT ''")
        shadow_columns = {
            str(row["name"]) for row in db.execute("PRAGMA table_info(shadow_samples)")
        }
        if "terminal_observed" not in shadow_columns:
            # Pre-release shadow rows did not prove the legacy final receipt.
            # Backfill as unobserved so they can never satisfy promotion.
            db.execute(
                "ALTER TABLE shadow_samples ADD COLUMN "
                "terminal_observed INTEGER NOT NULL DEFAULT 0"
            )
        if "terminal_delivered" not in shadow_columns:
            db.execute(
                "ALTER TABLE shadow_samples ADD COLUMN "
                "terminal_delivered INTEGER NOT NULL DEFAULT 0"
            )
        effect_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(effects)")}
        if "scope_ref" not in effect_columns:
            # v3 originally keyed effects only by work/kind/revision.  That is
            # correct for singleton surfaces but collapses independent
            # postterminal action acknowledgements.  Rebuild once, preserving
            # every prior receipt under the empty legacy scope.
            db.executescript(
                """
                CREATE TABLE effects_scoped (
                  idempotency_key TEXT PRIMARY KEY,
                  work_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  scope_ref TEXT NOT NULL DEFAULT '',
                  sequence INTEGER NOT NULL,
                  fencing_epoch INTEGER NOT NULL,
                  state TEXT NOT NULL,
                  private_receipt TEXT NOT NULL DEFAULT '',
                  error_class TEXT NOT NULL DEFAULT '',
                  attempts INTEGER NOT NULL DEFAULT 0,
                  intent_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(work_id, kind, sequence, scope_ref),
                  FOREIGN KEY(work_id) REFERENCES work_receipts(work_id)
                );
                INSERT INTO effects_scoped(
                  idempotency_key,work_id,kind,scope_ref,sequence,fencing_epoch,
                  state,private_receipt,error_class,attempts,intent_at,updated_at
                )
                SELECT idempotency_key,work_id,kind,'',sequence,fencing_epoch,
                       state,private_receipt,error_class,attempts,intent_at,updated_at
                  FROM effects;
                DROP TABLE effects;
                ALTER TABLE effects_scoped RENAME TO effects;
                """
            )
        work_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(work_receipts)")}
        if "writer_authority_at_start" not in work_columns:
            # Additive v3 migration.  A receipt is backfilled only when its
            # original authority is still directly knowable: the owner is a
            # writer during this migration, or the non-shadow receipt already
            # advanced beyond its initial event / reserved a visible effect.
            # Ambiguous sequence-1 receipts encountered after rollback remain
            # fail-closed so an old off-mode observation cannot steal work from
            # the legacy N-1 writer.
            db.execute(
                "ALTER TABLE work_receipts ADD COLUMN "
                "writer_authority_at_start INTEGER NOT NULL DEFAULT 0"
            )
            candidates = db.execute(
                """SELECT work_id,current_owner,surface_contract,sequence
                     FROM work_receipts
                    WHERE lifecycle_version=? AND shadow_only=0""",
                (LIFECYCLE_VERSION,),
            ).fetchall()
            for row in candidates:
                owner = str(row["current_owner"])
                surface = str(row["surface_contract"])
                policy_authority = (
                    self.rollout.brain_enabled(owner)
                    if surface == "brain-intake"
                    else self.rollout.writer_enabled(owner)
                )
                evidence = int(row["sequence"]) > 1 or bool(db.execute(
                    "SELECT 1 FROM effects WHERE work_id=? LIMIT 1",
                    (row["work_id"],),
                ).fetchone())
                if policy_authority or evidence:
                    db.execute(
                        "UPDATE work_receipts SET writer_authority_at_start=1 WHERE work_id=?",
                        (row["work_id"],),
                    )

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

    def start_work(
        self,
        *,
        origin_key: str,
        run_id: str,
        intake_agent: str,
        current_owner: str,
        surface_contract: str,
        text: str = "",
        has_media: bool = False,
        brain: bool = False,
        generation: int = 1,
        source_revision: int = 1,
        worker_route: str = "",
        classification: tuple[int, str] | None = None,
        work_id: str | None = None,
    ) -> dict[str, Any]:
        if surface_contract not in SURFACE_CONTRACTS:
            raise LifecycleError("unknown-surface-contract")
        if surface_contract.startswith("native-"):
            # Native Codex surfaces must never acquire Telegram formatting or
            # effect records.  This return value is deliberately non-writable.
            return {
                "surfaceContract": surface_contract,
                "native": True,
                "writerEnabled": False,
                "deliveryTier": None,
            }
        raw_origin_key = str(origin_key or "")
        raw_run_id = str(run_id or "")
        if not raw_origin_key or not raw_run_id:
            raise LifecycleError("work-identity-missing")
        if current_owner not in ALLOWED_OWNERS:
            raise LifecycleError("unknown-current-owner")
        tier, reason = classification or classify_delivery_tier(text, has_media=has_media, brain=brain)
        if tier not in {1, 2, 3} or reason not in SAFE_REASON_CODES:
            raise LifecycleError("invalid-delivery-classification")
        requested_work_id = str(work_id or canonical_work_id(raw_origin_key, raw_run_id))
        if not WORK_ID_RE.fullmatch(requested_work_id):
            raise LifecycleError("invalid-work-id")
        safe_run_id = clean_plain_text(raw_run_id, 160)
        now = utc_now()
        shadow = self.rollout.shadow_enabled(current_owner)
        writer = self.rollout.brain_enabled(current_owner) if surface_contract == "brain-intake" else self.rollout.writer_enabled(current_owner)
        with self.connect() as db, self.transaction(db):
            existing = db.execute("SELECT * FROM work_receipts WHERE origin_key=?", (raw_origin_key,)).fetchone()
            if existing:
                self._validate_reader(existing)
                immutable_matches = (
                    hmac.compare_digest(str(existing["work_id"]), requested_work_id)
                    and hmac.compare_digest(str(existing["run_id"]), safe_run_id)
                    and hmac.compare_digest(str(existing["current_owner"]), str(current_owner))
                    and hmac.compare_digest(str(existing["surface_contract"]), str(surface_contract))
                )
                if not immutable_matches:
                    raise LifecycleError("work-identity-mismatch")
                return self._public_receipt(existing)
            collision = db.execute("SELECT origin_key FROM work_receipts WHERE work_id=?", (requested_work_id,)).fetchone()
            if collision:
                raise LifecycleError("work-identity-collision")
            db.execute(
                """INSERT INTO work_receipts(
                  work_id,origin_key,run_id,generation,schema_version,lifecycle_version,
                  renderer_version,classifier_version,delivery_tier,classifier_reason,
                  sequence,fencing_epoch,intake_agent,current_owner,worker_route,phase,
                  outcome,delivery_state,surface_contract,source_revision,shadow_only,
                  writer_authority_at_start,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    requested_work_id, raw_origin_key, safe_run_id, max(1, generation), SCHEMA_VERSION,
                    LIFECYCLE_VERSION, RENDERER_VERSION, CLASSIFIER_VERSION, tier, reason,
                    1, 1, clean_plain_text(intake_agent, 40), clean_plain_text(current_owner, 40),
                    clean_plain_text(worker_route, 120), "received", None, "pending",
                    surface_contract, max(1, source_revision), int(shadow), int(writer), now, now,
                ),
            )
            self._insert_event(db, requested_work_id, 1, 1, "received", {
                "deliveryTier": tier,
                "classifierReason": reason,
                "surfaceContract": surface_contract,
            })
            row = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (requested_work_id,)).fetchone()
        return self._public_receipt(row)

    def _validate_reader(self, row: sqlite3.Row | Mapping[str, Any]) -> None:
        version = int(row["lifecycle_version"])
        if version not in SUPPORTED_READER_VERSIONS:
            raise UnknownSchemaError("unknown-future-lifecycle-version")

    def _public_receipt(self, row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        self._validate_reader(row)
        return {
            "schemaVersion": int(row["schema_version"]),
            "lifecycleVersion": int(row["lifecycle_version"]),
            "rendererVersion": row["renderer_version"],
            "classifierVersion": row["classifier_version"],
            "eventId": stable_id(
                "receipt",
                row["work_id"],
                row["generation"],
                row["sequence"],
                row["fencing_epoch"],
                length=32,
            ),
            "workId": row["work_id"],
            "runId": row["run_id"],
            "generation": int(row["generation"]),
            "deliveryTier": int(row["delivery_tier"]),
            "classifierReason": row["classifier_reason"],
            "sequence": int(row["sequence"]),
            "fencingEpoch": int(row["fencing_epoch"]),
            "intakeAgent": row["intake_agent"],
            "currentOwner": row["current_owner"],
            "workerRoute": row["worker_route"],
            "phase": row["phase"],
            "outcome": row["outcome"],
            "deliveryState": row["delivery_state"],
            "surfaceContract": row["surface_contract"],
            "sourceRevision": int(row["source_revision"]),
            # These booleans are safe lifecycle facts (never Telegram IDs).
            # Brain workers use them as a hard readiness fence so extraction
            # cannot outrun the gateway-owned acknowledgement and live card.
            "reactionDelivered": bool(row["reaction_delivered"]),
            "cardCreated": bool(row["card_created"]),
            "finalDelivered": bool(row["final_delivered"]),
            "cancelRequested": bool(row["cancel_requested"]),
            "shadowOnly": bool(row["shadow_only"]),
            "writerAuthorityAtStart": bool(row["writer_authority_at_start"]),
            "writerEnabled": self._writer_enabled_for_row(row),
        }

    @staticmethod
    def _writer_authority_at_start(row: sqlite3.Row | Mapping[str, Any]) -> bool:
        return (
            int(row["lifecycle_version"]) == LIFECYCLE_VERSION
            and int(row["writer_authority_at_start"]) == 1
        )

    def _visible_write_safety_enabled(self, row: sqlite3.Row | Mapping[str, Any]) -> bool:
        """Apply live emergency stops without re-electing active work."""
        owner = str(row["current_owner"])
        if owner not in ALLOWED_OWNERS:
            return False
        if self.rollout.global_kill_switch or not (self.rollout.host_enabled or {}).get(owner, True):
            return False
        if str(row["surface_contract"]) == "brain-intake" and self.rollout.brain_kill_switch:
            return False
        return True

    def _writer_enabled_for_row(self, row: sqlite3.Row | Mapping[str, Any]) -> bool:
        # Master-state and writer-version changes elect only *new* work.  A v3
        # receipt that won authority at intake remains the sole writer until it
        # safely drains, preventing a rollback from splitting one task between
        # v3 and legacy.  Emergency host/global/Brain stops stay live.
        return self._writer_authority_at_start(row) and self._visible_write_safety_enabled(row)

    def _require_writable(self, row: sqlite3.Row | Mapping[str, Any]) -> None:
        self._validate_reader(row)
        if int(row["lifecycle_version"]) != LIFECYCLE_VERSION:
            raise LifecycleError("lifecycle-receipt-read-only")
        if not self._writer_enabled_for_row(row):
            raise LifecycleError("lifecycle-writer-disabled")

    def _require_simulatable(self, row: sqlite3.Row | Mapping[str, Any]) -> None:
        """Allow private shadow evolution without granting visible effects."""
        self._validate_reader(row)
        if int(row["lifecycle_version"]) != LIFECYCLE_VERSION:
            raise LifecycleError("lifecycle-receipt-read-only")
        pinned_writer = self._writer_authority_at_start(row)
        shadow_enabled = (
            bool(row["shadow_only"])
            and self.rollout.shadow_enabled(str(row["current_owner"]))
        )
        # Private lifecycle bookkeeping may finish while a kill switch fences
        # visible effects; this leaves a durable pending outbox for recovery.
        if not pinned_writer and not shadow_enabled:
            raise LifecycleError("lifecycle-simulation-disabled")

    def read_work(self, work_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
        return self._public_receipt(row) if row else None

    def _insert_event(
        self,
        db: sqlite3.Connection,
        work_id: str,
        sequence: int,
        fencing_epoch: int,
        event_type: str,
        safe_payload: Mapping[str, Any] | None = None,
    ) -> None:
        payload = sanitize_telemetry(safe_payload or {})
        db.execute(
            "INSERT INTO lifecycle_events VALUES(?,?,?,?,?,?,?)",
            (
                stable_id("event", work_id, sequence, event_type, length=32), work_id,
                sequence, fencing_epoch, clean_plain_text(event_type, 80),
                json.dumps(payload, sort_keys=True), utc_now(),
            ),
        )

    def transition(
        self,
        work_id: str,
        phase: str,
        *,
        expected_sequence: int,
        fencing_epoch: int,
        safe_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if phase not in PHASES:
            raise IllegalTransitionError("unknown-phase")
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
            if not row:
                raise LifecycleError("unknown-work")
            self._require_simulatable(row)
            self._validate_event_fence(row, expected_sequence, fencing_epoch)
            current = str(row["phase"])
            if phase == current:
                return self._public_receipt(row)
            if phase not in LEGAL_TRANSITIONS[current]:
                raise IllegalTransitionError(f"illegal-transition:{current}:{phase}")
            if phase == "terminal":
                raise IllegalTransitionError("use-commit-terminal")
            sequence = int(row["sequence"]) + 1
            db.execute(
                "UPDATE work_receipts SET phase=?,sequence=?,updated_at=? WHERE work_id=?",
                (phase, sequence, utc_now(), work_id),
            )
            self._insert_event(db, work_id, sequence, fencing_epoch, phase, safe_payload)
            updated = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
        return self._public_receipt(updated)

    @staticmethod
    def _validate_event_fence(row: sqlite3.Row, expected_sequence: int, fencing_epoch: int) -> None:
        if str(row["phase"]) == "terminal":
            raise StaleEventError("terminal-work-cannot-reopen")
        if int(row["sequence"]) != int(expected_sequence):
            raise StaleEventError("stale-or-out-of-order-sequence")
        if int(row["fencing_epoch"]) != int(fencing_epoch):
            raise StaleEventError("stale-fencing-epoch")

    def promote_tier(self, work_id: str, *, expected_sequence: int, fencing_epoch: int) -> dict[str, Any]:
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
            if not row:
                raise LifecycleError("unknown-work")
            self._require_writable(row)
            self._validate_event_fence(row, expected_sequence, fencing_epoch)
            if int(row["delivery_tier"]) != 2 or bool(row["card_created"]):
                raise IllegalTransitionError("only-tier-2-may-promote-before-card")
            sequence = int(row["sequence"]) + 1
            db.execute(
                "UPDATE work_receipts SET delivery_tier=3,classifier_reason='promotion',sequence=?,updated_at=? WHERE work_id=?",
                (sequence, utc_now(), work_id),
            )
            self._insert_event(db, work_id, sequence, fencing_epoch, "tier-promoted", {
                "deliveryTier": 3, "classifierReason": "promotion",
            })
            updated = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
        return self._public_receipt(updated)

    def request_cancel(self, work_id: str, *, expected_sequence: int, fencing_epoch: int) -> dict[str, Any]:
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
            if not row:
                raise LifecycleError("unknown-work")
            self._require_simulatable(row)
            self._validate_event_fence(row, expected_sequence, fencing_epoch)
            sequence = int(row["sequence"]) + 1
            next_epoch = int(row["fencing_epoch"]) + 1
            db.execute(
                "UPDATE work_receipts SET cancel_requested=1,sequence=?,fencing_epoch=?,updated_at=? WHERE work_id=?",
                (sequence, next_epoch, utc_now(), work_id),
            )
            self._insert_event(db, work_id, sequence, next_epoch, "cancel-requested", {"status": "cancel_requested"})
            updated = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
        return self._public_receipt(updated)

    def update_worker_route(
        self,
        work_id: str,
        worker_route: str,
        *,
        expected_owner: str,
        expected_sequence: int,
        fencing_epoch: int,
    ) -> dict[str, Any]:
        """Record delegation without conflating the worker with task ownership."""
        bounded_route = clean_plain_text(worker_route, 120)
        if not bounded_route:
            raise LifecycleError("worker-route-missing")
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
            if not row:
                raise LifecycleError("unknown-work")
            self._require_simulatable(row)
            self._validate_event_fence(row, expected_sequence, fencing_epoch)
            if not hmac.compare_digest(str(row["current_owner"]), str(expected_owner)):
                raise StaleEventError("stale-current-owner")
            if hmac.compare_digest(str(row["worker_route"]), bounded_route):
                return self._public_receipt(row)
            sequence = int(row["sequence"]) + 1
            db.execute(
                "UPDATE work_receipts SET worker_route=?,sequence=?,updated_at=? WHERE work_id=?",
                (bounded_route, sequence, utc_now(), work_id),
            )
            self._insert_event(db, work_id, sequence, fencing_epoch, "worker-route-updated", {
                "workerRoute": bounded_route,
                "currentOwner": row["current_owner"],
            })
            updated = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
        return self._public_receipt(updated)

    def record_progress(
        self,
        work_id: str,
        *,
        expected_sequence: int,
        fencing_epoch: int,
        status: str = "progress",
    ) -> dict[str, Any]:
        """Advance the event fence for one coalesced, dashboard-safe update."""
        safe_status = clean_plain_text(status, 80)
        if safe_status not in {
            "progress", "phase_change", "heartbeat", "verifying",
            "awaiting_input", "recovery", "delivery",
        }:
            raise LifecycleError("unsafe-progress-status")
        with self.connect() as db, self.transaction(db):
            row = db.execute(
                "SELECT * FROM work_receipts WHERE work_id=?",
                (work_id,),
            ).fetchone()
            if not row:
                raise LifecycleError("unknown-work")
            self._require_simulatable(row)
            if str(row["phase"]) == "terminal":
                raise LifecycleError("progress-after-terminal")
            self._validate_event_fence(row, expected_sequence, fencing_epoch)
            sequence = int(row["sequence"]) + 1
            db.execute(
                "UPDATE work_receipts SET sequence=?,updated_at=? WHERE work_id=?",
                (sequence, utc_now(), work_id),
            )
            self._insert_event(
                db,
                work_id,
                sequence,
                fencing_epoch,
                "progress-recorded",
                {"status": safe_status, "phase": row["phase"]},
            )
            updated = db.execute(
                "SELECT * FROM work_receipts WHERE work_id=?",
                (work_id,),
            ).fetchone()
        return self._public_receipt(updated)

    def accept_handoff(
        self,
        work_id: str,
        new_owner: str,
        *,
        expected_owner: str,
        expected_sequence: int,
        fencing_epoch: int,
    ) -> dict[str, Any]:
        """CAS-accept an ownership handoff and fence the prior owner."""
        if new_owner not in ALLOWED_OWNERS:
            raise LifecycleError("unknown-current-owner")
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
            if not row:
                raise LifecycleError("unknown-work")
            self._require_simulatable(row)
            self._validate_event_fence(row, expected_sequence, fencing_epoch)
            if not hmac.compare_digest(str(row["current_owner"]), str(expected_owner)):
                raise StaleEventError("stale-current-owner")
            if hmac.compare_digest(str(row["current_owner"]), new_owner):
                return self._public_receipt(row)
            sequence = int(row["sequence"]) + 1
            next_epoch = int(row["fencing_epoch"]) + 1
            db.execute(
                "UPDATE work_receipts SET current_owner=?,sequence=?,fencing_epoch=?,updated_at=? WHERE work_id=?",
                (new_owner, sequence, next_epoch, utc_now(), work_id),
            )
            self._insert_event(db, work_id, sequence, next_epoch, "ownership-transferred", {
                "currentOwner": new_owner,
                "status": "ownership_transferred",
            })
            updated = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
        return self._public_receipt(updated)

    def transfer_owner(
        self,
        work_id: str,
        new_owner: str,
        *,
        expected_sequence: int,
        fencing_epoch: int,
    ) -> dict[str, Any]:
        """Backward-compatible wrapper; new callers should use accept_handoff."""
        current = self.read_work(work_id)
        if not current:
            raise LifecycleError("unknown-work")
        return self.accept_handoff(
            work_id,
            new_owner,
            expected_owner=str(current["currentOwner"]),
            expected_sequence=expected_sequence,
            fencing_epoch=fencing_epoch,
        )

    def update_source_revision(
        self,
        work_id: str,
        *,
        source_revision: int,
        expected_sequence: int,
        fencing_epoch: int,
        side_effects_started: bool,
    ) -> dict[str, Any]:
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
            if not row:
                raise LifecycleError("unknown-work")
            self._require_simulatable(row)
            self._validate_event_fence(row, expected_sequence, fencing_epoch)
            if source_revision <= int(row["source_revision"]):
                raise StaleEventError("stale-source-revision")
            sequence = int(row["sequence"]) + 1
            event = "correction-requested" if side_effects_started else "source-revised"
            db.execute(
                "UPDATE work_receipts SET source_revision=?,sequence=?,updated_at=? WHERE work_id=?",
                (source_revision, sequence, utc_now(), work_id),
            )
            self._insert_event(db, work_id, sequence, fencing_epoch, event, {"status": event})
            updated = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
        return self._public_receipt(updated)

    def claim_effect(
        self,
        work_id: str,
        kind: str,
        *,
        sequence: int,
        fencing_epoch: int,
        scope_ref: str = "",
    ) -> dict[str, Any]:
        if kind not in EFFECT_KINDS:
            raise LifecycleError("unknown-effect-kind")
        clean_scope = clean_plain_text(scope_ref, 160)
        if kind == "callback_ack" and not clean_scope:
            raise LifecycleError("callback-ack-scope-required")
        idempotency_key = (
            stable_id("effect", work_id, kind, sequence, clean_scope, length=32)
            if clean_scope
            else stable_id("effect", work_id, kind, sequence, length=32)
        )
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
            if not row:
                raise LifecycleError("unknown-work")
            self._require_writable(row)
            if int(row["sequence"]) != int(sequence):
                raise StaleEventError("stale-or-out-of-order-sequence")
            if int(row["fencing_epoch"]) != int(fencing_epoch):
                raise StaleEventError("stale-fencing-epoch")
            tier = int(row["delivery_tier"])
            if (kind == "reaction" and tier < 2) or (kind in {"card", "card_edit"} and tier != 3):
                raise LifecycleError("effect-not-allowed-for-delivery-tier")
            phase = str(row["phase"])
            if (
                bool(row["cancel_requested"])
                and kind in {"reaction", "card", "card_edit", "topic_create"}
                and not (kind == "card_edit" and phase == "terminal")
            ):
                raise LifecycleError("effect-blocked-after-cancellation")
            if kind == "final":
                outbox = db.execute("SELECT state FROM terminal_outbox WHERE work_id=?", (work_id,)).fetchone()
                if str(row["phase"]) != "terminal" or not outbox:
                    raise LifecycleError("final-effect-requires-terminal-commit")
            elif phase == "terminal" and kind == "card_edit":
                outbox = db.execute("SELECT state FROM terminal_outbox WHERE work_id=?", (work_id,)).fetchone()
                if not outbox:
                    raise LifecycleError("terminal-card-edit-requires-terminal-commit")
            elif phase == "terminal" and kind not in {"callback_ack"}:
                raise LifecycleError("nonterminal-effect-after-terminal")
            existing = db.execute("SELECT * FROM effects WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                return {
                    "allowed": False,
                    "idempotencyKey": idempotency_key,
                    "state": existing["state"],
                    "reason": "duplicate-or-reserved-effect",
                }
            if kind in SINGLETON_EFFECT_KINDS:
                reserved = db.execute(
                    """SELECT state,idempotency_key FROM effects
                       WHERE work_id=? AND kind=? AND state IN ('sending','delivered','indeterminate')
                       ORDER BY intent_at LIMIT 1""",
                    (work_id, kind),
                ).fetchone()
                if reserved:
                    return {
                        "allowed": False,
                        "idempotencyKey": reserved["idempotency_key"],
                        "state": reserved["state"],
                        "reason": "singleton-effect-fenced",
                    }
            if kind == "reaction" and bool(row["reaction_delivered"]):
                return {"allowed": False, "idempotencyKey": idempotency_key, "state": "delivered", "reason": "reaction-already-delivered"}
            if kind == "card" and bool(row["card_created"]):
                return {"allowed": False, "idempotencyKey": idempotency_key, "state": "delivered", "reason": "card-already-created"}
            if kind == "final" and (bool(row["final_delivered"]) or str(row["delivery_state"]) == "indeterminate"):
                return {"allowed": False, "idempotencyKey": idempotency_key, "state": row["delivery_state"], "reason": "final-fenced"}
            now = utc_now()
            db.execute(
                """INSERT INTO effects(
                     idempotency_key,work_id,kind,scope_ref,sequence,fencing_epoch,
                     state,private_receipt,error_class,attempts,intent_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    idempotency_key, work_id, kind, clean_scope, sequence,
                    fencing_epoch, "sending", "", "", 1, now, now,
                ),
            )
        return {"allowed": True, "idempotencyKey": idempotency_key, "state": "sending"}

    def finish_effect(
        self,
        idempotency_key: str,
        *,
        state: str,
        private_receipt: str = "",
        error_class: str = "",
    ) -> dict[str, Any]:
        if state not in {"delivered", "indeterminate", "dead_letter"}:
            raise LifecycleError("invalid-effect-state")
        with self.connect() as db, self.transaction(db):
            effect = db.execute("SELECT * FROM effects WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if not effect:
                raise LifecycleError("unknown-effect")
            if str(effect["state"]) != "sending":
                return {"ok": True, "state": effect["state"], "duplicate": True}
            if str(effect["kind"]) == "final":
                outbox = db.execute(
                    "SELECT state FROM terminal_outbox WHERE work_id=?",
                    (effect["work_id"],),
                ).fetchone()
                if not outbox or str(outbox["state"]) != "sending":
                    raise LifecycleError("terminal-delivery-not-claimed")
            db.execute(
                "UPDATE effects SET state=?,private_receipt=?,error_class=?,updated_at=? WHERE idempotency_key=? AND state='sending'",
                (state, clean_plain_text(private_receipt, 500), clean_plain_text(error_class, 80), utc_now(), idempotency_key),
            )
            kind = str(effect["kind"])
            assignments: list[str] = ["updated_at=?"]
            params: list[Any] = [utc_now()]
            if state == "delivered" and kind == "reaction":
                assignments.append("reaction_delivered=1")
            if state == "delivered" and kind == "card":
                assignments.append("card_created=1")
            params.append(effect["work_id"])
            db.execute(f"UPDATE work_receipts SET {','.join(assignments)} WHERE work_id=?", params)
        return {"ok": True, "state": state, "duplicate": False}

    def commit_terminal(
        self,
        work_id: str,
        outcome: str,
        *,
        expected_sequence: int,
        fencing_epoch: int,
        private_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if outcome not in OUTCOMES:
            raise LifecycleError("invalid-terminal-outcome")
        encoded = json.dumps(private_payload, sort_keys=True, ensure_ascii=True)
        expected_payload_hash = payload_hash(private_payload)
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
            if not row:
                raise LifecycleError("unknown-work")
            if str(row["phase"]) == "terminal":
                existing = db.execute("SELECT * FROM terminal_outbox WHERE work_id=?", (work_id,)).fetchone()
                if (
                    not existing
                    or not hmac.compare_digest(str(existing["outcome"]), outcome)
                    or not hmac.compare_digest(str(existing["payload_hash"]), expected_payload_hash)
                ):
                    raise StaleEventError("terminal-commit-conflict")
                return {"ok": True, "duplicate": True, "eventId": existing["event_id"] if existing else "", "outcome": row["outcome"]}
            self._require_simulatable(row)
            self._validate_event_fence(row, expected_sequence, fencing_epoch)
            sequence = int(row["sequence"]) + 1
            now = utc_now()
            event_id = stable_id("terminal", work_id, row["generation"], length=32)
            db.execute(
                """UPDATE work_receipts SET phase='terminal',outcome=?,delivery_state='pending',
                   sequence=?,terminal_at=?,updated_at=? WHERE work_id=?""",
                (outcome, sequence, now, now, work_id),
            )
            db.execute(
                "INSERT INTO terminal_outbox VALUES(?,?,?,?,?,?,?,?,?)",
                (event_id, work_id, outcome, "pending", encoded, expected_payload_hash, 0, now, now),
            )
            self._insert_event(db, work_id, sequence, fencing_epoch, "terminal", {
                "outcome": outcome, "deliveryState": "pending",
            })
        return {"ok": True, "duplicate": False, "eventId": event_id, "outcome": outcome}

    def claim_terminal_delivery(self, work_id: str) -> dict[str, Any]:
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM terminal_outbox WHERE work_id=?", (work_id,)).fetchone()
            if not row:
                raise LifecycleError("terminal-outbox-missing")
            work = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
            if not work or str(work["phase"]) != "terminal":
                raise LifecycleError("terminal-work-missing")
            self._require_writable(work)
            if row["state"] != "pending":
                return {"allowed": False, "state": row["state"], "eventId": row["event_id"]}
            db.execute(
                "UPDATE terminal_outbox SET state='sending',attempts=attempts+1,updated_at=? WHERE work_id=? AND state='pending'",
                (utc_now(), work_id),
            )
            db.execute("UPDATE work_receipts SET delivery_state='sending',updated_at=? WHERE work_id=?", (utc_now(), work_id))
            payload = json.loads(row["payload_json"])
        return {"allowed": True, "state": "sending", "eventId": row["event_id"], "payload": payload}

    def finish_terminal_delivery(self, work_id: str, state: str) -> dict[str, Any]:
        if state not in {"delivered", "indeterminate", "dead_letter"}:
            raise LifecycleError("invalid-terminal-delivery-state")
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM terminal_outbox WHERE work_id=?", (work_id,)).fetchone()
            if not row:
                raise LifecycleError("terminal-outbox-missing")
            if row["state"] != "sending":
                return {"ok": True, "duplicate": True, "state": row["state"]}
            db.execute("UPDATE terminal_outbox SET state=?,updated_at=? WHERE work_id=?", (state, utc_now(), work_id))
            db.execute(
                "UPDATE work_receipts SET delivery_state=?,final_delivered=?,updated_at=? WHERE work_id=?",
                (state, int(state == "delivered"), utc_now(), work_id),
            )
        return {"ok": True, "duplicate": False, "state": state}

    def requeue_terminal_delivery(
        self,
        work_id: str,
        *,
        expected_sequence: int,
        fencing_epoch: int,
        max_attempts: int = 5,
    ) -> dict[str, Any]:
        """Retry a known failed send; unknown outcomes remain permanently fenced."""
        bounded_max = max(1, min(int(max_attempts), 10))
        with self.connect() as db, self.transaction(db):
            work = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
            outbox = db.execute("SELECT * FROM terminal_outbox WHERE work_id=?", (work_id,)).fetchone()
            if not work or not outbox or str(work["phase"]) != "terminal":
                raise LifecycleError("terminal-outbox-missing")
            self._require_writable(work)
            if int(work["sequence"]) != int(expected_sequence):
                raise StaleEventError("stale-or-out-of-order-sequence")
            if int(work["fencing_epoch"]) != int(fencing_epoch):
                raise StaleEventError("stale-fencing-epoch")
            state = str(outbox["state"])
            if state == "pending":
                return {"ok": True, "duplicate": True, "state": state, "sequence": int(work["sequence"])}
            if state == "indeterminate":
                raise LifecycleError("indeterminate-terminal-delivery-fenced")
            if state != "dead_letter":
                raise LifecycleError("terminal-delivery-not-retryable")
            if int(outbox["attempts"]) >= bounded_max:
                raise LifecycleError("terminal-delivery-attempts-exhausted")
            sequence = int(work["sequence"]) + 1
            now = utc_now()
            db.execute(
                "UPDATE terminal_outbox SET state='pending',updated_at=? WHERE work_id=? AND state='dead_letter'",
                (now, work_id),
            )
            db.execute(
                """UPDATE work_receipts SET delivery_state='pending',final_delivered=0,
                   sequence=?,updated_at=? WHERE work_id=?""",
                (sequence, now, work_id),
            )
            self._insert_event(db, work_id, sequence, fencing_epoch, "terminal-delivery-requeued", {
                "deliveryState": "pending",
                "status": "retry_safe",
            })
        return {"ok": True, "duplicate": False, "state": "pending", "sequence": sequence}

    def update_render_hash(self, work_id: str, rendered: str) -> bool:
        digest = hashlib.sha256(rendered.encode()).hexdigest()
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
            if not row:
                raise LifecycleError("unknown-work")
            self._require_simulatable(row)
            if hmac.compare_digest(str(row["render_hash"]), digest):
                return False
            db.execute("UPDATE work_receipts SET render_hash=?,updated_at=? WHERE work_id=?", (digest, utc_now(), work_id))
        return True

    def create_action(
        self,
        *,
        work_id: str,
        lifecycle_revision: int,
        authorized_user: str,
        chat_ref: str,
        topic_ref: str,
        message_ref: str,
        action: str,
        artifact_ref: str = "",
        ttl_seconds: int = 600,
    ) -> str:
        if action not in ACTION_ALLOWLIST:
            raise UnauthorizedActionError("action-not-allowlisted")
        clean_user = clean_plain_text(authorized_user, 160)
        clean_chat = clean_plain_text(chat_ref, 160)
        clean_topic = clean_plain_text(topic_ref, 160)
        clean_message = clean_plain_text(message_ref, 160)
        clean_artifact = clean_plain_text(artifact_ref, 240)
        if not all((clean_user, clean_chat, clean_topic, clean_message)):
            raise UnauthorizedActionError("action-binding-incomplete")
        token = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(18)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
        expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=max(30, min(ttl_seconds, 3600)))
        with self.connect() as db, self.transaction(db):
            work = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (work_id,)).fetchone()
            if not work or int(work["sequence"]) != int(lifecycle_revision):
                raise StaleEventError("action-lifecycle-revision-mismatch")
            self._require_writable(work)
            db.execute(
                """INSERT INTO actions(
                     token_hash,nonce_hash,work_id,lifecycle_revision,authorized_owner,
                     authorized_user,chat_ref,topic_ref,message_ref,artifact_ref,action,
                     expires_at,consumed_at,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    token_hash, nonce_hash, work_id, lifecycle_revision, work["current_owner"],
                    clean_user, clean_chat, clean_topic, clean_message, clean_artifact, action,
                    expires.replace(microsecond=0).isoformat().replace("+00:00", "Z"), None, utc_now(),
                ),
            )
        return f"v3.{token}.{nonce}"

    def consume_action(
        self,
        callback_token: str,
        *,
        authorized_user: str,
        chat_ref: str,
        topic_ref: str,
        message_ref: str,
        artifact_ref: str = "",
    ) -> dict[str, Any]:
        parts = str(callback_token or "").split(".")
        if len(parts) != 3 or parts[0] != "v3":
            raise UnauthorizedActionError("malformed-action-token")
        token_hash = hashlib.sha256(parts[1].encode()).hexdigest()
        nonce_hash = hashlib.sha256(parts[2].encode()).hexdigest()
        with self.connect() as db, self.transaction(db):
            row = db.execute("SELECT * FROM actions WHERE token_hash=?", (token_hash,)).fetchone()
            if not row or not hmac.compare_digest(str(row["nonce_hash"]), nonce_hash):
                raise UnauthorizedActionError("unknown-or-altered-action")
            if row["consumed_at"]:
                raise UnauthorizedActionError("action-already-consumed")
            if parse_utc(row["expires_at"]) < dt.datetime.now(dt.timezone.utc):
                raise UnauthorizedActionError("action-expired")
            work = db.execute("SELECT * FROM work_receipts WHERE work_id=?", (row["work_id"],)).fetchone()
            if not work:
                raise UnauthorizedActionError("action-work-missing")
            if not hmac.compare_digest(str(row["authorized_owner"]), str(work["current_owner"])):
                raise UnauthorizedActionError("action-owner-mismatch")
            if int(work["sequence"]) != int(row["lifecycle_revision"]):
                raise UnauthorizedActionError("action-lifecycle-revision-stale")
            if not self._writer_enabled_for_row(work):
                raise UnauthorizedActionError("action-writer-disabled")
            expected = (
                str(row["authorized_user"]), str(row["chat_ref"]), str(row["topic_ref"]),
                str(row["message_ref"]), str(row["artifact_ref"]),
            )
            actual = (
                clean_plain_text(authorized_user, 160), clean_plain_text(chat_ref, 160),
                clean_plain_text(topic_ref, 160), clean_plain_text(message_ref, 160),
                clean_plain_text(artifact_ref, 240),
            )
            if not all(hmac.compare_digest(a, b) for a, b in zip(expected, actual)):
                raise UnauthorizedActionError("action-binding-mismatch")
            changed = db.execute(
                "UPDATE actions SET consumed_at=? WHERE token_hash=? AND consumed_at IS NULL",
                (utc_now(), token_hash),
            ).rowcount
            if changed != 1:
                raise UnauthorizedActionError("action-consume-race-lost")
        return {"ok": True, "workId": row["work_id"], "action": row["action"], "artifactRef": row["artifact_ref"]}

    def _shadow_stats(self, db: sqlite3.Connection) -> dict[str, Any]:
        row = db.execute(
            """SELECT COUNT(*) AS total,
                      COALESCE(SUM(
                        matched=1 AND terminal_observed=1 AND terminal_delivered=1
                      ),0) AS clean,
                      COALESCE(SUM(terminal_observed=1),0) AS observed
                 FROM shadow_samples WHERE owner=?""",
            (self.owner,),
        ).fetchone()
        total = int(row["total"] or 0)
        clean = int(row["clean"] or 0)
        observed = int(row["observed"] or 0)
        return {
            "owner": self.owner,
            "total": total,
            "clean": clean,
            "observed": observed,
            "unobserved": total - observed,
            "dirty": observed - clean,
            "minimum": self.rollout.shadow_min_samples,
            "eligible": total >= self.rollout.shadow_min_samples and total == clean,
        }

    def record_shadow_sample(self, work_id: str, *, observed_contract: str) -> dict[str, Any]:
        """Persist a real legacy surface observation; derive match internally."""
        work = self.read_work(work_id)
        if not work:
            raise LifecycleError("unknown-work")
        if not self.rollout.shadow_enabled(self.owner) or not work["shadowOnly"]:
            raise LifecycleError("shadow-sampling-disabled")
        if not hmac.compare_digest(str(work["currentOwner"]), self.owner):
            raise LifecycleError("shadow-owner-mismatch")
        contracts = {
            1: "final-only",
            2: "reaction-final",
            3: "reaction-card-final",
        }
        expected_contract = contracts.get(int(work["deliveryTier"]))
        observed = clean_plain_text(observed_contract, 80)
        if observed not in set(contracts.values()) or not expected_contract:
            raise LifecycleError("shadow-observed-contract-invalid")
        matched = hmac.compare_digest(observed, expected_contract)
        sample_id = stable_id("shadow", self.owner, work_id, length=28)
        with self.connect() as db, self.transaction(db):
            existing = db.execute(
                "SELECT legacy_contract,matched FROM shadow_samples WHERE owner=? AND work_id=?",
                (self.owner, work_id),
            ).fetchone()
            if existing:
                if (
                    not hmac.compare_digest(str(existing["legacy_contract"]), observed)
                    or int(existing["matched"]) != int(matched)
                ):
                    raise LifecycleError("shadow-observation-conflict")
            else:
                db.execute(
                    """INSERT INTO shadow_samples(
                         id,owner,work_id,tier,reason,legacy_contract,matched,
                         terminal_observed,terminal_delivered,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        sample_id, self.owner, work_id, work["deliveryTier"],
                        work["classifierReason"], observed, int(matched), 0, 0, utc_now(),
                    ),
                )
            return self._shadow_stats(db)

    def finish_shadow_sample(self, work_id: str, *, delivered: bool) -> dict[str, Any]:
        """Bind a shadow comparison to the confirmed legacy terminal result."""
        with self.connect() as db, self.transaction(db):
            work = db.execute(
                "SELECT * FROM work_receipts WHERE work_id=?", (work_id,),
            ).fetchone()
            if not work:
                raise LifecycleError("unknown-work")
            self._validate_reader(work)
            if not self.rollout.shadow_enabled(self.owner) or not bool(work["shadow_only"]):
                raise LifecycleError("shadow-sampling-disabled")
            if not hmac.compare_digest(str(work["current_owner"]), self.owner):
                raise LifecycleError("shadow-owner-mismatch")
            if str(work["phase"]) != "terminal":
                raise LifecycleError("shadow-terminal-not-committed")
            sample = db.execute(
                "SELECT * FROM shadow_samples WHERE owner=? AND work_id=?",
                (self.owner, work_id),
            ).fetchone()
            if not sample:
                raise LifecycleError("shadow-surface-observation-missing")
            terminal_delivered = int(bool(delivered))
            if int(sample["terminal_observed"]):
                if int(sample["terminal_delivered"]) != terminal_delivered:
                    raise LifecycleError("shadow-terminal-observation-conflict")
                result = self._shadow_stats(db)
                result["duplicate"] = True
                return result
            db.execute(
                """UPDATE shadow_samples
                      SET terminal_observed=1,terminal_delivered=?
                    WHERE owner=? AND work_id=? AND terminal_observed=0""",
                (terminal_delivered, self.owner, work_id),
            )
            result = self._shadow_stats(db)
            result["duplicate"] = False
            return result

    def status(self) -> dict[str, Any]:
        with self.connect() as db:
            counts = {
                str(row["phase"]): int(row["count"])
                for row in db.execute("SELECT phase,COUNT(*) AS count FROM work_receipts GROUP BY phase")
            }
            indeterminate = int(db.execute(
                "SELECT COUNT(*) FROM work_receipts WHERE delivery_state='indeterminate'"
            ).fetchone()[0])
            effect_indeterminate = int(db.execute(
                "SELECT COUNT(*) FROM effects WHERE state='indeterminate'"
            ).fetchone()[0])
            shadows = {
                str(row["owner"]): {
                    "total": int(row["total"]),
                    "clean": int(row["clean"] or 0),
                    "observed": int(row["observed"] or 0),
                }
                for row in db.execute(
                    """SELECT owner,COUNT(*) AS total,
                              COALESCE(SUM(
                                matched=1 AND terminal_observed=1 AND terminal_delivered=1
                              ),0) AS clean,
                              COALESCE(SUM(terminal_observed=1),0) AS observed
                         FROM shadow_samples GROUP BY owner"""
                )
            }
        return {
            "ok": indeterminate == 0 and effect_indeterminate == 0,
            "schemaVersion": SCHEMA_VERSION,
            "lifecycleVersion": LIFECYCLE_VERSION,
            "rollout": self.rollout.master_state,
            "globalKillSwitch": self.rollout.global_kill_switch,
            "brainKillSwitch": self.rollout.brain_kill_switch,
            "counts": counts,
            "indeterminate": indeterminate,
            "effectIndeterminate": effect_indeterminate,
            "shadow": shadows,
        }


def sanitize_telemetry(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Allow only bounded dashboard-safe lifecycle fields."""
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in TELEMETRY_FIELDS:
            continue
        if isinstance(value, bool) or value is None:
            clean[key] = value
        elif isinstance(value, (int, float)):
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = clean_plain_text(value, 160)
    return clean


def render_live_card(receipt: Mapping[str, Any], *, objective: str, phase_label: str, model: str, route: str, progress: int) -> str:
    if receipt.get("surfaceContract") not in {"telegram", "brain-intake"}:
        raise LifecycleError("telegram-renderer-used-for-native-surface")
    bounded_progress = max(0, min(100, int(progress)))
    return (
        f"<b>{safe_html_text(objective, 300)}</b>\n"
        f"Phase: {safe_html_text(phase_label, 80)} · {bounded_progress}%\n"
        f"Model: {safe_html_text(model, 120)}\n"
        f"Route: {safe_html_text(route, 140)}"
    )


def render_final(
    *,
    model: str,
    route: str,
    why: str,
    complete: str,
    done: Sequence[str],
    issues: Sequence[str],
    next_steps: Sequence[str],
    approvals: Sequence[str],
) -> str:
    def section(name: str, values: Sequence[str]) -> str:
        rows = [clean_plain_text(value, 500) for value in values if clean_plain_text(value, 500)] or ["n/a"]
        return f"<b>{name}</b>\n" + "\n".join(f"• {safe_html_text(value, 500)}" for value in rows[:6])

    return "\n\n".join([
        f"Model: {safe_html_text(model, 120)} | Route: {safe_html_text(route, 140)} | Why: {safe_html_text(why, 180)}",
        f"<b>Complete:</b> {safe_html_text(complete, 120)}",
        section("What was done:", done),
        section("Issues:", issues),
        section("Appropriate next steps:", next_steps),
        section("Approval needed:", approvals),
    ])


def retry_delay_seconds(attempt: int, *, retry_after: float | None = None, seed: str = "") -> float:
    if retry_after is not None:
        return max(0.0, float(retry_after))
    bounded = max(0, min(int(attempt), 8))
    base = min(30.0, 0.25 * (2 ** bounded))
    digest = int(hashlib.sha256(f"{seed}:{bounded}".encode()).hexdigest()[:8], 16)
    jitter = (digest / 0xFFFFFFFF) * min(1.0, base * 0.25)
    return round(base + jitter, 3)


def _cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.environ.get("TELEGRAM_LIFECYCLE_ROOT", str(Path.home() / ".openclaw/private/telegram-lifecycle")))
    parser.add_argument("--rollout", default=os.environ.get("TELEGRAM_LIFECYCLE_ROLLOUT", ""))
    parser.add_argument("--owner", choices=("josh2", "jaimes"), default=os.environ.get("TELEGRAM_GATEWAY_OWNER", "josh2"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sample = sub.add_parser("shadow-sample")
    sample.add_argument("--origin", required=True)
    sample.add_argument("--run-id", required=True)
    sample.add_argument("--text", default="")
    sample.add_argument(
        "--observed-contract",
        choices=("final-only", "reaction-final", "reaction-card-final"),
        required=True,
    )
    return parser


def main() -> int:
    args = _cli().parse_args()
    rollout = RolloutPolicy.load(args.rollout or None)
    lifecycle = GatewayLifecycle(args.root, rollout=rollout, owner=args.owner)
    if args.command == "status":
        result = lifecycle.status()
    else:
        receipt = lifecycle.start_work(
            origin_key=args.origin, run_id=args.run_id, intake_agent=args.owner,
            current_owner=args.owner, surface_contract="telegram", text=args.text,
        )
        result = lifecycle.record_shadow_sample(
            receipt["workId"],
            observed_contract=args.observed_contract,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
