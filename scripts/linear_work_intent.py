#!/usr/bin/env python3
"""Build a sanitized, idempotent outbox for durable Linear work.

This module deliberately does not call Linear directly. Connected Codex lanes
consume the intent with their Linear tool, then acknowledge the resulting issue
ID. Runtime execution therefore remains fail-open without exporting OAuth
credentials to headless processes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("CONTROL_TOWER_DATA_DIR", ROOT / "data"))
CONFIG_PATH = ROOT / "config" / "linear-integration.json"
TASKS_PATH = DATA_DIR / "agent-task-queue.json"

AGENT_ALIASES = {
    "josh": "josh2",
    "josh2": "josh2",
    "josh2.0": "josh2",
    "josh 2.0": "josh2",
    "j.a.i.n": "jain",
}
TASK_STATUS_TO_BOUNDARY = {
    "queued": "planned",
    "accepted": "accepted",
    "active": "active",
    "blocked": "blocked",
    "error": "blocked",
    "verifying": "verifying",
    "done": "done",
    "cancelled": "cancelled",
}
PRIORITY_VALUES = {"urgent": 1, "high": 2, "normal": 3, "low": 4}
SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:access[_ -]?token|refresh[_ -]?token|password|cookie|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:api[_ -]?key|token|secret|credentials?)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~-]{12,}"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compact(value: Any, limit: int = 600) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(json.dumps(value, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp_path, mode)
    os.replace(temp_path, path)
    directory_fd = os.open(path.parent, getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def read_outbox(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updatedAt": None, "intents": []}
    try:
        value = json.loads(path.read_text())
    except Exception as exc:
        raise SystemExit(f"Linear intent outbox is corrupt: {path}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("intents"), list):
        raise SystemExit(f"Linear intent outbox has an invalid shape: {path}")
    return value


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = read_json(path, {})
    if not config.get("enabled") or not config.get("intentBridge", {}).get("enabled"):
        raise SystemExit("Linear durable-work integration is disabled.")
    return config


def intent_path(config: dict[str, Any] | None = None) -> Path:
    override = os.environ.get("CONTROL_TOWER_LINEAR_INTENTS_PATH")
    if override:
        return Path(override)
    current = config or load_config()
    configured = Path(str(current.get("intentBridge", {}).get("path") or "data/linear-work-intents.json"))
    return configured if configured.is_absolute() else ROOT / configured


def is_canonical_runtime(config: dict[str, Any] | None = None) -> bool:
    current = config or load_config()
    canonical_root = Path(str(current.get("intentBridge", {}).get("canonicalRoot") or ROOT))
    try:
        return ROOT.resolve() == canonical_root.resolve()
    except Exception:
        return False


def canonical_ssh_target(config: dict[str, Any]) -> str:
    """Return the cross-host SSH target without depending on per-user aliases."""
    bridge = config.get("intentBridge", {})
    target = str(
        bridge.get("canonicalSshTarget")
        or bridge.get("canonicalHost")
        or "josh2"
    ).strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+(?:@[A-Za-z0-9._:-]+)?", target):
        raise SystemExit("Canonical Linear SSH target is invalid.")
    return target


def canonical_agent(value: Any) -> str:
    raw = " ".join(str(value or "").strip().lower().replace("_", " ").split())
    return AGENT_ALIASES.get(raw, raw.replace(" ", ""))


def assert_dashboard_safe(*values: Any) -> None:
    for value in values:
        text = str(value or "")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            raise SystemExit("Durable Linear metadata contains private or credential-like content.")


def linear_metadata(
    *,
    area: str,
    acceptance_criteria: list[str],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = config or load_config()
    allowed_areas = set(current.get("labels", {}).get("area", []))
    if area not in allowed_areas:
        raise SystemExit(f"Unknown Linear area '{area}'. Use one of: {', '.join(sorted(allowed_areas))}.")
    criteria = [compact(item, 300) for item in acceptance_criteria if compact(item, 300)]
    if not criteria:
        raise SystemExit("Durable tasks require at least one --acceptance-criterion.")
    assert_dashboard_safe(area, *criteria)
    return {
        "durable": True,
        "area": area,
        "acceptanceCriteria": criteria,
        "issueId": None,
        "syncState": "pending",
        "lastBoundary": None,
        "lastIntentId": None,
        "lastError": None,
        "revision": 1,
    }


def _intent_identity(task: dict[str, Any], boundary: str, owner: str, area: str) -> str:
    source = "|".join(
        [
            str(task.get("workId") or task.get("id") or ""),
            str(task.get("generation") or 1),
            str((task.get("linear") or {}).get("revision") or 1),
            boundary,
            owner,
            area,
        ]
    )
    return "linear-intent-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]


def build_intent(task: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    current = config or load_config()
    metadata = task.get("linear") if isinstance(task.get("linear"), dict) else {}
    if not metadata.get("durable"):
        raise SystemExit("Task is not opted into durable Linear tracking.")
    if task.get("privacy") != "dashboard-safe":
        raise SystemExit("Durable Linear tasks must use --privacy dashboard-safe.")

    owner = canonical_agent(task.get("owner"))
    agent_labels = current.get("labels", {}).get("agent", {})
    if owner not in agent_labels:
        raise SystemExit(f"No Linear agent label configured for '{owner}'.")
    area = str(metadata.get("area") or "")
    allowed_areas = set(current.get("labels", {}).get("area", []))
    if area not in allowed_areas:
        raise SystemExit(f"No valid Linear area configured for task {task.get('id')}.")
    criteria = [compact(item, 300) for item in metadata.get("acceptanceCriteria", []) if compact(item, 300)]
    if not criteria:
        raise SystemExit("Durable task has no acceptance criteria.")

    status = str(task.get("status") or "queued").lower()
    boundary = TASK_STATUS_TO_BOUNDARY.get(status)
    if not boundary:
        raise SystemExit(f"No Linear boundary mapping for task status '{status}'.")
    linear_state = current.get("statusMapping", {}).get(boundary)
    if not linear_state:
        raise SystemExit(f"No Linear state mapping for boundary '{boundary}'.")

    title = compact(task.get("title"), 160)
    objective = compact(task.get("objective"), 600)
    work_id = str(task.get("workId") or task.get("id") or "")
    task_id = str(task.get("id") or "")
    approval = compact(task.get("approval") or "none", 40)
    assert_dashboard_safe(title, objective, work_id, task_id, approval, *criteria)

    delegated = current.get("connector", {}).get("delegatedAgents", {})
    route_to = delegated.get(owner, owner)
    intent = {
        "id": _intent_identity(task, boundary, owner, area),
        "operation": "upsert",
        "syncState": "pending",
        "workId": work_id,
        "taskId": task_id,
        "generation": int(task.get("generation") or 1),
        "revision": int(metadata.get("revision") or 1),
        "issueId": metadata.get("issueId"),
        "searchKeys": [work_id, task_id, title],
        "title": title,
        "objective": objective,
        "acceptanceCriteria": criteria,
        "approvalState": approval,
        "owner": owner,
        "routeTo": route_to,
        "agentLabel": agent_labels[owner],
        "areaLabel": area,
        "labels": [agent_labels[owner], area],
        "team": current.get("workspace", {}).get("teamName"),
        "project": current.get("workspace", {}).get("projectName"),
        "priority": PRIORITY_VALUES.get(str(task.get("priority") or "normal"), 3),
        "boundary": boundary,
        "state": linear_state,
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "attempts": 0,
        "lastError": None,
    }
    intent["payloadHash"] = _payload_hash(intent)
    return intent


def _payload_hash(intent: dict[str, Any]) -> str:
    dynamic = {
        "payloadHash", "createdAt", "updatedAt", "attempts", "lastError", "syncState",
        "claimToken", "claimOwner", "claimExpiresAt", "claimedAt", "syncedAt",
        "supersededAt", "issueVerificationHash",
    }
    payload = {key: value for key, value in intent.items() if key not in dynamic}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _position(intent: dict[str, Any]) -> tuple[int, int]:
    return int(intent.get("generation") or 0), int(intent.get("revision") or 0)


def _latest_authoritative(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    authoritative = [row for row in rows if not str(row.get("syncState") or "").startswith("rejected_")]
    return max(authoritative, key=_position) if authoritative else None


def _prune_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unresolved_states = {"pending", "failed", "claimed"}
    unresolved = [row for row in rows if row.get("syncState") in unresolved_states]
    terminal = [row for row in rows if row.get("syncState") not in unresolved_states]
    terminal.sort(key=lambda row: str(row.get("updatedAt") or row.get("createdAt") or ""), reverse=True)
    kept_ids = {row.get("id") for row in unresolved}
    return unresolved + [row for row in terminal if row.get("id") not in kept_ids][:500]


def validate_ingested_intent(intent: dict[str, Any], *, config: dict[str, Any]) -> None:
    owner = canonical_agent(intent.get("owner"))
    area = str(intent.get("areaLabel") or "")
    agent_labels = config.get("labels", {}).get("agent", {})
    delegated = config.get("connector", {}).get("delegatedAgents", {})
    expected_route = delegated.get(owner, owner)
    expected_id = _intent_identity(
        {
            "workId": intent.get("workId"),
            "generation": intent.get("generation"),
            "linear": {"revision": intent.get("revision")},
        },
        str(intent.get("boundary") or ""),
        owner,
        area,
    )
    expected_state = config.get("statusMapping", {}).get(str(intent.get("boundary") or ""))
    if intent.get("operation") != "upsert" or intent.get("id") != expected_id:
        raise SystemExit("Linear intent identity is invalid.")
    if _position(intent) < (1, 1):
        raise SystemExit("Linear intent generation and revision must be positive.")
    if intent.get("team") != config.get("workspace", {}).get("teamName"):
        raise SystemExit("Linear intent team does not match canonical configuration.")
    if intent.get("project") != config.get("workspace", {}).get("projectName"):
        raise SystemExit("Linear intent project does not match canonical configuration.")
    if intent.get("agentLabel") != agent_labels.get(owner) or intent.get("labels") != [agent_labels.get(owner), area]:
        raise SystemExit("Linear intent Agent/Area labels are invalid.")
    if area not in set(config.get("labels", {}).get("area", [])):
        raise SystemExit("Linear intent Area label is invalid.")
    if intent.get("routeTo") != expected_route or intent.get("state") != expected_state:
        raise SystemExit("Linear intent route or lifecycle state is invalid.")
    assert_dashboard_safe(
        intent.get("title"), intent.get("objective"), intent.get("workId"), intent.get("taskId"),
        *(intent.get("acceptanceCriteria") or []),
    )
    if intent.get("payloadHash") != _payload_hash(intent):
        raise SystemExit("Linear intent payload hash is invalid.")


def _enqueue_intent_local(
    intent: dict[str, Any],
    *,
    path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    validate_ingested_intent(intent, config=config)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        outbox = read_outbox(path)
        rows = [row for row in outbox.get("intents", []) if isinstance(row, dict)]
        same_work = [row for row in rows if row.get("workId") == intent.get("workId")]
        latest = _latest_authoritative(same_work)
        existing = next((row for row in rows if row.get("id") == intent.get("id")), None)

        if latest and _position(intent) < _position(latest):
            intent["syncState"] = "rejected_stale"
            intent["lastError"] = "older_than_canonical"
            rows.insert(0, intent)
        elif latest and _position(intent) == _position(latest):
            if existing and existing.get("payloadHash") == intent.get("payloadHash"):
                intent = existing
            else:
                intent["syncState"] = "rejected_conflict"
                intent["lastError"] = "same_revision_conflict"
                rows.insert(0, intent)
        else:
            inherited_issue = next((row.get("issueId") for row in same_work if row.get("issueId")), None)
            if inherited_issue and not intent.get("issueId"):
                intent["issueId"] = inherited_issue
                intent["payloadHash"] = _payload_hash(intent)
            for row in same_work:
                if row.get("syncState") in {"pending", "failed", "claimed"}:
                    row["syncState"] = "superseded"
                    row["supersededAt"] = utc_now()
                    row.pop("claimToken", None)
            rows.insert(0, intent)

        outbox["version"] = 1
        outbox["updatedAt"] = utc_now()
        outbox["intents"] = _prune_rows(rows)
        write_json(path, outbox)
        fcntl.flock(lock, fcntl.LOCK_UN)
    return intent


def _submit_to_canonical(intent: dict[str, Any], *, config: dict[str, Any]) -> dict[str, Any]:
    bridge = config.get("intentBridge", {})
    host = canonical_ssh_target(config)
    root = str(bridge.get("canonicalRoot") or "")
    python = str(bridge.get("canonicalPython") or "python3")
    if not root:
        raise SystemExit("Canonical Linear intent root is not configured.")
    remote = f"cd {shlex.quote(root)} && {shlex.quote(python)} scripts/linear_work_intent.py ingest --stdin"
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, remote],
        input=json.dumps(intent),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise SystemExit(compact(result.stderr or result.stdout or "Canonical Linear intent ingest failed", 300))
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise SystemExit("Canonical Linear intent ingest returned invalid JSON.") from exc
    if not payload.get("ok") or not isinstance(payload.get("intent"), dict):
        raise SystemExit("Canonical Linear intent ingest was not acknowledged.")
    return payload["intent"]


def _local_replay_candidates(path: Path, *, limit: int) -> list[dict[str, Any]]:
    """Snapshot only the newest unresolved canonical-delivery failure per work ID."""
    if not path.exists() or limit <= 0:
        return []
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = [row for row in read_outbox(path).get("intents", []) if isinstance(row, dict)]
        latest_by_work: dict[str, dict[str, Any]] = {}
        for row in rows:
            work_id = str(row.get("workId") or "")
            if not work_id or str(row.get("syncState") or "").startswith("rejected_"):
                continue
            current = latest_by_work.get(work_id)
            if current is None or _position(row) > _position(current):
                latest_by_work[work_id] = row
        candidates = [
            dict(row)
            for row in latest_by_work.values()
            if row.get("syncState") == "failed" and row.get("lastError") == "canonical_unavailable"
        ]
        candidates.sort(key=lambda row: str(row.get("updatedAt") or row.get("createdAt") or ""))
        fcntl.flock(lock, fcntl.LOCK_UN)
    return candidates[:limit]


def _record_local_replay(
    path: Path,
    *,
    intent_id: str,
    canonical_result: dict[str, Any] | None,
) -> None:
    """Record a replay result without overwriting a newer local lifecycle boundary."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        outbox = read_outbox(path)
        rows = [row for row in outbox.get("intents", []) if isinstance(row, dict)]
        row = next((item for item in rows if item.get("id") == intent_id), None)
        if row and row.get("syncState") == "failed" and row.get("lastError") == "canonical_unavailable":
            row["attempts"] = int(row.get("attempts") or 0) + 1
            row["updatedAt"] = utc_now()
            if canonical_result is not None:
                canonical_state = str(canonical_result.get("syncState") or "pending")
                row["syncState"] = (
                    canonical_state if canonical_state.startswith("rejected_") else "forwarded"
                )
                row["canonicalSyncState"] = canonical_state
                row["lastError"] = canonical_result.get("lastError")
                row["forwardedAt"] = utc_now()
                if canonical_result.get("issueId"):
                    row["issueId"] = canonical_result.get("issueId")
            outbox["updatedAt"] = utc_now()
            outbox["intents"] = _prune_rows(rows)
            write_json(path, outbox)
        fcntl.flock(lock, fcntl.LOCK_UN)


