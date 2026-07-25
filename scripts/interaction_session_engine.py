#!/usr/bin/env python3
"""Host-local reliability envelope for browser and computer-use actions.

The engine coordinates routing, display leases, state-change verification,
bounded recovery, promotion, and operator pause/stop controls. Raw commands,
screens, URLs, selectors, page text, accessibility trees, and command output
are never written to receipts or shared telemetry.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import control_tower_foreground
    import interaction_route_guard
except ModuleNotFoundError:  # package import during repository tests
    from scripts import control_tower_foreground, interaction_route_guard


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "interaction-routing.json"
DEFAULT_STATE_ROOT = Path.home() / ".openclaw" / "state" / "interaction-sessions"
DEFAULT_RECEIPT_PATH = Path.home() / ".openclaw" / "state" / "interaction-receipts.jsonl"
DEFAULT_CONTROL_PATH = Path.home() / ".openclaw" / "state" / "interaction-control.json"
DEFAULT_PROMOTION_ROOT = Path.home() / ".openclaw" / "state" / "interaction-promotions"
REMOTE_ROOT = "~/.openclaw/workspace/mission-control"
SURFACES = interaction_route_guard.VISIBLE_SURFACES | {"semantic-operation"}
VISIBLE_SURFACES = interaction_route_guard.VISIBLE_SURFACES
INTENTS = {"click", "type", "select", "navigate", "inspect", "upload", "other"}
CONTROL_MODES = {"running", "paused", "stopped"}
TERMINAL_STATES = {"complete", "escalated", "stopped", "aborted"}
STATES = {
    "ready",
    "observing",
    "acting",
    "verifying",
    "recovering",
    "paused",
    "promotion-pending",
    "promoted",
    *TERMINAL_STATES,
}
FAILURE_CODES = {
    "action-failed",
    "command-timeout",
    "driver-down",
    "lease-denied",
    "operator-pause",
    "operator-stop",
    "semantic-miss",
    "verification-failed",
    "visual-state-required",
}
RECEIPT_KEYS = {
    "schema",
    "event",
    "sessionId",
    "owner",
    "host",
    "fromHost",
    "surface",
    "intent",
    "state",
    "attempt",
    "maxAttempts",
    "reason",
    "stateChanged",
    "durationMs",
    "timestamp",
    "leaseExpiresAt",
}


class InteractionError(RuntimeError):
    """Expected fail-closed interaction error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_identifier(value: Any, *, label: str, maximum: int = 64) -> str:
    text = str(value or "")
    if not re.fullmatch(rf"[A-Za-z0-9._-]{{1,{maximum}}}", text):
        raise InteractionError(f"{label} must use 1-{maximum} letters, numbers, dots, dashes, or underscores")
    return text


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def promotion_path(request_id: str, promotion_root: Path = DEFAULT_PROMOTION_ROOT) -> Path:
    return promotion_root / f"{safe_identifier(request_id, label='promotion request id', maximum=80)}.json"


def queue_promotion_request(
    session: dict[str, Any],
    *,
    kind: str,
    reason: str,
    promotion_root: Path = DEFAULT_PROMOTION_ROOT,
    lease_id: str = "",
) -> dict[str, Any]:
    if kind not in {"promote", "release"}:
        raise InteractionError("invalid promotion queue request")
    request_id = f"ipr-{secrets.token_hex(12)}"
    now = int(time.time())
    payload = {
        "schema": 1,
        "requestId": request_id,
        "kind": kind,
        "sessionId": str(session.get("sessionId")),
        "owner": str(session.get("owner")),
        "purpose": "browser" if str(session.get("surface")).startswith("browser") else "computer-use",
        "reason": reason[:64],
        "createdAt": utc_now(),
        "expiresEpoch": now + (600 if kind == "release" else 120),
    }
    if kind == "release":
        payload["leaseId"] = lease_id
    atomic_write_json(promotion_path(request_id, promotion_root), payload)
    return payload


def export_promotion_requests(promotion_root: Path = DEFAULT_PROMOTION_ROOT) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    if not promotion_root.is_dir():
        return requests
    for path in sorted(promotion_root.glob("ipr-*.json"))[:20]:
        payload = load_json(path, {})
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            continue
        request_id = str(payload.get("requestId") or "")
        try:
            safe_identifier(request_id, label="promotion request id", maximum=80)
        except InteractionError:
            continue
        kind = str(payload.get("kind") or "")
        if kind not in {"promote", "release"}:
            continue
        row = {
            "requestId": request_id,
            "kind": kind,
            "sessionId": str(payload.get("sessionId") or "")[:80],
            "owner": str(payload.get("owner") or "")[:48],
            "purpose": str(payload.get("purpose") or "")[:24],
            "reason": str(payload.get("reason") or "")[:64],
            "expiresEpoch": int(payload.get("expiresEpoch") or 0),
            "expired": int(payload.get("expiresEpoch") or 0) < int(time.time()),
        }
        if kind == "release":
            row["leaseId"] = str(payload.get("leaseId") or "")
        requests.append(row)
    return requests


