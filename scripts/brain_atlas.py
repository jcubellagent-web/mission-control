#!/usr/bin/env python3
"""Build a bounded, dashboard-safe Brain Atlas from canonical work receipts.

The generator is read-only by default: it opens the Control Tower work ledger
with SQLite ``mode=ro`` and prints JSON. An output file is written only when
``--output`` is explicitly supplied. It reads only the objective and phase
needed to derive a privacy-filtered title; it never emits those raw fields or
reads details, prompts, memory content, account data, or raw origin claims.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from brain_atlas_contract import (
    model_route_node_is_safe,
    safe_model_route_candidate,
    safe_work_label,
    work_label_is_safe,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "control-tower-work.sqlite3"
SCHEMA_VERSION = 1
SUPPORTED_STORE_SCHEMA = 1
MAX_WINDOW_DAYS = 7
HARD_NODE_CAP = 100
MIN_VERIFIED_PROOF_PATHS = 3
SOURCE_NAME = "control-tower-work-ledger"

AGENT_LABELS = {
    "josh2": "JOSH 2.0",
    "jaimes": "JAIMES",
    "jain": "J.A.I.N",
    "joshex": "JOSHeX",
}
EVENT_KINDS = {"start", "update", "heartbeat", "terminal"}
STATUSES = {
    "accepted", "planned", "routed", "active", "verifying",
    "done", "blocked", "error", "cancelled",
}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
REQUIRED_EVENT_COLUMNS = {
    "event_id", "work_id", "run_id", "generation", "sequence", "kind",
    "status", "owner_agent", "objective", "phase", "origin_claim_hash",
    "model_family", "model_id", "route_verified", "occurred_at",
    "accepted_revision",
}
NODE_ORDER = {"agent": 0, "work": 1, "receipt": 2, "model": 3}
EDGE_ORDER = {"owns": 0, "emitted": 1, "verified-route": 2}
NODE_FIELDS = {
    "id", "kind", "label", "status", "observedAt", "receiptCount",
    "generation", "sequence", "routeVerified", "family", "modelId",
}
EDGE_FIELDS = {"id", "kind", "source", "target", "evidenceReceipt", "observedAt"}
FORBIDDEN_GRAPH_FIELDS = {
    "objective", "detail", "origin", "originClaimHash", "workId", "runId", "eventId",
    "prompt", "prompts", "memory", "memoryContent", "privateAccountData",
}


class AtlasSourceError(RuntimeError):
    """Raised when the canonical receipt source cannot be verified safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_time(value: Any) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_id(kind: str, *parts: Any) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    digest = hashlib.sha256(f"brain-atlas-v1\x1f{kind}\x1f{material}".encode("utf-8")).hexdigest()[:24]
    return f"{kind}:{digest}"


def edge_id(kind: str, source: str, target: str, evidence_receipt: str) -> str:
    return stable_id("edge", kind, source, target, evidence_receipt)


def read_only_connection(path: Path) -> sqlite3.Connection:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise AtlasSourceError("source-missing")
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection
    except sqlite3.Error as exc:
        raise AtlasSourceError("source-unavailable") from exc


def verify_source(connection: sqlite3.Connection) -> tuple[int, int]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not {"store_meta", "work_events"}.issubset(tables):
        raise AtlasSourceError("unsupported-source-schema")
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(work_events)")
    }
    if not REQUIRED_EVENT_COLUMNS.issubset(columns):
        raise AtlasSourceError("unsupported-source-schema")
    meta = connection.execute(
        "SELECT schema_version,revision FROM store_meta WHERE singleton=1"
    ).fetchone()
    if not meta or int(meta["schema_version"]) != SUPPORTED_STORE_SCHEMA:
        raise AtlasSourceError("unsupported-source-version")
    return int(meta["schema_version"]), int(meta["revision"])


