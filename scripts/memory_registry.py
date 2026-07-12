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
ALLOWED_STATUS = {"candidate", "active", "disputed", "superseded", "expired", "rejected"}
AUTO_PROMOTE_TYPES = {"fact", "lesson", "entity", "relationship"}
FEEDBACK_OUTCOMES = {"helpful", "ignored", "corrected", "harmful"}
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{2,}", re.I)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def stable_hash(*parts: Any) -> str:
    text = "\x1f".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha256(text.encode()).hexdigest()


def clean_text(value: Any, limit: int = 1200) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


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
        """
    )
    return db


def visibility_allowed(agent: str, visibility: str, privacy: str) -> bool:
    if privacy == "sensitive":
        return agent == "joshex" and visibility in {"joshex", "private"}
    return visibility in {"shared", "ecosystem", agent} or agent == "joshex"


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
    db.execute(
        """INSERT INTO memory_candidates(
          id,proposed_by,memory_type,subject,predicate,object_text,owner,visibility,privacy,
          source_path,evidence,confidence,status,proposed_at,content_hash
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            candidate_id, args.agent, memory_type, clean_text(args.subject, 240), clean_text(args.predicate, 160),
            clean_text(args.value), args.owner, args.visibility, args.privacy, clean_text(args.source, 500),
            clean_text(args.evidence), args.confidence, "candidate", iso(), digest,
        ),
    )
    db.commit()
    return {"id": candidate_id, "status": "candidate", "duplicate": False}


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
            and row["privacy"] != "sensitive" and row["source_path"]
        )
        if safe:
            upsert_record(
                db, memory_type=row["memory_type"], subject=row["subject"], predicate=row["predicate"],
                value=row["object_text"], owner=row["owner"], visibility=row["visibility"], privacy=row["privacy"],
                source_path=row["source_path"], evidence=row["evidence"] or "", confidence=float(row["confidence"]),
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
        source_path=row["source_path"], evidence=row["evidence"] or "", confidence=float(row["confidence"]),
        supersedes=args.supersedes or "",
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


def retrieve(db: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
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
        rows = []
    visible = [row for row in rows if visibility_allowed(args.agent, row["visibility"], row["privacy"])][: args.limit]
    latency = round((time.perf_counter() - started) * 1000, 2)
    ids = [row["id"] for row in visible]
    retrieval_id = f"retrieval-{uuid.uuid4().hex[:14]}"
    db.execute(
        "INSERT INTO retrieval_events VALUES (?,?,?,?,?,?,?,?,?,?)",
        (retrieval_id, iso(), args.agent, args.scope, stable_hash(args.query), len(terms), len(ids), latency, json.dumps(ids), "hit" if ids else "miss"),
    )
    db.commit()
    return {
        "retrievalId": retrieval_id, "query": args.query, "agent": args.agent, "scope": args.scope, "latencyMs": latency,
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


def status_payload(db: sqlite3.Connection) -> dict[str, Any]:
    counts = {row["status"]: row["count"] for row in db.execute("SELECT status,COUNT(*) AS count FROM memory_records GROUP BY status")}
    candidates = {row["status"]: row["count"] for row in db.execute("SELECT status,COUNT(*) AS count FROM memory_candidates GROUP BY status")}
    retrieval = db.execute(
        """SELECT COUNT(*) AS total, SUM(CASE WHEN outcome='hit' THEN 1 ELSE 0 END) AS hits,
                  AVG(latency_ms) AS avg_latency FROM retrieval_events WHERE time >= ?""",
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
    graded = helpful + corrected + harmful
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
            "qualityRate": round(helpful / graded * 100, 1) if graded else None,
        },
        "governance": {
            "sourceOfTruth": "Checked-in AGENTS.md, MEMORY.md, and skills",
            "autoPromote": "Verified low-risk facts, lessons, entities, and relationships only",
            "manualReview": "Preferences, procedures, policy, sensitive facts, and conflicts",
            "privacy": "Control Tower receives counts and provenance health only",
        },
        "agentAccess": {
            "josh2": "local CLI", "jaimes": "shared SSH client", "jain": "shared SSH client", "joshex": "oversight SSH client",
        },
    }


def export_status(db: sqlite3.Connection) -> dict[str, Any]:
    payload = status_payload(db)
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


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
    retrieve_cmd = sub.add_parser("retrieve")
    retrieve_cmd.add_argument("--agent", required=True, choices=["joshex", "josh2", "jaimes", "jain"])
    retrieve_cmd.add_argument("--query", required=True)
    retrieve_cmd.add_argument("--scope", default="ecosystem")
    retrieve_cmd.add_argument("--limit", type=int, default=6)
    feedback_cmd = sub.add_parser("feedback")
    feedback_cmd.add_argument("--agent", required=True, choices=["joshex", "josh2", "jaimes", "jain"])
    feedback_cmd.add_argument("--outcome", required=True, choices=sorted(FEEDBACK_OUTCOMES))
    feedback_cmd.add_argument("--retrieval-id", default="")
    feedback_cmd.add_argument("--memory-id", default="")
    feedback_cmd.add_argument("--reason", required=True)
    feedback_cmd.add_argument("--correction", default="")
    sub.add_parser("status")
    sub.add_parser("export")
    args = parser.parse_args()
    db = connect()
    if args.command == "init": result = {"ok": True, "database": str(DB_PATH)}
    elif args.command == "build": result = build(db)
    elif args.command == "review": result = review(db, apply_safe=args.apply_safe)
    elif args.command == "candidates": result = candidate_rows(db, args.status)
    elif args.command == "approve": result = approve_candidate(db, args)
    elif args.command == "reject": result = reject_candidate(db, args)
    elif args.command == "propose": result = propose(db, args)
    elif args.command == "retrieve": result = retrieve(db, args)
    elif args.command == "feedback": result = record_feedback(db, args)
    elif args.command == "status": result = status_payload(db)
    else: result = export_status(db)
    if args.command not in {"retrieve", "status", "export"}:
        export_status(db)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