def complete_promotion_request(
    request_id: str,
    response: dict[str, Any],
    *,
    promotion_root: Path = DEFAULT_PROMOTION_ROOT,
    state_root: Path = DEFAULT_STATE_ROOT,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
) -> dict[str, Any]:
    path = promotion_path(request_id, promotion_root)
    request = load_json(path, {})
    if not isinstance(request, dict) or request.get("schema") != 1:
        raise InteractionError("promotion request not found")
    status = str(response.get("status") or "")
    if request.get("kind") == "release":
        if status not in {"released", "already-released"}:
            raise InteractionError("invalid release response")
        path.unlink(missing_ok=True)
        return {"ok": True, "status": status, "kind": "release"}
    session = read_session(str(request.get("sessionId")), state_root)
    if status == "leased":
        lease_id = str(response.get("leaseId") or "")
        if not lease_id:
            raise InteractionError("promotion response is missing its private lease id")
        session["lease"] = {
            "host": "josh2",
            "leaseId": lease_id,
            "active": True,
            "expiresAt": response.get("expiresAt"),
        }
        session["fromHost"] = "jaimes"
        session["host"] = "josh2"
        session["surface"] = "browser-visual" if request.get("purpose") == "browser" else "computer-use"
        session["state"] = "promoted"
        session["lastReason"] = str(request.get("reason") or "visual-state-required")[:64]
        session["promotionRequestId"] = None
        event = "promoted"
    elif status in {"rejected", "expired"}:
        session["state"] = "escalated"
        session["lastReason"] = "promotion-expired" if status == "expired" else "promotion-rejected"
        session["promotionRequestId"] = None
        event = "escalated"
    else:
        raise InteractionError("invalid promotion response")
    write_session(session, state_root)
    emit_receipt({**public_session(session), "event": event}, receipt_path)
    path.unlink(missing_ok=True)
    return {"ok": True, **public_session(session)}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    payload = load_json(path, {})
    return payload if isinstance(payload, dict) else {}