def safe_model_route(row: sqlite3.Row) -> tuple[str, str] | None:
    if int(row["route_verified"] or 0) != 1:
        return None
    return safe_model_route_candidate(row["model_family"], row["model_id"])


def verified_event(row: sqlite3.Row, *, window_start: dt.datetime, window_end: dt.datetime, source_revision: int) -> bool:
    try:
        occurred_at = parse_time(row["occurred_at"])
        return bool(
            window_start <= occurred_at <= window_end
            and IDENTIFIER.fullmatch(str(row["event_id"] or ""))
            and IDENTIFIER.fullmatch(str(row["work_id"] or ""))
            and IDENTIFIER.fullmatch(str(row["run_id"] or ""))
            and int(row["generation"]) >= 1
            and int(row["sequence"]) >= 1
            and str(row["kind"]) in EVENT_KINDS
            and str(row["status"]) in STATUSES
            and str(row["owner_agent"]) in AGENT_LABELS
            and SHA256.fullmatch(str(row["origin_claim_hash"] or ""))
            and int(row["route_verified"]) in {0, 1}
            and 1 <= int(row["accepted_revision"]) <= source_revision
        )
    except (TypeError, ValueError, OverflowError):
        return False


def read_receipts(
    connection: sqlite3.Connection,
    *,
    window_start: dt.datetime,
    window_end: dt.datetime,
) -> tuple[list[sqlite3.Row], int, int]:
    start_text, end_text = iso(window_start), iso(window_end)
    stale_count = int(connection.execute(
        "SELECT COUNT(*) FROM work_events WHERE occurred_at < ? OR occurred_at > ?",
        (start_text, end_text),
    ).fetchone()[0])
    source_count = int(connection.execute(
        "SELECT COUNT(*) FROM work_events WHERE occurred_at >= ? AND occurred_at <= ?",
        (start_text, end_text),
    ).fetchone()[0])
    rows = connection.execute(
        """SELECT event_id,work_id,run_id,generation,sequence,kind,status,
                  owner_agent,objective,phase,origin_claim_hash,model_family,model_id,
                  route_verified,occurred_at,accepted_revision
           FROM work_events
           WHERE occurred_at >= ? AND occurred_at <= ?
           ORDER BY occurred_at DESC, accepted_revision DESC, event_id ASC
           LIMIT 10000""",
        (start_text, end_text),
    ).fetchall()
    return list(rows), stale_count, source_count


def add_edge(
    edges: dict[str, dict[str, Any]],
    *,
    kind: str,
    source: str,
    target: str,
    receipt: str,
    observed_at: str,
) -> None:
    identifier = edge_id(kind, source, target, receipt)
    edges.setdefault(identifier, {
        "id": identifier,
        "kind": kind,
        "source": source,
        "target": target,
        "evidenceReceipt": receipt,
        "observedAt": observed_at,
    })


