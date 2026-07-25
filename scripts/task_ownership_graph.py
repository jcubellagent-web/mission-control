#!/usr/bin/env python3
"""Build a bounded, dashboard-safe task ownership and handoff graph.

The graph joins the canonical live work projection with the durable task and
handoff queues. Exact work/run identifiers are used only for reconciliation;
the serialized graph contains hashed node identifiers and privacy-filtered
labels. The builder never emits prompts, details, origin claims, queue notes,
artifact paths, or raw work identifiers.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from brain_atlas_contract import safe_work_label, work_label_is_safe


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
MAX_FLOWS = 8
AGENTS = ("joshex", "josh2", "jaimes", "jain")
AGENT_LABELS = {
    "joshex": "JOSHeX",
    "josh2": "JOSH 2.0",
    "jaimes": "JAIMES",
    "jain": "J.A.I.N",
}
ACTIVE_STATUSES = {"accepted", "planned", "routed", "active", "verifying", "running"}
DISPLAY_STATUSES = ACTIVE_STATUSES | {"queued", "blocked"}
TERMINAL_STATUSES = {"done", "blocked", "error", "cancelled", "canceled", "superseded"}
FINDING_ORDER = {
    "duplicate-active-owner": 0,
    "orphaned-work": 1,
    "stale-execution": 2,
    "missing-terminal-receipt": 3,
}
FINDING_SEVERITY = {
    "duplicate-active-owner": "critical",
    "orphaned-work": "high",
    "stale-execution": "high",
    "missing-terminal-receipt": "high",
}
FINDING_LABEL = {
    "duplicate-active-owner": "Duplicate active owner",
    "orphaned-work": "Active work has no live owner",
    "stale-execution": "Execution lease is stale",
    "missing-terminal-receipt": "Terminal handoff lacks a receipt",
}
HASHED_NODE = re.compile(r"^(?:work|handoff|receipt):[a-f0-9]{24}$")
HASHED_EDGE = re.compile(r"^edge:[a-f0-9]{24}$")
HASHED_FINDING = re.compile(r"^finding:[a-f0-9]{24}$")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def canonical_agent(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace(".", "")
    if text in {"josh", "josh2", "josh 20", "main"} or "josh 2" in text:
        return "josh2"
    if "jaimes" in text:
        return "jaimes"
    if text == "jain" or "jain" in text:
        return "jain"
    if "joshex" in text or text == "codex":
        return "joshex"
    return text if text in AGENTS else None


def stable_id(kind: str, *parts: Any) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    digest = hashlib.sha256(f"ownership-graph-v1\x1f{kind}\x1f{material}".encode()).hexdigest()[:24]
    return f"{kind}:{digest}"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def exact_key(row: dict[str, Any]) -> tuple[str, str, int] | None:
    work_id = row.get("workId")
    run_id = row.get("runId")
    try:
        generation = int(row.get("generation") or 1)
    except (TypeError, ValueError):
        return None
    if not isinstance(work_id, str) or not work_id or not isinstance(run_id, str) or not run_id or generation < 1:
        return None
    return work_id, run_id, generation


def row_time(row: dict[str, Any]) -> dt.datetime:
    for field in ("updatedAt", "completedAt", "time", "createdAt"):
        parsed = parse_time(row.get(field))
        if parsed is not None:
            return parsed
    return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def safe_label(row: dict[str, Any], owner: str | None) -> str:
    agent_label = AGENT_LABELS.get(owner or "", "Agent")
    return safe_work_label(row.get("title") or row.get("objective"), row.get("phase"), agent_label)


def finding_id(kind: str, work_node: str, *parts: Any) -> str:
    return stable_id("finding", kind, work_node, *parts)


def build_graph(
    tasks_payload: Any,
    handoffs_payload: Any,
    hot_payload: Any,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    current = (now or utc_now()).astimezone(dt.timezone.utc)
    generated_at = iso(current)
    tasks = tasks_payload.get("tasks", []) if isinstance(tasks_payload, dict) else []
    handoffs = handoffs_payload.get("handoffs", []) if isinstance(handoffs_payload, dict) else []
    active_works = hot_payload.get("activeWorks", []) if isinstance(hot_payload, dict) else []
    tasks = [row for row in tasks if isinstance(row, dict)]
    handoffs = [row for row in handoffs if isinstance(row, dict)]
    active_works = [row for row in active_works if isinstance(row, dict)]

    task_by_key = {key: row for row in tasks if (key := exact_key(row)) is not None}
    task_by_work_run = {(key[0], key[1]): row for key, row in task_by_key.items()}
    active_by_key: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for row in active_works:
        key = exact_key(row)
        if key is not None:
            active_by_key.setdefault(key, []).append(row)

    open_handoff_keys = {
        (key[0], key[1])
        for row in handoffs
        if str(row.get("status") or "").lower() in {"open", "accepted", "active", "pending", "in-progress"}
        if (key := exact_key(row)) is not None
    }

    findings: list[dict[str, Any]] = []
    finding_types_by_key: dict[tuple[str, str, int], set[str]] = {}

    def add_finding(kind: str, key: tuple[str, str, int], observed: dt.datetime, *identity: Any) -> None:
        work_node = stable_id("work", *key)
        finding_types_by_key.setdefault(key, set()).add(kind)
        findings.append({
            "id": finding_id(kind, work_node, *identity),
            "type": kind,
            "severity": FINDING_SEVERITY[kind],
            "label": FINDING_LABEL[kind],
            "workNode": work_node,
            "observedAt": iso(observed if observed.year > 1 else current),
        })

    for key, rows in active_by_key.items():
        controllers = [row for row in rows if str(row.get("executionRole") or "controller") != "worker"]
        owners = {canonical_agent(row.get("ownerAgent")) for row in controllers}
        owners.discard(None)
        if len(owners) > 1:
            add_finding("duplicate-active-owner", key, max((row_time(row) for row in controllers), default=current), *sorted(owners))
        for row in rows:
            lease = parse_time(row.get("leaseUntil"))
            if row.get("stale") is True or (lease is not None and lease <= current):
                add_finding("stale-execution", key, lease or row_time(row), canonical_agent(row.get("ownerAgent")))
                break

    for key, row in task_by_key.items():
        status = str(row.get("status") or "").lower()
        if status not in ACTIVE_STATUSES:
            continue
        hot_rows = active_by_key.get(key, [])
        live_controller = any(str(item.get("executionRole") or "controller") != "worker" for item in hot_rows)
        if not live_controller and (key[0], key[1]) not in open_handoff_keys:
            add_finding("orphaned-work", key, row_time(row))

    for row in handoffs:
        status = str(row.get("status") or "").lower()
        key = exact_key(row)
        if key is None or status not in TERMINAL_STATUSES:
            continue
        try:
            schema_version = int(row.get("handoffSchemaVersion") or 0)
        except (TypeError, ValueError):
            schema_version = 0
        if schema_version < 2:
            continue
        has_receipt = bool(row.get("terminalResultReceiptId") or row.get("terminalReceiptId"))
        if not has_receipt:
            add_finding("missing-terminal-receipt", key, row_time(row), row.get("id"))

    candidates: dict[tuple[str, str, int], dict[str, Any]] = {}
    for key, row in task_by_key.items():
        if str(row.get("status") or "").lower() in DISPLAY_STATUSES or key in finding_types_by_key:
            candidates[key] = row
    for key, rows in active_by_key.items():
        candidates.setdefault(key, task_by_key.get(key, rows[0]))
    for row in handoffs:
        key = exact_key(row)
        has_terminal_receipt = bool(row.get("terminalResultReceiptId") or row.get("terminalReceiptId"))
        if key is not None and (
            str(row.get("status") or "").lower() == "open"
            or key in finding_types_by_key
            or has_terminal_receipt
        ):
            candidates.setdefault(key, task_by_key.get(key, row))

    ordered_keys = sorted(
        candidates,
        key=lambda key: (
            0 if key in finding_types_by_key else 1,
            -row_time(candidates[key]).timestamp(),
            stable_id("work", *key),
        ),
    )[:MAX_FLOWS]

    nodes: dict[str, dict[str, Any]] = {
        f"agent:{agent}": {
            "id": f"agent:{agent}",
            "kind": "agent",
            "label": AGENT_LABELS[agent],
            "agent": agent,
            "observedAt": generated_at,
        }
        for agent in AGENTS
    }
    edges: list[dict[str, Any]] = []
    flows: list[dict[str, Any]] = []

    for key in ordered_keys:
        source = candidates[key]
        hot_rows = active_by_key.get(key, [])
        owner = next((canonical_agent(row.get("ownerAgent")) for row in hot_rows if canonical_agent(row.get("ownerAgent"))), None)
        owner = owner or canonical_agent(source.get("owner")) or "joshex"
        status = str(source.get("status") or (hot_rows[0].get("status") if hot_rows else "queued")).lower()
        observed = max([row_time(source), *(row_time(row) for row in hot_rows)])
        if observed.year <= 1:
            observed = current
        work_node = stable_id("work", *key)
        nodes[work_node] = {
            "id": work_node,
            "kind": "work",
            "label": safe_label(source, owner),
            "status": status,
            "observedAt": iso(observed),
            "stale": "stale-execution" in finding_types_by_key.get(key, set()),
        }
        edge_observed = iso(observed)
        edges.append({
            "id": stable_id("edge", "owns", owner, work_node),
            "kind": "owns",
            "source": f"agent:{owner}",
            "target": work_node,
            "observedAt": edge_observed,
        })

        matching_handoffs = [
            row for row in handoffs
            if (candidate_key := exact_key(row)) is not None and candidate_key == key
        ]
        latest_handoff = max(matching_handoffs, key=row_time, default=None)
        to_agent = canonical_agent(latest_handoff.get("to")) if latest_handoff else None
        handoff_status = str(latest_handoff.get("status") or "").lower() if latest_handoff else None
        has_receipt = bool(latest_handoff and (latest_handoff.get("terminalResultReceiptId") or latest_handoff.get("terminalReceiptId")))
        if latest_handoff:
            handoff_node = stable_id("handoff", latest_handoff.get("id"), *key)
            nodes[handoff_node] = {
                "id": handoff_node,
                "kind": "handoff",
                "label": "Handoff",
                "status": handoff_status or "open",
                "observedAt": iso(row_time(latest_handoff) if row_time(latest_handoff).year > 1 else observed),
            }
            edges.append({
                "id": stable_id("edge", "delegates", work_node, handoff_node),
                "kind": "delegates",
                "source": work_node,
                "target": handoff_node,
                "observedAt": nodes[handoff_node]["observedAt"],
            })
            if to_agent:
                edges.append({
                    "id": stable_id("edge", "receives", handoff_node, to_agent),
                    "kind": "receives",
                    "source": handoff_node,
                    "target": f"agent:{to_agent}",
                    "observedAt": nodes[handoff_node]["observedAt"],
                })
            if has_receipt:
                receipt_node = stable_id("receipt", latest_handoff.get("terminalResultReceiptId") or latest_handoff.get("terminalReceiptId"))
                nodes[receipt_node] = {
                    "id": receipt_node,
                    "kind": "receipt",
                    "label": "Terminal receipt",
                    "status": str(latest_handoff.get("terminalResultStatus") or handoff_status or "done").lower(),
                    "observedAt": nodes[handoff_node]["observedAt"],
                }
                edges.append({
                    "id": stable_id("edge", "terminates", handoff_node, receipt_node),
                    "kind": "terminates",
                    "source": handoff_node,
                    "target": receipt_node,
                    "observedAt": nodes[handoff_node]["observedAt"],
                })

        flows.append({
            "id": work_node,
            "label": nodes[work_node]["label"],
            "status": status,
            "ownerAgent": owner,
            "toAgent": to_agent,
            "handoffStatus": handoff_status,
            "terminalReceipt": has_receipt,
            "observedAt": edge_observed,
            "findingTypes": sorted(finding_types_by_key.get(key, set()), key=lambda item: FINDING_ORDER[item]),
        })

    findings.sort(key=lambda row: (FINDING_ORDER[row["type"]], row["workNode"], row["id"]))
    edges.sort(key=lambda row: (row["kind"], row["source"], row["target"]))
    node_rows = sorted(nodes.values(), key=lambda row: (row["kind"], row["id"]))
    finding_counts = {kind: sum(1 for row in findings if row["type"] == kind) for kind in FINDING_ORDER}
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "status": "attention" if findings else "ready",
        "source": {
            "liveWork": "control-tower-hot",
            "tasks": "agent-task-queue",
            "handoffs": "handoff-queue",
            "verified": isinstance(hot_payload, dict) and isinstance(tasks_payload, dict) and isinstance(handoffs_payload, dict),
        },
        "privacy": {
            "dashboardSafe": True,
            "rawIdentifiersIncluded": False,
            "promptsIncluded": False,
            "detailsIncluded": False,
            "privateContentIncluded": False,
        },
        "counts": {
            "nodes": len(node_rows),
            "edges": len(edges),
            "flows": len(flows),
            "findings": len(findings),
            "activeWork": sum(1 for row in flows if row["status"] in ACTIVE_STATUSES),
            "openHandoffs": sum(1 for row in flows if row["handoffStatus"] == "open"),
            "byFinding": finding_counts,
        },
        "findings": findings,
        "nodes": node_rows,
        "edges": edges,
        "flows": flows,
    }


def unavailable_graph(*, now: dt.datetime | None = None) -> dict[str, Any]:
    current = now or utc_now()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": iso(current),
        "status": "unavailable",
        "source": {"liveWork": "control-tower-hot", "tasks": "agent-task-queue", "handoffs": "handoff-queue", "verified": False},
        "privacy": {"dashboardSafe": True, "rawIdentifiersIncluded": False, "promptsIncluded": False, "detailsIncluded": False, "privateContentIncluded": False},
        "counts": {"nodes": 0, "edges": 0, "flows": 0, "findings": 0, "activeWork": 0, "openHandoffs": 0, "byFinding": {kind: 0 for kind in FINDING_ORDER}},
        "findings": [],
        "nodes": [],
        "edges": [],
        "flows": [],
    }


def sanitize_graph(value: Any, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """Revalidate the serialized graph at the dashboard presentation boundary."""
    fallback = unavailable_graph(now=now)
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion", "generatedAt", "status", "source", "privacy", "counts",
        "findings", "nodes", "edges", "flows",
    }:
        return fallback
    if value.get("schemaVersion") != 1 or value.get("status") not in {"ready", "attention", "unavailable"}:
        return fallback
    if parse_time(value.get("generatedAt")) is None:
        return fallback
    source = value.get("source")
    privacy = value.get("privacy")
    if source != {
        "liveWork": "control-tower-hot",
        "tasks": "agent-task-queue",
        "handoffs": "handoff-queue",
        "verified": value.get("status") != "unavailable",
    }:
        return fallback
    if privacy != {
        "dashboardSafe": True,
        "rawIdentifiersIncluded": False,
        "promptsIncluded": False,
        "detailsIncluded": False,
        "privateContentIncluded": False,
    }:
        return fallback
    nodes, edges, flows, findings = value.get("nodes"), value.get("edges"), value.get("flows"), value.get("findings")
    if not all(isinstance(rows, list) for rows in (nodes, edges, flows, findings)):
        return fallback
    if len(nodes) > 40 or len(edges) > 48 or len(flows) > MAX_FLOWS or len(findings) > 32:
        return fallback
    clean_nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for row in nodes:
        if not isinstance(row, dict) or not {"id", "kind", "label", "observedAt"}.issubset(row):
            return fallback
        if not set(row).issubset({"id", "kind", "label", "agent", "status", "observedAt", "stale"}):
            return fallback
        kind, identifier, label = row.get("kind"), row.get("id"), row.get("label")
        if kind == "agent":
            agent = row.get("agent")
            if agent not in AGENTS or identifier != f"agent:{agent}" or label != AGENT_LABELS[agent]:
                return fallback
        elif kind in {"work", "handoff", "receipt"}:
            if not isinstance(identifier, str) or not HASHED_NODE.fullmatch(identifier) or not identifier.startswith(f"{kind}:"):
                return fallback
            if kind == "work" and not work_label_is_safe(label):
                return fallback
            if kind == "handoff" and label != "Handoff":
                return fallback
            if kind == "receipt" and label != "Terminal receipt":
                return fallback
        else:
            return fallback
        if identifier in node_ids or parse_time(row.get("observedAt")) is None or type(row.get("stale", False)) is not bool:
            return fallback
        node_ids.add(identifier)
        clean_nodes.append(dict(row))
    clean_edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()
    for row in edges:
        if not isinstance(row, dict) or set(row) != {"id", "kind", "source", "target", "observedAt"}:
            return fallback
        if (
            not isinstance(row.get("id"), str)
            or not HASHED_EDGE.fullmatch(row["id"])
            or row["id"] in edge_ids
            or row.get("kind") not in {"owns", "delegates", "receives", "terminates"}
            or row.get("source") not in node_ids
            or row.get("target") not in node_ids
            or parse_time(row.get("observedAt")) is None
        ):
            return fallback
        edge_ids.add(row["id"])
        clean_edges.append(dict(row))
    clean_findings: list[dict[str, Any]] = []
    finding_ids: set[str] = set()
    for row in findings:
        if not isinstance(row, dict) or set(row) != {"id", "type", "severity", "label", "workNode", "observedAt"}:
            return fallback
        kind = row.get("type")
        if (
            kind not in FINDING_ORDER
            or row.get("severity") != FINDING_SEVERITY[kind]
            or row.get("label") != FINDING_LABEL[kind]
            or not isinstance(row.get("id"), str)
            or not HASHED_FINDING.fullmatch(row["id"])
            or row["id"] in finding_ids
            or row.get("workNode") not in node_ids
            or parse_time(row.get("observedAt")) is None
        ):
            return fallback
        finding_ids.add(row["id"])
        clean_findings.append(dict(row))
    clean_flows: list[dict[str, Any]] = []
    flow_ids: set[str] = set()
    for row in flows:
        if not isinstance(row, dict) or set(row) != {
            "id", "label", "status", "ownerAgent", "toAgent", "handoffStatus",
            "terminalReceipt", "observedAt", "findingTypes",
        }:
            return fallback
        types = row.get("findingTypes")
        if (
            row.get("id") not in node_ids
            or row["id"] in flow_ids
            or not work_label_is_safe(row.get("label"))
            or row.get("ownerAgent") not in AGENTS
            or row.get("toAgent") not in (*AGENTS, None)
            or not isinstance(row.get("status"), str)
            or len(row["status"]) > 24
            or row.get("handoffStatus") is not None and (not isinstance(row.get("handoffStatus"), str) or len(row["handoffStatus"]) > 24)
            or type(row.get("terminalReceipt")) is not bool
            or parse_time(row.get("observedAt")) is None
            or not isinstance(types, list)
            or any(kind not in FINDING_ORDER for kind in types)
        ):
            return fallback
        flow_ids.add(row["id"])
        clean_flows.append(dict(row))
    counts = value.get("counts")
    if not isinstance(counts, dict) or set(counts) != {"nodes", "edges", "flows", "findings", "activeWork", "openHandoffs", "byFinding"}:
        return fallback
    by_finding = counts.get("byFinding")
    expected_by_finding = {kind: sum(1 for row in clean_findings if row["type"] == kind) for kind in FINDING_ORDER}
    if (
        counts.get("nodes") != len(clean_nodes)
        or counts.get("edges") != len(clean_edges)
        or counts.get("flows") != len(clean_flows)
        or counts.get("findings") != len(clean_findings)
        or by_finding != expected_by_finding
        or type(counts.get("activeWork")) is not int
        or type(counts.get("openHandoffs")) is not int
    ):
        return fallback
    if value["status"] == "ready" and clean_findings or value["status"] == "attention" and not clean_findings:
        return fallback
    if value["status"] == "unavailable" and (clean_nodes or clean_edges or clean_flows or clean_findings):
        return fallback
    return {
        **value,
        "nodes": clean_nodes,
        "edges": clean_edges,
        "flows": clean_flows,
        "findings": clean_findings,
    }


def build_from_paths(*, root: Path = ROOT, now: dt.datetime | None = None) -> dict[str, Any]:
    data = root / "data"
    tasks = read_json(data / "agent-task-queue.json", None)
    handoffs = read_json(data / "handoff-queue.json", None)
    hot = read_json(data / "control-tower-hot.json", None)
    if not all(isinstance(value, dict) for value in (tasks, handoffs, hot)):
        return unavailable_graph(now=now)
    return build_graph(tasks, handoffs, hot, now=now)


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = build_from_paths(root=args.root)
    if args.output:
        atomic_write(args.output, payload)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0 if payload["status"] != "unavailable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