def engine_config(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("sessionEngine")
    return value if isinstance(value, dict) else {}


def max_attempts(config: dict[str, Any]) -> int:
    try:
        return max(1, min(3, int(engine_config(config).get("maxAttempts", 3))))
    except (TypeError, ValueError):
        return 3


def canonical_host_id() -> str:
    override = os.environ.get("INTERACTION_HOST_ID", "").strip().lower()
    if override in {"josh2", "jaimes", "joshex"}:
        return override
    username = getpass.getuser().lower()
    if username == "jc_agent":
        return "jaimes"
    if username == "josh2.0":
        return "josh2"
    hostname = socket.gethostname().lower()
    if "jaimes" in hostname:
        return "jaimes"
    if "josh2" in hostname or "josh-2" in hostname or "josh20" in hostname:
        return "josh2"
    return "joshex"


def state_path(session_id: str, state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    return state_root / f"{safe_identifier(session_id, label='session id', maximum=80)}.json"


def read_session(session_id: str, state_root: Path = DEFAULT_STATE_ROOT) -> dict[str, Any]:
    payload = load_json(state_path(session_id, state_root), {})
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise InteractionError("interaction session not found")
    return payload


def write_session(session: dict[str, Any], state_root: Path = DEFAULT_STATE_ROOT) -> None:
    session["updatedAt"] = utc_now()
    atomic_write_json(state_path(str(session.get("sessionId")), state_root), session)


def state_token(payload: bytes | str | dict[str, Any] | list[Any]) -> str:
    if isinstance(payload, bytes):
        encoded = payload
    elif isinstance(payload, str):
        encoded = payload.encode("utf-8", errors="replace")
    else:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(b"interaction-state-v1\0" + encoded).hexdigest()


def safe_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    receipt: dict[str, Any] = {}
    for key in RECEIPT_KEYS:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            receipt[key] = value
        elif isinstance(value, int):
            receipt[key] = max(0, min(value, 10_000_000))
        elif isinstance(value, str):
            receipt[key] = value[:96]
    receipt["schema"] = 1
    receipt.setdefault("timestamp", utc_now())
    return receipt


def emit_receipt(payload: dict[str, Any], receipt_path: Path = DEFAULT_RECEIPT_PATH) -> dict[str, Any]:
    receipt = safe_receipt(payload)
    append_jsonl(receipt_path, receipt)
    return receipt


def public_session(session: dict[str, Any]) -> dict[str, Any]:
    lease = session.get("lease") if isinstance(session.get("lease"), dict) else {}
    return safe_receipt(
        {
            "event": "status",
            "sessionId": session.get("sessionId"),
            "owner": session.get("owner"),
            "host": session.get("host"),
            "fromHost": session.get("fromHost"),
            "surface": session.get("surface"),
            "intent": session.get("intent"),
            "state": session.get("state"),
            "attempt": session.get("attempt"),
            "maxAttempts": session.get("maxAttempts"),
            "reason": session.get("lastReason"),
            "stateChanged": session.get("stateChanged"),
            "leaseExpiresAt": lease.get("expiresAt"),
            "timestamp": session.get("updatedAt"),
        }
    )


def control_state(path: Path = DEFAULT_CONTROL_PATH) -> dict[str, Any]:
    payload = load_json(path, {})
    if not isinstance(payload, dict) or payload.get("mode") not in CONTROL_MODES:
        return {"schema": 1, "mode": "running", "generation": 0, "updatedAt": None, "sessionId": None}
    return {
        "schema": 1,
        "mode": payload.get("mode"),
        "generation": max(0, int(payload.get("generation") or 0)),
        "updatedAt": payload.get("updatedAt"),
        "sessionId": payload.get("sessionId"),
    }


def set_control(mode: str, session_id: str | None = None, path: Path = DEFAULT_CONTROL_PATH) -> dict[str, Any]:
    if mode not in CONTROL_MODES:
        raise InteractionError("invalid operator control mode")
    previous = control_state(path)
    payload = {
        "schema": 1,
        "mode": mode,
        "generation": int(previous.get("generation") or 0) + 1,
        "updatedAt": utc_now(),
        "sessionId": safe_identifier(session_id, label="session id", maximum=80) if session_id else None,
    }
    atomic_write_json(path, payload)
    return payload


def operator_mode(session_id: str, path: Path = DEFAULT_CONTROL_PATH) -> str:
    control = control_state(path)
    scoped = control.get("sessionId")
    if scoped and scoped != session_id:
        return "running"
    return str(control.get("mode") or "running")


def halt_for_operator(
    session: dict[str, Any],
    mode: str,
    *,
    state_root: Path = DEFAULT_STATE_ROOT,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
    local_host: str | None = None,
) -> dict[str, Any]:
    if mode == "stopped":
        return finish_session(
            session,
            state="stopped",
            reason="operator-stopped",
            state_root=state_root,
            receipt_path=receipt_path,
            local_host=local_host,
        )
    if mode != "paused":
        raise InteractionError("invalid operator halt mode")
    release_visible_lease(session, local_host)
    session["state"] = "paused"
    session["lastReason"] = "operator-paused"
    write_session(session, state_root)
    emit_receipt({**public_session(session), "event": "paused"}, receipt_path)
    return session


def _run_json(cmd: list[str], timeout: int = 20) -> dict[str, Any]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    if proc.returncode != 0 or not isinstance(payload, dict) or payload.get("ok") is not True:
        raise InteractionError("visible display lease operation failed")
    return payload


def acquire_visible_lease(owner: str, purpose: str, ttl_seconds: int, local_host: str | None = None) -> dict[str, Any]:
    current = local_host or canonical_host_id()
    if current == "josh2":
        payload = control_tower_foreground.begin_lease(owner=owner, purpose=purpose, ttl_seconds=ttl_seconds)
        state = control_tower_foreground.lease_state()
        control_tower_foreground.publish_display_lease(state)
        return {
            "host": "josh2",
            "leaseId": payload["leaseId"],
            "active": True,
            "expiresAt": payload.get("expiresAt"),
        }
    payload = _run_json(
        [
            "/usr/bin/ssh",
            "josh2",
            f"cd {REMOTE_ROOT} && python3 scripts/control_tower_foreground.py begin --owner {owner} --purpose {purpose} --ttl-seconds {ttl_seconds}",
        ]
    )
    return {
        "host": "josh2",
        "leaseId": payload.get("leaseId"),
        "active": True,
        "expiresAt": payload.get("expiresAt"),
    }


def release_visible_lease(
    session: dict[str, Any],
    local_host: str | None = None,
    promotion_root: Path = DEFAULT_PROMOTION_ROOT,
) -> None:
    lease = session.get("lease") if isinstance(session.get("lease"), dict) else {}
    lease_id = str(lease.get("leaseId") or "")
    if not lease_id or lease.get("active") is not True:
        return
    current = local_host or canonical_host_id()
    try:
        if current == "josh2":
            control_tower_foreground.end_lease(lease_id=lease_id)
            control_tower_foreground.publish_display_lease(None)
            control_tower_foreground.restore_after_release()
        elif current == "jaimes":
            queue_promotion_request(
                session,
                kind="release",
                reason="session-terminal",
                promotion_root=promotion_root,
                lease_id=lease_id,
            )
        else:
            _run_json(
                [
                    "/usr/bin/ssh",
                    "josh2",
                    f"cd {REMOTE_ROOT} && python3 scripts/control_tower_foreground.py end --lease-id {lease_id}",
                ],
                timeout=90,
            )
    finally:
        lease["active"] = False
        lease.pop("leaseId", None)


def begin_session(
    *,
    owner: str,
    target_host: str,
    surface: str,
    intent: str,
    reason: str = "",
    private_context: bool = False,
    acknowledged: bool = False,
    ttl_seconds: int = 180,
    config_path: Path = DEFAULT_CONFIG,
    state_root: Path = DEFAULT_STATE_ROOT,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
    promotion_root: Path = DEFAULT_PROMOTION_ROOT,
    control_path: Path = DEFAULT_CONTROL_PATH,
    local_host: str | None = None,
) -> dict[str, Any]:
    owner = safe_identifier(owner, label="owner", maximum=48)
    if target_host not in {"josh2", "jaimes", "joshex"}:
        raise InteractionError("invalid target host")
    if surface not in SURFACES:
        raise InteractionError("invalid interaction surface")
    if intent not in INTENTS:
        raise InteractionError("invalid interaction intent")
    config = load_config(config_path)
    if engine_config(config).get("enabled") is not True:
        raise InteractionError("interaction session engine is disabled")
    route = interaction_route_guard.evaluate(
        target_host=target_host,
        surface=surface,
        reason=reason,
        private_context=private_context,
        acknowledged=acknowledged,
        config=config,
    )
    if route.get("decision") == "acknowledgement-required":
        raise InteractionError("personal-device acknowledgement is required")
    resolved_host = str(route.get("targetHost") or target_host)
    current_host = local_host or canonical_host_id()
    queued_initial_promotion = route.get("decision") == "promote" and current_host == "jaimes"
    session_host = "jaimes" if queued_initial_promotion else resolved_host
    session_id = f"ix-{secrets.token_hex(12)}"
    mode = operator_mode(session_id, control_path)
    if mode != "running":
        raise InteractionError(f"operator control is {mode}")
    session: dict[str, Any] = {
        "schema": 1,
        "sessionId": session_id,
        "owner": owner,
        "requestedHost": target_host,
        "host": session_host,
        "fromHost": route.get("fromHost"),
        "surface": surface,
        "intent": intent,
        "privateContext": bool(private_context),
        "state": "ready",
        "attempt": 0,
        "maxAttempts": max_attempts(config),
        "beforeToken": None,
        "afterToken": None,
        "stateChanged": None,
        "lastReason": str(route.get("reason") or "")[:64],
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "lease": {"active": False},
    }
    if queued_initial_promotion:
        write_session(session, state_root)
        emit_receipt({**public_session(session), "event": "started"}, receipt_path)
        return promote_session(
            session,
            reason="visual-state-required",
            state_root=state_root,
            receipt_path=receipt_path,
            local_host=current_host,
            promotion_root=promotion_root,
        )
    if resolved_host == "josh2" and surface in VISIBLE_SURFACES:
        purpose = "browser" if surface.startswith("browser") else "computer-use"
        try:
            session["lease"] = acquire_visible_lease(owner, purpose, ttl_seconds, local_host)
        except InteractionError:
            session["state"] = "escalated"
            session["lastReason"] = "lease-denied"
            write_session(session, state_root)
            emit_receipt({**public_session(session), "event": "escalated"}, receipt_path)
            raise
    write_session(session, state_root)
    emit_receipt({**public_session(session), "event": "started"}, receipt_path)
    return session


def transition(
    session: dict[str, Any],
    state: str,
    *,
    reason: str = "",
    state_root: Path = DEFAULT_STATE_ROOT,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
) -> dict[str, Any]:
    if state not in STATES:
        raise InteractionError("invalid interaction state")
    if session.get("state") in TERMINAL_STATES:
        raise InteractionError("interaction session is terminal")
    session["state"] = state
    if reason:
        session["lastReason"] = reason[:64]
    write_session(session, state_root)
    emit_receipt({**public_session(session), "event": state}, receipt_path)
    return session


def observe(
    session: dict[str, Any],
    payload: bytes | str | dict[str, Any] | list[Any],
    *,
    phase: str,
    state_root: Path = DEFAULT_STATE_ROOT,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
    control_path: Path = DEFAULT_CONTROL_PATH,
) -> dict[str, Any]:
    if phase not in {"before", "after"}:
        raise InteractionError("observation phase must be before or after")
    mode = operator_mode(str(session.get("sessionId")), control_path)
    if mode != "running":
        halt_for_operator(session, mode, state_root=state_root, receipt_path=receipt_path)
        raise InteractionError(f"operator control is {mode}")
    session["state"] = "observing" if phase == "before" else "verifying"
    session[f"{phase}Token"] = state_token(payload)
    write_session(session, state_root)
    return session


def start_attempt(
    session: dict[str, Any],
    *,
    state_root: Path = DEFAULT_STATE_ROOT,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
    control_path: Path = DEFAULT_CONTROL_PATH,
) -> dict[str, Any]:
    mode = operator_mode(str(session.get("sessionId")), control_path)
    if mode != "running":
        halt_for_operator(session, mode, state_root=state_root, receipt_path=receipt_path)
        raise InteractionError(f"operator control is {mode}")
    attempt = int(session.get("attempt") or 0) + 1
    if attempt > int(session.get("maxAttempts") or 1):
        return transition(session, "escalated", reason="retry-budget-exhausted", state_root=state_root, receipt_path=receipt_path)
    session["attempt"] = attempt
    session["state"] = "acting"
    session["afterToken"] = None
    session["stateChanged"] = None
    write_session(session, state_root)
    emit_receipt({**public_session(session), "event": "attempted"}, receipt_path)
    return session


def verify(
    session: dict[str, Any],
    payload: bytes | str | dict[str, Any] | list[Any],
    *,
    expectation: str = "changed",
    state_root: Path = DEFAULT_STATE_ROOT,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
    control_path: Path = DEFAULT_CONTROL_PATH,
) -> tuple[dict[str, Any], bool]:
    if expectation not in {"changed", "unchanged"}:
        raise InteractionError("verification expectation must be changed or unchanged")
    if not session.get("beforeToken"):
        raise InteractionError("before-state observation is required")
    session = observe(
        session,
        payload,
        phase="after",
        state_root=state_root,
        receipt_path=receipt_path,
        control_path=control_path,
    )
    changed = not secrets.compare_digest(str(session.get("beforeToken")), str(session.get("afterToken")))
    verified = changed if expectation == "changed" else not changed
    session["stateChanged"] = changed
    if verified:
        session["state"] = "complete"
        session["lastReason"] = "verified"
        release_visible_lease(session)
        write_session(session, state_root)
        emit_receipt({**public_session(session), "event": "verified"}, receipt_path)
        return session, True
    if int(session.get("attempt") or 0) >= int(session.get("maxAttempts") or 1):
        session["state"] = "escalated"
        session["lastReason"] = "verification-failed"
        release_visible_lease(session)
    else:
        session["state"] = "recovering"
        session["lastReason"] = "verification-failed"
    write_session(session, state_root)
    emit_receipt({**public_session(session), "event": session["state"]}, receipt_path)
    return session, False


def fail_session(
    session: dict[str, Any],
    reason: str,
    *,
    promote: bool = False,
    config_path: Path = DEFAULT_CONFIG,
    state_root: Path = DEFAULT_STATE_ROOT,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
    local_host: str | None = None,
    promotion_root: Path = DEFAULT_PROMOTION_ROOT,
) -> dict[str, Any]:
    if reason not in FAILURE_CODES:
        raise InteractionError("invalid failure reason")
    config = load_config(config_path)
    promotion_reasons = set(engine_config(config).get("promotionReasons", []))
    can_promote = (
        promote
        and session.get("host") == "jaimes"
        and session.get("privateContext") is not True
        and reason in promotion_reasons
    )
    if can_promote:
        return promote_session(
            session,
            reason=reason,
            state_root=state_root,
            receipt_path=receipt_path,
            local_host=local_host,
            promotion_root=promotion_root,
        )
    if int(session.get("attempt") or 0) >= int(session.get("maxAttempts") or 1):
        session["state"] = "escalated"
        release_visible_lease(session, local_host)
    else:
        session["state"] = "recovering"
    session["lastReason"] = reason
    write_session(session, state_root)
    emit_receipt({**public_session(session), "event": session["state"]}, receipt_path)
    return session


def promote_session(
    session: dict[str, Any],
    *,
    reason: str,
    state_root: Path = DEFAULT_STATE_ROOT,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
    local_host: str | None = None,
    promotion_root: Path = DEFAULT_PROMOTION_ROOT,
) -> dict[str, Any]:
    if session.get("privateContext") is True:
        raise InteractionError("private account context cannot be promoted between hosts")
    if session.get("host") != "jaimes":
        raise InteractionError("only a JAIMES headless session can be promoted")
    current = local_host or canonical_host_id()
    if current != "jaimes":
        purpose = "browser" if str(session.get("surface")).startswith("browser") else "computer-use"
        session["lease"] = acquire_visible_lease(str(session.get("owner")), purpose, 180, current)
        session["fromHost"] = "jaimes"
        session["host"] = "josh2"
        session["surface"] = "browser-visual" if purpose == "browser" else "computer-use"
        session["state"] = "promoted"
        event = "promoted"
    else:
        request = queue_promotion_request(
            session,
            kind="promote",
            reason=reason,
            promotion_root=promotion_root,
        )
        session["state"] = "promotion-pending"
        session["promotionRequestId"] = request["requestId"]
        event = "promotion-pending"
    session["lastReason"] = reason[:64]
    write_session(session, state_root)
    emit_receipt({**public_session(session), "event": event}, receipt_path)
    return session


def resume_session(
    session: dict[str, Any],
    *,
    state_root: Path = DEFAULT_STATE_ROOT,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
    control_path: Path = DEFAULT_CONTROL_PATH,
    local_host: str | None = None,
) -> dict[str, Any]:
    if session.get("state") != "paused":
        raise InteractionError("only a paused interaction session can be resumed")
    set_control("running", str(session.get("sessionId")), control_path)
    if session.get("host") == "josh2" and session.get("surface") in VISIBLE_SURFACES:
        purpose = "browser" if str(session.get("surface")).startswith("browser") else "computer-use"
        session["lease"] = acquire_visible_lease(str(session.get("owner")), purpose, 180, local_host)
    session["state"] = "ready"
    session["lastReason"] = "operator-resumed"
    write_session(session, state_root)
    emit_receipt({**public_session(session), "event": "resumed"}, receipt_path)
    return session


def finish_session(
    session: dict[str, Any],
    *,
    state: str,
    reason: str,
    state_root: Path = DEFAULT_STATE_ROOT,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
    local_host: str | None = None,
) -> dict[str, Any]:
    if state not in TERMINAL_STATES:
        raise InteractionError("finish state must be terminal")
    release_visible_lease(session, local_host)
    session["state"] = state
    session["lastReason"] = reason[:64]
    write_session(session, state_root)
    emit_receipt({**public_session(session), "event": state}, receipt_path)
    return session


def command_spec(path: Path, default_timeout: int) -> tuple[list[str], int]:
    payload = load_json(path, {})
    if not isinstance(payload, dict) or not isinstance(payload.get("command"), list):
        raise InteractionError("command file must contain a command array")
    command = [str(value) for value in payload["command"]]
    if not command or len(command) > 64 or any(not value or len(value) > 4096 for value in command):
        raise InteractionError("command file contains an invalid command")
    try:
        timeout = max(1, min(180, int(payload.get("timeoutSeconds") or default_timeout)))
    except (TypeError, ValueError):
        timeout = default_timeout
    return command, timeout


def run_private_command(
    command: list[str],
    timeout_seconds: int,
    session_id: str,
    control_path: Path = DEFAULT_CONTROL_PATH,
    poll_seconds: float = 0.1,
    terminate_grace: float = 2.0,
) -> tuple[int, bytes, str]:
    def terminate_group(proc: subprocess.Popen[Any]) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=terminate_grace)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=2)

    started = time.monotonic()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        reason = "completed"
        while proc.poll() is None:
            mode = operator_mode(session_id, control_path)
            if mode != "running":
                reason = f"operator-{mode}"
                terminate_group(proc)
                break
            if time.monotonic() - started > timeout_seconds:
                reason = "command-timeout"
                terminate_group(proc)
                break
            time.sleep(max(0.02, poll_seconds))
        stdout.seek(0)
        stderr.seek(0)
        output = stdout.read() + stderr.read()
        return int(proc.returncode if proc.returncode is not None else 126), output, reason