def flush_local_intents(
    *,
    path: Path | None = None,
    config: dict[str, Any] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Replay fail-open rows from a noncanonical host to the Josh2 outbox."""
    current = config or load_config()
    if is_canonical_runtime(current):
        return {"ok": True, "attempted": 0, "forwarded": 0, "failed": 0, "skipped": "canonical_runtime"}
    resolved_path = path or intent_path(current)
    candidates = _local_replay_candidates(resolved_path, limit=max(0, limit))
    forwarded = 0
    failed = 0
    for candidate in candidates:
        outgoing = dict(candidate)
        outgoing["syncState"] = "pending"
        outgoing["lastError"] = None
        outgoing.pop("canonicalSyncState", None)
        outgoing.pop("forwardedAt", None)
        try:
            result = _submit_to_canonical(outgoing, config=current)
        except (OSError, subprocess.SubprocessError, SystemExit):
            failed += 1
            _record_local_replay(
                resolved_path,
                intent_id=str(candidate.get("id") or ""),
                canonical_result=None,
            )
            continue
        forwarded += 1
        _record_local_replay(
            resolved_path,
            intent_id=str(candidate.get("id") or ""),
            canonical_result=result,
        )
    return {
        "ok": failed == 0,
        "attempted": len(candidates),
        "forwarded": forwarded,
        "failed": failed,
    }


def enqueue_task_intent(
    task: dict[str, Any],
    *,
    path: Path | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    metadata = task.get("linear") if isinstance(task.get("linear"), dict) else {}
    if not metadata.get("durable"):
        return None
    current = config or load_config()
    intent = build_intent(task, config=current)
    resolved_path = path or intent_path(current)
    local_only = os.environ.get("CONTROL_TOWER_LINEAR_LOCAL_ONLY") == "1"
    if path is None and not is_canonical_runtime(current) and not local_only:
        if resolved_path.exists():
            flush_local_intents(path=resolved_path, config=current, limit=20)
        try:
            intent = _submit_to_canonical(intent, config=current)
        except (OSError, subprocess.SubprocessError, SystemExit):
            intent["syncState"] = "failed"
            intent["lastError"] = "canonical_unavailable"
            intent = _enqueue_intent_local(intent, path=resolved_path, config=current)
    else:
        intent = _enqueue_intent_local(intent, path=resolved_path, config=current)

    metadata["syncState"] = intent.get("syncState")
    metadata["lastBoundary"] = intent.get("boundary")
    metadata["lastIntentId"] = intent.get("id")
    metadata["lastError"] = intent.get("lastError")
    metadata["routeTo"] = intent.get("routeTo")
    if intent.get("issueId"):
        metadata["issueId"] = intent.get("issueId")
    task["linear"] = metadata
    return intent


def _update_task_after_result(
    *,
    task_id: str,
    intent_id: str,
    issue_id: str | None,
    sync_state: str,
    error_code: str | None,
    tasks_path: Path = TASKS_PATH,
) -> None:
    if not tasks_path.exists():
        return
    connector_to_publish: dict[str, Any] | None = None
    lock_path = tasks_path.with_suffix(".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(tasks_path.read_text())
        except Exception as exc:
            raise SystemExit(f"Agent task queue is corrupt: {tasks_path}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            raise SystemExit(f"Agent task queue has an invalid shape: {tasks_path}")
        for task in data.get("tasks", []):
            if task.get("id") == task_id:
                metadata = task.get("linear") if isinstance(task.get("linear"), dict) else {}
                if issue_id:
                    metadata["issueId"] = issue_id
                if metadata.get("lastIntentId") == intent_id:
                    metadata["syncState"] = sync_state
                    metadata["lastError"] = error_code
                task["linear"] = metadata
                task["updatedAt"] = utc_now()
            connector = task.get("linearConnector") if isinstance(task.get("linearConnector"), dict) else {}
            if connector.get("sourceTaskId") == task_id and connector.get("latestIntentId") == intent_id:
                connector_status = "done" if sync_state == "synced" else "blocked"
                summary = (
                    f"Linear issue {issue_id} synchronized."
                    if issue_id
                    else f"Linear synchronization blocked: {error_code}."
                )
                already_terminal = task.get("status") == connector_status and task.get("summary") == summary
                task["status"] = connector_status
                task["completedAt"] = utc_now()
                task["updatedAt"] = utc_now()
                task["summary"] = summary
                if not already_terminal:
                    notes = task.setdefault("notes", [])
                    notes.insert(0, {
                        "time": utc_now(),
                        "agent": str(task.get("owner") or "jaimes"),
                        "status": connector_status,
                        "note": task["summary"],
                    })
                    del notes[50:]
                if not (
                    connector.get("terminalPublishedIntentId") == intent_id
                    and connector.get("terminalPublishedState") == sync_state
                ):
                    connector["terminalPublishState"] = "pending"
                    connector_to_publish = dict(task)
        data["updatedAt"] = utc_now()
        write_json(tasks_path, data)
        fcntl.flock(lock, fcntl.LOCK_UN)
    if connector_to_publish is None:
        return
    _publish_connector_terminal(connector_to_publish, sync_state=sync_state)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(tasks_path.read_text())
        except Exception as exc:
            raise SystemExit(f"Agent task queue is corrupt: {tasks_path}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            raise SystemExit(f"Agent task queue has an invalid shape: {tasks_path}")
        for task in data.get("tasks", []):
            connector = task.get("linearConnector") if isinstance(task.get("linearConnector"), dict) else {}
            if task.get("id") == connector_to_publish.get("id") and connector.get("latestIntentId") == intent_id:
                connector["terminalPublishState"] = "done"
                connector["terminalPublishedIntentId"] = intent_id
                connector["terminalPublishedState"] = sync_state
                connector["terminalPublishedAt"] = utc_now()
                task["linearConnector"] = connector
                task["updatedAt"] = utc_now()
                break
        data["updatedAt"] = utc_now()
        write_json(tasks_path, data)
        fcntl.flock(lock, fcntl.LOCK_UN)


def _publish_connector_terminal(task: dict[str, Any], *, sync_state: str) -> None:
    """Publish the exact connector terminal so JAIMES cannot remain visibly active."""
    done = sync_state == "synced"
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_publish.py"),
        "--agent", str(task.get("owner") or "jaimes"),
        "--type", "complete" if done else "blocked",
        "--status", "done" if done else "blocked",
        "--title", compact(task.get("title") or "Linear connector", 150),
        "--tool", "linear_work_intent.py",
        "--detail", compact(task.get("summary") or "Linear synchronization finished.", 500),
        "--brain-feed",
        "--rollup",
        "--phase", "linear-sync-complete" if done else "linear-sync-blocked",
        "--work-event", "terminal",
        "--work-id", str(task.get("workId") or task.get("id") or ""),
        "--run-id", str(task.get("runId") or ""),
        "--generation", str(task.get("generation") or 1),
        "--origin", str(task.get("origin") or "linear-intent-delegation"),
        "--origin-claim-hash", str(task.get("originClaimHash") or ""),
        "--route-unverified",
    ]
    result = subprocess.run(cmd, cwd=ROOT, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            compact(result.stderr.strip() or result.stdout.strip() or "Connector terminal publish failed", 500)
        )


def _parse_time(value: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _claim_expired(row: dict[str, Any]) -> bool:
    expires = _parse_time(row.get("claimExpiresAt"))
    return bool(expires and expires <= dt.datetime.now(dt.timezone.utc))


def claim_intent(
    intent_id: str,
    *,
    consumer: str,
    lease_seconds: int = 300,
    path: Path | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = config or load_config()
    consumer_agent = canonical_agent(consumer)
    allowed_consumers = set(current.get("connector", {}).get("directAgents", []))
    if consumer_agent not in allowed_consumers:
        raise SystemExit(f"Agent '{consumer_agent}' does not have a direct Linear connector lane.")
    if lease_seconds < 30 or lease_seconds > 900:
        raise SystemExit("Claim lease must be between 30 and 900 seconds.")
    resolved_path = path or intent_path(current)
    lock_path = resolved_path.with_suffix(resolved_path.suffix + ".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        outbox = read_outbox(resolved_path)
        rows = [row for row in outbox.get("intents", []) if isinstance(row, dict)]
        row = next((item for item in rows if item.get("id") == intent_id), None)
        if not row:
            raise SystemExit(f"Linear intent not found: {intent_id}")
        same_work = [item for item in rows if item.get("workId") == row.get("workId")]
        latest = _latest_authoritative(same_work)
        if not latest:
            raise SystemExit("Linear intent has no authoritative work state.")
        if latest.get("id") != row.get("id"):
            raise SystemExit("Cannot claim a superseded Linear intent.")
        state = row.get("syncState")
        if state == "claimed" and not _claim_expired(row):
            raise SystemExit("Linear intent is already claimed.")
        if state not in {"pending", "failed", "claimed"}:
            raise SystemExit(f"Cannot claim a Linear intent in state '{state}'.")
        if row.get("routeTo") != consumer_agent:
            raise SystemExit(f"Linear intent is routed to '{row.get('routeTo')}', not '{consumer_agent}'.")
        token = secrets.token_hex(16)
        now = dt.datetime.now(dt.timezone.utc)
        row["syncState"] = "claimed"
        row["claimOwner"] = consumer_agent
        row["claimToken"] = token
        row["claimedAt"] = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        row["claimExpiresAt"] = (now + dt.timedelta(seconds=lease_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        row["attempts"] = int(row.get("attempts") or 0) + 1
        row["updatedAt"] = utc_now()
        outbox["updatedAt"] = utc_now()
        write_json(resolved_path, outbox)
        fcntl.flock(lock, fcntl.LOCK_UN)
    return row


def update_intent_result(
    intent_id: str,
    *,
    claim_token: str,
    verified_work_id: str,
    issue_id: str | None = None,
    error_code: str | None = None,
    path: Path | None = None,
    tasks_path: Path = TASKS_PATH,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = config or load_config()
    if bool(issue_id) == bool(error_code):
        raise SystemExit("Provide exactly one of issue_id or error_code.")
    team_key = str(current.get("workspace", {}).get("teamKey") or "")
    if issue_id and not re.fullmatch(rf"{re.escape(team_key)}-\d+", issue_id):
        raise SystemExit(f"Linear issue ID must use the configured {team_key}-* team key.")
    if error_code and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", error_code):
        raise SystemExit("Error code must be a short machine-safe value.")
    resolved_path = path or intent_path(current)
    lock_path = resolved_path.with_suffix(resolved_path.suffix + ".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        outbox = read_outbox(resolved_path)
        rows = [row for row in outbox.get("intents", []) if isinstance(row, dict)]
        row = next((item for item in rows if item.get("id") == intent_id), None)
        if not row:
            raise SystemExit(f"Linear intent not found: {intent_id}")
        if str(row.get("workId") or "") != verified_work_id:
            raise SystemExit("Verified work ID does not match the claimed Linear intent.")
        same_work = [item for item in rows if item.get("workId") == row.get("workId")]
        latest = _latest_authoritative(same_work)
        if not latest:
            raise SystemExit("Linear intent has no authoritative work state.")
        if latest.get("id") != row.get("id"):
            raise SystemExit("Cannot complete a superseded Linear intent.")
        already_synced = row.get("syncState") == "synced"
        if already_synced:
            if issue_id and row.get("issueId") == issue_id:
                pass
            else:
                raise SystemExit("A synced Linear intent cannot be changed.")
        else:
            if row.get("syncState") != "claimed" or row.get("claimToken") != claim_token:
                raise SystemExit("Linear intent claim token is missing or invalid.")
            if _claim_expired(row):
                raise SystemExit("Linear intent claim has expired.")
            if issue_id and row.get("issueId") and row.get("issueId") != issue_id:
                raise SystemExit("Linear intent is already linked to a different issue.")
            row["updatedAt"] = utc_now()
            if issue_id:
                row["issueId"] = issue_id
                row["syncState"] = "synced"
                row["syncedAt"] = utc_now()
                row["lastError"] = None
                verification = "|".join(
                    [issue_id, verified_work_id, str(current["workspace"]["teamId"]), str(current["workspace"]["projectId"])]
                )
                row["issueVerificationHash"] = hashlib.sha256(verification.encode("utf-8")).hexdigest()
            else:
                row["syncState"] = "failed"
                row["lastError"] = error_code
            row.pop("claimToken", None)
            row.pop("claimExpiresAt", None)
            outbox["updatedAt"] = utc_now()
            write_json(resolved_path, outbox)
        fcntl.flock(lock, fcntl.LOCK_UN)
    _update_task_after_result(
        task_id=str(row.get("taskId") or ""),
        intent_id=intent_id,
        issue_id=issue_id,
        sync_state=str(row["syncState"]),
        error_code=error_code,
        tasks_path=tasks_path,
    )
    return row


def pending_intents(
    *,
    path: Path | None = None,
    route_to: str = "",
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    current = config or load_config()
    resolved_path = path or intent_path(current)
    outbox = read_outbox(resolved_path)
    rows = []
    for row in outbox.get("intents", []):
        if row.get("syncState") in {"pending", "failed"} or (
            row.get("syncState") == "claimed" and _claim_expired(row)
        ):
            clean = dict(row)
            clean.pop("claimToken", None)
            rows.append(clean)
    if route_to:
        wanted = canonical_agent(route_to)
        delegated = current.get("connector", {}).get("delegatedAgents", {})
        if wanted in delegated:
            rows = [row for row in rows if row.get("owner") == wanted]
        else:
            rows = [row for row in rows if row.get("routeTo") == wanted]
    return rows


def _proxy_cli_to_canonical(argv: list[str], *, config: dict[str, Any]) -> int:
    bridge = config.get("intentBridge", {})
    host = canonical_ssh_target(config)
    root = str(bridge.get("canonicalRoot") or "")
    python = str(bridge.get("canonicalPython") or "python3")
    remote = f"cd {shlex.quote(root)} && {shlex.quote(python)} scripts/linear_work_intent.py {shlex.join(argv)}"
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, remote],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode == 0 and argv and argv[0] in {"ack", "fail"}:
        try:
            payload = json.loads(result.stdout or "{}")
            row = payload.get("intent", {})
            _update_task_after_result(
                task_id=str(row.get("taskId") or ""),
                intent_id=str(row.get("id") or ""),
                issue_id=row.get("issueId"),
                sync_state=str(row.get("syncState") or ""),
                error_code=row.get("lastError"),
            )
        except Exception:
            pass
    return result.returncode


def main() -> int:
    current = load_config()
    argv = sys.argv[1:]
    local_only = os.environ.get("CONTROL_TOWER_LINEAR_LOCAL_ONLY") == "1"
    if not is_canonical_runtime(current) and not local_only and (not argv or argv[0] != "flush-local"):
        try:
            flush_local_intents(config=current, limit=50)
        except SystemExit as exc:
            print(f"Local Linear replay warning: {exc}", file=sys.stderr)
        return _proxy_cli_to_canonical(sys.argv[1:], config=current)

    parser = argparse.ArgumentParser(description="Manage sanitized Linear durable-work intents.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    pending = sub.add_parser("pending")
    pending.add_argument("--route-to", default="")
    pending.add_argument("--limit", type=int, default=50)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("--stdin", action="store_true", required=True)
    flush_local = sub.add_parser("flush-local")
    flush_local.add_argument("--limit", type=int, default=50)
    claim = sub.add_parser("claim")
    claim.add_argument("--intent-id", required=True)
    claim.add_argument("--consumer", required=True)
    claim.add_argument("--lease-seconds", type=int, default=300)
    ack = sub.add_parser("ack")
    ack.add_argument("--intent-id", required=True)
    ack.add_argument("--claim-token", required=True)
    ack.add_argument("--issue-id", required=True)
    ack.add_argument("--verified-work-id", required=True)
    fail = sub.add_parser("fail")
    fail.add_argument("--intent-id", required=True)
    fail.add_argument("--claim-token", required=True)
    fail.add_argument("--verified-work-id", required=True)
    fail.add_argument("--error-code", required=True)
    args = parser.parse_args()
    if args.cmd == "pending":
        rows = pending_intents(route_to=args.route_to, config=current)[: max(0, args.limit)]
        print(json.dumps({"ok": True, "count": len(rows), "intents": rows}, indent=2))
    elif args.cmd == "ingest":
        try:
            incoming = json.loads(sys.stdin.read())
        except json.JSONDecodeError as exc:
            raise SystemExit("Canonical Linear intent ingest received invalid JSON.") from exc
        row = _enqueue_intent_local(incoming, path=intent_path(current), config=current)
        print(json.dumps({"ok": True, "intent": row}, indent=2))
    elif args.cmd == "flush-local":
        result = flush_local_intents(config=current, limit=max(0, args.limit))
        print(json.dumps(result, indent=2))
    elif args.cmd == "claim":
        row = claim_intent(args.intent_id, consumer=args.consumer, lease_seconds=args.lease_seconds, config=current)
        print(json.dumps({"ok": True, "intent": row}, indent=2))
    elif args.cmd == "ack":
        row = update_intent_result(
            args.intent_id,
            claim_token=args.claim_token,
            verified_work_id=args.verified_work_id,
            issue_id=args.issue_id,
            config=current,
        )
        print(json.dumps({"ok": True, "intent": row}, indent=2))
    else:
        row = update_intent_result(
            args.intent_id,
            claim_token=args.claim_token,
            verified_work_id=args.verified_work_id,
            error_code=args.error_code,
            config=current,
        )
        print(json.dumps({"ok": True, "intent": row}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