def build_graph(
    rows: Iterable[sqlite3.Row],
    *,
    window_start: dt.datetime,
    window_end: dt.datetime,
    source_revision: int,
    max_nodes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    excluded = {
        "legacyOrInvalid": 0,
        "capacityReceipts": 0,
        "capacityRoutes": 0,
        "unverifiedRoutes": 0,
        "unsafeVerifiedRoutes": 0,
    }
    ordered_rows = list(rows)
    reserved = []
    for row in ordered_rows:
        if verified_event(
            row,
            window_start=window_start,
            window_end=window_end,
            source_revision=source_revision,
        ) and safe_model_route(row) is not None:
            reserved.append(row)
            if len(reserved) >= MIN_VERIFIED_PROOF_PATHS:
                break
    reserved_ids = {str(row["event_id"]) for row in reserved}
    ordered_rows = [
        *reserved,
        *(row for row in ordered_rows if str(row["event_id"]) not in reserved_ids),
    ]
    for row in ordered_rows:
        if not verified_event(
            row,
            window_start=window_start,
            window_end=window_end,
            source_revision=source_revision,
        ):
            excluded["legacyOrInvalid"] += 1
            continue

        owner = str(row["owner_agent"])
        observed_at = iso(parse_time(row["occurred_at"]))
        agent_node = f"agent:{owner}"
        #JAIMES: A displayed work node is one owned execution generation; handoffs
        # must remain separate exact paths instead of merging into one owner conflict.
        work_node = stable_id(
            "work", owner, row["work_id"], int(row["generation"])
        )
        receipt_node = stable_id("receipt", row["event_id"])
        required = {agent_node, work_node, receipt_node} - nodes.keys()
        if len(nodes) + len(required) > max_nodes:
            excluded["capacityReceipts"] += 1
            continue

        if agent_node not in nodes:
            nodes[agent_node] = {
                "id": agent_node,
                "kind": "agent",
                "label": AGENT_LABELS[owner],
                "observedAt": observed_at,
                "receiptCount": 0,
            }
        if work_node not in nodes:
            nodes[work_node] = {
                "id": work_node,
                "kind": "work",
                "label": safe_work_label(
                    row["objective"], row["phase"], AGENT_LABELS[owner]
                ),
                "status": str(row["status"]),
                "observedAt": observed_at,
                "receiptCount": 0,
                "generation": int(row["generation"]),
            }
        nodes[receipt_node] = {
            "id": receipt_node,
            "kind": "receipt",
            "label": f"{str(row['kind']).title()} receipt",
            "status": str(row["status"]),
            "observedAt": observed_at,
            "receiptCount": 1,
            "generation": int(row["generation"]),
            "sequence": int(row["sequence"]),
            "routeVerified": bool(row["route_verified"]),
        }
        nodes[agent_node]["receiptCount"] += 1
        nodes[work_node]["receiptCount"] += 1
        add_edge(
            edges,
            kind="owns",
            source=agent_node,
            target=work_node,
            receipt=receipt_node,
            observed_at=observed_at,
        )
        add_edge(
            edges,
            kind="emitted",
            source=work_node,
            target=receipt_node,
            receipt=receipt_node,
            observed_at=observed_at,
        )

        route = safe_model_route(row)
        if not bool(row["route_verified"]):
            excluded["unverifiedRoutes"] += 1
        elif route is None:
            excluded["unsafeVerifiedRoutes"] += 1
        else:
            family, model_id = route
            model_node = stable_id("model", family, model_id)
            if model_node not in nodes and len(nodes) >= max_nodes:
                excluded["capacityRoutes"] += 1
            else:
                if model_node not in nodes:
                    nodes[model_node] = {
                        "id": model_node,
                        "kind": "model",
                        "label": f"{family}/{model_id}",
                        "observedAt": observed_at,
                        "receiptCount": 0,
                        "family": family,
                        "modelId": model_id,
                    }
                nodes[model_node]["receiptCount"] += 1
                add_edge(
                    edges,
                    kind="verified-route",
                    source=receipt_node,
                    target=model_node,
                    receipt=receipt_node,
                    observed_at=observed_at,
                )

    ordered_nodes = sorted(nodes.values(), key=lambda row: (NODE_ORDER[row["kind"]], row["id"]))
    ordered_edges = sorted(edges.values(), key=lambda row: (EDGE_ORDER[row["kind"]], row["id"]))
    return ordered_nodes, ordered_edges, excluded


def empty_reason(source_rows: int, excluded: dict[str, int], max_nodes: int) -> str:
    if source_rows == 0:
        return "no-receipts-in-window"
    if excluded["legacyOrInvalid"] >= source_rows:
        return "no-verified-receipts-in-window"
    if max_nodes < 3 or excluded["capacityReceipts"]:
        return "node-cap-excluded-all-receipts"
    return "no-verified-receipts-in-window"


def atlas_payload(
    *,
    generated_at: dt.datetime,
    window_start: dt.datetime,
    window_days: int,
    max_nodes: int,
    status: str,
    source_verified: bool,
    source_schema_version: int | None,
    source_revision: int | None,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    source_rows: int,
    stale_count: int,
    excluded: dict[str, int],
    reason: str | None,
) -> dict[str, Any]:
    kind_counts = {
        kind: sum(1 for node in nodes if node["kind"] == kind)
        for kind in NODE_ORDER
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": iso(generated_at),
        "status": status,
        "empty": not nodes,
        "emptyReason": reason,
        "source": {
            "name": SOURCE_NAME,
            "verified": source_verified,
            "schemaVersion": source_schema_version,
            "revision": source_revision,
        },
        "window": {
            "days": window_days,
            "start": iso(window_start),
            "end": iso(generated_at),
        },
        "limits": {"maxNodes": max_nodes, "hardMaxNodes": HARD_NODE_CAP},
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "agents": kind_counts["agent"],
            "works": kind_counts["work"],
            "receipts": kind_counts["receipt"],
            "models": kind_counts["model"],
            "sourceRowsInWindow": source_rows,
            "excluded": {"timeOutOfWindow": stale_count, **excluded},
        },
        "policy": {
            "identifiers": "deterministic-sha256-prefix; canonical agent ids only",
            "edges": "exact accepted work/event keys only; no inferred or fuzzy relationships",
            "content": "privacy-filtered work title and operational receipt metadata only",
            "excludedFields": [
                "objective", "detail", "origin", "originClaimHash", "workId", "runId", "eventId",
                "prompts", "memoryContent", "privateAccountData",
            ],
        },
        "nodes": nodes,
        "edges": edges,
    }


