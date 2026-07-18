#!/usr/bin/env python3
"""Transactional, dashboard-safe work ledger for Control Tower.

The SQLite database is the durable local source of truth on Josh 2.0.  The
``control-tower-hot.json`` file is a small, atomically replaced projection for
the kiosk.  Publishers may retry an event with the same ``event_id``; retries
are idempotent, while stale generations and sequences are rejected.

Only dashboard-safe fields are accepted.  In particular, raw Telegram message
ids, chat ids, prompts, and other origin claims are never persisted: callers
provide an origin claim and this module stores only its SHA-256 digest.
"""
# #JAIMES: this WAL ledger plus its atomic hot projection is the canonical
# identity/lease source for Live Work and verified model-route highlighting.
from __future__ import annotations

import argparse
from contextlib import closing
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "control-tower-work.sqlite3"
DEFAULT_HOT_PATH = DATA_DIR / "control-tower-hot.json"

SCHEMA_VERSION = 1
ACTIVE_STATUSES = {"accepted", "planned", "routed", "active", "verifying"}
TERMINAL_STATUSES = {"done", "blocked", "error", "cancelled"}
STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES
EVENT_KINDS = {"start", "update", "heartbeat", "terminal"}
MODEL_FAMILIES = {"codex", "antigravity", "ollama", "grok"}
MODEL_ALIASES = {
    "openai": "codex",
    "codex/openai": "codex",
    "google": "antigravity",
    "gemini": "antigravity",
    "google/gemini": "antigravity",
    "local": "ollama",
    "xai": "grok",
    "xai/grok": "grok",
}
AGENT_ALIASES = {
    "josh": "josh2",
    "josh2": "josh2",
    "josh2.0": "josh2",
    "josh 2.0": "josh2",
    "jaimes": "jaimes",
    "jain": "jain",
    "j.a.i.n": "jain",
    "joshex": "joshex",
    "codex": "joshex",
}
AGENT_LABELS = {
    "josh2": "JOSH 2.0",
    "jaimes": "JAIMES",
    "jain": "J.A.I.N",
    "joshex": "JOSHeX",
}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
SECRET_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?<![A-Za-z0-9])sb_secret_[A-Za-z0-9_-]+"),
    re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)(password|client_secret|access_token|refresh_token|authorization)\s*[:=]"),
    re.compile(r"(?i)\b(cookie|oauth|bearer)\s*[:=]\s*\S+"),
)


class WorkStoreError(RuntimeError):
    """Base exception for rejected work events."""