def run_reliable_command(
    session: dict[str, Any],
    *,
    action_file: Path,
    observe_file: Path,
    recovery_file: Path | None = None,
    expectation: str = "changed",
    config_path: Path = DEFAULT_CONFIG,
    state_root: Path = DEFAULT_STATE_ROOT,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
    control_path: Path = DEFAULT_CONTROL_PATH,
) -> tuple[dict[str, Any], bool]:
    config = load_config(config_path)
    engine = engine_config(config)
    default_timeout = max(1, min(180, int(engine.get("commandTimeoutSeconds") or 45)))
    poll_seconds = max(0.02, min(1.0, int(engine.get("operatorPollMilliseconds") or 100) / 1000))
    terminate_grace = max(0.1, min(10.0, float(engine.get("terminateGraceSeconds") or 2)))
    action_cmd, action_timeout = command_spec(action_file, default_timeout)
    observe_cmd, observe_timeout = command_spec(observe_file, default_timeout)
    recovery_cmd: list[str] | None = None
    recovery_timeout = default_timeout
    if recovery_file:
        recovery_cmd, recovery_timeout = command_spec(recovery_file, default_timeout)

    while int(session.get("attempt") or 0) < int(session.get("maxAttempts") or 1):
        before_code, before_output, before_reason = run_private_command(
            observe_cmd, observe_timeout, str(session["sessionId"]), control_path, poll_seconds, terminate_grace
        )
        if before_reason.startswith("operator-"):
            mode = "paused" if before_reason == "operator-paused" else "stopped"
            return halt_for_operator(session, mode, state_root=state_root, receipt_path=receipt_path), False
        if before_code != 0:
            session = fail_session(session, "driver-down", state_root=state_root, receipt_path=receipt_path)
            break
        session = observe(
            session,
            before_output,
            phase="before",
            state_root=state_root,
            receipt_path=receipt_path,
            control_path=control_path,
        )
        session = start_attempt(
            session,
            state_root=state_root,
            receipt_path=receipt_path,
            control_path=control_path,
        )
        started = time.monotonic()
        action_code, _action_output, action_reason = run_private_command(
            action_cmd, action_timeout, str(session["sessionId"]), control_path, poll_seconds, terminate_grace
        )
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        if action_reason.startswith("operator-"):
            mode = "paused" if action_reason == "operator-paused" else "stopped"
            session = halt_for_operator(session, mode, state_root=state_root, receipt_path=receipt_path)
            return session, False
        if action_code == 0:
            after_code, after_output, after_reason = run_private_command(
                observe_cmd, observe_timeout, str(session["sessionId"]), control_path, poll_seconds, terminate_grace
            )
            if after_reason.startswith("operator-"):
                mode = "paused" if after_reason == "operator-paused" else "stopped"
                session = halt_for_operator(session, mode, state_root=state_root, receipt_path=receipt_path)
                return session, False
            if after_code == 0:
                session, verified = verify(
                    session,
                    after_output,
                    expectation=expectation,
                    state_root=state_root,
                    receipt_path=receipt_path,
                    control_path=control_path,
                )
                emit_receipt({**public_session(session), "event": "action-result", "durationMs": duration_ms}, receipt_path)
                if verified:
                    return session, True
        reason = "command-timeout" if action_reason == "command-timeout" else "action-failed" if action_code else "verification-failed"
        if session.get("state") == "escalated":
            break
        session = fail_session(session, reason, state_root=state_root, receipt_path=receipt_path)
        if session.get("state") == "escalated":
            break
        if recovery_cmd:
            transition(session, "recovering", reason=reason, state_root=state_root, receipt_path=receipt_path)
            recovery_code, _output, recovery_reason = run_private_command(
                recovery_cmd, recovery_timeout, str(session["sessionId"]), control_path, poll_seconds, terminate_grace
            )
            if recovery_reason.startswith("operator-"):
                mode = "paused" if recovery_reason == "operator-paused" else "stopped"
                session = halt_for_operator(session, mode, state_root=state_root, receipt_path=receipt_path)
                return session, False
            if recovery_code != 0:
                session = fail_session(session, "action-failed", state_root=state_root, receipt_path=receipt_path)
                break
    if session.get("state") not in TERMINAL_STATES:
        session = finish_session(session, state="escalated", reason="retry-budget-exhausted", state_root=state_root, receipt_path=receipt_path)
    return session, False