def validate_atlas(payload: dict[str, Any]) -> list[str]:
    """Return dashboard-safe invariant failures without exposing graph content."""

    problems: list[str] = []
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    edges = payload.get("edges") if isinstance(payload.get("edges"), list) else []
    limits = payload.get("limits") if isinstance(payload.get("limits"), dict) else {}
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    window = payload.get("window") if isinstance(payload.get("window"), dict) else {}
    status = str(payload.get("status") or "")
    empty = payload.get("empty") is True
    max_nodes = int(limits.get("maxNodes") or 0)
    if not 1 <= max_nodes <= HARD_NODE_CAP or len(nodes) > max_nodes:
        problems.append("node-cap")
    if len(edges) > 300:
        problems.append("edge-cap")
    if counts.get("nodes") != len(nodes) or counts.get("edges") != len(edges):
        problems.append("count-mismatch")
    if int(window.get("days") or 0) not in range(1, MAX_WINDOW_DAYS + 1):
        problems.append("window")
    if status == "ready" and (empty or not nodes or source.get("verified") is not True):
        problems.append("ready-state")
    elif status == "empty" and (not empty or nodes or edges or source.get("verified") is not True):
        problems.append("empty-state")
    elif status == "unavailable" and (not empty or nodes or edges or source.get("verified") is not False):
        problems.append("unavailable-state")
    elif status not in {"ready", "empty", "unavailable"}:
        problems.append("status")

    node_ids: dict[str, str] = {}
    node_rows: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            problems.append("node-shape")
            continue
        if set(node) - NODE_FIELDS or set(node) & FORBIDDEN_GRAPH_FIELDS:
            problems.append("node-fields")
        identifier = str(node.get("id") or "")
        kind = str(node.get("kind") or "")
        if kind == "work" and not work_label_is_safe(node.get("label")):
            problems.append("work-label")
        if kind == "model" and not model_route_node_is_safe(
            node.get("family"), node.get("modelId"), node.get("label")
        ):
            problems.append("model-route")
        if identifier in node_ids or kind not in NODE_ORDER:
            problems.append("node-identity")
        node_ids[identifier] = kind
        node_rows[identifier] = node
    edge_ids: set[str] = set()
    semantic_edges: set[tuple[str, str, str, str]] = set()
    emitted_by_receipt: dict[str, list[tuple[str, str]]] = {}
    emitted_by_path: dict[tuple[str, str], int] = {}
    owns_by_path: dict[tuple[str, str], int] = {}
    owner_by_work: dict[str, str] = {}
    routes_by_receipt: dict[str, int] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            problems.append("edge-shape")
            continue
        if set(edge) - EDGE_FIELDS or set(edge) & FORBIDDEN_GRAPH_FIELDS:
            problems.append("edge-fields")
        identifier = str(edge.get("id") or "")
        kind = str(edge.get("kind") or "")
        source_id = str(edge.get("source") or "")
        target_id = str(edge.get("target") or "")
        receipt_id = str(edge.get("evidenceReceipt") or "")
        if identifier in edge_ids or kind not in EDGE_ORDER:
            problems.append("edge-identity")
        edge_ids.add(identifier)
        semantic_key = (kind, source_id, target_id, receipt_id)
        if semantic_key in semantic_edges:
            problems.append("duplicate-edge")
        semantic_edges.add(semantic_key)
        if kind in EDGE_ORDER and identifier != edge_id(
            kind, source_id, target_id, receipt_id
        ):
            problems.append("edge-id")
        if source_id not in node_ids or target_id not in node_ids or node_ids.get(receipt_id) != "receipt":
            problems.append("dangling-edge")
            continue
        if kind not in EDGE_ORDER:
            continue
        expected = {
            "owns": ("agent", "work"),
            "emitted": ("work", "receipt"),
            "verified-route": ("receipt", "model"),
        }[kind]
        if (node_ids[source_id], node_ids[target_id]) != expected:
            problems.append("edge-type")
            continue
        if kind == "owns":
            prior_owner = owner_by_work.setdefault(target_id, source_id)
            if prior_owner != source_id:
                problems.append("ambiguous-owner")
            owns_by_path[(target_id, receipt_id)] = (
                owns_by_path.get((target_id, receipt_id), 0) + 1
            )
        elif kind == "emitted":
            if target_id != receipt_id:
                problems.append("ambiguous-path")
            emitted_by_receipt.setdefault(receipt_id, []).append((source_id, receipt_id))
            emitted_by_path[(source_id, receipt_id)] = (
                emitted_by_path.get((source_id, receipt_id), 0) + 1
            )
        else:
            if source_id != receipt_id:
                problems.append("route-proof")
            if node_rows.get(source_id, {}).get("routeVerified") is not True:
                problems.append("route-proof")
            routes_by_receipt[source_id] = routes_by_receipt.get(source_id, 0) + 1

    receipt_ids = {
        identifier for identifier, kind in node_ids.items() if kind == "receipt"
    }
    for receipt_id in receipt_ids:
        paths = emitted_by_receipt.get(receipt_id, [])
        if len(paths) != 1 or routes_by_receipt.get(receipt_id, 0) > 1:
            problems.append("ambiguous-path")
    for path, emitted_count in emitted_by_path.items():
        if emitted_count != 1 or owns_by_path.get(path, 0) != 1:
            problems.append("ambiguous-path")
    for path, owns_count in owns_by_path.items():
        if owns_count != 1 or emitted_by_path.get(path, 0) != 1:
            problems.append("ambiguous-path")
    return sorted(set(problems))


