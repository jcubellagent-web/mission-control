#!/usr/bin/env python3
"""Governed shared memory registry for the agent ecosystem.

Canonical files and skills remain authoritative. This registry adds typed,
searchable records, provenance, validity, review state, and safe telemetry.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DB_PATH = Path(os.environ.get("MEMORY_REGISTRY_DB", DATA / "memory-registry.sqlite"))
STATUS_PATH = Path(os.environ.get("MEMORY_OPERATIONS_PATH", DATA / "memory-operations.json"))
INDEX_PATH = DATA / "agent-semantic-memory-index.json"
ALLOWED_TYPES = {"fact", "decision", "preference", "procedure", "lesson", "entity", "relationship", "episode"}
ALLOWED_STATUS = {"candidate", "active", "disputed", "superseded", "expired", "rejected", "forgotten"}
AUTO_PROMOTE_TYPES = {"fact", "lesson", "entity", "relationship"}
FEEDBACK_OUTCOMES = {"helpful", "ignored", "corrected", "harmful"}
REUSE_OUTCOMES = {"selected", "used", "ignored"}
REUSE_REASON_CODES = {
    "applied",
    "context-only",
    "duplicate-work-avoided",
    "not-relevant",
    "stale",
    "conflict",
    "other",
}
PUBLIC_PRIVACY = {"dashboard-safe", "public"}
KNOWN_OWNER_PRIVATE_PRIVACY = {
    "agent-private",
    "sensitive-account",
    "sensitive",
    "private",
    "josh-only",
    "personal",
    "confidential",
    "restricted",
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
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{2,}", re.I)
MEMORY_ACTIVITY_WINDOW_MINUTES = 30
MEMORY_ACTIVITY_MOTION_SECONDS = 90
MEMORY_ACTIVITY_AGENTS = ("joshex", "josh2", "jaimes", "jain")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def atomic_write_json(path: Path, payload: Any) -> None:
    """Publish dashboard-safe status without exposing a partial JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def stable_hash(*parts: Any) -> str:
    text = "\x1f".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha256(text.encode()).hexdigest()


def clean_text(value: Any, limit: int = 1200) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def canonical_agent(value: Any) -> str:
    raw = " ".join(str(value or "").strip().lower().replace("_", " ").split())
    return AGENT_ALIASES.get(raw, raw.replace(" ", ""))


def normalize_privacy_label(value: Any) -> str:
    return clean_text(value, 80).strip().lower().replace("_", "-").replace(" ", "-")


def privacy_class(value: Any) -> str:
    """Return the sharing class for a privacy label.

    Privacy is deliberately deny-by-default. Only the explicit
    ``dashboard-safe`` and ``public`` labels may cross an owner boundary;
    agent-private, sensitive-account, legacy private labels, blanks, and
    unknown labels remain owner/JOSHeX scoped.
    """

    label = normalize_privacy_label(value)
    return "public" if label in PUBLIC_PRIVACY else "owner-private"


def context_hash(kind: str, value: Any) -> str | None:
    text = clean_text(value, 240)
    return stable_hash(f"memory-{kind}", text) if text else None


def context_hashes(args: argparse.Namespace) -> dict[str, str | None]:
    return {
        "workIdHash": context_hash("work-id", getattr(args, "work_id", "")),
        "runIdHash": context_hash("run-id", getattr(args, "run_id", "")),
        "sessionIdHash": context_hash("session-id", getattr(args, "session_id", "")),
    }