def _state_payload(path: Path) -> bytes:
    return path.read_bytes()


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--receipt-path", type=Path, default=DEFAULT_RECEIPT_PATH)
    parser.add_argument("--control-path", type=Path, default=DEFAULT_CONTROL_PATH)
    parser.add_argument("--promotion-root", type=Path, default=DEFAULT_PROMOTION_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start")
    start.add_argument("--owner", required=True)
    start.add_argument("--target-host", required=True, choices=("josh2", "jaimes", "joshex"))
    start.add_argument("--surface", required=True, choices=sorted(SURFACES))
    start.add_argument("--intent", required=True, choices=sorted(INTENTS))
    start.add_argument("--reason", default="")
    start.add_argument("--private-context", action="store_true")
    start.add_argument("--acknowledge-personal-device", action="store_true")
    start.add_argument("--ttl-seconds", type=int, default=180)

    for name in ("status", "attempt", "complete", "abort", "promote", "resume"):
        sub = commands.add_parser(name)
        sub.add_argument("--session-id", required=True)
    commands.choices["promote"].add_argument("--reason", choices=sorted(FAILURE_CODES), default="visual-state-required")

    observe_parser = commands.add_parser("observe")
    observe_parser.add_argument("--session-id", required=True)
    observe_parser.add_argument("--phase", required=True, choices=("before", "after"))
    observe_parser.add_argument("--state-file", type=Path, required=True)

    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--session-id", required=True)
    verify_parser.add_argument("--state-file", type=Path, required=True)
    verify_parser.add_argument("--expect", choices=("changed", "unchanged"), default="changed")

    fail_parser = commands.add_parser("fail")
    fail_parser.add_argument("--session-id", required=True)
    fail_parser.add_argument("--reason", required=True, choices=sorted(FAILURE_CODES))
    fail_parser.add_argument("--promote", action="store_true")

    control = commands.add_parser("control")
    control.add_argument("--mode", required=True, choices=("running", "paused", "stopped"))
    control.add_argument("--session-id")

    run_command = commands.add_parser("run-command")
    run_command.add_argument("--session-id", required=True)
    run_command.add_argument("--action-command-file", type=Path, required=True)
    run_command.add_argument("--observe-command-file", type=Path, required=True)
    run_command.add_argument("--recovery-command-file", type=Path)
    run_command.add_argument("--expect", choices=("changed", "unchanged"), default="changed")

    commands.add_parser("broker-export")
    broker_complete = commands.add_parser("broker-complete")
    broker_complete.add_argument("--request-id", required=True)

    args = parser.parse_args()
    try:
        if args.command == "broker-export":
            _print({"ok": True, "requests": export_promotion_requests(args.promotion_root)})
            return 0
        if args.command == "broker-complete":
            try:
                response = json.loads(sys.stdin.read() or "{}")
            except json.JSONDecodeError as exc:
                raise InteractionError("invalid private broker response") from exc
            if not isinstance(response, dict):
                raise InteractionError("invalid private broker response")
            result = complete_promotion_request(
                args.request_id,
                response,
                promotion_root=args.promotion_root,
                state_root=args.state_root,
                receipt_path=args.receipt_path,
            )
            _print(result)
            return 0
        if args.command == "start":
            session = begin_session(
                owner=args.owner,
                target_host=args.target_host,
                surface=args.surface,
                intent=args.intent,
                reason=args.reason,
                private_context=args.private_context,
                acknowledged=args.acknowledge_personal_device,
                ttl_seconds=args.ttl_seconds,
                config_path=args.config,
                state_root=args.state_root,
                receipt_path=args.receipt_path,
                promotion_root=args.promotion_root,
                control_path=args.control_path,
            )
            _print({"ok": True, **public_session(session)})
            return 0
        if args.command == "control":
            payload = set_control(args.mode, args.session_id, args.control_path)
            _print({"ok": True, **payload})
            return 0

        session = read_session(args.session_id, args.state_root)
        if args.command == "status":
            _print({"ok": True, **public_session(session), "operatorMode": operator_mode(args.session_id, args.control_path)})
            return 0
        if args.command == "attempt":
            session = start_attempt(
                session,
                state_root=args.state_root,
                receipt_path=args.receipt_path,
                control_path=args.control_path,
            )
            _print({"ok": session.get("state") == "acting", **public_session(session)})
            return 0 if session.get("state") == "acting" else 4
        if args.command == "observe":
            session = observe(
                session,
                _state_payload(args.state_file),
                phase=args.phase,
                state_root=args.state_root,
                receipt_path=args.receipt_path,
                control_path=args.control_path,
            )
            _print({"ok": True, **public_session(session)})
            return 0
        if args.command == "verify":
            session, verified = verify(
                session,
                _state_payload(args.state_file),
                expectation=args.expect,
                state_root=args.state_root,
                receipt_path=args.receipt_path,
                control_path=args.control_path,
            )
            _print({"ok": verified, **public_session(session)})
            return 0 if verified else 4 if session.get("state") == "recovering" else 5
        if args.command == "fail":
            session = fail_session(
                session,
                args.reason,
                promote=args.promote,
                config_path=args.config,
                state_root=args.state_root,
                receipt_path=args.receipt_path,
                promotion_root=args.promotion_root,
            )
            _print({"ok": session.get("state") not in {"escalated", "stopped"}, **public_session(session)})
            return 0 if session.get("state") not in {"escalated", "stopped"} else 5
        if args.command == "promote":
            session = promote_session(
                session,
                reason=args.reason,
                state_root=args.state_root,
                receipt_path=args.receipt_path,
                promotion_root=args.promotion_root,
            )
            _print({"ok": True, **public_session(session)})
            return 0
        if args.command == "resume":
            session = resume_session(
                session,
                state_root=args.state_root,
                receipt_path=args.receipt_path,
                control_path=args.control_path,
            )
            _print({"ok": True, **public_session(session)})
            return 0
        if args.command == "run-command":
            session, verified = run_reliable_command(
                session,
                action_file=args.action_command_file,
                observe_file=args.observe_command_file,
                recovery_file=args.recovery_command_file,
                expectation=args.expect,
                config_path=args.config,
                state_root=args.state_root,
                receipt_path=args.receipt_path,
                control_path=args.control_path,
            )
            _print({"ok": verified, **public_session(session)})
            return 0 if verified else 5
        final_state = "complete" if args.command == "complete" else "aborted"
        reason = "completed-by-caller" if final_state == "complete" else "aborted-by-caller"
        session = finish_session(session, state=final_state, reason=reason, state_root=args.state_root, receipt_path=args.receipt_path)
        _print({"ok": True, **public_session(session)})
        return 0
    except (InteractionError, OSError, subprocess.SubprocessError, ValueError) as exc:
        _print({"ok": False, "error": str(exc)[:160], "errorType": type(exc).__name__})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