def generate_atlas(
    db_path: Path,
    *,
    as_of: dt.datetime | None = None,
    days: int = MAX_WINDOW_DAYS,
    max_nodes: int = HARD_NODE_CAP,
) -> dict[str, Any]:
    if not 1 <= int(days) <= MAX_WINDOW_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_WINDOW_DAYS}")
    if not 1 <= int(max_nodes) <= HARD_NODE_CAP:
        raise ValueError(f"max_nodes must be between 1 and {HARD_NODE_CAP}")
    generated_at = (as_of or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0)
    window_start = generated_at - dt.timedelta(days=int(days))
    with closing(read_only_connection(Path(db_path))) as connection:
        source_schema, source_revision = verify_source(connection)
        rows, stale_count, source_count = read_receipts(
            connection,
            window_start=window_start,
            window_end=generated_at,
        )
        nodes, edges, excluded = build_graph(
            rows,
            window_start=window_start,
            window_end=generated_at,
            source_revision=source_revision,
            max_nodes=int(max_nodes),
        )
    reason = None if nodes else empty_reason(source_count, excluded, int(max_nodes))
    payload = atlas_payload(
        generated_at=generated_at,
        window_start=window_start,
        window_days=int(days),
        max_nodes=int(max_nodes),
        status="ready" if nodes else "empty",
        source_verified=True,
        source_schema_version=source_schema,
        source_revision=source_revision,
        nodes=nodes,
        edges=edges,
        source_rows=source_count,
        stale_count=stale_count,
        excluded=excluded,
        reason=reason,
    )
    if validate_atlas(payload):
        raise AtlasSourceError("generated-payload-invalid")
    return payload