def ensure_column(db: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS memory_records (
          id TEXT PRIMARY KEY,
          memory_type TEXT NOT NULL,
          subject TEXT NOT NULL,
          predicate TEXT NOT NULL,
          object_text TEXT NOT NULL,
          owner TEXT NOT NULL,
          visibility TEXT NOT NULL,
          privacy TEXT NOT NULL,
          source_path TEXT NOT NULL,
          source_ref TEXT,
          evidence TEXT,
          confidence REAL NOT NULL,
          status TEXT NOT NULL,
          valid_from TEXT,
          valid_until TEXT,
          recorded_at TEXT NOT NULL,
          supersedes TEXT,
          content_hash TEXT NOT NULL UNIQUE,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS memory_subject_predicate ON memory_records(subject, predicate, status);
        CREATE TABLE IF NOT EXISTS memory_candidates (
          id TEXT PRIMARY KEY,
          proposed_by TEXT NOT NULL,
          memory_type TEXT NOT NULL,
          subject TEXT NOT NULL,
          predicate TEXT NOT NULL,
          object_text TEXT NOT NULL,
          owner TEXT NOT NULL,
          visibility TEXT NOT NULL,
          privacy TEXT NOT NULL,
          source_path TEXT NOT NULL,
          evidence TEXT,
          confidence REAL NOT NULL,
          status TEXT NOT NULL,
          proposed_at TEXT NOT NULL,
          reviewed_at TEXT,
          review_reason TEXT,
          content_hash TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS retrieval_events (
          id TEXT PRIMARY KEY,
          time TEXT NOT NULL,
          agent TEXT NOT NULL,
          scope TEXT NOT NULL,
          query_hash TEXT NOT NULL,
          term_count INTEGER NOT NULL,
          matched_count INTEGER NOT NULL,
          latency_ms REAL NOT NULL,
          memory_ids_json TEXT NOT NULL,
          outcome TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_feedback (
          id TEXT PRIMARY KEY,
          time TEXT NOT NULL,
          agent TEXT NOT NULL,
          retrieval_id TEXT,
          memory_id TEXT,
          outcome TEXT NOT NULL,
          reason TEXT NOT NULL,
          correction_candidate_id TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS memory_feedback_retrieval ON memory_feedback(retrieval_id, time);
        CREATE INDEX IF NOT EXISTS memory_feedback_memory ON memory_feedback(memory_id, time);
        CREATE TABLE IF NOT EXISTS memory_reviews (
          id TEXT PRIMARY KEY,
          started_at TEXT NOT NULL,
          completed_at TEXT NOT NULL,
          status TEXT NOT NULL,
          candidates_seen INTEGER NOT NULL,
          promoted INTEGER NOT NULL,
          disputed INTEGER NOT NULL,
          expired INTEGER NOT NULL,
          detail_json TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
          id UNINDEXED, subject, predicate, object_text, evidence, tokenize='porter unicode61'
        );
        CREATE TABLE IF NOT EXISTS memory_reuse_events (
          id TEXT PRIMARY KEY,
          time TEXT NOT NULL,
          agent TEXT NOT NULL,
          retrieval_id TEXT NOT NULL,
          memory_id TEXT NOT NULL,
          outcome TEXT NOT NULL,
          reason_code TEXT NOT NULL,
          work_id_hash TEXT,
          run_id_hash TEXT,
          session_id_hash TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          UNIQUE(retrieval_id, memory_id, outcome)
        );
        CREATE INDEX IF NOT EXISTS memory_reuse_retrieval
          ON memory_reuse_events(retrieval_id, memory_id, time);
        CREATE TABLE IF NOT EXISTS memory_deletions (
          id TEXT PRIMARY KEY,
          time TEXT NOT NULL,
          source_hash TEXT NOT NULL,
          actor TEXT NOT NULL,
          candidate_count INTEGER NOT NULL,
          record_count INTEGER NOT NULL,
          fts_count INTEGER NOT NULL,
          status TEXT NOT NULL
        );
        """
    )
    ensure_column(db, "retrieval_events", "work_id_hash", "TEXT")
    ensure_column(db, "retrieval_events", "run_id_hash", "TEXT")
    ensure_column(db, "retrieval_events", "session_id_hash", "TEXT")
    ensure_column(db, "retrieval_events", "preflight", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "memory_candidates", "source_ref", "TEXT NOT NULL DEFAULT ''")
    ensure_column(db, "memory_candidates", "source_kind", "TEXT NOT NULL DEFAULT 'legacy'")
    ensure_column(db, "memory_candidates", "extraction_version", "TEXT NOT NULL DEFAULT ''")
    ensure_column(db, "memory_candidates", "governance_eligible", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(db, "memory_candidates", "injection_status", "TEXT NOT NULL DEFAULT 'not-applicable'")
    ensure_column(db, "memory_candidates", "source_state", "TEXT NOT NULL DEFAULT 'active'")
    ensure_column(db, "memory_candidates", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
    db.commit()
    return db


def visibility_allowed(agent: str, visibility: str, privacy: str, owner: str = "") -> bool:
    requester = canonical_agent(agent)
    record_owner = canonical_agent(owner)
    if requester == "joshex":
        return True
    if privacy_class(privacy) != "public":
        return bool(record_owner and requester == record_owner)
    visible_to = canonical_agent(visibility)
    return str(visibility or "").strip().lower() in {"shared", "ecosystem", "public"} or visible_to == requester


def index_fts(db: sqlite3.Connection, record_id: str, subject: str, predicate: str, value: str, evidence: str) -> None:
    db.execute("DELETE FROM memory_fts WHERE id = ?", (record_id,))
    db.execute(
        "INSERT INTO memory_fts(id, subject, predicate, object_text, evidence) VALUES (?, ?, ?, ?, ?)",
        (record_id, subject, predicate, value, evidence),
    )


def upsert_record(
    db: sqlite3.Connection,
    *,
    memory_type: str,
    subject: str,
    predicate: str,
    value: str,
    owner: str,
    visibility: str,
    privacy: str,
    source_path: str,
    source_ref: str = "",
    evidence: str = "",
    confidence: float = 0.9,
    status: str = "active",
    valid_from: str = "",
    valid_until: str = "",
    supersedes: str = "",
    metadata: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    memory_type = memory_type if memory_type in ALLOWED_TYPES else "fact"
    status = status if status in ALLOWED_STATUS else "active"
    subject, predicate, value = clean_text(subject, 240), clean_text(predicate, 160), clean_text(value)
    digest = stable_hash(memory_type, subject, predicate, value, owner, source_path)
    existing = db.execute("SELECT id FROM memory_records WHERE content_hash = ?", (digest,)).fetchone()
    if existing:
        return str(existing["id"]), False
    record_id = f"mem-{uuid.uuid4().hex[:16]}"
    db.execute(
        """INSERT INTO memory_records(
          id,memory_type,subject,predicate,object_text,owner,visibility,privacy,
          source_path,source_ref,evidence,confidence,status,valid_from,valid_until,
          recorded_at,supersedes,content_hash,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            record_id, memory_type, subject, predicate, value, owner, visibility, privacy,
            clean_text(source_path, 500), clean_text(source_ref, 240), clean_text(evidence),
            max(0.0, min(1.0, confidence)), status, valid_from or None, valid_until or None,
            iso(), supersedes or None, digest, json.dumps(metadata or {}, sort_keys=True),
        ),
    )
    index_fts(db, record_id, subject, predicate, value, clean_text(evidence))
    return record_id, True


def source_rows() -> Iterable[dict[str, Any]]:
    decisions = load_json(DATA / "decisions.json", {}).get("decisions", [])
    for row in decisions:
        yield {
            "memory_type": "decision", "subject": row.get("title") or "Ecosystem decision",
            "predicate": "decision", "value": row.get("detail") or row.get("status") or "Recorded decision",
            "owner": row.get("agent") or "ecosystem", "visibility": "shared", "privacy": row.get("privacy") or "dashboard-safe",
            "source_path": "data/decisions.json", "source_ref": row.get("id") or "", "confidence": 0.98,
            "valid_from": row.get("time") or "", "metadata": {"decisionStatus": row.get("status")},
        }
    tasks = load_json(DATA / "agent-task-queue.json", {}).get("tasks", [])
    for row in tasks:
        if str(row.get("status") or "").lower() not in {"done", "complete", "completed", "closed"}:
            continue
        yield {
            "memory_type": "episode", "subject": row.get("title") or "Completed ecosystem task",
            "predicate": "completed", "value": row.get("result") or row.get("objective") or "Task completed",
            "owner": row.get("owner") or "ecosystem", "visibility": "shared", "privacy": row.get("privacy") or "dashboard-safe",
            "source_path": "data/agent-task-queue.json", "source_ref": row.get("id") or "", "confidence": 0.92,
            "valid_from": row.get("completedAt") or row.get("updatedAt") or "",
        }
    graph = load_json(INDEX_PATH, {})
    for row in graph.get("nodes", []):
        if row.get("type") not in {"agent", "capability", "topic"}:
            continue
        label = row.get("label") or row.get("id")
        yield {
            "memory_type": "entity", "subject": label, "predicate": "known entity",
            "value": f"{row.get('type', 'entity')} represented in the ecosystem knowledge graph",
            "owner": "ecosystem", "visibility": "shared", "privacy": "dashboard-safe",
            "source_path": "data/agent-semantic-memory-index.json", "source_ref": row.get("id") or "", "confidence": 0.86,
            "metadata": {"legacyGraphSources": row.get("sources", [])[:8]},
        }


def build(db: sqlite3.Connection) -> dict[str, Any]:
    added = 0
    seen = 0
    for row in source_rows():
        seen += 1
        _, created = upsert_record(db, **row)
        added += int(created)
    db.commit()
    return {"seen": seen, "added": added}


def propose(db: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    memory_type = args.type if args.type in ALLOWED_TYPES else "fact"
    digest = stable_hash(memory_type, args.subject, args.predicate, args.value, args.owner, args.source)
    row = db.execute("SELECT id,status FROM memory_candidates WHERE content_hash = ?", (digest,)).fetchone()
    if row:
        return {"id": row["id"], "status": row["status"], "duplicate": True}
    candidate_id = f"candidate-{uuid.uuid4().hex[:16]}"
    source_kind = clean_text(getattr(args, "source_kind", "legacy"), 40) or "legacy"
    source_ref = clean_text(getattr(args, "source_ref", ""), 240)
    extraction_version = clean_text(getattr(args, "extraction_version", ""), 120)
    governance_eligible = int(bool(getattr(args, "governance_eligible", source_kind != "brain-source")))
    injection_status = clean_text(getattr(args, "injection_status", "not-applicable"), 40) or "not-applicable"
    source_state = clean_text(getattr(args, "source_state", "active"), 40) or "active"
    metadata = getattr(args, "metadata", {})
    db.execute(
        """INSERT INTO memory_candidates(
          id,proposed_by,memory_type,subject,predicate,object_text,owner,visibility,privacy,
          source_path,evidence,confidence,status,proposed_at,content_hash,source_ref,source_kind,
          extraction_version,governance_eligible,injection_status,source_state,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            candidate_id, args.agent, memory_type, clean_text(args.subject, 240), clean_text(args.predicate, 160),
            clean_text(args.value), args.owner, args.visibility, args.privacy, clean_text(args.source, 500),
            clean_text(args.evidence), args.confidence, "candidate", iso(), digest,
            source_ref, source_kind, extraction_version, governance_eligible, injection_status,
            source_state, json.dumps(metadata if isinstance(metadata, dict) else {}, sort_keys=True),
        ),
    )
    db.commit()
    return {"id": candidate_id, "status": "candidate", "duplicate": False}


def brain_candidate_governance_ready(row: sqlite3.Row) -> bool:
    """Fail closed unless a Brain candidate passed the trusted review boundary."""
    if row["source_kind"] != "brain-source":
        return True
    return (
        bool(row["governance_eligible"])
        and row["injection_status"] == "clear"
        and row["source_state"] == "active"
    )


def candidate_metadata(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(row["metadata_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def review(db: sqlite3.Connection, *, apply_safe: bool) -> dict[str, Any]:
    started = iso()
    rows = db.execute("SELECT * FROM memory_candidates WHERE status = 'candidate' ORDER BY proposed_at").fetchall()
    promoted = disputed = 0
    for row in rows:
        conflict = db.execute(
            """SELECT id,object_text FROM memory_records
               WHERE subject = ? AND predicate = ? AND status = 'active' AND object_text != ? LIMIT 1""",
            (row["subject"], row["predicate"], row["object_text"]),
        ).fetchone()
        if conflict:
            db.execute(
                "UPDATE memory_candidates SET status='disputed',reviewed_at=?,review_reason=? WHERE id=?",
                (iso(), f"Conflicts with {conflict['id']}", row["id"]),
            )
            disputed += 1
            continue
        safe = (
            apply_safe and row["memory_type"] in AUTO_PROMOTE_TYPES and float(row["confidence"]) >= 0.9
            and privacy_class(row["privacy"]) == "public" and row["source_path"]
            and row["source_state"] == "active"
            and row["injection_status"] not in {"flagged", "quarantined"}
            and brain_candidate_governance_ready(row)
        )
        if safe:
            upsert_record(
                db, memory_type=row["memory_type"], subject=row["subject"], predicate=row["predicate"],
                value=row["object_text"], owner=row["owner"], visibility=row["visibility"], privacy=row["privacy"],
                source_path=row["source_path"], source_ref=row["source_ref"], evidence=row["evidence"] or "",
                confidence=float(row["confidence"]), metadata=candidate_metadata(row),
            )
            db.execute(
                "UPDATE memory_candidates SET status='active',reviewed_at=?,review_reason='Auto-promoted: verified low-risk memory' WHERE id=?",
                (iso(), row["id"]),
            )
            promoted += 1
    expired = db.execute(
        "UPDATE memory_records SET status='expired' WHERE status='active' AND valid_until IS NOT NULL AND valid_until < ?",
        (iso(),),
    ).rowcount
    completed = iso()
    report = {
        "candidatesSeen": len(rows), "promoted": promoted, "disputed": disputed,
        "pending": len(rows) - promoted - disputed, "expired": expired,
    }
    db.execute(
        "INSERT INTO memory_reviews VALUES (?,?,?,?,?,?,?,?,?)",
        (f"review-{uuid.uuid4().hex[:12]}", started, completed, "ok", len(rows), promoted, disputed, expired, json.dumps(report),),
    )
    db.commit()
    return report


def candidate_rows(db: sqlite3.Connection, status: str = "candidate") -> dict[str, Any]:
    rows = db.execute(
        "SELECT * FROM memory_candidates WHERE status = ? ORDER BY proposed_at LIMIT 100",
        (status,),
    ).fetchall()
    return {
        "status": status,
        "candidates": [
            {
                "id": row["id"], "type": row["memory_type"], "subject": row["subject"],
                "predicate": row["predicate"], "value": row["object_text"], "owner": row["owner"],
                "visibility": row["visibility"], "privacy": row["privacy"], "source": row["source_path"],
                "evidence": row["evidence"], "confidence": row["confidence"], "proposedBy": row["proposed_by"],
                "proposedAt": row["proposed_at"], "reviewReason": row["review_reason"],
            }
            for row in rows
        ],
    }


def approve_candidate(db: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    row = db.execute("SELECT * FROM memory_candidates WHERE id = ?", (args.id,)).fetchone()
    if not row or row["status"] not in {"candidate", "disputed"}:
        raise SystemExit(f"Candidate {args.id} is not pending review.")
    if row["source_state"] != "active":
        raise SystemExit("Candidate source is not active.")
    if row["source_kind"] == "brain-source" and not brain_candidate_governance_ready(row):
        raise SystemExit("Brain candidate is not eligible for approval or has not passed the untrusted-content boundary.")
    conflicts = db.execute(
        "SELECT id FROM memory_records WHERE subject=? AND predicate=? AND status='active' AND object_text!=?",
        (row["subject"], row["predicate"], row["object_text"]),
    ).fetchall()
    conflict_ids = [item["id"] for item in conflicts]
    if conflict_ids and not args.supersedes:
        raise SystemExit(f"Candidate conflicts with {', '.join(conflict_ids)}; pass --supersedes <id> after verification.")
    if args.supersedes and args.supersedes not in conflict_ids:
        raise SystemExit("--supersedes must identify the active conflicting record.")
    record_id, _ = upsert_record(
        db, memory_type=row["memory_type"], subject=row["subject"], predicate=row["predicate"],
        value=row["object_text"], owner=row["owner"], visibility=row["visibility"], privacy=row["privacy"],
        source_path=row["source_path"], source_ref=row["source_ref"], evidence=row["evidence"] or "",
        confidence=float(row["confidence"]), supersedes=args.supersedes or "",
        metadata=candidate_metadata(row),
    )
    if args.supersedes:
        db.execute("UPDATE memory_records SET status='superseded' WHERE id=?", (args.supersedes,))
    db.execute(
        "UPDATE memory_candidates SET status='active',reviewed_at=?,review_reason=? WHERE id=?",
        (iso(), f"Approved by {args.reviewer}", args.id),
    )
    db.commit()
    return {"id": args.id, "recordId": record_id, "status": "active", "reviewer": args.reviewer, "supersedes": args.supersedes}


def reject_candidate(db: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    changed = db.execute(
        "UPDATE memory_candidates SET status='rejected',reviewed_at=?,review_reason=? WHERE id=? AND status IN ('candidate','disputed')",
        (iso(), f"Rejected by {args.reviewer}: {args.reason}", args.id),
    ).rowcount
    if not changed:
        raise SystemExit(f"Candidate {args.id} is not pending review.")
    db.commit()
    return {"id": args.id, "status": "rejected", "reviewer": args.reviewer, "reason": args.reason}


def forget_source(db: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    """Tombstone all registry state bound to one private source reference."""
    if not args.confirm:
        raise SystemExit("Source forgetting requires --confirm.")
    source = clean_text(args.source, 500)
    if not source or not source.startswith("brain-source:"):
        raise SystemExit("Only an exact Brain source reference may use this path.")
    candidates = db.execute(
        "SELECT id FROM memory_candidates WHERE source_path=? OR source_ref=?",
        (source, source),
    ).fetchall()
    records = db.execute(
        "SELECT id FROM memory_records WHERE source_path=? OR source_ref=?",
        (source, source),
    ).fetchall()
    candidate_ids = [row["id"] for row in candidates]
    record_ids = [row["id"] for row in records]
    if candidate_ids:
        placeholders = ",".join("?" for _ in candidate_ids)
        db.execute(
            f"""UPDATE memory_candidates
                SET status='forgotten',source_state='forgotten',subject='',predicate='',
                    object_text='',evidence='',metadata_json='{{}}',review_reason='Source forgotten'
                WHERE id IN ({placeholders})""",
            candidate_ids,
        )
    fts_deleted = 0
    for record_id in record_ids:
        fts_deleted += db.execute("DELETE FROM memory_fts WHERE id=?", (record_id,)).rowcount
    if record_ids:
        placeholders = ",".join("?" for _ in record_ids)
        db.execute(
            f"""UPDATE memory_records
                SET status='forgotten',subject='',predicate='',object_text='',evidence='',
                    valid_from=NULL,valid_until=NULL,supersedes=NULL,metadata_json='{{}}'
                WHERE id IN ({placeholders})""",
            record_ids,
        )
    deletion_id = f"memory-delete-{uuid.uuid4().hex[:14]}"
    db.execute(
        "INSERT INTO memory_deletions VALUES(?,?,?,?,?,?,?,?)",
        (
            deletion_id, iso(), stable_hash(source), args.actor, len(candidate_ids),
            len(record_ids), fts_deleted, "forgotten",
        ),
    )
    db.commit()
    return {
        "id": deletion_id, "status": "forgotten",
        "candidateCount": len(candidate_ids), "recordCount": len(record_ids),
        "ftsDeleted": fts_deleted,
    }


def retrieve(db: sqlite3.Connection, args: argparse.Namespace, *, preflight: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    terms = [token.lower() for token in TOKEN_RE.findall(args.query)][:16]
    match = " OR ".join(f'"{term}"' for term in terms) if terms else '"__none__"'
    try:
        rows = db.execute(
            """SELECT r.*, bm25(memory_fts) AS rank FROM memory_fts
               JOIN memory_records r ON r.id = memory_fts.id
               WHERE memory_fts MATCH ? AND r.status = 'active'
               ORDER BY rank LIMIT ?""",
            (match, max(args.limit * 4, 12)),
        ).fetchall()
    except sqlite3.OperationalError:
        if preflight:
            raise
        rows = []
    visible = [
        row for row in rows
        if visibility_allowed(args.agent, row["visibility"], row["privacy"], row["owner"])
    ][: args.limit]
    latency = round((time.perf_counter() - started) * 1000, 2)
    ids = [row["id"] for row in visible]
    retrieval_id = f"retrieval-{uuid.uuid4().hex[:14]}"
    context = context_hashes(args)
    db.execute(
        """INSERT INTO retrieval_events(
          id,time,agent,scope,query_hash,term_count,matched_count,latency_ms,
          memory_ids_json,outcome,work_id_hash,run_id_hash,session_id_hash,preflight
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            retrieval_id, iso(), args.agent, args.scope, stable_hash(args.query), len(terms), len(ids),
            latency, json.dumps(ids), "hit" if ids else "miss", context["workIdHash"],
            context["runIdHash"], context["sessionIdHash"], int(preflight),
        ),
    )
    db.commit()
    result = {
        "retrievalId": retrieval_id, "agent": args.agent, "scope": args.scope, "latencyMs": latency,
        "context": {key: value for key, value in context.items() if value},
        "results": [
            {
                "id": row["id"], "type": row["memory_type"], "subject": row["subject"],
                "predicate": row["predicate"], "value": row["object_text"], "owner": row["owner"],
                "confidence": row["confidence"], "source": row["source_path"], "sourceRef": row["source_ref"],
                "validFrom": row["valid_from"], "validUntil": row["valid_until"], "status": row["status"],
            }
            for row in visible
        ],
    }
    if not preflight:
        result["query"] = args.query
    return result


def reuse_preflight(args: argparse.Namespace) -> dict[str, Any]:
    """Retrieve reusable context without ever blocking the caller.

    A registry, database, or FTS failure returns a small dashboard-safe status
    with ``proceed=true``. The raw query and raw workflow identifiers are never
    written to telemetry or echoed by this preflight response.
    """

    db = None
    try:
        db = connect()
        result = retrieve(db, args, preflight=True)
        export_status(db)
        result.update({
            "ok": True,
            "proceed": True,
            "status": "hit" if result["results"] else "miss",
            "failOpen": False,
        })
        return result
    except Exception:
        return {
            "ok": True,
            "proceed": True,
            "status": "unavailable",
            "failOpen": True,
            "errorCode": "memory-registry-unavailable",
            "agent": args.agent,
            "scope": args.scope,
            "context": {key: value for key, value in context_hashes(args).items() if value},
            "results": [],
        }
    finally:
        if db is not None:
            db.close()


def reuse_context(
    db: sqlite3.Connection,
    retrieval: sqlite3.Row,
    args: argparse.Namespace,
    memory_id: str,
) -> dict[str, str | None]:
    provided = context_hashes(args)
    columns = {
        "workIdHash": "work_id_hash",
        "runIdHash": "run_id_hash",
        "sessionIdHash": "session_id_hash",
    }
    merged: dict[str, str | None] = {}
    for output_name, column in columns.items():
        recorded = retrieval[column]
        supplied = provided[output_name]
        if recorded and supplied and recorded != supplied:
            raise SystemExit(f"{output_name} does not match the retrieval context.")
        merged[output_name] = supplied or recorded
    prior = db.execute(
        """SELECT work_id_hash,run_id_hash,session_id_hash
           FROM memory_reuse_events
           WHERE retrieval_id=? AND memory_id=?
           ORDER BY time,id LIMIT 1""",
        (retrieval["id"], memory_id),
    ).fetchone()
    if prior:
        for output_name, column in columns.items():
            recorded = prior[column]
            current = merged[output_name]
            if recorded and current and recorded != current:
                raise SystemExit(f"{output_name} does not match the established reuse context.")
            merged[output_name] = current or recorded
    return merged


def record_reuse_outcome(db: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    retrieval = db.execute("SELECT * FROM retrieval_events WHERE id=?", (args.retrieval_id,)).fetchone()
    if not retrieval:
        raise SystemExit(f"Unknown retrieval {args.retrieval_id}.")
    if canonical_agent(retrieval["agent"]) != canonical_agent(args.agent):
        raise SystemExit("Reuse outcome agent must match the retrieval owner.")
    memory = db.execute("SELECT id FROM memory_records WHERE id=?", (args.memory_id,)).fetchone()
    if not memory:
        raise SystemExit(f"Unknown memory {args.memory_id}.")
    if args.memory_id not in json.loads(retrieval["memory_ids_json"] or "[]"):
        raise SystemExit("The memory was not returned by the specified retrieval.")
    if args.outcome == "used":
        selected = db.execute(
            """SELECT id FROM memory_reuse_events
               WHERE retrieval_id=? AND memory_id=? AND outcome='selected'""",
            (args.retrieval_id, args.memory_id),
        ).fetchone()
        if not selected:
            raise SystemExit("Record selected before recording used.")
    existing = db.execute(
        "SELECT id FROM memory_reuse_events WHERE retrieval_id=? AND memory_id=? AND outcome=?",
        (args.retrieval_id, args.memory_id, args.outcome),
    ).fetchone()
    context = reuse_context(db, retrieval, args, args.memory_id)
    if existing:
        return {
            "id": existing["id"], "status": "recorded", "duplicate": True,
            "agent": args.agent, "retrievalId": args.retrieval_id,
            "memoryId": args.memory_id, "outcome": args.outcome,
            "context": {key: value for key, value in context.items() if value},
        }
    event_id = f"reuse-{uuid.uuid4().hex[:14]}"
    db.execute(
        """INSERT INTO memory_reuse_events(
          id,time,agent,retrieval_id,memory_id,outcome,reason_code,
          work_id_hash,run_id_hash,session_id_hash,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id, iso(), args.agent, args.retrieval_id, args.memory_id, args.outcome,
            args.reason_code, context["workIdHash"], context["runIdHash"],
            context["sessionIdHash"], "{}",
        ),
    )
    db.commit()
    return {
        "id": event_id, "status": "recorded", "duplicate": False,
        "agent": args.agent, "retrievalId": args.retrieval_id,
        "memoryId": args.memory_id, "outcome": args.outcome,
        "context": {key: value for key, value in context.items() if value},
    }


def record_feedback(db: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    if not args.retrieval_id and not args.memory_id:
        raise SystemExit("Feedback requires --retrieval-id or --memory-id.")
    retrieval = None
    if args.retrieval_id:
        retrieval = db.execute("SELECT * FROM retrieval_events WHERE id=?", (args.retrieval_id,)).fetchone()
        if not retrieval:
            raise SystemExit(f"Unknown retrieval {args.retrieval_id}.")
    memory = None
    if args.memory_id:
        memory = db.execute("SELECT * FROM memory_records WHERE id=?", (args.memory_id,)).fetchone()
        if not memory:
            raise SystemExit(f"Unknown memory {args.memory_id}.")
        if retrieval and args.memory_id not in json.loads(retrieval["memory_ids_json"] or "[]"):
            raise SystemExit("The memory was not returned by the specified retrieval.")
    if args.outcome in {"corrected", "harmful"} and not args.memory_id:
        raise SystemExit(f"{args.outcome} feedback requires --memory-id.")
    if args.outcome == "corrected" and not args.correction:
        raise SystemExit("Corrected feedback requires --correction.")

    candidate_id = None
    if args.outcome == "corrected":
        proposal = argparse.Namespace(
            agent=args.agent, type=memory["memory_type"], subject=memory["subject"],
            predicate=memory["predicate"], value=args.correction, owner=memory["owner"],
            visibility=memory["visibility"], privacy=memory["privacy"],
            source=f"feedback:{args.retrieval_id or args.memory_id}",
            evidence=f"Outcome correction: {clean_text(args.reason, 600)}", confidence=0.95,
        )
        candidate_id = propose(db, proposal)["id"]

    feedback_id = f"feedback-{uuid.uuid4().hex[:14]}"
    db.execute(
        """INSERT INTO memory_feedback(
          id,time,agent,retrieval_id,memory_id,outcome,reason,correction_candidate_id,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            feedback_id, iso(), args.agent, args.retrieval_id or None, args.memory_id or None,
            args.outcome, clean_text(args.reason, 800), candidate_id,
            json.dumps({"correctionProvided": bool(args.correction)}),
        ),
    )
    db.commit()
    return {
        "id": feedback_id, "status": "recorded", "agent": args.agent, "outcome": args.outcome,
        "retrievalId": args.retrieval_id or None, "memoryId": args.memory_id or None,
        "correctionCandidateId": candidate_id,
    }


def privacy_audit_payload(db: sqlite3.Connection) -> dict[str, Any]:
    rows = db.execute(
        "SELECT owner,visibility,privacy FROM memory_records WHERE status='active'"
    ).fetchall()
    public_records = owner_private_records = unknown_owner_private = cross_owner_leaks = 0
    agents = ("josh2", "jaimes", "jain")
    for row in rows:
        label = normalize_privacy_label(row["privacy"])
        if privacy_class(label) == "public":
            public_records += 1
            continue
        owner_private_records += 1
        if label not in KNOWN_OWNER_PRIVATE_PRIVACY:
            unknown_owner_private += 1
        owner = canonical_agent(row["owner"])
        cross_owner_leaks += sum(
            visibility_allowed(agent, row["visibility"], row["privacy"], row["owner"])
            for agent in agents
            if agent != owner
        )
    return {
        "checkedAt": iso(),
        "ok": cross_owner_leaks == 0,
        "policy": "deny-by-default",
        "publicLabels": sorted(PUBLIC_PRIVACY),
        "activePublic": public_records,
        "activeOwnerPrivate": owner_private_records,
        "unknownLabelsOwnerScoped": unknown_owner_private,
        "crossOwnerPrivateLeaks": cross_owner_leaks,
    }


def memory_activity_payload(db: sqlite3.Connection) -> dict[str, Any]:
    """Return bounded, counts-only telemetry for the Control Tower Atlas.

    The dashboard can show that a governed memory operation happened, but it
    never receives queries, memory content, raw identifiers, source paths,
    feedback reasons, or workflow context hashes.
    """

    now = utc_now()
    window_start = iso(now - dt.timedelta(minutes=MEMORY_ACTIVITY_WINDOW_MINUTES))
    retrieval = db.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN outcome='hit' THEN 1 ELSE 0 END) AS hits,
                  SUM(CASE WHEN outcome='miss' THEN 1 ELSE 0 END) AS misses,
                  MAX(time) AS last_at,
                  MAX(CASE WHEN outcome='hit' THEN time END) AS last_hit_at,
                  MAX(CASE WHEN outcome='miss' THEN time END) AS last_miss_at
           FROM retrieval_events WHERE time >= ?""",
        (window_start,),
    ).fetchone()
    reuse = db.execute(
        """SELECT SUM(CASE WHEN outcome='selected' THEN 1 ELSE 0 END) AS selected,
                  SUM(CASE WHEN outcome='used' THEN 1 ELSE 0 END) AS used,
                  SUM(CASE WHEN outcome='ignored' THEN 1 ELSE 0 END) AS ignored,
                  MAX(CASE WHEN outcome='selected' THEN time END) AS last_selected_at,
                  MAX(CASE WHEN outcome='used' THEN time END) AS last_used_at,
                  MAX(CASE WHEN outcome='ignored' THEN time END) AS last_ignored_at
           FROM memory_reuse_events WHERE time >= ?""",
        (window_start,),
    ).fetchone()
    feedback = db.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN outcome='helpful' THEN 1 ELSE 0 END) AS helpful,
                  SUM(CASE WHEN outcome='ignored' THEN 1 ELSE 0 END) AS ignored,
                  SUM(CASE WHEN outcome='corrected' THEN 1 ELSE 0 END) AS corrected,
                  SUM(CASE WHEN outcome='harmful' THEN 1 ELSE 0 END) AS harmful,
                  MAX(time) AS last_at,
                  MAX(CASE WHEN outcome='corrected' THEN time END) AS last_corrected_at
           FROM memory_feedback WHERE time >= ?""",
        (window_start,),
    ).fetchone()
    candidates = db.execute(
        """SELECT
                  SUM(CASE WHEN proposed_at >= ? THEN 1 ELSE 0 END) AS proposed,
                  SUM(CASE WHEN status='active' AND reviewed_at >= ? THEN 1 ELSE 0 END) AS promoted,
                  MAX(CASE WHEN proposed_at >= ? THEN proposed_at END) AS last_proposed_at,
                  MAX(CASE WHEN status='active' AND reviewed_at >= ? THEN reviewed_at END) AS last_promoted_at
           FROM memory_candidates""",
        (window_start, window_start, window_start, window_start),
    ).fetchone()
    agent_rows = {
        canonical_agent(row["agent"]): row
        for row in db.execute(
            """SELECT agent,COUNT(*) AS total,
                      SUM(CASE WHEN outcome='hit' THEN 1 ELSE 0 END) AS hits,
                      SUM(CASE WHEN outcome='miss' THEN 1 ELSE 0 END) AS misses,
                      MAX(time) AS last_at
               FROM retrieval_events WHERE time >= ? GROUP BY agent""",
            (window_start,),
        ).fetchall()
    }
    reuse_agent_rows: dict[str, dict[str, Any]] = {
        agent: {
            "selected": 0,
            "used": 0,
            "crossAgentUsed": 0,
            "lastSelectedAt": None,
            "lastUsedAt": None,
            "lastCrossAgentUsedAt": None,
        }
        for agent in MEMORY_ACTIVITY_AGENTS
    }
    reuse_links: dict[tuple[str, str], dict[str, Any]] = {}
    for row in db.execute(
        """SELECT event.agent,event.outcome,event.time,record.owner
           FROM memory_reuse_events event
           JOIN memory_records record ON record.id=event.memory_id
           WHERE event.time >= ?
           ORDER BY event.time,event.id""",
        (window_start,),
    ).fetchall():
        consumer = canonical_agent(row["agent"])
        if consumer not in reuse_agent_rows:
            continue
        metrics = reuse_agent_rows[consumer]
        outcome = str(row["outcome"] or "")
        if outcome == "selected":
            metrics["selected"] += 1
            metrics["lastSelectedAt"] = row["time"]
        elif outcome == "used":
            metrics["used"] += 1
            metrics["lastUsedAt"] = row["time"]
            source = canonical_agent(row["owner"])
            if source in MEMORY_ACTIVITY_AGENTS and source != consumer:
                metrics["crossAgentUsed"] += 1
                metrics["lastCrossAgentUsedAt"] = row["time"]
                link = reuse_links.setdefault((source, consumer), {
                    "sourceAgent": source,
                    "consumerAgent": consumer,
                    "uses": 0,
                    "lastUsedAt": None,
                })
                link["uses"] += 1
                link["lastUsedAt"] = row["time"]
    cross_agent_used = sum(row["crossAgentUsed"] for row in reuse_agent_rows.values())
    last_cross_agent_used = max(
        (row["lastCrossAgentUsedAt"] for row in reuse_agent_rows.values() if row["lastCrossAgentUsedAt"]),
        default=None,
    )
    agents = []
    for agent in MEMORY_ACTIVITY_AGENTS:
        row = agent_rows.get(agent)
        reuse_row = reuse_agent_rows[agent]
        agents.append({
            "agent": agent,
            "retrievals": int(row["total"] or 0) if row else 0,
            "hits": int(row["hits"] or 0) if row else 0,
            "misses": int(row["misses"] or 0) if row else 0,
            "lastRetrievalAt": row["last_at"] if row else None,
            **reuse_row,
        })
    return {
        "schemaVersion": 2,
        "generatedAt": iso(now),
        "windowMinutes": MEMORY_ACTIVITY_WINDOW_MINUTES,
        "motionWindowSeconds": MEMORY_ACTIVITY_MOTION_SECONDS,
        "source": {"name": "governed-memory-registry", "verified": True},
        "privacy": {
            "queryIncluded": False,
            "contentIncluded": False,
            "rawIdentifiersIncluded": False,
            "reasonsIncluded": False,
            "countsOnly": True,
        },
        "counts": {
            "retrievals": int(retrieval["total"] or 0),
            "hits": int(retrieval["hits"] or 0),
            "misses": int(retrieval["misses"] or 0),
            "selected": int(reuse["selected"] or 0),
            "used": int(reuse["used"] or 0),
            "crossAgentUsed": cross_agent_used,
            "reuseIgnored": int(reuse["ignored"] or 0),
            "feedback": int(feedback["total"] or 0),
            "helpful": int(feedback["helpful"] or 0),
            "feedbackIgnored": int(feedback["ignored"] or 0),
            "corrected": int(feedback["corrected"] or 0),
            "harmful": int(feedback["harmful"] or 0),
            "proposed": int(candidates["proposed"] or 0),
            "promoted": int(candidates["promoted"] or 0),
        },
        "lastObservedAt": {
            "retrieval": retrieval["last_at"],
            "hit": retrieval["last_hit_at"],
            "miss": retrieval["last_miss_at"],
            "selected": reuse["last_selected_at"],
            "used": reuse["last_used_at"],
            "crossAgentUsed": last_cross_agent_used,
            "reuseIgnored": reuse["last_ignored_at"],
            "feedback": feedback["last_at"],
            "corrected": feedback["last_corrected_at"],
            "proposed": candidates["last_proposed_at"],
            "promoted": candidates["last_promoted_at"],
        },
        "agents": agents,
        "reuseLinks": sorted(reuse_links.values(), key=lambda row: (row["sourceAgent"], row["consumerAgent"])),
    }


def status_payload(db: sqlite3.Connection) -> dict[str, Any]:
    counts = {row["status"]: row["count"] for row in db.execute("SELECT status,COUNT(*) AS count FROM memory_records GROUP BY status")}
    candidates = {row["status"]: row["count"] for row in db.execute("SELECT status,COUNT(*) AS count FROM memory_candidates GROUP BY status")}
    retrieval = db.execute(
        """SELECT COUNT(*) AS total, SUM(CASE WHEN outcome='hit' THEN 1 ELSE 0 END) AS hits,
                  AVG(latency_ms) AS avg_latency,
                  SUM(CASE WHEN preflight=1 THEN 1 ELSE 0 END) AS preflights
           FROM retrieval_events WHERE time >= ?""",
        (iso(utc_now() - dt.timedelta(days=7)),),
    ).fetchone()
    feedback = db.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN outcome='helpful' THEN 1 ELSE 0 END) AS helpful,
                  SUM(CASE WHEN outcome='ignored' THEN 1 ELSE 0 END) AS ignored,
                  SUM(CASE WHEN outcome='corrected' THEN 1 ELSE 0 END) AS corrected,
                  SUM(CASE WHEN outcome='harmful' THEN 1 ELSE 0 END) AS harmful
           FROM memory_feedback WHERE time >= ?""",
        (iso(utc_now() - dt.timedelta(days=30)),),
    ).fetchone()
    reuse = db.execute(
        """WITH recent AS (
                 SELECT * FROM memory_reuse_events WHERE time >= ?
               )
               SELECT COUNT(*) AS total,
                  SUM(CASE WHEN event.outcome='selected' THEN 1 ELSE 0 END) AS selected,
                  SUM(CASE WHEN event.outcome='used' AND EXISTS (
                    SELECT 1 FROM recent selected
                    WHERE selected.retrieval_id=event.retrieval_id
                      AND selected.memory_id=event.memory_id
                      AND selected.outcome='selected'
                  ) THEN 1 ELSE 0 END) AS used,
                  SUM(CASE WHEN event.outcome='ignored' THEN 1 ELSE 0 END) AS ignored
               FROM recent event""",
        (iso(utc_now() - dt.timedelta(days=30)),),
    ).fetchone()
    last_review = db.execute("SELECT * FROM memory_reviews ORDER BY completed_at DESC LIMIT 1").fetchone()
    total = int(retrieval["total"] or 0)
    hits = int(retrieval["hits"] or 0)
    pending = int(candidates.get("candidate", 0))
    disputed = int(candidates.get("disputed", 0)) + int(counts.get("disputed", 0))
    feedback_total = int(feedback["total"] or 0)
    helpful = int(feedback["helpful"] or 0)
    ignored = int(feedback["ignored"] or 0)
    corrected = int(feedback["corrected"] or 0)
    harmful = int(feedback["harmful"] or 0)
    preflights = int(retrieval["preflights"] or 0)
    selected = int(reuse["selected"] or 0)
    used = int(reuse["used"] or 0)
    reuse_ignored = int(reuse["ignored"] or 0)
    status = "attention" if disputed else "watch" if pending else "ok"
    return {
        "updatedAt": iso(), "status": status,
        "summary": "Shared memory is healthy" if status == "ok" else f"{pending} candidate(s), {disputed} conflict(s) need review",
        "registry": {
            "active": int(counts.get("active", 0)), "superseded": int(counts.get("superseded", 0)),
            "expired": int(counts.get("expired", 0)), "sources": 4,
        },
        "review": {
            "pending": pending, "disputed": disputed,
            "lastRun": last_review["completed_at"] if last_review else None,
            "lastStatus": last_review["status"] if last_review else "not-run",
        },
        "retrieval": {
            "queries7d": total, "hits7d": hits, "hitRate": round(hits / total * 100, 1) if total else None,
            "avgLatencyMs": round(float(retrieval["avg_latency"] or 0), 1),
            "feedback30d": feedback_total, "helpful30d": helpful, "ignored30d": ignored,
            "corrected30d": corrected, "harmful30d": harmful,
            "qualityRate": round(helpful / feedback_total * 100, 1) if feedback_total else None,
            "qualityDefinition": "helpful feedback divided by all feedback, including ignored, corrected, and harmful",
            "preflights7d": preflights,
            "selected30d": selected, "used30d": used, "reuseIgnored30d": reuse_ignored,
            "selectedUseRate": round(used / selected * 100, 1) if selected else None,
        },
        "governance": {
            "sourceOfTruth": "Checked-in AGENTS.md, MEMORY.md, and skills",
            "autoPromote": "Verified low-risk facts, lessons, entities, and relationships only",
            "manualReview": "Preferences, procedures, policy, sensitive facts, and conflicts",
            "privacy": "Only dashboard-safe/public memory may cross owners; all other and unknown labels are owner/JOSHeX scoped",
        },
        "agentAccess": {
            "josh2": "local CLI", "jaimes": "shared SSH client", "jain": "shared SSH client", "joshex": "oversight SSH client",
        },
        "activity": memory_activity_payload(db),
        "privacy": privacy_audit_payload(db),
    }


def export_status(db: sqlite3.Connection) -> dict[str, Any]:
    payload = status_payload(db)
    atomic_write_json(STATUS_PATH, payload)
    return payload


def add_context_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--work-id", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("build")
    review_cmd = sub.add_parser("review")
    review_cmd.add_argument("--apply-safe", action="store_true")
    candidates_cmd = sub.add_parser("candidates")
    candidates_cmd.add_argument("--status", default="candidate", choices=sorted(ALLOWED_STATUS))
    approve_cmd = sub.add_parser("approve")
    approve_cmd.add_argument("--id", required=True)
    approve_cmd.add_argument("--reviewer", required=True, choices=["joshex", "josh2", "jaimes", "jain", "josh"])
    approve_cmd.add_argument("--supersedes", default="")
    reject_cmd = sub.add_parser("reject")
    reject_cmd.add_argument("--id", required=True)
    reject_cmd.add_argument("--reviewer", required=True, choices=["joshex", "josh2", "jaimes", "jain", "josh"])
    reject_cmd.add_argument("--reason", required=True)
    propose_cmd = sub.add_parser("propose")
    propose_cmd.add_argument("--agent", required=True, choices=["joshex", "josh2", "jaimes", "jain"])
    propose_cmd.add_argument("--type", required=True, choices=sorted(ALLOWED_TYPES))
    propose_cmd.add_argument("--subject", required=True)
    propose_cmd.add_argument("--predicate", default="states")
    propose_cmd.add_argument("--value", required=True)
    propose_cmd.add_argument("--owner", default="ecosystem")
    propose_cmd.add_argument("--visibility", default="shared")
    propose_cmd.add_argument("--privacy", default="agent-private")
    propose_cmd.add_argument("--source", required=True)
    propose_cmd.add_argument("--evidence", default="")
    propose_cmd.add_argument("--confidence", type=float, default=0.8)
    propose_cmd.add_argument("--source-ref", default="")
    propose_cmd.add_argument("--source-kind", default="legacy", choices=["legacy", "brain-source"])
    propose_cmd.add_argument("--extraction-version", default="")
    propose_cmd.add_argument("--governance-eligible", action="store_true")
    propose_cmd.add_argument("--injection-status", default="not-applicable", choices=["not-applicable", "clear", "flagged", "quarantined"])
    retrieve_cmd = sub.add_parser("retrieve")
    retrieve_cmd.add_argument("--agent", required=True, choices=["joshex", "josh2", "jaimes", "jain"])
    retrieve_cmd.add_argument("--query", required=True)
    retrieve_cmd.add_argument("--scope", default="ecosystem")
    retrieve_cmd.add_argument("--limit", type=int, default=6)
    add_context_arguments(retrieve_cmd)
    preflight_cmd = sub.add_parser("preflight")
    preflight_cmd.add_argument("--agent", required=True, choices=["joshex", "josh2", "jaimes", "jain"])
    preflight_cmd.add_argument("--query", required=True)
    preflight_cmd.add_argument("--scope", default="ecosystem")
    preflight_cmd.add_argument("--limit", type=int, default=3)
    add_context_arguments(preflight_cmd)
    reuse_cmd = sub.add_parser("reuse-outcome")
    reuse_cmd.add_argument("--agent", required=True, choices=["joshex", "josh2", "jaimes", "jain"])
    reuse_cmd.add_argument("--retrieval-id", required=True)
    reuse_cmd.add_argument("--memory-id", required=True)
    reuse_cmd.add_argument("--outcome", required=True, choices=sorted(REUSE_OUTCOMES))
    reuse_cmd.add_argument("--reason-code", default="other", choices=sorted(REUSE_REASON_CODES))
    add_context_arguments(reuse_cmd)
    feedback_cmd = sub.add_parser("feedback")
    feedback_cmd.add_argument("--agent", required=True, choices=["joshex", "josh2", "jaimes", "jain"])
    feedback_cmd.add_argument("--outcome", required=True, choices=sorted(FEEDBACK_OUTCOMES))
    feedback_cmd.add_argument("--retrieval-id", default="")
    feedback_cmd.add_argument("--memory-id", default="")
    feedback_cmd.add_argument("--reason", required=True)
    feedback_cmd.add_argument("--correction", default="")
    forget_cmd = sub.add_parser("forget-source")
    forget_cmd.add_argument("--source", required=True)
    forget_cmd.add_argument("--actor", required=True, choices=["joshex", "josh2", "josh"])
    forget_cmd.add_argument("--confirm", action="store_true")
    sub.add_parser("privacy-check")
    sub.add_parser("status")
    sub.add_parser("export")
    args = parser.parse_args()
    if args.command == "preflight":
        print(json.dumps(reuse_preflight(args), indent=2))
        return 0
    db = connect()
    if args.command == "init": result = {"ok": True, "database": str(DB_PATH)}
    elif args.command == "build": result = build(db)
    elif args.command == "review": result = review(db, apply_safe=args.apply_safe)
    elif args.command == "candidates": result = candidate_rows(db, args.status)
    elif args.command == "approve": result = approve_candidate(db, args)
    elif args.command == "reject": result = reject_candidate(db, args)
    elif args.command == "propose": result = propose(db, args)
    elif args.command == "retrieve": result = retrieve(db, args)
    elif args.command == "reuse-outcome": result = record_reuse_outcome(db, args)
    elif args.command == "feedback": result = record_feedback(db, args)
    elif args.command == "forget-source": result = forget_source(db, args)
    elif args.command == "privacy-check": result = privacy_audit_payload(db)
    elif args.command == "status": result = status_payload(db)
    else: result = export_status(db)
    if args.command == "retrieve":
        export_status(db)
    elif args.command not in {"privacy-check", "status", "export"}:
        export_status(db)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