class OutOfOrderEvent(WorkStoreError):
    """Raised when an event would regress the canonical lifecycle."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def canonical_agent(value: Any) -> str:
    raw = " ".join(str(value or "").strip().lower().replace("_", " ").split())
    agent = AGENT_ALIASES.get(raw, raw.replace(" ", ""))
    if agent not in AGENT_LABELS:
        raise WorkStoreError(f"Unknown agent '{value}'.")
    return agent


def canonical_model_family(value: Any) -> str:
    raw = str(value or "").strip().lower()
    family = MODEL_ALIASES.get(raw, raw)
    if family and family not in MODEL_FAMILIES:
        raise WorkStoreError(
            f"Unknown model family '{value}'. Use codex, antigravity, ollama, or grok."
        )
    return family


def safe_identifier(value: Any, field: str, *, required: bool = True) -> str:
    text = str(value or "").strip()
    if not text and not required:
        return ""
    if not IDENTIFIER.fullmatch(text):
        raise WorkStoreError(f"{field} must be a dashboard-safe identifier (max 160 characters).")
    return text


def ensure_dashboard_safe(*values: Any) -> None:
    blob = "\n".join(str(value or "") for value in values)
    for pattern in SECRET_PATTERNS:
        if pattern.search(blob):
            raise WorkStoreError("Refusing to persist a value that looks like a secret or credential.")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def origin_digest(*, origin_claim: str = "", origin_claim_hash: str = "", fallback: str) -> str:
    if origin_claim and origin_claim_hash:
        raise WorkStoreError("Provide origin_claim or origin_claim_hash, not both.")
    if origin_claim_hash:
        digest = str(origin_claim_hash).strip().lower()
        if not SHA256.fullmatch(digest):
            raise WorkStoreError("origin_claim_hash must be a lowercase SHA-256 hex digest.")
        return digest
    raw = str(origin_claim or fallback)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class WorkStore:
    """SQLite-backed work/event store with an atomic kiosk projection."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH, hot_path: Path = DEFAULT_HOT_PATH) -> None:
        self.db_path = Path(db_path)
        self.hot_path = Path(hot_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.hot_path.parent.mkdir(parents=True, exist_ok=True)
        self._secure_database_files(create=True)
        self._initialize()
        self._secure_database_files()

    def _secure_database_files(self, *, create: bool = False) -> None:
        """Keep the operational ledger private even under a permissive umask."""
        if create and not self.db_path.exists():
            try:
                descriptor = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
        for path in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        ):
            try:
                path.chmod(0o600)
            except FileNotFoundError:
                continue

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS store_meta (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT
                );
                INSERT OR IGNORE INTO store_meta(singleton, schema_version, revision, updated_at)
                VALUES (1, 1, 0, NULL);

                CREATE TABLE IF NOT EXISTS work_events (
                    event_id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    generation INTEGER NOT NULL CHECK (generation > 0),
                    sequence INTEGER NOT NULL CHECK (sequence > 0),
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    owner_agent TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    origin_claim_hash TEXT NOT NULL,
                    model_family TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    route_verified INTEGER NOT NULL CHECK (route_verified IN (0, 1)),
                    lease_until TEXT,
                    occurred_at TEXT NOT NULL,
                    accepted_revision INTEGER NOT NULL,
                    UNIQUE(work_id, generation, sequence)
                );
                CREATE INDEX IF NOT EXISTS work_events_work_time
                    ON work_events(work_id, occurred_at DESC);

                CREATE TABLE IF NOT EXISTS origin_claims (
                    origin_claim_hash TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    first_run_id TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    source_event_id TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS current_works (
                    work_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    owner_agent TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    origin_claim_hash TEXT NOT NULL,
                    model_family TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    route_verified INTEGER NOT NULL,
                    lease_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_meaningful_at TEXT NOT NULL,
                    terminal_at TEXT,
                    last_event_id TEXT NOT NULL,
                    revision INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS current_works_owner_status
                    ON current_works(owner_agent, status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS model_route_events (
                    event_id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    owner_agent TEXT NOT NULL,
                    model_family TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    route_verified INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    accepted_revision INTEGER NOT NULL,
                    FOREIGN KEY(event_id) REFERENCES work_events(event_id)
                );

                CREATE TABLE IF NOT EXISTS active_model_routes (
                    work_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    owner_agent TEXT NOT NULL,
                    model_family TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    activated_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    lease_until TEXT,
                    source_event_id TEXT NOT NULL,
                    revision INTEGER NOT NULL
                );
                """
            )
            connection.commit()

    def get(self, work_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM current_works WHERE work_id = ?", (work_id,)
            ).fetchone()
        return self._work_json(row) if row else None

    def _duplicate_claim_response(self, canonical: sqlite3.Row) -> dict[str, Any]:
        return {
            "accepted": True,
            "idempotent": True,
            "duplicateClaim": True,
            "revision": int(canonical["revision"]),
            "event": {
                "eventId": canonical["last_event_id"],
                "workId": canonical["work_id"],
                "runId": canonical["run_id"],
                "generation": int(canonical["generation"]),
                "sequence": int(canonical["sequence"]),
            },
            "work": self._work_json(canonical),
        }

    def publish(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and transactionally append one lifecycle event.

        ``sequence`` may be omitted; the store then allocates the next sequence
        while holding ``BEGIN IMMEDIATE``.  A higher generation starts a fresh
        run for the same stable work id.  A non-terminal event cannot reopen a
        terminal generation.
        """

        kind = str(payload.get("kind") or "update").strip().lower()
        if kind not in EVENT_KINDS:
            raise WorkStoreError(f"Unknown event kind '{kind}'.")
        privacy = str(payload.get("privacy") or "dashboard-safe").strip().lower()
        if privacy != "dashboard-safe":
            raise WorkStoreError("The canonical work ledger accepts dashboard-safe fields only.")
        work_id = safe_identifier(payload.get("work_id") or payload.get("workId"), "work_id")
        run_id = safe_identifier(payload.get("run_id") or payload.get("runId"), "run_id")
        event_id = safe_identifier(
            payload.get("event_id") or payload.get("eventId") or new_id("wev"), "event_id"
        )
        agent = canonical_agent(payload.get("agent"))
        requested_generation = payload.get("generation")
        requested_sequence = payload.get("sequence")
        occurred_at = str(payload.get("occurred_at") or payload.get("occurredAt") or utc_now())
        parse_time(occurred_at)

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT accepted_revision, work_id, run_id, generation, sequence FROM work_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if duplicate:
                if duplicate["work_id"] != work_id or duplicate["run_id"] != run_id:
                    raise WorkStoreError("event_id is already bound to a different work or run.")
                current = connection.execute(
                    "SELECT * FROM current_works WHERE work_id = ?", (work_id,)
                ).fetchone()
                connection.rollback()
                return {
                    "accepted": True,
                    "idempotent": True,
                    "revision": int(duplicate["accepted_revision"]),
                    "event": {
                        "eventId": event_id,
                        "workId": work_id,
                        "runId": run_id,
                        "generation": int(duplicate["generation"]),
                        "sequence": int(duplicate["sequence"]),
                    },
                    "work": self._work_json(current) if current else None,
                }

            current = connection.execute(
                "SELECT * FROM current_works WHERE work_id = ?", (work_id,)
            ).fetchone()
            generation = int(requested_generation or (current["generation"] if current else 1))
            if generation <= 0:
                raise WorkStoreError("generation must be positive.")
            if current and generation < int(current["generation"]):
                raise OutOfOrderEvent("Event generation is older than current work generation.")
            new_generation = bool(current and generation > int(current["generation"]))
            if current and not new_generation and run_id != current["run_id"]:
                raise OutOfOrderEvent("run_id may change only when generation increases.")
            if current and not new_generation and current["status"] in TERMINAL_STATUSES and kind != "terminal":
                retry_claim = ""
                if payload.get("origin_claim") or payload.get("origin_claim_hash"):
                    retry_claim = origin_digest(
                        origin_claim=str(payload.get("origin_claim") or ""),
                        origin_claim_hash=str(payload.get("origin_claim_hash") or ""),
                        fallback="unused",
                    )
                if kind == "start" and retry_claim == str(current["origin_claim_hash"]):
                    connection.rollback()
                    return self._duplicate_claim_response(current)
                raise OutOfOrderEvent("A terminal generation cannot be reopened; increment generation.")

            prior_sequence = 0 if not current or new_generation else int(current["sequence"])
            sequence = int(requested_sequence or (prior_sequence + 1))
            if sequence <= prior_sequence:
                raise OutOfOrderEvent(
                    f"sequence {sequence} does not follow current sequence {prior_sequence}."
                )
            if new_generation and sequence != 1:
                raise OutOfOrderEvent("A new generation must begin at sequence 1.")

            prior_status = str(current["status"]) if current and not new_generation else ""
            status = str(payload.get("status") or prior_status or "active").strip().lower()
            if status not in STATUSES:
                raise WorkStoreError(f"Unknown work status '{status}'.")
            if kind == "terminal" and status not in TERMINAL_STATUSES:
                raise WorkStoreError("terminal events require done, blocked, error, or cancelled status.")
            if kind != "terminal" and status in TERMINAL_STATUSES:
                kind = "terminal"
            if kind == "heartbeat" and prior_status in TERMINAL_STATUSES:
                raise OutOfOrderEvent("Terminal work cannot receive a heartbeat.")

            objective = compact(
                payload.get("objective") or (current["objective"] if current and not new_generation else ""),
                220,
            )
            if not objective:
                raise WorkStoreError("objective is required for the first event in a generation.")
            phase = compact(
                payload.get("phase") or (current["phase"] if current and not new_generation else status),
                120,
            )
            tool = compact(
                payload.get("tool") or (current["tool"] if current and not new_generation else "unknown"),
                80,
            )
            raw_detail = payload.get("detail")
            if (
                kind == "heartbeat"
                and not raw_detail
                and current
                and not new_generation
            ):
                raw_detail = current["detail"]
            detail = compact(raw_detail or "", 500)
            origin = compact(
                payload.get("origin") or (current["origin"] if current and not new_generation else "agent-runtime"),
                80,
            )
            model_family = canonical_model_family(
                payload.get("model_family")
                if payload.get("model_family") is not None
                else (current["model_family"] if current and not new_generation else "")
            )
            model_id = compact(
                payload.get("model_id")
                if payload.get("model_id") is not None
                else (current["model_id"] if current and not new_generation else ""),
                120,
            )
            route_verified = _bool(
                payload.get("route_verified")
                if payload.get("route_verified") is not None
                else (current["route_verified"] if current and not new_generation else False)
            )
            if payload.get("clear_route"):
                model_family, model_id, route_verified = "", "", False
            if route_verified and (not model_family or not model_id):
                raise WorkStoreError("A verified route requires both model_family and model_id.")
            ensure_dashboard_safe(objective, phase, tool, detail, origin, model_id)

            fallback_claim = f"{origin}|{work_id}|{run_id}|{generation}"
            claim_hash = origin_digest(
                origin_claim=str(payload.get("origin_claim") or ""),
                origin_claim_hash=str(payload.get("origin_claim_hash") or ""),
                fallback=fallback_claim,
            )
            if current and not new_generation and not payload.get("origin_claim") and not payload.get("origin_claim_hash"):
                claim_hash = str(current["origin_claim_hash"])

            claimed = connection.execute(
                "SELECT * FROM origin_claims WHERE origin_claim_hash = ?", (claim_hash,)
            ).fetchone()
            claimed_work_id = str(claimed["work_id"]) if claimed else ""
            duplicate_claim = bool(
                claimed
                and (
                    claimed_work_id != work_id
                    or (
                        kind == "start"
                        and current
                        and not new_generation
                        and str(current["origin_claim_hash"]) == claim_hash
                    )
                )
            )
            if duplicate_claim:
                canonical = connection.execute(
                    "SELECT * FROM current_works WHERE work_id = ?", (claimed_work_id,)
                ).fetchone()
                connection.rollback()
                if not canonical:
                    raise WorkStoreError("Origin claim points to missing canonical work.")
                return self._duplicate_claim_response(canonical)

            lease_seconds = int(payload.get("lease_seconds") or 180)
            if lease_seconds < 15 or lease_seconds > 3600:
                raise WorkStoreError("lease_seconds must be between 15 and 3600.")
            lease_until = None
            if status in ACTIVE_STATUSES:
                lease_until = (
                    parse_time(occurred_at) + dt.timedelta(seconds=lease_seconds)
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            terminal_at = occurred_at if status in TERMINAL_STATUSES else None
            created_at = (
                occurred_at if not current or new_generation else str(current["created_at"])
            )
            last_meaningful_at = (
                str(current["last_meaningful_at"])
                if kind == "heartbeat" and current and not new_generation
                else occurred_at
            )

            meta = connection.execute(
                "SELECT revision FROM store_meta WHERE singleton = 1"
            ).fetchone()
            revision = int(meta["revision"]) + 1
            connection.execute(
                """
                INSERT INTO work_events(
                    event_id, work_id, run_id, generation, sequence, kind, status,
                    owner_agent, objective, phase, tool, detail, origin,
                    origin_claim_hash, model_family, model_id, route_verified,
                    lease_until, occurred_at, accepted_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, work_id, run_id, generation, sequence, kind, status,
                    agent, objective, phase, tool, detail, origin, claim_hash,
                    model_family, model_id, int(route_verified), lease_until,
                    occurred_at, revision,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO origin_claims(
                    origin_claim_hash, work_id, first_run_id, claimed_at, source_event_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (claim_hash, work_id, run_id, occurred_at, event_id),
            )
            connection.execute(
                """
                INSERT INTO current_works(
                    work_id, run_id, generation, sequence, status, owner_agent,
                    objective, phase, tool, detail, origin, origin_claim_hash,
                    model_family, model_id, route_verified, lease_until, created_at,
                    updated_at, last_meaningful_at, terminal_at, last_event_id, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(work_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    generation=excluded.generation,
                    sequence=excluded.sequence,
                    status=excluded.status,
                    owner_agent=excluded.owner_agent,
                    objective=excluded.objective,
                    phase=excluded.phase,
                    tool=excluded.tool,
                    detail=excluded.detail,
                    origin=excluded.origin,
                    origin_claim_hash=excluded.origin_claim_hash,
                    model_family=excluded.model_family,
                    model_id=excluded.model_id,
                    route_verified=excluded.route_verified,
                    lease_until=excluded.lease_until,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    last_meaningful_at=excluded.last_meaningful_at,
                    terminal_at=excluded.terminal_at,
                    last_event_id=excluded.last_event_id,
                    revision=excluded.revision
                """,
                (
                    work_id, run_id, generation, sequence, status, agent, objective,
                    phase, tool, detail, origin, claim_hash, model_family, model_id,
                    int(route_verified), lease_until, created_at, occurred_at,
                    last_meaningful_at, terminal_at, event_id, revision,
                ),
            )

            prior_route = connection.execute(
                "SELECT * FROM active_model_routes WHERE work_id = ?", (work_id,)
            ).fetchone()
            route_active = bool(
                status in ACTIVE_STATUSES and route_verified and model_family and model_id
            )
            if route_active:
                activated_at = (
                    str(prior_route["activated_at"])
                    if prior_route
                    and prior_route["run_id"] == run_id
                    and prior_route["model_family"] == model_family
                    and prior_route["model_id"] == model_id
                    else occurred_at
                )
                connection.execute(
                    """
                    INSERT INTO active_model_routes(
                        work_id, run_id, owner_agent, model_family, model_id,
                        activated_at, updated_at, lease_until, source_event_id, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(work_id) DO UPDATE SET
                        run_id=excluded.run_id,
                        owner_agent=excluded.owner_agent,
                        model_family=excluded.model_family,
                        model_id=excluded.model_id,
                        activated_at=excluded.activated_at,
                        updated_at=excluded.updated_at,
                        lease_until=excluded.lease_until,
                        source_event_id=excluded.source_event_id,
                        revision=excluded.revision
                    """,
                    (
                        work_id, run_id, agent, model_family, model_id, activated_at,
                        occurred_at, lease_until, event_id, revision,
                    ),
                )
                route_action = "activated" if not prior_route else "refreshed"
            else:
                connection.execute("DELETE FROM active_model_routes WHERE work_id = ?", (work_id,))
                route_action = "deactivated" if prior_route else "unverified"
            connection.execute(
                """
                INSERT INTO model_route_events(
                    event_id, work_id, run_id, generation, sequence, owner_agent,
                    model_family, model_id, route_verified, action, occurred_at,
                    accepted_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, work_id, run_id, generation, sequence, agent,
                    model_family, model_id, int(route_verified), route_action,
                    occurred_at, revision,
                ),
            )
            connection.execute(
                "UPDATE store_meta SET schema_version=?, revision=?, updated_at=? WHERE singleton=1",
                (SCHEMA_VERSION, revision, occurred_at),
            )
            accepted_row = connection.execute(
                "SELECT * FROM current_works WHERE work_id = ?", (work_id,)
            ).fetchone()
            accepted_work = self._work_json(accepted_row)
            connection.commit()

        self._secure_database_files()
        projection = self.write_projection()
        return {
            "accepted": True,
            "idempotent": False,
            "revision": revision,
            "event": {
                "eventId": event_id,
                "workId": work_id,
                "runId": run_id,
                "generation": generation,
                "sequence": sequence,
                "kind": kind,
                "status": status,
                "originClaimHash": claim_hash,
            },
            "work": accepted_work,
            "projectionRevision": projection["revision"],
        }

    def _work_json(self, row: sqlite3.Row | None, *, now: dt.datetime | None = None) -> dict[str, Any] | None:
        if row is None:
            return None
        current_time = now or dt.datetime.now(dt.timezone.utc)
        lease_until = row["lease_until"]
        stale = bool(
            row["status"] in ACTIVE_STATUSES
            and lease_until
            and parse_time(str(lease_until)) < current_time
        )
        return {
            "workId": row["work_id"],
            "runId": row["run_id"],
            "generation": int(row["generation"]),
            "sequence": int(row["sequence"]),
            "status": row["status"],
            "ownerAgent": row["owner_agent"],
            "ownerLabel": AGENT_LABELS.get(str(row["owner_agent"]), str(row["owner_agent"])),
            "objective": row["objective"],
            "phase": row["phase"],
            "tool": row["tool"],
            "detail": row["detail"],
            "origin": row["origin"],
            "originClaimHash": row["origin_claim_hash"],
            "modelFamily": row["model_family"] or None,
            "modelId": row["model_id"] or None,
            "routeVerified": bool(row["route_verified"]),
            "leaseUntil": lease_until,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "lastMeaningfulAt": row["last_meaningful_at"],
            "terminalAt": row["terminal_at"],
            "lastEventId": row["last_event_id"],
            "revision": int(row["revision"]),
            "stale": stale,
        }

    def projection(self) -> dict[str, Any]:
        generated_at = utc_now()
        now = parse_time(generated_at)
        with closing(self._connect()) as connection:
            meta = connection.execute("SELECT * FROM store_meta WHERE singleton=1").fetchone()
            rows = connection.execute(
                "SELECT * FROM current_works ORDER BY updated_at DESC LIMIT 250"
            ).fetchall()
            route_rows = connection.execute(
                "SELECT * FROM active_model_routes ORDER BY updated_at DESC"
            ).fetchall()
        works = [self._work_json(row, now=now) for row in rows]
        active = [
            row for row in works
            if row and row["status"] in ACTIVE_STATUSES and not row["stale"]
        ]
        routes = []
        for row in route_rows:
            if row["lease_until"] and parse_time(str(row["lease_until"])) < now:
                continue
            routes.append({
                "workId": row["work_id"],
                "runId": row["run_id"],
                "ownerAgent": row["owner_agent"],
                "modelFamily": row["model_family"],
                "modelId": row["model_id"],
                "routeVerified": True,
                "activatedAt": row["activated_at"],
                "updatedAt": row["updated_at"],
                "leaseUntil": row["lease_until"],
                "sourceEventId": row["source_event_id"],
                "revision": int(row["revision"]),
            })
        by_agent = {agent: 0 for agent in AGENT_LABELS}
        for row in active:
            by_agent[row["ownerAgent"]] = by_agent.get(row["ownerAgent"], 0) + 1
        active_expiries = sorted(
            str(row["leaseUntil"])
            for row in active
            if row.get("leaseUntil")
        )
        return {
            "schemaVersion": SCHEMA_VERSION,
            "revision": int(meta["revision"]),
            "generatedAt": generated_at,
            "storeUpdatedAt": meta["updated_at"],
            "source": "control-tower-work-store",
            "freshness": {
                "transport": "atomic-file+sse",
                "lastEventAt": meta["updated_at"],
                "activeLeaseSeconds": 180,
                "nextExpiryAt": active_expiries[0] if active_expiries else None,
                "expiryRule": "client-and-projector-must-hide-active-at-leaseUntil",
            },
            "counts": {
                "currentWorks": len(works),
                "activeWorks": len(active),
                "activeModelRoutes": len(routes),
                "activeByAgent": by_agent,
            },
            "activeWorks": active,
            "activeModelRoutes": routes,
            "works": works,
        }

    def write_projection(self) -> dict[str, Any]:
        lock_path = self.hot_path.with_suffix(self.hot_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            payload = self.projection()
            try:
                existing = json.loads(self.hot_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
            if int(existing.get("revision") or -1) > int(payload["revision"]):
                return existing
            temporary = self.hot_path.with_name(f".{self.hot_path.name}.{os.getpid()}.tmp")
            try:
                with temporary.open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2, ensure_ascii=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.hot_path)
            finally:
                temporary.unlink(missing_ok=True)
            return payload


def publish_work_event(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    hot_path: Path = DEFAULT_HOT_PATH,
    **payload: Any,
) -> dict[str, Any]:
    """Small callable contract for publishers and Telegram coordinators."""

    return WorkStore(db_path=db_path, hot_path=hot_path).publish(payload)


def _add_common(parser: argparse.ArgumentParser, *, objective_required: bool = False) -> None:
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--hot-path", type=Path, default=DEFAULT_HOT_PATH)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generation", type=int)
    parser.add_argument("--sequence", type=int)
    parser.add_argument("--event-id", default="")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--objective", required=objective_required, default="")
    parser.add_argument("--phase", default="")
    parser.add_argument("--tool", default="")
    parser.add_argument("--detail", default="")
    parser.add_argument("--origin", default="agent-runtime")
    origin = parser.add_mutually_exclusive_group()
    origin.add_argument("--origin-claim", default="")
    origin.add_argument("--origin-claim-hash", default="")
    parser.add_argument("--model-family", default=None)
    parser.add_argument("--model-id", default=None)
    route = parser.add_mutually_exclusive_group()
    route.add_argument("--route-verified", action="store_true", default=None)
    route.add_argument("--route-unverified", action="store_false", dest="route_verified")
    parser.add_argument("--clear-route", action="store_true")
    parser.add_argument("--lease-seconds", type=int, default=180)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="Begin a stable work generation.")
    _add_common(start, objective_required=True)
    start.add_argument("--status", choices=sorted(ACTIVE_STATUSES), default="active")
    update = sub.add_parser("update", help="Publish a meaningful phase/status change.")
    _add_common(update)
    update.add_argument("--status", choices=sorted(STATUSES), default=None)
    heartbeat = sub.add_parser("heartbeat", help="Renew an active lease without faking progress.")
    _add_common(heartbeat)
    terminal = sub.add_parser("terminal", help="Close one exact work generation.")
    _add_common(terminal)
    terminal.add_argument("--status", choices=sorted(TERMINAL_STATUSES), required=True)
    project = sub.add_parser("project", help="Regenerate the atomic hot projection.")
    project.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    project.add_argument("--hot-path", type=Path, default=DEFAULT_HOT_PATH)
    args = parser.parse_args()

    store = WorkStore(db_path=args.db_path, hot_path=args.hot_path)
    if args.command == "project":
        print(json.dumps(store.write_projection(), indent=2))
        return 0
    payload = {
        "kind": "terminal" if args.command == "terminal" else args.command,
        "work_id": args.work_id,
        "run_id": args.run_id,
        "generation": args.generation,
        "sequence": args.sequence,
        "event_id": args.event_id or None,
        "agent": args.agent,
        "objective": args.objective,
        "phase": args.phase,
        "tool": args.tool,
        "detail": args.detail,
        "origin": args.origin,
        "origin_claim": args.origin_claim,
        "origin_claim_hash": args.origin_claim_hash,
        "model_family": args.model_family,
        "model_id": args.model_id,
        "route_verified": args.route_verified,
        "clear_route": args.clear_route,
        "lease_seconds": args.lease_seconds,
        "status": getattr(args, "status", None),
    }
    try:
        result = store.publish(payload)
    except WorkStoreError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