def unavailable_atlas(
    *,
    as_of: dt.datetime,
    days: int,
    max_nodes: int,
    reason: str,
) -> dict[str, Any]:
    excluded = {
        "legacyOrInvalid": 0,
        "capacityReceipts": 0,
        "capacityRoutes": 0,
        "unverifiedRoutes": 0,
        "unsafeVerifiedRoutes": 0,
    }
    return atlas_payload(
        generated_at=as_of,
        window_start=as_of - dt.timedelta(days=days),
        window_days=days,
        max_nodes=max_nodes,
        status="unavailable",
        source_verified=False,
        source_schema_version=None,
        source_revision=None,
        nodes=[],
        edges=[],
        source_rows=0,
        stale_count=0,
        excluded=excluded,
        reason=reason,
    )


def generate_safely(
    db_path: Path,
    *,
    as_of: dt.datetime | None = None,
    days: int = MAX_WINDOW_DAYS,
    max_nodes: int = HARD_NODE_CAP,
) -> dict[str, Any]:
    generated_at = (as_of or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0)
    try:
        return generate_atlas(db_path, as_of=generated_at, days=days, max_nodes=max_nodes)
    except AtlasSourceError as exc:
        reason = exc.code
    except (sqlite3.Error, OSError):
        reason = "source-unavailable"
    return unavailable_atlas(
        as_of=generated_at,
        days=int(days),
        max_nodes=int(max_nodes),
        reason=reason,
    )


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--as-of", default="")
    parser.add_argument("--days", type=int, default=MAX_WINDOW_DAYS)
    parser.add_argument("--max-nodes", type=int, default=HARD_NODE_CAP)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.days <= MAX_WINDOW_DAYS:
        parser.error(f"--days must be between 1 and {MAX_WINDOW_DAYS}")
    if not 1 <= args.max_nodes <= HARD_NODE_CAP:
        parser.error(f"--max-nodes must be between 1 and {HARD_NODE_CAP}")
    try:
        as_of = parse_time(args.as_of) if args.as_of else dt.datetime.now(dt.timezone.utc)
    except ValueError:
        parser.error("--as-of must be an ISO-8601 timestamp")
    payload = generate_safely(
        args.db,
        as_of=as_of,
        days=args.days,
        max_nodes=args.max_nodes,
    )
    problems = validate_atlas(payload)
    if args.output and not args.validate_only:
        atomic_write(args.output, payload)
    if args.validate_only:
        print(json.dumps({
            "ok": not problems and payload["status"] in {"ready", "empty"},
            "status": payload["status"],
            "sourceVerified": payload["source"]["verified"],
            "windowDays": payload["window"]["days"],
            "nodes": payload["counts"]["nodes"],
            "edges": payload["counts"]["edges"],
            "problems": problems,
            "emptyReason": payload["emptyReason"],
        }, indent=2, ensure_ascii=True))
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0 if not problems and payload["status"] in {"ready", "empty"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
