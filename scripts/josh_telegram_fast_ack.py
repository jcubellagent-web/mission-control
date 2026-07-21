#!/usr/bin/env python3
"""Handle Josh 2.0 Telegram direct-chat fast-ack/session state.

This watcher does not poll Telegram, so it does not compete with OpenCLAW's
Telegram channel. It watches OpenCLAW session metadata for the direct Josh chat
and can send a tiny acknowledgement when explicitly enabled. The real work card
starts after the objective is known.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import tempfile
import textwrap
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

HOME = Path.home()
WORKSPACE = HOME / ".openclaw" / "workspace"
SESSIONS_PATH = HOME / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
SESSION_DIR = SESSIONS_PATH.parent
STATE_PATH = HOME / ".openclaw" / "telegram" / "fast_ack_state.json"
DIRECT_SESSION_KEY = "agent:main:telegram:direct:6218150306"
CONTROL_CENTER_CHAT_ID = "-1003589561528"
JOSH_CONTROL_CENTER_TOPICS = {"1", "18", "21", "22"}
TELEGRAM_GROUP_TOPIC_RE = re.compile(r"telegram:group:(-?\d+):(?:topic:)?(\d+)")
#JAIMES: JOSH 2.0 now advertises Gemini Flash as the front-line triage lane.
DEFAULT_MODEL = "Gemini Flash / local OpenCLAW session"
DEFAULT_ROUTE = "Josh 2.0 Telegram -> OpenCLAW task"
COORDINATOR_SCRIPT = WORKSPACE / "mission-control" / "scripts" / "inbox_coordinator.py"
#JAIMES: Fast-ack cards use the workspace copy, colocated with send_josh_reply.py and the real Telegram credentials path.
WORK_CARD_SCRIPT = WORKSPACE / "scripts" / "josh_work_card.py"
SEND_REPLY_SCRIPT = WORK_CARD_SCRIPT.with_name("send_josh_reply.py")
STALE_BOOTSTRAP_SECONDS = 120
MAX_UNACKED_PROMPT_AGE_SECONDS = 30
HEARTBEAT_SECONDS = 20
MAX_ACTIVE_CARD_SECONDS = 10 * 60
INTERPRETED_CARD_ADOPTION_WINDOW_SECONDS = 3 * 60
TERMINAL_CLOSE_LEASE_SECONDS = 30
STALE_FINAL_GATE_SECONDS = 90
TERMINAL_OUTBOX_MAX_ATTEMPTS = 12
TERMINAL_VISIBILITY_MAX_ATTEMPTS = 12
TERMINAL_VISIBILITY_MAX_AGE_SECONDS = 90
TERMINAL_CARD_STATUSES = {"done", "failed", "paused"}
MAX_TERMINAL_CARD_RECORDS = 100
PROGRESS_EVENT_SPECS = {
    "worker_started": {
        "summary": "Asynchronous worker started",
        "lifecycle_status": "progress",
        "phase": "active",
        "requires_verified_execution": False,
    },
    "verifying": {
        "summary": "Model execution verified; formatting final result",
        "lifecycle_status": "verifying",
        "phase": "verifying",
        "requires_verified_execution": True,
    },
}
MAX_PROGRESS_EVENT_BYTES = 4096
INBOX_REACTION_ATTEMPTS = 2
INBOX_REACTION_TIMEOUT_SECONDS = 3
INBOX_REACTION_RETRY_DELAY_SECONDS = 0.15
APPROVAL_ACTIONS_PATH = WORKSPACE / "memory" / "telegram_approval_actions.json"
WORK_CARD_STATE_PATH = WORKSPACE / "memory" / "josh_work_cards.json"
TERMINAL_OUTBOX_DIR = STATE_PATH.parent / "terminal-final-outbox"
DEFAULT_TERMINAL_VISIBILITY_OUTBOX_DIR = STATE_PATH.parent / "terminal-visibility-outbox"
TERMINAL_VISIBILITY_OUTBOX_DIR = DEFAULT_TERMINAL_VISIBILITY_OUTBOX_DIR
TELEGRAM_META_PATTERN = re.compile(r"Conversation info.*?```\s*\n\nSender .*?```\s*\n\n", re.S)
CONVERSATION_INFO_BLOCK_RE = re.compile(r"Conversation info \(untrusted metadata\):\s*```json\s*(\{.*?\})\s*```", re.S)
CARD_KEY_TS_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{6})")
CARD_KEY_SESSION_PATTERN = re.compile(r"^fast-ack-(.*)-\d{4}-\d{2}-\d{2}T\d{6}")
CURRENT_USER_REQUEST_PATTERN = re.compile(r"(?:^|\n)Current user request:\s*(.*?)\s*$", re.S)
JAIMES_MENTION_RE = re.compile(r"(?:^|[\s,.:;!?()\[\]{}])@jaimes(?=$|[\s,.:;!?()\[\]{}])", re.I)

if str(WORKSPACE / "scripts") not in sys.path:
    sys.path.insert(0, str(WORKSPACE / "scripts"))
if str(WORKSPACE / "mission-control" / "scripts") not in sys.path:
    sys.path.insert(0, str(WORKSPACE / "mission-control" / "scripts"))

try:
    from send_josh_reply import API_BASE, build_payload  # type: ignore
except Exception:  # noqa: BLE001
    API_BASE = ""
    build_payload = None

try:
    from agent_skill_router import select_skill, write_selection  # type: ignore
except Exception:  # noqa: BLE001
    select_skill = None
    write_selection = None

try:
    from telegram_channel_registry import owner_accepts, topics_for_owner  # type: ignore
    JOSH_CONTROL_CENTER_TOPICS = topics_for_owner("josh2", CONTROL_CENTER_CHAT_ID) or JOSH_CONTROL_CENTER_TOPICS
except Exception:  # noqa: BLE001
    owner_accepts = lambda owner, chat_id, thread_id, direct=False: (  # type: ignore
        owner == "josh2"
        and (direct or (str(chat_id) == CONTROL_CENTER_CHAT_ID and str(thread_id) in JOSH_CONTROL_CENTER_TOPICS))
    )

try:
    from telegram_gateway_lifecycle import (  # type: ignore
        GatewayLifecycle,
        LifecycleError,
        RolloutPolicy,
        classify_delivery_tier,
        render_live_card,
    )
except Exception:  # noqa: BLE001
    GatewayLifecycle = None  # type: ignore
    LifecycleError = RuntimeError  # type: ignore
    RolloutPolicy = None  # type: ignore
    classify_delivery_tier = None  # type: ignore
    render_live_card = None  # type: ignore

try:
    from objective_quality import objective_is_near_copy, semantic_reinterpretation  # type: ignore
except Exception:  # noqa: BLE001
    # Fail closed so an import problem cannot expose a prompt echo as an
    # apparent agent interpretation on Telegram or Control Tower.
    objective_is_near_copy = lambda _prompt, _objective: True
    semantic_reinterpretation = lambda _prompt: ""

try:
    from telegram_ux_helpers import (  # type: ignore
        approve_all_step as ux_approve_all_step,
        button_label as ux_button_label,
        final_action_steps as ux_final_action_steps,
        steps_are_all_applicable as ux_steps_are_all_applicable,
    )
except Exception:  # noqa: BLE001
    ux_approve_all_step = None
    ux_button_label = None
    ux_final_action_steps = None
    ux_steps_are_all_applicable = None


LIFECYCLE_ROLLOUT_PATH = WORKSPACE / "mission-control" / "config" / "telegram-lifecycle-rollout.json"
LIFECYCLE_PRIVATE_ROOT = HOME / ".openclaw" / "private" / "telegram-lifecycle"
_GATEWAY_LIFECYCLE = None


def gateway_lifecycle():
    global _GATEWAY_LIFECYCLE
    if GatewayLifecycle is None or RolloutPolicy is None:
        return None
    policy = RolloutPolicy.load(LIFECYCLE_ROLLOUT_PATH)
    if _GATEWAY_LIFECYCLE is None:
        _GATEWAY_LIFECYCLE = GatewayLifecycle(
            LIFECYCLE_PRIVATE_ROOT,
            rollout=policy,
            owner="josh2",
        )
    else:
        policy.validate()
        _GATEWAY_LIFECYCLE.rollout = policy
    return _GATEWAY_LIFECYCLE


def lifecycle_rollout_state() -> str:
    """Read only the bounded rollout state; malformed input fails closed."""
    try:
        payload = json.loads(LIFECYCLE_ROLLOUT_PATH.read_text())
    except (OSError, ValueError, TypeError):
        return "invalid"
    return str(payload.get("masterState") or "off").strip().lower()


def begin_gateway_lifecycle(
    *,
    key: str,
    origin_run_id: str,
    work_id: str,
    work_run_id: str,
    prompt: str,
) -> dict[str, Any]:
    """Persist classification before the first Telegram-visible effect.

    Rollout ``off`` leaves the legacy path byte-for-byte visible and creates no
    per-work v3 receipt.  Shadow and writer modes use only dashboard-safe
    classifier metadata; raw prompt text is never stored in the journal.  The
    explicit work ID preserves the identity already used by Control Tower.
    """
    lifecycle = gateway_lifecycle()
    if lifecycle is None:
        return {
            "error": "gateway-lifecycle-unavailable",
            "required": lifecycle_rollout_state() in {"josh2", "all"},
        }
    if (
        lifecycle.rollout.global_kill_switch
        or not (lifecycle.rollout.host_enabled or {}).get("josh2", True)
    ):
        return {"error": "gateway-kill-switch-active", "required": True}
    existing = lifecycle.read_work(work_id)
    if existing:
        writer = bool(existing.get("writerEnabled"))
        pinned_writer = bool(existing.get("writerAuthorityAtStart"))
        if pinned_writer and not writer:
            # An emergency host/global stop must not hand an in-flight v3 task
            # to the legacy visible writer.  Master rollback is different:
            # pinned receipts remain writerEnabled and continue draining.
            return {
                "lifecycle": lifecycle,
                "receipt": existing,
                "writer": False,
                "shadow": False,
                "error": "lifecycle-writer-safety-disabled",
                "required": True,
                "killed": True,
            }
        return {
            "lifecycle": lifecycle,
            "receipt": existing,
            "writer": writer,
            "shadow": bool(existing.get("shadowOnly")) and lifecycle.rollout.shadow_enabled("josh2"),
        }
    writer = bool(lifecycle.rollout.writer_enabled("josh2"))
    shadow = bool(lifecycle.rollout.shadow_enabled("josh2"))
    if not writer and not shadow:
        return {}
    try:
        classification = classify_delivery_tier(clean_prompt(prompt))
        receipt = lifecycle.start_work(
            origin_key=key,
            run_id=work_run_id,
            work_id=work_id,
            intake_agent="josh2",
            current_owner="josh2",
            surface_contract="telegram",
            text="",
            worker_route="pending",
            classification=classification,
        )
        if str(receipt.get("workId") or "") != work_id:
            raise LifecycleError("work-identity-mismatch")
        if receipt.get("phase") == "received":
            receipt = lifecycle.transition(
                work_id,
                "classified",
                expected_sequence=int(receipt["sequence"]),
                fencing_epoch=int(receipt["fencingEpoch"]),
                safe_payload={
                    "deliveryTier": int(receipt["deliveryTier"]),
                    "classifierReason": str(receipt["classifierReason"]),
                },
            )
        return {
            "lifecycle": lifecycle,
            "receipt": receipt,
            "writer": writer,
            "shadow": shadow,
        }
    except Exception as exc:  # noqa: BLE001 - fail closed before any send
        return {
            "error": type(exc).__name__,
            "required": writer,
        }


def refresh_gateway_receipt(context: dict[str, Any]) -> dict[str, Any]:
    lifecycle = context.get("lifecycle")
    receipt = context.get("receipt") or {}
    if lifecycle is None or not receipt.get("workId"):
        return receipt
    current = lifecycle.read_work(str(receipt["workId"])) or receipt
    context["receipt"] = current
    return current


def advance_gateway_phase(context: dict[str, Any], phase: str) -> dict[str, Any]:
    """Advance one legal phase idempotently using the current CAS fence."""
    lifecycle = context.get("lifecycle")
    receipt = refresh_gateway_receipt(context)
    current_phase = str(receipt.get("phase") or "") if receipt else ""
    already_beyond = {
        "classified": {"acknowledged", "working", "awaiting_input", "verifying"},
        "acknowledged": {"working", "awaiting_input", "verifying"},
        "working": {"awaiting_input", "verifying"},
    }
    if (
        lifecycle is None
        or not receipt
        or current_phase in {phase, "terminal"}
        or current_phase in already_beyond.get(phase, set())
    ):
        return receipt
    receipt = lifecycle.transition(
        str(receipt["workId"]),
        phase,
        expected_sequence=int(receipt["sequence"]),
        fencing_epoch=int(receipt["fencingEpoch"]),
        safe_payload={"phase": phase},
    )
    context["receipt"] = receipt
    return receipt


def set_gateway_worker_route(context: dict[str, Any], route: str) -> dict[str, Any]:
    lifecycle = context.get("lifecycle")
    receipt = refresh_gateway_receipt(context)
    if lifecycle is None or not receipt or not hasattr(lifecycle, "update_worker_route"):
        return receipt
    receipt = lifecycle.update_worker_route(
        str(receipt["workId"]),
        worker_route=str(route or "pending"),
        expected_owner="josh2",
        expected_sequence=int(receipt["sequence"]),
        fencing_epoch=int(receipt["fencingEpoch"]),
    )
    context["receipt"] = receipt
    return receipt


def claim_gateway_effect(context: dict[str, Any], kind: str) -> dict[str, Any]:
    """Reserve a visible writer effect before calling the trusted adapter."""
    if not context.get("writer"):
        return {"allowed": True, "legacy": True}
    lifecycle = context.get("lifecycle")
    receipt = refresh_gateway_receipt(context)
    if lifecycle is None or not receipt:
        return {"allowed": False, "state": "dead_letter", "reason": "lifecycle-unavailable"}
    return lifecycle.claim_effect(
        str(receipt["workId"]),
        kind,
        sequence=int(receipt["sequence"]),
        fencing_epoch=int(receipt["fencingEpoch"]),
    )


def finish_gateway_effect(
    context: dict[str, Any],
    claim: dict[str, Any],
    *,
    delivered: bool,
    indeterminate: bool = False,
    error_class: str = "",
) -> None:
    lifecycle = context.get("lifecycle")
    key = str(claim.get("idempotencyKey") or "")
    if lifecycle is None or not key or claim.get("legacy"):
        return
    state = "delivered" if delivered else "indeterminate" if indeterminate else "dead_letter"
    lifecycle.finish_effect(
        key,
        state=state,
        private_receipt="telegram-confirmed" if delivered else "",
        error_class=error_class,
    )
    refresh_gateway_receipt(context)


def gateway_public_fields(context: dict[str, Any]) -> dict[str, Any]:
    receipt = refresh_gateway_receipt(context)
    if not receipt:
        return {}
    return {
        "gateway_work_id": str(receipt.get("workId") or ""),
        "lifecycle_version": int(receipt.get("lifecycleVersion") or 0),
        "delivery_tier": int(receipt.get("deliveryTier") or 0),
        "classifier_reason": str(receipt.get("classifierReason") or ""),
        "lifecycle_sequence": int(receipt.get("sequence") or 0),
        "fencing_epoch": int(receipt.get("fencingEpoch") or 0),
        "lifecycle_writer_enabled": bool(context.get("writer")),
        "lifecycle_shadow": bool(context.get("shadow")),
    }


def gateway_context_for_card(card: dict[str, Any]) -> dict[str, Any]:
    work_id = str(card.get("work_id") or card.get("gateway_work_id") or "")
    # A Control Tower work identity may also be attached to legacy cards.
    # Only the explicit lifecycle version makes that identity a v3 journal
    # key; otherwise store initialization would make legacy delivery depend
    # on a filesystem surface it does not own.
    if not card_uses_lifecycle_v3(card) or not work_id:
        return {}
    lifecycle = gateway_lifecycle()
    if lifecycle is None:
        return {}
    receipt = lifecycle.read_work(work_id)
    if not receipt:
        return {}
    return {
        "lifecycle": lifecycle,
        "receipt": receipt,
        "writer": bool(receipt.get("writerEnabled")),
        "shadow": bool(receipt.get("shadowOnly")) and lifecycle.rollout.shadow_enabled("josh2"),
    }


def advance_gateway_progress(context: dict[str, Any], status: str) -> dict[str, Any]:
    """Advance one trusted progress fence without creating a visible effect."""
    lifecycle = context.get("lifecycle")
    receipt = refresh_gateway_receipt(context)
    if lifecycle is None or not receipt:
        raise LifecycleError("lifecycle-unavailable")
    phase = str(receipt.get("phase") or "")
    if phase == "terminal":
        raise LifecycleError("progress-after-terminal")
    if status == "verifying" and phase in {"working", "awaiting_input"}:
        receipt = lifecycle.transition(
            str(receipt["workId"]),
            "verifying",
            expected_sequence=int(receipt["sequence"]),
            fencing_epoch=int(receipt["fencingEpoch"]),
            safe_payload={"status": "verifying"},
        )
    else:
        receipt = lifecycle.record_progress(
            str(receipt["workId"]),
            expected_sequence=int(receipt["sequence"]),
            fencing_epoch=int(receipt["fencingEpoch"]),
            status=status,
        )
    context["receipt"] = receipt
    return receipt


def run_gateway_card_update(
    card: dict[str, Any],
    command: list[str],
    *,
    meta: dict[str, Any] | None = None,
    status: str = "progress",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fence one coalesced live-card edit before the trusted helper call."""
    if card.get("no_card_required"):
        return {"ok": False, "error": "card-effect-forbidden-for-delivery-tier"}
    if dry_run:
        return {"ok": True, "dry_run": True}
    context = gateway_context_for_card(card)
    receipt = context.get("receipt") or {}
    lifecycle_managed = int(card.get("lifecycle_version") or 0) >= 3
    if lifecycle_managed and not receipt:
        return {"ok": False, "error": "lifecycle-receipt-unavailable"}
    effect: dict[str, Any] = {"allowed": True, "legacy": True}
    if receipt:
        if receipt.get("writerAuthorityAtStart") and not receipt.get("writerEnabled"):
            return {"ok": False, "error": "lifecycle-writer-safety-disabled", "killed": True}
        try:
            receipt = advance_gateway_progress(context, status)
            if context.get("writer"):
                effect = claim_gateway_effect(context, "card_edit")
                if not effect.get("allowed"):
                    return {
                        "ok": False,
                        "error": "canonical-card-edit-fenced",
                        "delivery_state": str(effect.get("state") or ""),
                    }
        except Exception as exc:  # noqa: BLE001
            if context.get("writer"):
                return {"ok": False, "error": type(exc).__name__}
    result = run_cmd(with_work_card_target(command, meta))
    if context.get("writer") and effect.get("allowed"):
        finish_gateway_effect(
            context,
            effect,
            delivered=bool(result.get("ok")),
            indeterminate=not bool(result.get("ok")),
            error_class="card-edit-receipt-missing" if not result.get("ok") else "",
        )
    return result


def _safe_progress_fragment(value: Any, limit: int = 120) -> str:
    return re.sub(r"[^A-Za-z0-9._:/@+ -]", "", str(value or ""))[:limit].strip()


def _progress_job_matches_card(job: dict[str, Any], card: dict[str, Any], run_id: str) -> bool:
    origin = job.get("origin") if isinstance(job.get("origin"), dict) else {}
    return bool(
        str(job.get("jobId") or "") == str(card.get("job_id") or "")
        and str(job.get("workId") or "") == str(card.get("work_id") or "")
        and str(job.get("ledgerRunId") or "") == str(card.get("ledger_run_id") or "")
        and str(job.get("originClaimHash") or "") == str(card.get("origin_claim_hash") or "")
        and str(origin.get("runId") or "") == run_id
        and str(origin.get("cardKey") or "") == str(card.get("key") or "")
        and (
            not card.get("telegram_chat_id")
            or str(origin.get("chatId") or "") == str(card.get("telegram_chat_id") or "")
        )
        and (
            not card.get("telegram_thread_id")
            or str(origin.get("threadId") or "") == str(card.get("telegram_thread_id") or "")
        )
    )


def progress_event_from_stdin() -> dict[str, Any]:
    """Apply a fixed, origin-bound worker progress event through lifecycle v3."""
    raw = sys.stdin.read(MAX_PROGRESS_EVENT_BYTES + 1)
    if len(raw.encode("utf-8")) > MAX_PROGRESS_EVENT_BYTES:
        return {"ok": False, "status": "progress-event-too-large"}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"ok": False, "status": "invalid-progress-event"}
    if not isinstance(payload, dict) or set(payload) != {"runId", "progressCode"}:
        return {"ok": False, "status": "invalid-progress-event-fields"}
    if not isinstance(payload.get("runId"), str) or not isinstance(payload.get("progressCode"), str):
        return {"ok": False, "status": "invalid-progress-event-types"}
    run_id = payload["runId"]
    progress_code = payload["progressCode"]
    spec = PROGRESS_EVENT_SPECS.get(progress_code)
    if not re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,256}", run_id) or not spec:
        return {"ok": False, "status": "unsupported-progress-event"}

    with fast_ack_state_lock():
        state = load_json(STATE_PATH, {})
        active = state.get("active_cards") if isinstance(state, dict) else {}
        card = active.get(run_id) if isinstance(active, dict) else None
        if not isinstance(card, dict):
            return {"ok": False, "status": "run-card-not-ready"}
        if not card.get("coordinator_owned") or not str(card.get("job_id") or ""):
            return {"ok": False, "status": "progress-origin-not-coordinator-owned"}
        if str(card.get("status") or "").lower() in TERMINAL_CARD_STATUSES | {
            "closing-before-final", "awaiting-final-gate",
        }:
            return {"ok": False, "status": "progress-after-terminal"}
        card_snapshot = copy.deepcopy(card)

    # The coordinator status subprocess and every Telegram/card operation run
    # outside the fast-ack state lock. The exact private job snapshot, not the
    # worker's input, supplies all model, route, and target facts.
    job = coordinator_job_snapshot(str(card_snapshot.get("job_id") or ""))
    if not job or not _progress_job_matches_card(job, card_snapshot, run_id):
        return {"ok": False, "status": "progress-origin-mismatch"}
    if str(job.get("status") or "") != "running":
        return {"ok": False, "status": "worker-not-running"}
    actual = job.get("actual") if isinstance(job.get("actual"), dict) else {}
    if spec["requires_verified_execution"] and not (
        bool(actual.get("executionVerified")) and bool(actual.get("modelVerified"))
    ):
        return {"ok": False, "status": "execution-not-verified"}
    route = job.get("route") if isinstance(job.get("route"), dict) else {}
    if spec["requires_verified_execution"]:
        model_label = (
            f"provider={_safe_progress_fragment(actual.get('actualProvider'))}; "
            f"model={_safe_progress_fragment(actual.get('actualModel'))}; "
            f"worker={_safe_progress_fragment(actual.get('actualWorker'))}; "
            f"host={_safe_progress_fragment(actual.get('actualHost'))}"
        )
    else:
        model_label = (
            f"planned provider={_safe_progress_fragment(route.get('provider'))}; "
            f"model={_safe_progress_fragment(route.get('model'))}; "
            f"worker={_safe_progress_fragment(route.get('worker'))}; "
            f"host={_safe_progress_fragment(route.get('host'))}"
        )
    if spec["requires_verified_execution"]:
        route_label = (
            f"verified route={_safe_progress_fragment(route.get('routeId'))}; "
            f"provider={_safe_progress_fragment(actual.get('actualProvider'))}; "
            f"worker={_safe_progress_fragment(actual.get('actualWorker'))}; "
            f"host={_safe_progress_fragment(actual.get('actualHost'))}"
        )
    else:
        route_label = (
            f"planned route={_safe_progress_fragment(route.get('routeId'))}; "
            f"reason={_safe_progress_fragment(route.get('routingReason'))}; "
            f"fallback={_safe_progress_fragment(route.get('fallback') or 'none')}"
        )
    summary = str(spec["summary"])
    command = [
        sys.executable,
        str(WORK_CARD_SCRIPT),
        "update",
        "--key",
        str(card_snapshot.get("key") or ""),
        "--model",
        model_label,
        "--route",
        route_label,
        "--now",
        summary,
        "--done",
        summary,
        "--no-brain-feed",
    ]
    meta = {
        "telegram_chat_id": str(card_snapshot.get("telegram_chat_id") or ""),
        "telegram_thread_id": str(card_snapshot.get("telegram_thread_id") or ""),
    }
    if card_snapshot.get("no_card_required"):
        context = gateway_context_for_card(card_snapshot)
        receipt = context.get("receipt") or {}
        if int(card_snapshot.get("lifecycle_version") or 0) >= 3 and not receipt:
            return {"ok": False, "status": "lifecycle-receipt-unavailable"}
        if receipt.get("writerAuthorityAtStart") and not receipt.get("writerEnabled"):
            return {"ok": False, "status": "lifecycle-writer-safety-disabled"}
        try:
            if receipt:
                advance_gateway_progress(context, str(spec["lifecycle_status"]))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "status": "progress-fence-failed", "error": type(exc).__name__}
        result: dict[str, Any] = {"ok": True, "no_card_required": True}
    else:
        result = run_gateway_card_update(
            card_snapshot,
            command,
            meta=meta,
            status=str(spec["lifecycle_status"]),
        )
    if not result.get("ok"):
        return {"ok": False, "status": "progress-card-update-failed"}

    changed_at = utc_now()
    state_accepted = False
    with fast_ack_state_lock():
        state = load_json(STATE_PATH, {})
        active = state.get("active_cards") if isinstance(state, dict) else {}
        current = active.get(run_id) if isinstance(active, dict) else None
        if (
            isinstance(current, dict)
            and str(current.get("key") or "") == str(card_snapshot.get("key") or "")
            and str(current.get("job_id") or "") == str(card_snapshot.get("job_id") or "")
            and str(current.get("work_id") or "") == str(card_snapshot.get("work_id") or "")
            and str(current.get("ledger_run_id") or "") == str(card_snapshot.get("ledger_run_id") or "")
            and str(current.get("origin_claim_hash") or "") == str(card_snapshot.get("origin_claim_hash") or "")
            and str(current.get("status") or "").lower() not in TERMINAL_CARD_STATUSES | {
                "closing-before-final", "awaiting-final-gate",
            }
        ):
            current["last_progress_at"] = changed_at
            current["last_card_update_at"] = changed_at
            if spec["requires_verified_execution"]:
                current["runtime_model"] = _safe_progress_fragment(actual.get("actualModel"))
                current["route"] = route_label
                current["route_verified"] = True
            save_json(STATE_PATH, state)
            state_accepted = True
    if state_accepted:
        publish_josh(
            str(card_snapshot.get("objective") or "Josh 2.0 Telegram task"),
            "active",
            summary,
            work_id=str(card_snapshot.get("work_id") or ""),
            run_id=str(card_snapshot.get("ledger_run_id") or ""),
            phase=str(spec["phase"]),
            model_id=_safe_progress_fragment(
                actual.get("actualModel") if spec["requires_verified_execution"] else route.get("model")
            ),
            route_verified=bool(spec["requires_verified_execution"]),
            origin_claim_hash=str(card_snapshot.get("origin_claim_hash") or ""),
            brain_feed=False,
        )
    return {
        "ok": True,
        "status": "progress-recorded" if state_accepted else "progress-recorded-before-terminal",
        "progress_code": progress_code,
        "no_card_required": bool(card_snapshot.get("no_card_required")),
    }


def parse_telegram_target_from_key(key: str) -> dict[str, Any]:
    match = TELEGRAM_GROUP_TOPIC_RE.search(key or "")
    if match:
        return {
            "telegram_chat_id": match.group(1),
            "telegram_thread_id": match.group(2),
            "telegram_session_key": key,
        }
    if "telegram:direct:" in key or "telegram:dm:" in key:
        return {"telegram_chat_id": "6218150306", "telegram_session_key": key}
    return {"telegram_session_key": key}


def normalize_telegram_chat_id(chat_id: Any) -> Any:
    text = str(chat_id or "").strip()
    if text.startswith("telegram:"):
        text = text.split(":", 1)[1].strip()
    return int(text) if text.lstrip("-").isdigit() else text


def apply_telegram_target(payload: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    if not meta:
        return payload
    chat_id = meta.get("telegram_chat_id")
    if chat_id:
        payload["chat_id"] = normalize_telegram_chat_id(chat_id)
    thread_id = meta.get("telegram_thread_id")
    if thread_id and str(thread_id).strip() != "1":
        payload["message_thread_id"] = int(thread_id) if str(thread_id).isdigit() else thread_id
    return payload


def target_chat_id(meta: dict[str, Any] | None = None) -> Any:
    if meta and meta.get("telegram_chat_id"):
        return normalize_telegram_chat_id(meta.get("telegram_chat_id"))
    if build_payload is None:
        return ""
    try:
        return build_payload("", None, silent=True).get("chat_id", "")
    except Exception:
        return ""


def api_post(method: str, payload: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    if not API_BASE:
        return {"ok": False, "error": "missing API base"}
    req = urllib.request.Request(
        f"{API_BASE}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def send_chat_action(action: str = "typing", meta: dict[str, Any] | None = None) -> None:
    chat_id = target_chat_id(meta)
    if not chat_id:
        return
    payload = apply_telegram_target({"chat_id": chat_id, "action": action}, meta)
    api_post("sendChatAction", payload, timeout=6)


def send_message_draft(draft_id: int, text: str = "", meta: dict[str, Any] | None = None) -> None:
    """Disabled by default: draft lane renders badly/overlaps in Telegram."""
    if os.environ.get("JOSH_TELEGRAM_DRAFTS", "").lower() not in {"1", "true", "yes"}:
        return
    chat_id = target_chat_id(meta)
    if not chat_id:
        return
    safe = " ".join((text or "").replace("\n", " · ").split())[:280]
    payload = apply_telegram_target({"chat_id": chat_id, "draft_id": draft_id, "text": safe}, meta)
    api_post("sendMessageDraft", payload, timeout=6)


def fast_ack_enabled() -> bool:
    raw = os.environ.get("JOSH_TELEGRAM_FAST_ACK", "").strip().lower()
    #JAIMES: default fast ack on for Josh Telegram so `👀` lands before normal task execution unless explicitly disabled.
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def live_cards_enabled(meta: dict[str, Any] | None = None) -> bool:
    """Default live cards on only for the owned group Inbox; allow explicit override."""
    raw = os.environ.get("JOSH_TELEGRAM_LIVE_CARDS", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool(
        meta
        and str(meta.get("telegram_chat_id") or "") == CONTROL_CENTER_CHAT_ID
        and str(meta.get("telegram_thread_id") or "") == "1"
    )


def send_initial_ack(text: str, timeout: int = 15, meta: dict[str, Any] | None = None) -> str:
    if not API_BASE or build_payload is None:
        return ""
    payload = apply_telegram_target(build_payload(text, None, silent=True), meta)
    req = urllib.request.Request(
        f"{API_BASE}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return str(data.get("result", {}).get("message_id") or "")
    except Exception:
        return ""


def prompt_conversation_info(prompt: str) -> dict[str, Any]:
    match = CONVERSATION_INFO_BLOCK_RE.search(prompt or "")
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(1))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def send_prompt_reaction(prompt: str, emoji: str = "👀", timeout: int = 10, meta: dict[str, Any] | None = None) -> bool:
    info = prompt_conversation_info(prompt)
    message_id = str(info.get("message_id") or "").strip()
    if not message_id:
        return False
    chat_id = str(info.get("chat_id") or meta.get("telegram_chat_id") if meta else info.get("chat_id") or "").strip()
    return send_message_reaction(message_id, chat_id=chat_id, emoji=emoji, timeout=timeout, meta=meta)


def send_message_reaction(message_id: str, chat_id: str = "", emoji: str = "👀", timeout: int = 10, meta: dict[str, Any] | None = None) -> bool:
    if not chat_id:
        chat_id = str(target_chat_id(meta) or "")
    if not chat_id or not message_id:
        return False
    payload = {"chat_id": normalize_telegram_chat_id(chat_id), "message_id": int(message_id) if message_id.isdigit() else message_id, "reaction": [{"type": "emoji", "emoji": emoji}]}
    if meta:
        payload = apply_telegram_target(payload, meta)
    result = api_post("setMessageReaction", payload, timeout=timeout)
    return bool(result.get("ok"))


def requires_inbox_reaction(message_id: str, meta: dict[str, Any] | None = None) -> bool:
    """Return true only for an exact, owned Inbox message with a Telegram ID."""
    return bool(
        str(message_id or "").isdigit()
        and meta
        and str(meta.get("telegram_chat_id") or "") == CONTROL_CENTER_CHAT_ID
        and str(meta.get("telegram_thread_id") or "") == "1"
    )


def place_inbox_reaction(message_id: str, meta: dict[str, Any] | None = None) -> bool:
    """Place the required Inbox eyes reaction with a short bounded retry."""
    for attempt in range(INBOX_REACTION_ATTEMPTS):
        if send_message_reaction(
            message_id,
            emoji="👀",
            timeout=INBOX_REACTION_TIMEOUT_SECONDS,
            meta=meta,
        ):
            return True
        if attempt + 1 < INBOX_REACTION_ATTEMPTS:
            time.sleep(INBOX_REACTION_RETRY_DELAY_SECONDS)
    return False


def edit_message(message_id: str, text: str, timeout: int = 15, meta: dict[str, Any] | None = None) -> bool:
    if not message_id or not API_BASE or build_payload is None:
        return False
    payload = apply_telegram_target(build_payload(text, None, silent=True), meta)
    payload["message_id"] = message_id
    req = urllib.request.Request(
        f"{API_BASE}/editMessageText",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return bool(data.get("ok"))
    except Exception:
        return False


def delete_message(message_id: str, timeout: int = 15, meta: dict[str, Any] | None = None) -> bool:
    if not message_id:
        return False
    chat_id = target_chat_id(meta)
    if not chat_id:
        return False
    payload = apply_telegram_target({"chat_id": chat_id, "message_id": message_id}, meta)
    result = api_post("deleteMessage", payload, timeout=timeout)
    return bool(result.get("ok"))


def send_buttons_message(text: str, buttons: list, timeout: int = 15, meta: dict[str, Any] | None = None) -> str:
    if not API_BASE or build_payload is None:
        return ""
    payload = apply_telegram_target(build_payload(text, buttons, silent=True), meta)
    req = urllib.request.Request(
        f"{API_BASE}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return str(data.get("result", {}).get("message_id") or "")
    except Exception:
        return ""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # #JAIMES: use a unique temp file so concurrent pollers do not clobber the same ack-state write.
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            tmp = Path(handle.name)
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        os.chmod(path, 0o600)
    finally:
        if tmp and tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def protocol_lock_path(effect_path: Path) -> Path:
    text = str(effect_path)
    suffix = ".effects.json"
    return Path(text[:-len(suffix)] + ".protocol.lock") if text.endswith(suffix) else Path(text + ".protocol.lock")


@contextmanager
def telegram_effect_lock(effect_path: Path, timeout: float = 0.25):
    lock_path = protocol_lock_path(effect_path)
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.mkdir(lock_path, 0o700)
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 2.0:
                    os.rmdir(lock_path)
                    continue
            except (FileNotFoundError, OSError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("telegram effect protocol lock unavailable")
            time.sleep(0.005)
    try:
        yield
    finally:
        try:
            os.rmdir(lock_path)
        except FileNotFoundError:
            pass


def atomic_protocol_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temp = Path(handle.name)
        handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temp.replace(path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temp.exists():
            temp.unlink()


def telegram_effect_protocol(args: argparse.Namespace) -> dict[str, Path] | None:
    effect_path = Path(str(getattr(args, "effect_path", "") or ""))
    cancel_path = Path(str(getattr(args, "cancel_path", "") or ""))
    if not str(getattr(args, "effect_path", "") or "") or not str(getattr(args, "cancel_path", "") or ""):
        return None
    return {
        "effect": effect_path,
        "cancel": cancel_path,
        "surface_deadline_ms": int(getattr(args, "surface_deadline_ms", 0) or 0),
    }


def begin_telegram_surface(protocol: dict[str, Path] | None, stage: str) -> bool:
    """Fence cancellation and durably checkpoint immediately before a send."""
    if not protocol:
        return True
    effect_path = protocol["effect"]
    cancel_path = protocol["cancel"]
    try:
        with telegram_effect_lock(effect_path):
            if cancel_path.exists():
                return False
            current = load_json(effect_path, {})
            if not isinstance(current, dict):
                current = {}
            atomic_protocol_json(effect_path, {
                **current,
                "version": 1,
                "state": "attempting",
                "stage": stage,
                "surface_started_at": current.get("surface_started_at") or utc_now(),
                "updated_at": utc_now(),
            })
    except TimeoutError:
        return False
    return True


def telegram_claim_not_cancelled(protocol: dict[str, Path] | None) -> bool:
    """Check cancellation for idempotent acknowledgement work without fencing fallback."""
    if not protocol:
        return True
    try:
        with telegram_effect_lock(protocol["effect"]):
            return not protocol["cancel"].exists()
    except TimeoutError:
        return False


def update_telegram_effect(protocol: dict[str, Path] | None, **values: Any) -> None:
    if not protocol:
        return
    effect_path = protocol["effect"]
    try:
        with telegram_effect_lock(effect_path):
            current = load_json(effect_path, {})
            if not isinstance(current, dict):
                current = {}
            atomic_protocol_json(effect_path, {**current, **values, "version": 1, "updated_at": utc_now()})
    except TimeoutError:
        pass


@contextmanager
def fast_ack_state_lock():
    """Serialize cross-process fast-ack state merges without holding network work."""
    #JAIMES: Keep every fast-ack read/merge/write inside this separate flock;
    # a stale poller must never replace a coordinator claim written mid-poll.
    lock_path = STATE_PATH.with_suffix(STATE_PATH.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_fast_ack_state_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a locked state snapshot and an immutable merge base."""
    with fast_ack_state_lock():
        state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    return state, copy.deepcopy(state)


POLL_STATE_FIELDS = {
    "last_checked_at",
    "direct_session_id",
    "model",
    "status",
    "last_error",
    "last_error_at",
    "last_sent_at",
    "last_result",
    "latest_pending_ack",
}


def _three_way_poll_value(base: dict[str, Any], candidate: dict[str, Any], latest: dict[str, Any], key: str) -> tuple[bool, Any]:
    """Resolve one poll-owned value without reverting a concurrent writer."""
    missing = object()
    base_value = base.get(key, missing)
    candidate_value = candidate.get(key, missing)
    latest_value = latest.get(key, missing)
    if candidate_value == base_value:
        return latest_value is not missing, latest_value
    if latest_value == base_value:
        return candidate_value is not missing, candidate_value
    # Both writers changed the value. Preserve the newer on-disk writer; poll
    # fields are advisory and must not erase a concurrent claim or ack handoff.
    return latest_value is not missing, latest_value


def merge_poll_state(candidate: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """Merge a stale poll snapshot into current state while preserving claims."""
    with fast_ack_state_lock():
        latest = load_json(STATE_PATH, {})
        if not isinstance(latest, dict):
            latest = {}
        merged = copy.deepcopy(latest)

        for key in POLL_STATE_FIELDS:
            keep, value = _three_way_poll_value(base, candidate, latest, key)
            if keep:
                merged[key] = value
            else:
                merged.pop(key, None)

        for key, limit in (("acked_prompt_events", 200), ("processed_progress_events", 300)):
            combined = {
                str(item)
                for source in (latest.get(key), candidate.get(key))
                for item in (source or [])
                if str(item)
            }
            merged[key] = sorted(combined)[-limit:]

        base_cards = base.get("active_cards") if isinstance(base.get("active_cards"), dict) else {}
        candidate_cards = candidate.get("active_cards") if isinstance(candidate.get("active_cards"), dict) else {}
        latest_cards = latest.get("active_cards") if isinstance(latest.get("active_cards"), dict) else {}
        merged_cards = copy.deepcopy(latest_cards)
        missing = object()
        for card_key in set(base_cards) | set(candidate_cards):
            base_card = base_cards.get(card_key, missing)
            candidate_card = candidate_cards.get(card_key, missing)
            latest_card = latest_cards.get(card_key, missing)
            if candidate_card == base_card:
                continue
            if latest_card == base_card:
                if candidate_card is missing:
                    merged_cards.pop(card_key, None)
                else:
                    merged_cards[card_key] = copy.deepcopy(candidate_card)
                continue
            if candidate_card is missing:
                # A concurrent writer changed this card after the poll snapshot;
                # never prune that newer record.
                continue
            if isinstance(latest_card, dict) and isinstance(candidate_card, dict):
                merged_card = {**latest_card, **copy.deepcopy(candidate_card)}
                latest_status = str(latest_card.get("status") or "").lower()
                candidate_status = str(candidate_card.get("status") or "").lower()
                reconciled_close = bool(
                    candidate_card.get("terminal_close_reconciled_at")
                    and candidate_status in TERMINAL_CARD_STATUSES
                )
                if latest_status in {*TERMINAL_CARD_STATUSES, "closing-before-final", "awaiting-final-gate"} and not reconciled_close:
                    # A poll snapshot loaded before the final-delivery gate
                    # must not reopen a terminal/closing card during merge.
                    for field in (
                        "status",
                        "ended_at",
                        "last_progress_at",
                        "last_card_update_at",
                        "terminal_close_started_at",
                        "terminal_closed_before_final_at",
                    ):
                        if field in latest_card:
                            merged_card[field] = latest_card[field]
                if reconciled_close:
                    merged_card.pop("terminal_close_started_at", None)
                merged_cards[card_key] = merged_card
            elif latest_card is missing:
                merged_cards[card_key] = copy.deepcopy(candidate_card)
        merged["active_cards"] = merged_cards
        save_json(STATE_PATH, merged)
        return merged


def persist_claim_state(stable: str, card: dict[str, Any], last_claim: dict[str, Any]) -> dict[str, Any]:
    """Add one coordinator claim without replacing poller-owned state."""
    with fast_ack_state_lock():
        state = load_json(STATE_PATH, {})
        if not isinstance(state, dict):
            state = {}
        active = state.setdefault("active_cards", {})
        if not isinstance(active, dict):
            active = {}
            state["active_cards"] = active
        active[stable] = copy.deepcopy(card)
        state["last_claim_at"] = utc_now()
        state["last_claim"] = copy.deepcopy(last_claim)
        save_json(STATE_PATH, state)
        return state


def record_fast_ack_error(error_name: str) -> None:
    """Record a watcher exception without racing a per-message claim."""
    with fast_ack_state_lock():
        state = load_json(STATE_PATH, {})
        if not isinstance(state, dict):
            state = {}
        state["last_error_at"] = utc_now()
        state["last_error"] = error_name
        save_json(STATE_PATH, state)


def positive_telegram_message_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.isdigit() and int(text) > 0 else ""


def exact_control_center_inbox(meta: dict[str, Any] | None = None) -> bool:
    return bool(
        meta
        and str(meta.get("telegram_chat_id") or "") == CONTROL_CENTER_CHAT_ID
        and str(meta.get("telegram_thread_id") or "") == "1"
    )


def _json_receipt(raw: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(raw or ""))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def work_card_state_receipt(card_key: str) -> dict[str, Any]:
    state = load_json(WORK_CARD_STATE_PATH, {})
    cards = state.get("cards") if isinstance(state, dict) else {}
    card = cards.get(card_key) if isinstance(cards, dict) else {}
    if not isinstance(card, dict):
        card = {}
    header_message_id = positive_telegram_message_id(card.get("header_message_id"))
    declared_contract = str(card.get("surface_contract") or "")
    if declared_contract == "live-only-v2":
        header_required = False
    elif declared_contract == "header-live-v1":
        header_required = True
    elif "header_required" in card:
        header_required = bool(card.get("header_required"))
    else:
        # Missing state and legacy state retain the stricter old requirement.
        header_required = True
    return {
        "header_message_id": header_message_id,
        "live_message_id": positive_telegram_message_id(card.get("message_id")),
        "final_message_id": positive_telegram_message_id(card.get("final_message_id")),
        "status": str(card.get("status") or ""),
        "header_delivery_status": str(card.get("header_delivery_status") or ""),
        "live_delivery_status": str(card.get("live_delivery_status") or ""),
        "final_delivery_status": str(card.get("final_delivery_status") or ""),
        "header_required": header_required,
        "surface_contract": str(card.get("surface_contract") or ("header-live-v1" if header_required else "live-only-v2")),
    }


def parse_work_card_start_receipt(card_key: str, result: dict[str, Any]) -> dict[str, Any]:
    """Read the helper receipt, falling back only to the same persisted card."""
    payload = _json_receipt(result.get("stdout"))
    persisted = work_card_state_receipt(card_key)
    header_message_id = positive_telegram_message_id(payload.get("header_message_id")) or persisted["header_message_id"]
    live_message_id = positive_telegram_message_id(payload.get("message_id") or payload.get("live_message_id")) or persisted["live_message_id"]
    surface_contract = str(payload.get("surface_contract") or persisted["surface_contract"])
    declared_header_required = payload.get("header_required") if "header_required" in payload else None
    if surface_contract == "live-only-v2":
        header_required = False
        declaration_consistent = declared_header_required in {None, False}
    elif surface_contract == "header-live-v1":
        header_required = True
        declaration_consistent = declared_header_required in {None, True}
    else:
        # Unknown and legacy receipts keep the stricter historical surface.
        header_required = True
        declaration_consistent = not surface_contract
    return {
        "command_ok": bool(result.get("ok")),
        "header_message_id": header_message_id,
        "live_message_id": live_message_id,
        "surface_ok": bool(
            declaration_consistent
            and live_message_id
            and (header_message_id or not header_required)
        ),
        "header_required": header_required,
        "surface_contract": surface_contract,
        "persisted_status": persisted["status"],
        "header_delivery_status": persisted["header_delivery_status"],
        "live_delivery_status": persisted["live_delivery_status"],
        "surface_indeterminate": "indeterminate" in {
            persisted["header_delivery_status"].lower(),
            persisted["live_delivery_status"].lower(),
        },
    }


def safe_same_key_card_retry(card_key: str, receipt: dict[str, Any]) -> bool:
    """Retry only when every required prior surface is durable and unambiguous."""
    persisted = work_card_state_receipt(card_key)
    return bool(
        (persisted["header_message_id"] or not persisted["header_required"])
        and not persisted["live_message_id"]
        and persisted["header_delivery_status"].lower() != "indeterminate"
        and persisted["live_delivery_status"].lower() != "indeterminate"
        and persisted["status"].lower() not in TERMINAL_CARD_STATUSES
        and not receipt.get("surface_ok")
    )


def receipt_requires_header(receipt: dict[str, Any]) -> bool:
    """Honor the versioned live-only contract while keeping old receipts safe."""
    contract = str(receipt.get("surface_contract") or "")
    if contract in {"live-only-v2", "tier-1-final-v3", "tier-2-final-v3"}:
        return False
    return True


def run_work_card_start(cmd: list[str]) -> dict[str, Any]:
    try:
        # The child has one bounded live-card send, plus an optional diagnostic
        # header send. Keep the parent alive long enough for its timeout
        # handler to persist an indeterminate receipt instead of killing it
        # after request write.
        return dict(run_cmd(cmd, timeout=25))
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": "work-card start timed out"}
    except Exception as exc:  # noqa: BLE001 - keep the native fallback available
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": type(exc).__name__}


def local_time_label() -> str:
    return dt.datetime.now().astimezone().strftime("%H:%M:%S %Z")


def parse_utc(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def card_started_at(card: dict[str, Any]) -> dt.datetime | None:
    started = parse_utc(card.get("started_at"))
    if started:
        return started
    key = str(card.get("key") or "")
    match = CARD_KEY_TS_PATTERN.search(key)
    if not match:
        return None
    try:
        return dt.datetime.strptime("".join(match.groups()), "%Y-%m-%d%H%M%S").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def card_session_id(card: dict[str, Any]) -> str:
    explicit = str(card.get("session_id") or "")
    if explicit:
        return explicit
    key = str(card.get("key") or "")
    match = CARD_KEY_SESSION_PATTERN.search(key)
    return match.group(1) if match else ""


def work_card_origin(card: dict[str, Any]) -> tuple[str, str]:
    chat_id = normalize_telegram_chat_id(card.get("chat_id") or card.get("telegram_chat_id"))
    thread_id = str(card.get("thread_id") or card.get("telegram_thread_id") or "").strip()
    return str(chat_id or ""), thread_id


def interpreted_work_card_candidates(
    meta: dict[str, Any] | None,
    started_at: dt.datetime | None = None,
    excluded_keys: set[str] | None = None,
    max_age_seconds: int = INTERPRETED_CARD_ADOPTION_WINDOW_SECONDS,
) -> list[tuple[str, dict[str, Any]]]:
    """Return recent visible cards that contain an agent-written objective."""
    work_state = load_json(WORK_CARD_STATE_PATH, {})
    cards = work_state.get("cards") if isinstance(work_state, dict) else {}
    if not isinstance(cards, dict):
        return []
    meta = meta or {}
    expected_chat = str(normalize_telegram_chat_id(meta.get("telegram_chat_id")) or "")
    expected_thread = str(meta.get("telegram_thread_id") or "").strip()
    excluded = excluded_keys or set()
    now = dt.datetime.now(dt.timezone.utc)
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for key, raw_card in cards.items():
        if key in excluded or not isinstance(raw_card, dict):
            continue
        if str(raw_card.get("status") or "").lower() not in {"running", "active"}:
            continue
        title = str(raw_card.get("title") or "").strip()
        if not title or title.lower() in {"josh 2.0 telegram task", "telegram task"}:
            continue
        card_chat, card_thread = work_card_origin(raw_card)
        if expected_chat and card_chat != expected_chat:
            continue
        if expected_thread and card_thread != expected_thread:
            continue
        candidate_started = parse_utc(raw_card.get("started_at") or raw_card.get("updated_at"))
        if not candidate_started:
            continue
        if started_at:
            delta = (candidate_started - started_at).total_seconds()
            if delta < -5 or delta > INTERPRETED_CARD_ADOPTION_WINDOW_SECONDS:
                continue
            distance = abs(delta)
        else:
            age = (now - candidate_started).total_seconds()
            if age < -5 or age > max_age_seconds:
                continue
            distance = age
        candidates.append((distance, str(key), copy.deepcopy(raw_card)))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [(key, card) for _, key, card in candidates]


def adopt_interpreted_work_cards(
    state: dict[str, Any],
    meta: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Bind an agent-created objective card to a watcher run awaiting interpretation."""
    active = state.get("active_cards")
    if not isinstance(active, dict):
        return []
    owned_keys = {
        str(card.get("key") or "")
        for card in active.values()
        if isinstance(card, dict)
        and not card.get("requires_objective_interpretation")
        and str(card.get("status") or "").lower() not in TERMINAL_CARD_STATUSES
    }
    adopted: list[dict[str, str]] = []
    pending = sorted(
        (
            (run_id, card)
            for run_id, card in active.items()
            if isinstance(card, dict)
            and card.get("requires_objective_interpretation")
            and str(card.get("status") or "").lower() not in TERMINAL_CARD_STATUSES
        ),
        key=lambda item: str(item[1].get("started_at") or ""),
    )
    for run_id, card in pending:
        card_meta = {
            "telegram_chat_id": card.get("telegram_chat_id") or (meta or {}).get("telegram_chat_id"),
            "telegram_thread_id": card.get("telegram_thread_id") or (meta or {}).get("telegram_thread_id"),
        }
        candidates = interpreted_work_card_candidates(
            card_meta,
            started_at=card_started_at(card),
            excluded_keys=owned_keys,
        )
        if not candidates:
            continue
        key, visible = candidates[0]
        objective = str(visible.get("title") or "").strip()
        card.update({
            "key": key,
            "objective": objective,
            "model": str(visible.get("model") or card.get("model") or DEFAULT_MODEL),
            "route": str(visible.get("route") or card.get("route") or DEFAULT_ROUTE),
            "header_message_id": positive_telegram_message_id(visible.get("header_message_id")),
            "live_message_id": positive_telegram_message_id(visible.get("message_id")),
            "card_start_ok": bool(visible.get("message_id")),
            "header_required": str(visible.get("surface_contract") or "") != "live-only-v2",
            "surface_contract": str(visible.get("surface_contract") or "header-live-v1"),
            "requires_objective_interpretation": False,
            "objective_interpreted": True,
            "adopted_at": utc_now(),
            "status": "active",
        })
        owned_keys.add(key)
        adopted.append({"run_id": str(run_id), "key": key, "objective": objective})
    return adopted


def session_metadatas() -> list[dict[str, Any]]:
    sessions = load_json(SESSIONS_PATH, {})
    if not isinstance(sessions, dict):
        return []

    candidates: list[dict[str, Any]] = []
    for key, value in sessions.items():
        if not isinstance(value, dict):
            continue
        session_id = value.get("sessionId") or value.get("session_id")
        if not session_id:
            continue
        channel = value.get("channel") or value.get("platform") or value.get("origin", {}).get("platform")
        if channel != "telegram" and "telegram" not in key:
            continue
        target = parse_telegram_target_from_key(key)
        topic = str(target.get("telegram_thread_id") or "")
        is_direct = "telegram:direct:" in key or "telegram:dm:" in key
        if not is_direct and not owner_accepts(
            "josh2",
            target.get("telegram_chat_id"),
            topic,
            direct=False,
        ):
            continue
        normalized = dict(value)
        normalized["sessionId"] = session_id
        normalized["channel"] = "telegram"
        normalized["model"] = value.get("model") or DEFAULT_MODEL
        normalized.update(target)
        try:
            normalized["_sort_updated_at"] = int(value.get("updatedAt") or 0)
        except Exception:
            normalized["_sort_updated_at"] = 0
        candidates.append(normalized)
    return sorted(candidates, key=lambda item: item.get("_sort_updated_at") or 0, reverse=True)


def session_metadata() -> dict[str, Any]:
    """Compatibility accessor for callers that need only the freshest lane."""
    candidates = session_metadatas()
    return candidates[0] if candidates else {}


def session_paths_for(session_id: str, meta: dict[str, Any] | None = None) -> list[Path]:
    """Return possible trajectory paths for direct and Telegram-topic sessions."""
    candidates: list[Path] = [SESSION_DIR / f"{session_id}.trajectory.jsonl"]
    thread_id = str((meta or {}).get("telegram_thread_id") or "")
    if thread_id:
        candidates.append(SESSION_DIR / f"{session_id}-topic-{thread_id}.trajectory.jsonl")
    candidates.extend(
        sorted(
            SESSION_DIR.glob(f"{session_id}-topic-*.trajectory.jsonl"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
            reverse=True,
        )
    )
    seen: set[str] = set()
    paths: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def first_existing_session_path(session_id: str, meta: dict[str, Any] | None = None) -> Path:
    for path in session_paths_for(session_id, meta):
        if path.exists():
            return path
    return SESSION_DIR / f"{session_id}.trajectory.jsonl"


def recent_prompt_events(session_id: str, meta: dict[str, Any] | None = None) -> list[dict[str, str]]:
    path = first_existing_session_path(session_id, meta)
    if not path.exists():
        return []
    events: list[dict[str, str]] = []
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 256_000))
            raw = handle.read().decode("utf-8", errors="ignore")
    except Exception:
        return []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except Exception:
            continue
        if item.get("type") != "prompt.submitted":
            continue
        ts = str(item.get("ts") or "")
        data = item.get("data") or {}
        if ts:
            events.append({
                "session_id": session_id,
                "ts": ts,
                "run_id": str(item.get("runId") or ""),
                "seq": str(item.get("seq") or ""),
                "prompt": str(data.get("prompt") or ""),
            })
    return events


def friendly_tool_name(name: str) -> str:
    raw = (name or "").split(".")[-1].replace("_", " ").strip().lower()
    labels = {
        "exec command": "local check",
        "apply patch": "file edit",
        "parallel": "parallel checks",
        "tool search tool": "tool lookup",
    }
    return labels.get(raw, raw or "task step")


def safe_compact(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"(sk-[A-Za-z0-9_-]{8,}|xai-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]+|sb_secret_[A-Za-z0-9_-]+|sb_publishable_[A-Za-z0-9_-]+)", "[redacted]", text)
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def plain_command_summary(value: Any) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"^/bin/(?:zsh|bash)\s+-lc\s+", "", text).strip().strip("'\"")
    text = re.sub(r"^cd\s+[^&;]+(?:&&|;)\s*", "", text).strip().strip("'\"")
    lower = text.lower()
    if "state_visibility_guard.py" in lower:
        return "refreshing Control Tower and Brain Feed visibility"
    if "update_mission_control.py" in lower:
        return "regenerating Control Tower dashboard data"
    if "ecosystem_health_sweep.py" in lower:
        return "checking Josh 2.0, JAIMES, J.AI.N, and Control Tower health"
    if "xai_agent.py" in lower:
        return "checking the xAI/Grok helper connection"
    if lower.startswith("jq ") or " jq " in lower:
        return "reading the dashboard health summary"
    if lower.startswith("scp "):
        return "copying a needed helper script to the worker host"
    if "agent_publish.py" in lower:
        return "publishing the latest status to Brain Feed"
    if "open_mission_control_kiosk" in lower:
        return "bringing Control Tower back onto the Josh 2.0 screen"
    if "openclaw update status" in lower:
        return "checking whether OpenCLAW has an update available"
    if "openclaw update" in lower:
        return "updating OpenCLAW and installed plugins"
    if "openclaw doctor" in lower:
        return "checking OpenCLAW configuration for repairable issues"
    if "openclaw gateway status" in lower:
        return "checking that the OpenCLAW gateway is running"
    if "openclaw gateway" in lower:
        return "restarting or repairing the OpenCLAW gateway"
    if "openclaw health" in lower:
        return "checking Josh 2.0 auth, gateway, Telegram, jobs, and Control Tower"
    if "openclaw infer" in lower or "model run" in lower:
        return "testing that Josh 2.0 can reach the selected model"
    if "npm run build" in lower:
        return "building Control Tower to catch UI/runtime errors"
    if "python3" in lower and "mission-control/scripts/" in lower:
        script = lower.split("mission-control/scripts/", 1)[1].split()[0].strip("'\"")
        return f"running the {script.replace('_', ' ').replace('.py', '')} helper"
    if lower.startswith("date "):
        return "checking the current time on Josh 2.0"
    return safe_compact(text, 120) or "checking the next needed system signal"


def tool_arguments(data: dict[str, Any]) -> dict[str, Any]:
    args = data.get("arguments") or {}
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {"text": args}
    return args if isinstance(args, dict) else {}


def tool_call_detail(name: str, data: dict[str, Any]) -> str:
    tool = friendly_tool_name(name)
    args = tool_arguments(data)
    query = args.get("query") or args.get("q") or args.get("search")
    if query:
        return f"{tool}: searching {safe_compact(query, 90)} to verify context"
    pattern = args.get("pattern")
    if pattern:
        return f"{tool}: finding {safe_compact(pattern, 90)} in the current files/output"
    cmd = args.get("cmd") or args.get("command")
    if cmd:
        return f"{tool}: {plain_command_summary(cmd)}"
    path = args.get("path") or args.get("file") or args.get("ref_id")
    if path:
        return f"{tool}: inspecting {safe_compact(path, 90)}"
    prompt = args.get("prompt") or args.get("text")
    if prompt:
        return f"{tool}: evaluating {safe_compact(prompt, 90)}"
    return f"{tool}: checking the next relevant signal"


def content_preview(data: dict[str, Any]) -> str:
    items = data.get("contentItems") or data.get("content") or []
    if isinstance(items, list):
        texts = []
        for item in items[:2]:
            if isinstance(item, dict):
                texts.append(str(item.get("text") or item.get("content") or ""))
            else:
                texts.append(str(item))
        raw = " ".join(texts)
    else:
        raw = str(items)
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            raw = str(parsed.get("summary") or parsed.get("content") or parsed.get("result") or raw)
    except Exception:
        pass
    raw = re.sub(r"<<<EXTERNAL_UNTRUSTED_CONTENT.*?>>>", " ", raw)
    raw = re.sub(r"Source:\s*[^-]+---", " ", raw)
    return safe_compact(raw, 100)


def tool_result_detail(name: str, data: dict[str, Any], call_detail: str = "") -> str:
    tool = friendly_tool_name(name)
    if data.get("success") is False:
        return f"{tool}: internal check returned no actionable result"
    if call_detail and ":" in call_detail and tool in {"local check", "file edit"}:
        return f"{tool}: completed {call_detail.split(':', 1)[1].strip()}"
    preview = content_preview(data)
    if re.search(r"(?i)(?:tool.*failed|agent\)\s+failed|🛠️|⚠️.*failed|traceback|exit\s+\d+)", preview):
        return f"{tool}: internal check returned no actionable result"
    if "web search" in tool:
        return f"{tool}: found relevant web context" + (f": {preview}" if preview else "")
    if "memory search" in tool:
        return f"{tool}: found prior context" if preview else f"{tool}: checked memory; no key context found"
    if preview:
        return f"{tool}: {preview}"
    if call_detail and ":" in call_detail:
        return f"{tool}: completed {call_detail.split(':', 1)[1].strip()}"
    return f"{tool}: completed"


def recent_progress_events(session_id: str, meta: dict[str, Any] | None = None) -> list[dict[str, str]]:
    path = first_existing_session_path(session_id, meta)
    if not path.exists():
        return []
    events: list[dict[str, str]] = []
    call_details: dict[str, str] = {}
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 384_000))
            raw = handle.read().decode("utf-8", errors="ignore")
    except Exception:
        return []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except Exception:
            continue
        event_type = str(item.get("type") or "")
        if event_type not in {"tool.call", "tool.result", "model.completed"}:
            continue
        data = item.get("data") or {}
        name = str(data.get("name") or data.get("toolName") or event_type)
        friendly_name = friendly_tool_name(name)
        summary = ""
        if event_type == "tool.call":
            detail = tool_call_detail(name, data)
            call_details[str(data.get("toolCallId") or "")] = detail
            summary = f"Running {detail}"
        elif event_type == "tool.result":
            call_detail = call_details.get(str(data.get("toolCallId") or ""), "")
            summary = f"Finished {tool_result_detail(name, data, call_detail)}"
        elif event_type == "model.completed":
            summary = "Final response sent"
        final_text = ""
        if event_type == "model.completed":
            texts = data.get("assistantTexts") or data.get("assistant_texts") or []
            if isinstance(texts, list) and texts:
                final_text = str(texts[0] or "")
        events.append({
            "event_id": f"{item.get('runId') or ''}:{item.get('seq') or ''}:{event_type}",
            "run_id": str(item.get("runId") or ""),
            "ts": str(item.get("ts") or ""),
            "type": event_type,
            "summary": summary,
            "final_text": final_text,
        })
    return events


def is_ack_only_final(text: str) -> bool:
    """True when the model stopped after echoing only the helper-owned ack."""
    if (text or "").strip() == "👀":
        return True
    normalized = re.sub(r"[^a-z]+", " ", (text or "").lower()).strip()
    return normalized in {
        "recieved determining objective",
        "received determining objective",
    }


def mitigation_steps_from_text(text: str) -> list[str]:
    if ux_final_action_steps is not None:
        return ux_final_action_steps(text)[1]
    if not text:
        return []
    match = re.search(r"(?im)^\s*(?:\*\*)?(?:Approval needed|Mitigation steps for approval):?(?:\*\*)?\s*$", text)
    if not match:
        return []
    body = text[match.end():]
    steps: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"(?i)^\*{0,2}(complete|what was done|tldr|issues|appropriate next steps|approval options|objective|status|next|model|control tower|context|sources?|references?)\b", line):
            break
        line = re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", line).strip()
        line = clean_approval_step(line)
        if not line or line.lower() in {"n/a", "na", "none", "not applicable"}:
            continue
        steps.append(line)
        if len(steps) >= 5:
            break
    return steps


def clean_approval_step(step: str) -> str:
    text = " ".join((step or "").split())
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[-*•\s]+", "", text).strip()
    text = text.strip("*_ ")
    text = re.sub(r"^\*{1,2}|\*{1,2}$", "", text).strip()
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return text


def actionable_approval_step(step: str) -> bool:
    normalized = " ".join(clean_approval_step(step).strip().lower().split())
    if normalized in {"", "n/a", "na", "none", "not applicable", "no action needed"}:
        return False
    if re.match(r"^(context|complete|what was done|issues|appropriate next steps|approval needed|approval options|objective|status|next|model|route|using|sources?|references?)\b", normalized):
        return False
    if re.match(r"^https?://", normalized):
        return False
    if re.match(r"^context:\s*\d+%$", normalized):
        return False
    if re.match(r"^(say|send|reply)\s+[\"'`]", normalized) or re.search(r"\bif you want\b", normalized):
        return False
    return True


def approval_callback(objective: str, step: str, index: int) -> str:
    digest = hashlib.sha1(f"josh2|{objective}|{step}|{index}".encode("utf-8")).hexdigest()[:10]
    return f"approve:josh2:{digest}:{index}"


def save_approval_actions(actions: dict[str, Any]) -> None:
    existing = load_json(APPROVAL_ACTIONS_PATH, {})
    if not isinstance(existing, dict):
        existing = {}
    existing.update(actions)
    save_json(APPROVAL_ACTIONS_PATH, existing)


def approval_button_label(step: str) -> str:
    label = clean_approval_step(step)
    label = re.sub(r"(?i)^(optional:\s*)", "", label).strip()
    label = re.sub(r"(?i)^(approve|approval to|approval for)\s+", "", label).strip()
    label = label.rstrip(".")
    label = label[:38] + ("..." if len(label) > 38 else "")
    return f"Approve: {label or 'next action'}"


def send_approval_options(objective: str, final_text: str, dry_run: bool = False) -> str:
    mode = "approval"
    steps: list[str]
    if ux_final_action_steps is not None:
        mode, steps = ux_final_action_steps(final_text)
    else:
        steps = [step for step in mitigation_steps_from_text(final_text) if actionable_approval_step(step)]
    steps = [step for step in steps if actionable_approval_step(step)]
    if not steps:
        return ""
    actions: dict[str, Any] = {}
    buttons = []
    if ux_steps_are_all_applicable is not None and ux_steps_are_all_applicable(mode, steps, final_text):
        all_step = ux_approve_all_step(steps) if ux_approve_all_step is not None else "Run all listed steps"
        callback = approval_callback(objective, all_step, 0)
        actions[callback] = {
            "agent": "josh2",
            "objective": objective,
            "step": all_step,
            "created_at": utc_now(),
        }
        buttons.append([{"text": "Approve all", "callback_data": callback}])
    prefix = "Approve" if mode == "approval" else "Next"
    for index, step in enumerate(steps[:4], start=1):
        callback = approval_callback(objective, step, index)
        actions[callback] = {
            "agent": "josh2",
            "objective": objective,
            "step": step,
            "created_at": utc_now(),
        }
        if ux_button_label is not None:
            label = ux_button_label(step, prefix=prefix, limit=46)
        else:
            label = approval_button_label(step)
        buttons.append([{"text": label, "callback_data": callback}])
    buttons.append([{"text": "Hold / no action", "callback_data": "next:hold"}])
    if dry_run:
        return "dry-run-approval-buttons"
    save_approval_actions(actions)
    title = "Approval options:" if mode == "approval" else "Next step options:"
    return send_buttons_message(title, buttons)


def clean_prompt(prompt: str) -> str:
    raw = prompt or ""
    match = CURRENT_USER_REQUEST_PATTERN.search(raw)
    if match:
        text = match.group(1).strip()
    else:
        text = TELEGRAM_META_PATTERN.sub("", raw).strip()
    text = re.sub(r"\[media attached:[^\n]*\]\s*", "", text, flags=re.I)
    text = re.sub(r"media://inbound/[^\s)]+", "", text, flags=re.I)
    text = re.sub(r"\(\s*image(?:/[a-z0-9.+-]+)?\s*\)", "", text, flags=re.I)
    text = " ".join(text.split())
    if not text:
        return "Review attached image"
    return text


def prompt_layout_for_objective(prompt: str) -> str:
    """Clean transport metadata while preserving pasted-card line boundaries."""
    raw = prompt or ""
    match = CURRENT_USER_REQUEST_PATTERN.search(raw)
    text = match.group(1).strip() if match else TELEGRAM_META_PATTERN.sub("", raw).strip()
    text = re.sub(r"\[media attached:[^\n]*\]\s*", "", text, flags=re.I)
    text = re.sub(r"media://inbound/[^\s)]+", "", text, flags=re.I)
    text = re.sub(r"\(\s*image(?:/[a-z0-9.+-]+)?\s*\)", "", text, flags=re.I)
    return text or "Review attached image"


def is_hold_request(prompt: str) -> bool:
    text = clean_prompt(prompt).strip().lower()
    return text in {"next:hold", "hold", "hold / no action", "no action", "pause", "stop"}


OBJECTIVE_RULES = [
    (("post-restart readiness", "route-readiness", "route readiness", "workhorse readiness", "readiness pressure test", "readiness test"), "Run readiness test"),
    (("telegram/route", "telegram route", "route repair", "route-readiness repair", "readiness repair"), "Repair Telegram routing"),
    (("keychain", "cookie.codex", "codex cookie"), "Fix Codex keychain alert"),
]

LEADING_REQUEST_RE = re.compile(
    r"^(please\s+)?(can you|could you|would you|may you|make sure|check|review|look at|help me|i want you to)\s+",
    re.I,
)

CONTROL_TOWER_MARKERS = ("control tower", "mission control", "misson control", "brain feed", "live work board", "work board", "dashboard", "kiosk")
CONTROL_TOWER_ACTION_RE = re.compile(
    r"\b(check|review|look at|inspect|open|bring up|pulled up|pull up|verify|refresh|fix|resolve|clean up|close|show|make sure)\b",
    re.I,
)
CONTROL_TOWER_STATUS_RE = re.compile(
    r"\b(why|what'?s going on|what is going on|what'?s happening|how come|keep saying|keeps saying|stuck|been switching|switching in and out)\b",
    re.I,
)


def current_request_text(text: str) -> str:
    """Select the actionable ask, never a trailing safety constraint."""
    embedded_card_row = re.compile(
        r"^(?:[🎯🤖📊⏱️✅⚠️➡️🔐]\s*)?"
        r"(?:objective|model|steps?|eta|complete|what was done|issues|"
        r"appropriate next steps|approval needed|status|progress)\s*(?::|$)",
        re.I,
    )
    eligible: list[str] = []
    skip_objective_value = False
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"(?:🎯\s*)?objective\s*", line, re.I):
            skip_objective_value = True
            continue
        if skip_objective_value:
            skip_objective_value = False
            continue
        if embedded_card_row.match(line) or line.startswith(("```", "- ")):
            continue
        eligible.append(line)
    parts = [
        p.strip(" ,.-")
        for p in re.split(r"(?<=[.!?])\s+|\n+", "\n".join(eligible))
        if p.strip()
    ]
    normalized_parts = [
        re.sub(r"^read[- ]only\s+acceptance\s+check\s*:\s*", "", part, flags=re.I).strip()
        for part in parts
    ]
    constraint_only = re.compile(
        r"^(?:(?:please\s+)?(?:make|do)\s+no\s+changes|"
        r"(?:please\s+)?do\s+not\s+(?:make|apply|change|edit)\b|"
        r"read[- ]only(?:\s+only)?)[.!]?$",
        re.I,
    )
    intent = re.compile(
        r"\b(?:assess|audit|check|evaluate|examine|find|fix|implement|inspect|"
        r"investigate|repair|review|run|test|validate|verify|build|add|remove|update)\b",
        re.I,
    )
    leading_intent = re.compile(
        r"^(?:(?:please\s+)?|(?:can|could|would)\s+you\s+)(?:assess|audit|check|"
        r"evaluate|examine|find|fix|implement|inspect|investigate|repair|review|"
        r"run|test|validate|verify|build|add|remove|update)\b",
        re.I,
    )
    candidates = [part for part in normalized_parts if part and intent.search(part) and not constraint_only.fullmatch(part)]
    if not candidates:
        return " ".join(eligible)
    selected = max(candidates, key=lambda part: (3 if leading_intent.search(part) else 2, len(part.split())))
    has_no_change_constraint = any(
        constraint_only.fullmatch(part)
        or re.search(r"\b(?:read[- ]only|make no changes|do not make changes)\b", part, re.I)
        for part in parts
    )
    if has_no_change_constraint and not re.search(r"\b(?:read[- ]only|without (?:making )?changes)\b", selected, re.I):
        selected = f"{selected} read-only"
    return selected


OBJECTIVE_MAX_WORDS = 12
OBJECTIVE_MAX_CHARS = 80
OBJECTIVE_DANGLING_WORDS = {
    "a", "an", "and", "or", "the", "that", "with", "for", "to", "in", "on", "at", "across", "from"
}


#JAIMES: derive the visible objective from the current request's action and target before any topic label.
def bounded_objective(action: str, target: str, outcome: str = "") -> str:
    """Keep action, target, and outcome intact inside the mobile header budget."""
    target_words = target.strip(" .?!").split()
    action_words = action.split()
    outcome_words = outcome.strip(" .?!").split()
    maximum_outcome_words = max(0, OBJECTIVE_MAX_WORDS - len(action_words) - 1)
    outcome_words = outcome_words[:maximum_outcome_words]
    while len(outcome_words) > 1 and outcome_words[-1].lower() in OBJECTIVE_DANGLING_WORDS:
        outcome_words.pop()
    reserved = len(action_words) + len(outcome_words)
    target_budget = max(1, OBJECTIVE_MAX_WORDS - reserved)
    if len(target_words) > target_budget and target_words[0].lower() in {"the", "this", "that"}:
        target_words = target_words[1:]
    target_words = target_words[:target_budget]
    while len(target_words) > 1 and target_words[-1].lower() in OBJECTIVE_DANGLING_WORDS:
        target_words.pop()

    def render() -> str:
        return " ".join(
            part for part in (" ".join(action_words), " ".join(target_words), " ".join(outcome_words)) if part
        )

    candidate = render()
    while len(candidate) > OBJECTIVE_MAX_CHARS and len(target_words) > 1:
        target_words.pop()
        while len(target_words) > 1 and target_words[-1].lower() in OBJECTIVE_DANGLING_WORDS:
            target_words.pop()
        candidate = render()
    while len(candidate) > OBJECTIVE_MAX_CHARS and len(outcome_words) > 1:
        outcome_words.pop()
        while len(outcome_words) > 1 and outcome_words[-1].lower() in OBJECTIVE_DANGLING_WORDS:
            outcome_words.pop()
        candidate = render()
    return candidate[:OBJECTIVE_MAX_CHARS].rstrip(" ,;:-")


def verification_outcome(target: str) -> str:
    if re.search(r"\b(work|works|working|operate|operates|correct|correctly|intended|pass|passes)\b", target, re.I):
        return ""
    if re.search(r"\b(changes|updates|fixes|cards|messages|responses|workflows|behaviors|features|integrations)\b", target, re.I):
        return "work as intended"
    if re.search(r"\b(change|update|fix|card|message|response|workflow|behavior|feature|integration)\b", target, re.I):
        return "works as intended"
    if re.fullmatch(r"(?:the\s+)?(?:JOSHeX|JAIMES|J\.A\.I\.N|JAIN|Josh\s+2\.0|OpenCLAW|Telegram)", target, re.I):
        return "operates correctly"
    # A bare "Verify <target>" merely mirrors an imperative request. Keep the
    # target, but make the desired outcome explicit so the card states what
    # success means in the agent's own words.
    return "meets the intended requirements"


def action_specific_objective(text: str) -> str:
    """Paraphrase an explicit current-request action before broad topic rules.

    Agent names and generic nouns such as ``update`` are context, not intent.
    Keeping the concrete action and target together prevents a request like
    "Testing the new JOSHeX changes" from collapsing into an ecosystem label.
    """
    request = " ".join((text or "").split()).strip(" .?!")
    courtesy_patterns = (
        r"^please\s+",
        r"^(?:can|could|would|may)\s+you(?:\s+please)?\s+",
        r"^i\s+want\s+you\s+to(?:\s+please)?\s+",
    )
    for _ in range(3):
        before = request
        for pattern in courtesy_patterns:
            request = re.sub(pattern, "", request, count=1, flags=re.I)
        if request == before:
            break

    why = re.match(r"^why\s+(?:did|does|is|was|has|have|are|were)\s+(.+)$", request, re.I)
    if why:
        target = re.sub(r"\bchange$", "changed", why.group(1).strip(), flags=re.I)
        return bounded_objective("Investigate why", target)
    what = re.match(r"^what\s+(?:did|does|is|was|has|have|are|were)\s+(.+)$", request, re.I)
    if what:
        target = re.sub(r"\bchange\b", "changed", what.group(1).strip(), count=1, flags=re.I)
        return bounded_objective("Explain what", target)
    yes_no = re.match(
        r"^(?:is|are|was|were|does|do|did|has|have)\s+(.+?)\s+"
        r"(?:working|work|worked|correct|correctly|ready|healthy|fixed|complete|completed|pass|passing)$",
        request,
        re.I,
    )
    if yes_no:
        target = yes_no.group(1).strip()
        return bounded_objective("Verify whether", target, verification_outcome(target) or "works as intended")

    rewrites = (
        (r"^(?:test(?:ing)?|validate|validating|verify|confirm|check|make sure)\s+", "Verify", True),
        (r"^(?:deep[- ]?scan|scan|review|audit)\s+", "Audit", False),
        (r"^(?:look at|inspect)\s+", "Inspect", False),
        (r"^(?:examine|assess)\s+", "Assess", False),
        (r"^(?:find out|find|investigate)\s+", "Investigate", False),
        (r"^(?:fix|repair|resolve)\s+", "Repair", False),
        (r"^(?:add|implement)\s+", "Implement", False),
        (r"^(?:update|upgrade)\s+", "Update", False),
        (r"^(?:sync|synchronize|reconcile|align)\s+", "Synchronize", False),
        (r"^(?:triage)\s+", "Triage", False),
        (r"^(?:run|execute)\s+", "Run", False),
        (r"^(?:tell me|explain|remind me)\s+", "Explain", False),
    )
    for pattern, action, add_outcome in rewrites:
        if not re.match(pattern, request, flags=re.I):
            continue
        target = re.sub(pattern, "", request, count=1, flags=re.I).strip(" .?!")
        if not target:
            return ""
        explicit_outcome = re.search(
            r"\s+(?:so that|so (?:i|we) can|to ensure|in order to|and make sure|and ensure)\s+",
            target,
            re.I,
        )
        if explicit_outcome:
            outcome = target[explicit_outcome.start():].strip()
            target = target[:explicit_outcome.start()].strip()
        else:
            outcome = verification_outcome(target) if add_outcome else ""
        return bounded_objective(action, target, outcome)
    return ""


def topic_objective(lowered: str) -> str:
    """Use category labels only when entity and intent context both agree."""
    if "jaimes" in lowered and any(marker in lowered for marker in ("strict", "settings", "prevent him", "following my instructions")):
        return "Tune JAIMES instruction-following settings"
    if any(marker in lowered for marker in ("crypto", "wallet", "portfolio", "profit target", "trade card", "trading autonomy")):
        return "Tune JAIMES crypto action mode"
    fantasy_context = any(marker in lowered for marker in ("fantasy baseball", "espn", "roster", "add/drop", "waiver"))
    if fantasy_context and any(marker in lowered for marker in ("next week", "next matchup", "week 7", "future lineup")):
        return "Check ESPN next-week lineup"
    if fantasy_context:
        return "Sync fantasy baseball roster"
    if "telegram" in lowered and any(marker in lowered for marker in ("button", "buttons", "work card", "live card", "formatting", "reaction")):
        return "Tune Telegram UX"
    if "openclaw" in lowered and any(marker in lowered for marker in ("upgrade", "update", "latest version")):
        return "Update OpenCLAW stack"
    if any(marker in lowered for marker in ("automation", "automations", "cron", "crons", "schedule", "jobs")):
        return "Review automation schedule"
    if "sorare" in lowered and any(marker in lowered for marker in ("lineup", "game week", "gw")):
        return "Review Sorare lineup state"
    return ""


def summarize_objective(text: str) -> str:
    clean = " ".join(current_request_text(text).split())
    lowered = clean.lower()
    if "telegram" in lowered and "health" in lowered and "read-only" in lowered:
        return "Assess Telegram health read-only"
    if "objective" in lowered and any(
        marker in lowered for marker in ("copy", "quote", "similar", "own words", "interpret", "paraphrase")
    ):
        return "Make agent task objectives reflect interpreted intent"
    if "correct objective" in lowered and "current task" in lowered:
        return "Fix current-task objective mapping"
    if "inbox" in lowered and any(
        marker in lowered
        for marker in ("routing", "model route", "model routing", "brain feed", "gateway", "health check", "workflow")
    ):
        return "Verify Inbox routing and health"
    if (
        "gmail" in lowered
        or "mailbox" in lowered
        or ("email" in lowered and any(marker in lowered for marker in ("inbox", "triage", "unread", "messages")))
    ):
        return "Triage Gmail inbox"
    specific = action_specific_objective(clean)
    if specific:
        return specific
    topic = topic_objective(lowered)
    if topic:
        return topic
    for markers, summary in OBJECTIVE_RULES:
        if any(marker in lowered for marker in markers):
            return summary
    if any(marker in lowered for marker in CONTROL_TOWER_MARKERS):
        # If Josh asks why the board says something, explain/fix the status
        # instead of re-entering a generic "Check Control Tower state" loop.
        if CONTROL_TOWER_STATUS_RE.search(lowered):
            return "Explain Control Tower status"
        if CONTROL_TOWER_ACTION_RE.search(lowered):
            return "Check Control Tower state"
    for markers, summary in ():
        if any(marker in lowered for marker in markers):
            return summary
    clean = LEADING_REQUEST_RE.sub("", clean).strip(" .")
    # Turn an unmatched request into a short operator objective rather than a
    # clipped copy of the user's sentence. This stays deterministic so the
    # required live card does not wait on another model call.
    clean = re.split(r"\s+(?:so that|so I can|and then|and make|and ensure)\b", clean, maxsplit=1, flags=re.I)[0]
    verb_rewrites = (
        (r"^(?:deep[- ]?scan|scan)\s+", "Audit "),
        (r"^(?:check|confirm|make sure)\s+", "Verify "),
        (r"^(?:review|audit)\s+", "Audit "),
        (r"^(?:look at|inspect)\s+", "Inspect "),
        (r"^(?:examine|assess)\s+", "Assess "),
        (r"^(?:find|find out|investigate)\s+", "Investigate "),
        (r"^(?:fix|repair|resolve)\s+", "Repair "),
        (r"^(?:add|implement)\s+", "Implement "),
        (r"^(?:tell me|explain|remind me)\s+", "Explain "),
    )
    rewritten = ""
    for pattern, replacement in verb_rewrites:
        if re.match(pattern, clean, flags=re.I):
            rewritten = re.sub(pattern, replacement, clean, count=1, flags=re.I)
            break
    generic_object = re.sub(r"^(?:this|the)\s+", "", clean, flags=re.I)
    clean = rewritten or f"Handle {generic_object}"
    words = clean.split()
    if len(words) > 8:
        clean = " ".join(words[:8])
    return clean[:80] or "Handle Telegram task"


def objective_from_prompt(prompt: str) -> str:
    text = clean_prompt(prompt)
    lowered = text.lower().strip()
    if is_hold_request(prompt):
        return "Hold / no action"
    if lowered.startswith("/overview"):
        return "Run Control Tower overview"
    if lowered.startswith("/steer"):
        rest = text[len("/steer"):].strip()
        return rest or "Handle steering request"
    if lowered.startswith("/status"):
        return "Report Josh 2.0 status"
    if lowered.startswith("/mc"):
        return "Check Control Tower"
    if lowered.startswith("/models"):
        return "Show active model routing"
    if lowered.startswith("/route"):
        return "Show routing options"
    if lowered.startswith("/daily"):
        return "Run daily overview"
    return summarize_objective(prompt_layout_for_objective(prompt))


def classify_privacy(prompt: str) -> str:
    text = clean_prompt(prompt).lower()
    if any(marker in text for marker in ("espn", "fantasy baseball", "roster", "lineup", "matchup", "waiver", "trade")):
        return "agent-private"
    private_markers = {
        "password", "cookie", "oauth", "token", "keychain", "gmail", "email",
        "calendar", "account", "login", "sorare", "browser", "chrome",
        "bank", "stripe", "payment", "private", "personal account",
    }
    return "sensitive-account" if any(marker in text for marker in private_markers) else "dashboard-safe"


def classify_task_type(prompt: str) -> str:
    text = clean_prompt(prompt).lower()
    if any(marker in text for marker in ("espn", "fantasy baseball", "roster", "lineup", "matchup", "waiver", "trade")):
        return "connected-account-triage"
    if any(marker in text for marker in ("keychain", "cookie.codex", "codex cookie", "alert on your screen")):
        return "macos-keychain-alert"
    if any(marker in text for marker in ("breaking", "latest news", "x.com", "twitter", "market narrative", "sentiment", "current events")):
        return "current-events"
    if any(marker in text for marker in ("summarize", "summary", "digest", "overview", "readability", "review", "explain", "analyze")):
        return "summary"
    if any(marker in text for marker in ("fix", "patch", "update", "install", "upgrade", "test", "build", "repo", "code", "script")):
        return "repo-patch"
    if any(marker in text for marker in ("health", "status", "mission control", "brain feed", "sync")):
        return "summary"
    return "connected-account-triage" if classify_privacy(prompt) != "dashboard-safe" else "summary"


def display_model_route(route_result: dict[str, Any], fallback_model: str) -> tuple[str, str]:
    model_route = route_result.get("modelRoute") if isinstance(route_result, dict) else {}
    if not isinstance(model_route, dict):
        return fallback_model, DEFAULT_ROUTE
    first_stop = str(model_route.get("firstStop") or "codex")
    provider = str(model_route.get("provider") or first_stop)
    raw_model = str(model_route.get("model") or "").strip()
    model = raw_model if raw_model and raw_model.lower() not in {provider.lower(), first_stop.lower(), "codex"} else fallback_model
    reason = str(model_route.get("reason") or "").strip()
    route_label = str(model_route.get("routeLabel") or "").strip()
    model_lower = model.lower()
    agent = str(model_route.get("owner") or route_result.get("agent") or "josh")
    if "gemini" in model_lower and "pro" in model_lower:
        friendly_model = "Gemini Pro"
    elif "gemini" in model_lower:
        friendly_model = "Gemini Flash"
    elif "grok" in model_lower or first_stop == "xai":
        friendly_model = "Grok"
    elif first_stop == "openrouter":
        friendly_model = "OpenRouter"
    else:
        friendly_model = "Codex"
    if first_stop == "gemini":
        why = "safe summary/review"
        fallback = "Codex/OpenAI if tools or private execution are needed"
    elif first_stop == "xai":
        why = "public current-events"
        fallback = "Codex/OpenAI for execution"
    elif first_stop == "openrouter":
        why = "fallback/specialist check"
        fallback = "Codex/OpenAI for execution"
    else:
        friendly_model = "Codex"
        why = "execution/private fit"
        fallback = "Gemini was not selected"
    lane = route_label or first_stop
    if "gemini auth is blocked" in reason.lower() or "unsupported-client" in reason.lower():
        why = "Gemini unavailable; using Codex/OpenAI for execution"
        fallback = "Gemini blocked: UNSUPPORTED_CLIENT / unsupported CLI subscription client"
    detail = f"provider={provider}; model={model}; lane={lane}; why={why}; fallback={fallback}"
    if reason:
        detail += f"; router_reason={reason}"
    return detail, f"provider={provider}; model={model}; lane={lane}; owner={agent}; fallback={fallback}"


def auto_route_for_prompt(prompt: str, fallback_model: str) -> dict[str, Any]:
    task_type = classify_task_type(prompt)
    privacy = classify_privacy(prompt)
    if COORDINATOR_SCRIPT.exists():
        try:
            result = run_cmd([
                "python3",
                str(COORDINATOR_SCRIPT),
                "route",
                "--privacy",
                privacy,
                "--telemetry",
            ], timeout=12, input_text=prompt)
            if result.get("ok") and result.get("stdout"):
                route_result = json.loads(str(result["stdout"]))
                provider = str(route_result.get("provider") or "unknown")
                model = str(route_result.get("model") or fallback_model)
                worker = str(route_result.get("worker") or "unknown-worker")
                host = str(route_result.get("host") or "unknown-host")
                reason = str(route_result.get("routingReason") or "auto route")
                fallback = str(route_result.get("fallback") or "none")
                role = str(route_result.get("role") or route_result.get("routeId") or "coordinator")
                display = f"planned provider={provider}; model={model}; worker={worker}; host={host}; why={reason}; fallback={fallback}"
                route_line = f"planned provider={provider}; model={model}; lane={role}; worker={worker}; host={host}; reason={reason}; fallback={fallback}"
                return {
                    "model": display,
                    "route": route_line,
                    "task_type": task_type,
                    "privacy": privacy,
                    "route_plan": route_result,
                }
        except Exception:
            pass
    cmd = [
        "python3",
        "mission-control/scripts/agent_route.py",
        "--task-type",
        task_type,
        "--title",
        "Josh 2.0 Telegram task",
        "--objective",
        "Josh 2.0 Telegram task",
        "--privacy",
        privacy,
        "--requester",
        "josh2",
        "--prefer",
        "josh",
    ]
    if task_type in {"summary", "digest", "daily-digest"}:
        cmd += ["--capability", "gemini-review"]
    try:
        result = run_cmd(cmd, timeout=12)
        if result.get("ok") and result.get("stdout"):
            route_result = json.loads(str(result["stdout"]))
            model_line, route_line = display_model_route(route_result, fallback_model)
            return {"model": model_line, "route": route_line, "task_type": task_type, "privacy": privacy, "route_plan": None}
    except Exception:
        pass
    return {
        "model": fallback_model,
        "route": f"{DEFAULT_ROUTE}; auto route unavailable, using local Codex fallback",
        "task_type": task_type,
        "privacy": privacy,
        "route_plan": None,
    }


def skill_for_prompt(prompt: str) -> dict[str, str]:
    if select_skill is None:
        return {"id": "", "label": "", "reason": ""}
    try:
        selection = select_skill(prompt, "josh")
        if write_selection is not None:
            write_selection(selection, clean_prompt(prompt))
        return {
            "id": str(selection.get("id") or ""),
            "label": str(selection.get("label") or ""),
            "reason": str(selection.get("reason") or ""),
        }
    except Exception:
        return {"id": "", "label": "", "reason": ""}


def run_cmd(cmd: list[str], timeout: int = 20, input_text: str | None = None) -> dict[str, str | int | bool]:
    proc = subprocess.run(cmd, cwd=WORKSPACE, text=True, input=input_text, capture_output=True, timeout=timeout)
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


def with_work_card_target(cmd: list[str], meta: dict[str, Any] | None = None) -> list[str]:
    if not meta:
        return cmd
    routed = list(cmd)
    chat_id = str(meta.get("telegram_chat_id") or "").strip()
    thread_id = str(meta.get("telegram_thread_id") or "").strip()
    if chat_id:
        routed.extend(["--chat-id", chat_id])
    # The work-card feature gates need the logical topic id even though the
    # Telegram transport later omits General/Topic 1 from the Bot API payload.
    if thread_id:
        routed.extend(["--thread-id", thread_id])
    return routed


def telegram_work_identity(key: str, run_id: str) -> tuple[str, str, str]:
    stable = f"{key}|{run_id}".encode("utf-8")
    digest = hashlib.sha256(stable).hexdigest()
    return (
        f"work-telegram-{digest[:24]}",
        f"run-telegram-{digest[24:48]}",
        hashlib.sha256(b"telegram-origin|" + stable).hexdigest(),
    )


def canonical_model_family(value: str) -> str:
    lowered = str(value or "").lower()
    if "gemini" in lowered or "google" in lowered or "antigravity" in lowered:
        return "antigravity"
    if "grok" in lowered or "xai" in lowered or "x.ai" in lowered:
        return "grok"
    if any(token in lowered for token in ("ollama", "llama", "qwen", "gemma", "glm")):
        return "ollama"
    return "codex"


def publish_josh(
    title: str,
    status: str,
    detail: str,
    *,
    work_id: str = "",
    run_id: str = "",
    phase: str = "",
    model_id: str = "",
    route_verified: bool | None = None,
    origin_claim_hash: str = "",
    brain_feed: bool = True,
    work_event: str = "",
    event_id: str = "",
) -> bool:
    cmd = [
        "python3",
        "mission-control/scripts/agent_publish.py",
        "--agent",
        "josh2",
        "--type",
        "status",
        "--status",
        status,
        "--title",
        title,
        "--tool",
        "Josh 2.0 Telegram",
        "--detail",
        detail[:260],
        "--privacy",
        "dashboard-safe",
    ]
    if brain_feed:
        cmd.append("--brain-feed")
    if work_event:
        cmd += ["--work-event", work_event]
    if event_id:
        cmd += ["--event-id", event_id]
    if work_id:
        cmd += ["--work-id", work_id]
    if run_id:
        cmd += ["--run-id", run_id]
    if phase:
        cmd += ["--phase", phase]
    if model_id:
        cmd += ["--model-family", canonical_model_family(model_id), "--model-id", model_id[:120]]
    if route_verified:
        cmd.append("--route-verified")
    elif route_verified is False:
        cmd.append("--route-unverified")
    if origin_claim_hash:
        cmd += ["--origin-claim-hash", origin_claim_hash]
    # Visibility is part of the Telegram task contract, not best-effort
    # decoration. Retry bounded transient failures and require the canonical
    # publisher's accepted work-ledger receipt before reporting success.
    for attempt, delay in enumerate((0.0, 0.15, 0.4), start=1):
        if delay:
            time.sleep(delay)
        try:
            result = subprocess.run(
                cmd,
                cwd=WORKSPACE,
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            if result.returncode != 0:
                continue
            payload = json.loads(result.stdout or "{}")
            work_ledger = payload.get("workLedger") if isinstance(payload, dict) else None
            if isinstance(payload, dict) and payload.get("ok") is True and isinstance(work_ledger, dict) and work_ledger.get("accepted") is True:
                return True
        except Exception:
            continue
    return False


def event_age_seconds(ts: str) -> float | None:
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        event_time = dt.datetime.fromisoformat(ts)
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - event_time).total_seconds()
    except Exception:
        return None


def internal_replay_prompt(prompt: str) -> bool:
    """Ignore framework-injected compaction and continuation rows."""
    lowered = (prompt or "").lstrip().lower()
    return lowered.startswith((
        "[context compaction",
        "[prior context",
        "[your active task list was preserved",
        "[async delegation",
    ))


def should_skip_stale_prompt_event(ts: str, first_bootstrap: bool) -> bool:
    age = event_age_seconds(ts)
    if age is None:
        return False
    if first_bootstrap and age > STALE_BOOTSTRAP_SECONDS:
        return True
    return age > MAX_UNACKED_PROMPT_AGE_SECONDS


def send_ack(
    event: dict[str, str],
    model: str,
    dry_run: bool = False,
    meta: dict[str, Any] | None = None,
    effect_protocol: dict[str, Path] | None = None,
) -> dict[str, Any]:
    message_id = str(event.get("message_id") or "")
    if message_id and meta:
        key = f"fast-ack-telegram-{meta.get('telegram_chat_id')}-{meta.get('telegram_thread_id')}-message-{message_id}"
    elif event.get("run_id"):
        stable_identity = hashlib.sha1(
            f"{event.get('session_id') or ''}:{event.get('run_id') or ''}".encode("utf-8")
        ).hexdigest()[:20]
        key = f"fast-ack-run-{stable_identity}"
    else:
        key = f"fast-ack-{event['session_id']}-{event['ts'].replace(':', '').replace('.', '-')}"
    origin_run_id = str(event.get("run_id") or message_id or event.get("ts") or "run")
    work_id, work_run_id, origin_claim_hash = telegram_work_identity(key, origin_run_id)
    prompt = event.get("prompt", "")
    # A dry run is observational only.  Do not even begin a shadow lifecycle:
    # start_work and its initial transition are durable writes.
    gateway = {} if dry_run else begin_gateway_lifecycle(
        key=key,
        origin_run_id=origin_run_id,
        work_id=work_id,
        work_run_id=work_run_id,
        prompt=prompt,
    )
    if gateway.get("error") and gateway.get("required"):
        return {
            "ok": False,
            "status": "lifecycle-unavailable",
            "error": "canonical_gateway_lifecycle_unavailable",
            "reaction_ok": False,
            "ack_message_id": "",
            "key": key,
            "objective": "",
            "run_id": event.get("run_id") or "",
            "last_card_update_at": utc_now(),
        }
    gateway_receipt = gateway.get("receipt") or {}
    gateway_writer = bool(gateway.get("writer"))
    delivery_tier = int(gateway_receipt.get("deliveryTier") or 0)
    draft_id = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    ack_message_id = ""
    ack_sent = False
    reaction_required = (
        delivery_tier >= 2
        if gateway_writer
        else requires_inbox_reaction(message_id, meta)
    )
    if dry_run:
        ack_message_id = "dry-run-message"
        ack_sent = delivery_tier >= 2 if gateway_writer else True
    else:
        should_react = delivery_tier >= 2 if gateway_writer else fast_ack_enabled()
        if should_react:
            # The exact Inbox path is reaction-first by contract. A global
            # watcher event may lack a message ID, so it remains best-effort.
            if effect_protocol and not telegram_claim_not_cancelled(effect_protocol):
                return {
                    "ok": False,
                    "status": "cancelled-before-surface",
                    "error": "claim_cancelled_before_telegram_surface",
                    "reaction_ok": False,
                    "ack_message_id": "",
                    "key": key,
                    "objective": objective_from_prompt(prompt),
                    "run_id": event.get("run_id") or "",
                    "last_card_update_at": utc_now(),
                    **gateway_public_fields(gateway),
                }
            reaction_claim = claim_gateway_effect(gateway, "reaction")
            if not reaction_claim.get("allowed"):
                if str(reaction_claim.get("state") or "") == "delivered":
                    ack_sent = True
                else:
                    return {
                        "ok": False,
                        "status": "reaction-fenced",
                        "error": "canonical_reaction_effect_fenced",
                        "reaction_ok": False,
                        "ack_message_id": "",
                        "key": key,
                        "objective": objective_from_prompt(prompt),
                        "run_id": event.get("run_id") or "",
                        "last_card_update_at": utc_now(),
                        **gateway_public_fields(gateway),
                    }
            else:
                ack_sent = (
                    place_inbox_reaction(message_id, meta=meta)
                    if requires_inbox_reaction(message_id, meta)
                    else (send_message_reaction(message_id, meta=meta) if message_id else send_prompt_reaction(prompt, meta=meta))
                )
                finish_gateway_effect(
                    gateway,
                    reaction_claim,
                    delivered=ack_sent,
                    indeterminate=not ack_sent,
                    error_class="reaction-receipt-missing" if not ack_sent else "",
                )
        else:
            ack_sent = False
        if reaction_required and not ack_sent:
            return {
                "ok": False,
                "status": "reaction-failed",
                "error": "eyes_reaction_failed",
                "reaction_ok": False,
                "ack_message_id": "",
                "key": key,
                "objective": objective_from_prompt(prompt),
                "run_id": event.get("run_id") or "",
                "last_card_update_at": utc_now(),
                **gateway_public_fields(gateway),
            }
        # Lifecycle v3 deliberately omits unreceipted typing/draft calls. Its
        # visible contract is reaction/card/final only; legacy and shadow keep
        # their existing transient behavior for byte-compatible comparison.
        if not gateway_writer:
            send_chat_action(meta=meta)
            send_message_draft(draft_id, "", meta=meta)
    if gateway.get("receipt"):
        try:
            advance_gateway_phase(gateway, "acknowledged")
        except Exception as exc:  # noqa: BLE001 - visible effect is already fenced
            return {
                "ok": False,
                "status": "lifecycle-transition-failed",
                "error": type(exc).__name__,
                "reaction_ok": bool(ack_sent),
                "ack_message_id": ack_message_id,
                "key": key,
                "objective": "",
                "run_id": event.get("run_id") or "",
                "last_card_update_at": utc_now(),
                **gateway_public_fields(gateway),
            }
    # The visible acknowledgement must be first. Route and skill probes may
    # involve remote health checks and must never delay the eyes reaction.
    objective = objective_from_prompt(prompt)
    if objective_is_near_copy(prompt, objective):
        objective = semantic_reinterpretation(prompt)
    if not objective or objective_is_near_copy(prompt, objective):
        # #JOSH2: a reaction may be immediate, but no Telegram header or
        # Control Tower row is published until the agent supplies its own
        # interpretation of intent, target, and desired outcome.
        if gateway.get("receipt"):
            try:
                advance_gateway_phase(gateway, "awaiting_input")
            except Exception:
                pass
        return {
            "ok": True,
            "status": "awaiting-objective-interpretation",
            "reaction_ok": bool(dry_run or ack_sent),
            "ack_message_id": ack_message_id,
            "key": key,
            "objective": "",
            "requires_objective_interpretation": True,
            "run_id": event.get("run_id") or "",
            "last_card_update_at": utc_now(),
            "card_start_ok": False,
            "header_message_id": "",
            "live_message_id": "",
            **gateway_public_fields(gateway),
        }
    route = auto_route_for_prompt(prompt, model or DEFAULT_MODEL)
    skill = skill_for_prompt(prompt)
    display_model = route["model"]
    display_route = route["route"]
    if skill.get("label"):
        display_model = f"{display_model}; skill: {skill['label']}"
        display_route = f"{display_route}; runbook={skill['id']}"
    if gateway.get("receipt"):
        try:
            route_plan = route.get("route_plan") if isinstance(route.get("route_plan"), dict) else {}
            set_gateway_worker_route(
                gateway,
                str(route_plan.get("routeId") or route.get("route") or "josh2-pending"),
            )
        except Exception as exc:  # noqa: BLE001
            if gateway_writer:
                return {
                    "ok": False,
                    "status": "lifecycle-route-failed",
                    "error": type(exc).__name__,
                    "reaction_ok": bool(ack_sent),
                    "ack_message_id": ack_message_id,
                    "key": key,
                    "objective": objective,
                    "run_id": event.get("run_id") or "",
                    "last_card_update_at": utc_now(),
                    **gateway_public_fields(gateway),
                }
    if is_hold_request(prompt):
        if not dry_run:
            publish_josh(
                objective,
                "cancelled",
                "Hold requested; no live work card started.",
                work_id=work_id,
                run_id=work_run_id,
                phase="cancelled",
                model_id=str(route.get("model") or model),
                origin_claim_hash=origin_claim_hash,
            )
        return {
            "ok": True,
            "reaction_ok": bool(dry_run or ack_sent),
            "ack_message_id": ack_message_id,
            "key": key,
            "model": display_model,
            "route": display_route,
            "skill": skill,
            "objective": objective,
            "work_id": work_id,
            "ledger_run_id": work_run_id,
            "origin_claim_hash": origin_claim_hash,
            "run_id": event.get("run_id") or "",
            "no_card_required": True,
            "last_card_update_at": utc_now(),
        }
    start_visible_card = delivery_tier == 3 if gateway_writer else live_cards_enabled(meta)
    card_start_attempts = 0
    header_message_id = ""
    live_message_id = ""
    header_required = False
    surface_contract = (
        f"tier-{delivery_tier}-final-v3"
        if gateway_writer and delivery_tier in {1, 2}
        else "live-only-v2"
    )
    card_start_ok = True
    if not dry_run and start_visible_card:
        if not telegram_claim_not_cancelled(effect_protocol):
            return {
                "ok": False,
                "status": "cancelled-before-surface",
                "error": "claim_cancelled_before_card_surface",
                "reaction_ok": bool(ack_sent),
                "key": key,
                "card_start_ok": False,
                "header_message_id": "",
                "live_message_id": "",
            }
        card_claim = claim_gateway_effect(gateway, "card")
        if not card_claim.get("allowed") and str(card_claim.get("state") or "") != "delivered":
            return {
                "ok": False,
                "status": "card-fenced",
                "error": "canonical_card_effect_fenced",
                "reaction_ok": bool(ack_sent),
                "key": key,
                "card_start_ok": False,
                "header_message_id": "",
                "live_message_id": "",
                **gateway_public_fields(gateway),
            }
        card_start_cmd = with_work_card_target([
            "python3",
            str(WORK_CARD_SCRIPT),
            "start",
            "--key",
            key,
            "--title",
            objective,
            "--model",
            display_model,
            "--route",
            display_route,
            "--now",
            "Objective and runbook confirmed",
            "--done",
            f"Received Telegram task|Objective determined: {objective}|Skill selected: {skill.get('label') or 'none'}",
            "--next",
            "Work automatically; show buttons only for final approval steps if needed",
            "--ack-message-id",
            ack_message_id,
            "--timeout",
            "6",
        ], meta)
        if effect_protocol:
            card_start_cmd.extend([
                "--effect-path", str(effect_protocol["effect"]),
                "--cancel-path", str(effect_protocol["cancel"]),
            ])
            if int(effect_protocol.get("surface_deadline_ms") or 0) > 0:
                card_start_cmd.extend([
                    "--surface-deadline-ms",
                    str(effect_protocol["surface_deadline_ms"]),
                ])
        card_start_attempts = 0
        if card_claim.get("allowed"):
            card_start_attempts = 1
            card_start = run_work_card_start(card_start_cmd)
        else:
            # A replay after a confirmed card must recover the same durable
            # receipt and must never call sendMessage again.
            card_start = {"ok": True, "recovered": True}
        card_receipt = parse_work_card_start_receipt(key, card_start)
        if card_claim.get("allowed") and exact_control_center_inbox(meta) and safe_same_key_card_retry(key, card_receipt):
            card_start_attempts += 1
            card_start = run_work_card_start(card_start_cmd)
            card_receipt = parse_work_card_start_receipt(key, card_start)
        header_message_id = str(card_receipt.get("header_message_id") or "")
        live_message_id = str(card_receipt.get("live_message_id") or "")
        header_required = bool(card_receipt.get("header_required"))
        surface_contract = str(card_receipt.get("surface_contract") or ("header-live-v1" if header_required else "live-only-v2"))
        card_start_ok = bool(card_receipt.get("surface_ok")) if exact_control_center_inbox(meta) else bool(card_start.get("ok"))
        effect_state = (
            "surface-started"
            if card_start_ok or header_message_id or live_message_id
            else "indeterminate"
            if card_receipt.get("surface_indeterminate")
            else "failed-before-surface"
        )
        update_telegram_effect(
            effect_protocol,
            state=effect_state,
            stage="header-live-card" if header_required else "live-card",
            reaction_ok=bool(ack_sent),
            header_message_id=header_message_id,
            live_message_id=live_message_id,
        )
        if card_claim.get("allowed"):
            finish_gateway_effect(
                gateway,
                card_claim,
                delivered=card_start_ok,
                indeterminate=bool(card_receipt.get("surface_indeterminate")),
                error_class="card-receipt-missing" if not card_start_ok else "",
            )
    else:
        card_start = {"ok": True, "skipped": True}
    if not dry_run and start_visible_card and exact_control_center_inbox(meta) and not card_start_ok:
        return {
            "ok": False,
            "status": "surface-failed",
            "error": "inbox_required_surface_receipt_missing",
            "reaction_ok": bool(ack_sent),
            "ack_message_id": ack_message_id,
            "key": key,
            "model": display_model,
            "route": display_route,
            "route_plan": route.get("route_plan"),
            "skill": skill,
            "objective": objective,
            "run_id": event.get("run_id") or "",
            "last_card_update_at": utc_now(),
            "card_start_ok": False,
            "card_start_attempts": card_start_attempts,
            "header_message_id": header_message_id,
            "live_message_id": live_message_id,
            "header_required": header_required,
            "surface_contract": surface_contract,
            "surface_indeterminate": bool(card_receipt.get("surface_indeterminate")),
            "card_start_receipt": str(card_start.get("stdout") or ""),
            **gateway_public_fields(gateway),
        }
    if gateway.get("receipt"):
        try:
            advance_gateway_phase(gateway, "working")
            if gateway.get("shadow"):
                predicted = int((gateway.get("receipt") or {}).get("deliveryTier") or 3)
                actual_contract = (
                    "reaction-card-final" if ack_sent and start_visible_card
                    else "reaction-final" if ack_sent
                    else "final-only"
                )
                gateway["lifecycle"].record_shadow_sample(
                    str((gateway.get("receipt") or {})["workId"]),
                    observed_contract=actual_contract,
                )
                if predicted == 3 and render_live_card is not None:
                    rendered = render_live_card(
                        gateway.get("receipt") or {},
                        objective=objective,
                        phase_label="Working",
                        model=display_model,
                        route=display_route,
                        progress=50,
                    )
                    gateway["lifecycle"].update_render_hash(
                        str((gateway.get("receipt") or {})["workId"]),
                        rendered,
                    )
        except Exception as exc:  # noqa: BLE001
            if gateway_writer:
                return {
                    "ok": False,
                    "status": "lifecycle-transition-failed",
                    "error": type(exc).__name__,
                    "reaction_ok": bool(ack_sent),
                    "key": key,
                    "card_start_ok": card_start_ok,
                    "header_message_id": header_message_id,
                    "live_message_id": live_message_id,
                    **gateway_public_fields(gateway),
                }
    visibility_publish_ok = True
    if not dry_run:
        visibility_publish_ok = publish_josh(
            objective,
            "active",
            f"Objective confirmed; {display_model}; skill={skill.get('label') or 'none'}",
            work_id=work_id,
            run_id=work_run_id,
            phase="active",
            model_id=str(model or DEFAULT_MODEL),
            route_verified=False,
            origin_claim_hash=origin_claim_hash,
            work_event="start",
        )
        if not visibility_publish_ok:
            return {
                "ok": False,
                "status": "visibility-failed",
                "error": "canonical_josh2_work_publish_failed",
                "reaction_ok": bool(ack_sent),
                "ack_message_id": ack_message_id,
                "key": key,
                "objective": objective,
                "work_id": work_id,
                "ledger_run_id": work_run_id,
                "run_id": event.get("run_id") or "",
                "card_start_ok": card_start_ok,
                "header_message_id": header_message_id,
                "live_message_id": live_message_id,
                "visibility_publish_ok": False,
            }
    return {
        "ok": True,
        "reaction_ok": bool(dry_run or ack_sent),
        "ack_message_id": ack_message_id,
        "key": key,
        "model": display_model,
        "runtime_model": str(model or DEFAULT_MODEL),
        "route": display_route,
        "route_plan": route.get("route_plan"),
        "skill": skill,
        "objective": objective,
        "work_id": work_id,
        "ledger_run_id": work_run_id,
        "origin_claim_hash": origin_claim_hash,
        "run_id": event.get("run_id") or "",
        "last_card_update_at": utc_now(),
        "card_start_ok": card_start_ok,
        "card_start_attempts": card_start_attempts,
        "header_message_id": header_message_id,
        "live_message_id": live_message_id,
        "header_required": header_required,
        "surface_contract": surface_contract,
        "card_start_receipt": str(card_start.get("stdout") or ""),
        "visibility_publish_ok": visibility_publish_ok,
        "no_card_required": bool(gateway_writer and delivery_tier in {1, 2}),
        **gateway_public_fields(gateway),
    }


def coordinator_job_snapshot(job_id: str) -> dict[str, Any]:
    if not job_id or not COORDINATOR_SCRIPT.exists():
        return {}
    try:
        result = run_cmd([sys.executable, str(COORDINATOR_SCRIPT), "status", "--job-id", job_id], timeout=8)
        if not result.get("ok") or not result.get("stdout"):
            return {}
        payload = json.loads(str(result["stdout"]))
        job = payload.get("job") or {}
        return job if isinstance(job, dict) else {}
    except Exception:
        return {}


def coordinator_job_status(job_id: str) -> str:
    return str(coordinator_job_snapshot(job_id).get("status") or "")


def prune_terminal_cards(state: dict[str, Any], keep: int = MAX_TERMINAL_CARD_RECORDS) -> int:
    active = state.get("active_cards")
    if not isinstance(active, dict):
        return 0
    terminal = [
        (key, card)
        for key, card in active.items()
        if isinstance(card, dict) and str(card.get("status") or "").lower() in TERMINAL_CARD_STATUSES
    ]
    terminal.sort(
        key=lambda item: str(item[1].get("ended_at") or item[1].get("last_card_update_at") or item[1].get("started_at") or ""),
        reverse=True,
    )
    removed = 0
    for key, _ in terminal[max(0, keep):]:
        active.pop(key, None)
        removed += 1
    return removed


def update_active_cards(state: dict[str, Any], session_id: str, dry_run: bool = False, meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not live_cards_enabled(meta):
        state["processed_progress_events"] = sorted(set(state.get("processed_progress_events") or []))[-300:]
        return []
    active = state.get("active_cards") or {}
    processed = set(state.get("processed_progress_events") or [])
    updates: list[dict[str, Any]] = []
    adopt_interpreted_work_cards(state, meta=meta)
    for event in recent_progress_events(session_id, meta=meta):
        event_id = event["event_id"]
        if event_id in processed:
            continue
        card = active.get(event["run_id"])
        if not card:
            processed.add(event_id)
            continue
        if str(card.get("status") or "").lower() in {"done", "failed", "paused"}:
            processed.add(event_id)
            continue
        if str(card.get("status") or "").lower() in {"closing-before-final", "awaiting-final-gate"}:
            continue
        if card.get("requires_objective_interpretation"):
            # Keep the event pending. Once the agent creates its interpreted
            # card, adoption replays progress into that same visible surface.
            continue
        if card.get("coordinator_owned"):
            # Coordinator workers report only the strict fixed-code progress
            # envelope. Never render trajectory/model/tool summaries into the
            # same card through this legacy observer path.
            processed.add(event_id)
            continue
        objective = str(card.get("objective") or "Josh 2.0 Telegram task")
        key = str(card.get("key") or "")
        if not key:
            continue
        processed.add(event_id)
        if card.get("no_card_required"):
            # Tier 1/2 intentionally have no live-card surface.  Consume model
            # and tool progress into private watcher state only; their next
            # Telegram-visible write is the gateway-owned final.
            if event["type"] == "model.completed":
                result = {"ok": True, "deferred_to_pre_final_gate": True, "no_card_required": True}
                card["status"] = "awaiting-final-gate"
            else:
                result = {"ok": True, "no_card_required": True}
                card["status"] = "active"
                if not dry_run:
                    safe_summary = {
                        "tool.call": "Tool execution started",
                        "tool.result": "Tool execution completed",
                    }.get(str(event.get("type") or ""), "Work progressed")
                    publish_josh(
                        objective,
                        "active",
                        safe_summary,
                        work_id=str(card.get("work_id") or ""),
                        run_id=str(card.get("ledger_run_id") or ""),
                        phase="active",
                        model_id=str(card.get("runtime_model") or card.get("model") or DEFAULT_MODEL),
                        route_verified=not bool(card.get("coordinator_owned")),
                        origin_claim_hash=str(card.get("origin_claim_hash") or ""),
                    )
            card["last_card_update_at"] = utc_now()
            card["last_progress_at"] = card["last_card_update_at"]
            updates.append({"event": event_id, "result": result})
            continue
        if event["type"] == "model.completed":
            # Final text and outcome are validated by before_agent_finalize.
            # The watcher records model completion but never races that gate
            # with a second terminal Telegram edit.
            result = {"ok": True, "deferred_to_pre_final_gate": True}
            card["status"] = "awaiting-final-gate"
            card["last_card_update_at"] = utc_now()
            card["last_progress_at"] = card["last_card_update_at"]
            updates.append({"event": event_id, "result": result})
        else:
            safe_summary = {
                "tool.call": "Tool execution started",
                "tool.result": "Tool execution completed",
            }.get(str(event.get("type") or ""), "Work progressed")
            cmd = [
                "python3",
                str(WORK_CARD_SCRIPT),
                "update",
                "--key",
                key,
                "--title",
                objective,
                "--model",
                str(card.get("model") or DEFAULT_MODEL),
                "--route",
                str(card.get("route") or DEFAULT_ROUTE),
                "--now",
                safe_summary,
                "--done",
                safe_summary,
            ]
            if not dry_run:
                result = run_gateway_card_update(card, cmd, meta=meta, status="progress")
                if result.get("ok"):
                    publish_josh(
                        objective,
                        "active",
                        safe_summary,
                        work_id=str(card.get("work_id") or ""),
                        run_id=str(card.get("ledger_run_id") or ""),
                        phase="active",
                        model_id=str(card.get("runtime_model") or card.get("model") or DEFAULT_MODEL),
                        route_verified=True,
                        origin_claim_hash=str(card.get("origin_claim_hash") or ""),
                    )
            else:
                result = {"ok": True, "dry_run": True}
            card["status"] = "active"
            card["last_card_update_at"] = utc_now()
            card["last_progress_at"] = card["last_card_update_at"]
            updates.append({"event": event_id, "result": result})
    now = dt.datetime.now(dt.timezone.utc)
    for run_id, card in active.items():
        if not isinstance(card, dict) or str(card.get("status") or "").lower() in TERMINAL_CARD_STATUSES:
            continue
        if str(card.get("status") or "").lower() in {"closing-before-final", "awaiting-final-gate"}:
            continue
        if card.get("requires_objective_interpretation"):
            continue
        if card.get("no_card_required"):
            # Coordinator recovery owns terminal delivery.  Never manufacture
            # a work card as a failure/heartbeat/expiry fallback for Tier 1/2.
            if card.get("coordinator_owned"):
                worker_job = coordinator_job_snapshot(str(card.get("job_id") or ""))
                worker_status = str(worker_job.get("status") or "")
                if worker_status in {"done", "failed"} and worker_job.get("delivered"):
                    card["status"] = worker_status
                    card["ended_at"] = utc_now()
                    card["last_card_update_at"] = card["ended_at"]
                elif worker_status in {"done", "failed"}:
                    if card_uses_lifecycle_v3(card):
                        result = queue_lifecycle_terminal_fallback(
                            str(run_id),
                            card,
                            card or (meta or {}),
                            terminal_status="failed",
                            issue="The selected worker stopped without a verified final delivery.",
                            next_step="Retry after the worker route is healthy.",
                            dry_run=dry_run,
                        )
                        updates.append({"event": f"worker-terminal:{run_id}", "result": result})
                    else:
                        card["status"] = "awaiting-final-gate"
                        card["final_delivery_status"] = "retry"
                        card["last_card_update_at"] = utc_now()
            continue
        last_raw = str(card.get("last_card_update_at") or "")
        try:
            last = dt.datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
        except Exception:
            last = now
        objective = str(card.get("objective") or "Josh 2.0 Telegram task")
        key = str(card.get("key") or "")
        if not key:
            continue
        coordinator_owned = bool(card.get("coordinator_owned"))
        worker_job = coordinator_job_snapshot(str(card.get("job_id") or "")) if coordinator_owned else {}
        worker_status = str(worker_job.get("status") or "")
        if worker_status in {"done", "failed"} and worker_job.get("delivered"):
            card["status"] = worker_status
            card["ended_at"] = utc_now()
            card["last_card_update_at"] = card["ended_at"]
            continue
        if worker_status in {"done", "failed"}:
            # A terminal coordinator row without a delivered final is not a
            # terminal Telegram task. Emit the canonical failure final through
            # the same origin-scoped work-card state and retry until accepted.
            if card_uses_lifecycle_v3(card):
                result = queue_lifecycle_terminal_fallback(
                    str(run_id),
                    card,
                    card or (meta or {}),
                    terminal_status="failed",
                    issue="The selected worker exhausted its safe delivery path.",
                    next_step="Retry after the worker route is healthy.",
                    dry_run=dry_run,
                )
                updates.append({"event": f"worker-terminal:{run_id}", "result": result})
                continue
            cmd = [
                "python3",
                str(WORK_CARD_SCRIPT),
                "fail",
                "--key",
                key,
                "--title",
                objective,
                "--model",
                str(card.get("model") or DEFAULT_MODEL),
                "--route",
                str(card.get("route") or DEFAULT_ROUTE),
                "--done",
                "Worker routing was checked|The worker did not deliver a verified result|A structured failure summary was prepared",
                "--blocker",
                "The selected worker exhausted its safe delivery path",
                "--next",
                "Retry after the worker route is healthy",
            ]
            result = {"ok": True, "dry_run": True} if dry_run else run_cmd(with_work_card_target(cmd, card or meta))
            if result.get("ok"):
                card["status"] = "failed"
                card["ended_at"] = utc_now()
                card["last_card_update_at"] = card["ended_at"]
                if not dry_run:
                    publish_josh(
                        objective,
                        "error",
                        "The selected worker exhausted its safe delivery path.",
                        work_id=str(card.get("work_id") or ""),
                        run_id=str(card.get("ledger_run_id") or ""),
                        phase="error",
                        model_id=str(card.get("runtime_model") or card.get("model") or DEFAULT_MODEL),
                        route_verified=not bool(card.get("coordinator_owned")),
                        origin_claim_hash=str(card.get("origin_claim_hash") or ""),
                    )
            else:
                card["final_delivery_status"] = "retry"
                card["last_card_update_at"] = utc_now()
            continue
        # #JAIMES: coordinator-owned workers still need visible heartbeat edits.
        # Let them bypass session-expiry handling below, then reach the shared
        # heartbeat path so a long model call never leaves the Inbox card frozen.
        if not coordinator_owned and card_session_id(card) and card_session_id(card) != session_id:
            summary = "Previous Telegram session ended; Josh 2.0 is back on standby."
            if card_uses_lifecycle_v3(card):
                result = queue_lifecycle_terminal_fallback(
                    str(run_id),
                    card,
                    card or (meta or {}),
                    terminal_status="expired",
                    issue=summary,
                    next_step="Send the request again if the work is still needed.",
                    dry_run=dry_run,
                )
                updates.append({"event": f"session-ended:{run_id}:{utc_now()}", "result": result})
                continue
            cmd = [
                "python3",
                str(WORK_CARD_SCRIPT),
                "done",
                "--key",
                key,
                "--title",
                objective,
                "--model",
                str(card.get("model") or DEFAULT_MODEL),
                "--route",
                str(card.get("route") or DEFAULT_ROUTE),
                "--done",
                summary,
                "--blocker",
                "None",
                "--no-final-summary",
            ]
            result = {"ok": True, "dry_run": True} if dry_run else run_cmd(with_work_card_target(cmd, meta))
            if not dry_run:
                publish_josh(
                    objective,
                    "cancelled",
                    summary,
                    work_id=str(card.get("work_id") or ""),
                    run_id=str(card.get("ledger_run_id") or ""),
                    phase="cancelled",
                    model_id=str(card.get("runtime_model") or card.get("model") or DEFAULT_MODEL),
                    origin_claim_hash=str(card.get("origin_claim_hash") or ""),
                )
            card["status"] = "done"
            card["ended_at"] = utc_now()
            card["last_card_update_at"] = card["ended_at"]
            updates.append({"event": f"session-ended:{run_id}:{card['ended_at']}", "result": result})
            continue
        started_at = card_started_at(card) or last
        if not coordinator_owned and (now - started_at).total_seconds() > MAX_ACTIVE_CARD_SECONDS:
            summary = "No recent tool or model progress; Josh 2.0 is back on standby."
            if card_uses_lifecycle_v3(card):
                result = queue_lifecycle_terminal_fallback(
                    str(run_id),
                    card,
                    card or (meta or {}),
                    terminal_status="expired",
                    issue=summary,
                    next_step="Retry the task if it is still needed.",
                    dry_run=dry_run,
                )
                updates.append({"event": f"expired:{run_id}:{utc_now()}", "result": result})
                continue
            cmd = [
                "python3",
                str(WORK_CARD_SCRIPT),
                "done",
                "--key",
                key,
                "--title",
                objective,
                "--model",
                str(card.get("model") or DEFAULT_MODEL),
                "--route",
                str(card.get("route") or DEFAULT_ROUTE),
                "--done",
                summary,
                "--blocker",
                "None",
                "--no-final-summary",
            ]
            result = {"ok": True, "dry_run": True} if dry_run else run_cmd(with_work_card_target(cmd, meta))
            if not dry_run:
                publish_josh(
                    objective,
                    "cancelled",
                    summary,
                    work_id=str(card.get("work_id") or ""),
                    run_id=str(card.get("ledger_run_id") or ""),
                    phase="cancelled",
                    model_id=str(card.get("runtime_model") or card.get("model") or DEFAULT_MODEL),
                    origin_claim_hash=str(card.get("origin_claim_hash") or ""),
                )
            card["status"] = "done"
            card["ended_at"] = utc_now()
            card["last_card_update_at"] = card["ended_at"]
            updates.append({"event": f"expired:{run_id}:{card['ended_at']}", "result": result})
            continue
        if (now - last).total_seconds() < HEARTBEAT_SECONDS:
            continue
        summary = f"Still working; waiting for next model/tool update ({local_time_label()})"
        cmd = [
            "python3",
            str(WORK_CARD_SCRIPT),
            "update",
            "--key",
            key,
            "--title",
            objective,
                "--model",
                str(card.get("model") or DEFAULT_MODEL),
                "--route",
                str(card.get("route") or DEFAULT_ROUTE),
            "--now",
            summary,
            "--done",
            summary,
            "--no-brain-feed",
        ]
        result = (
            {"ok": True, "dry_run": True}
            if dry_run
            else run_gateway_card_update(card, cmd, meta=meta, status="heartbeat")
        )
        # Do not keep re-publishing heartbeat-only work-card text to
        # Brain Feed; it makes stale cards look like current truth.
        if not dry_run and result.get("ok"):
            publish_josh(
                objective,
                "active",
                summary,
                work_id=str(card.get("work_id") or ""),
                run_id=str(card.get("ledger_run_id") or ""),
                phase="heartbeat",
                model_id=str(card.get("runtime_model") or card.get("model") or DEFAULT_MODEL),
                route_verified=not coordinator_owned,
                origin_claim_hash=str(card.get("origin_claim_hash") or ""),
                brain_feed=True,
                work_event="heartbeat",
            )
        card["last_card_update_at"] = utc_now()
        updates.append({"event": f"heartbeat:{run_id}:{card['last_card_update_at']}", "result": result})
    state["processed_progress_events"] = sorted(processed)[-300:]
    return updates


def reconcile_orphan_work_cards(
    state: dict[str, Any],
    dry_run: bool = False,
    meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    adopt_interpreted_work_cards(state, meta=meta)
    work_state = load_json(WORK_CARD_STATE_PATH, {})
    cards = work_state.get("cards") if isinstance(work_state, dict) else {}
    if not isinstance(cards, dict):
        return []
    owned_keys = {
        str(card.get("key") or "")
        for card in (state.get("active_cards") or {}).values()
        if isinstance(card, dict) and str(card.get("status") or "").lower() not in {"done", "failed", "paused"}
    }
    now = dt.datetime.now(dt.timezone.utc)
    reconciled: list[dict[str, Any]] = []
    for key, card in cards.items():
        if not isinstance(card, dict):
            continue
        if str(card.get("status") or "").lower() not in {"running", "active"}:
            continue
        if key in owned_keys:
            continue
        # Manual interpreted cards are owned by the model run even when the
        # watcher restarts or briefly loses its correlation state. Never infer
        # that Josh is idle from absence alone; only watcher-created fast-ack
        # surfaces are eligible for stale cleanup.
        if not str(key).startswith("fast-ack-"):
            continue
        updated = parse_utc(card.get("updated_at"))
        if updated and (now - updated).total_seconds() < MAX_ACTIVE_CARD_SECONDS:
            continue
        title = str(card.get("title") or "Josh 2.0 Telegram task")
        summary = "No active model or tool run owns this card; Josh 2.0 is idle."
        orphan = recover_card_lifecycle_identity({
            **card,
            "key": str(key),
            "objective": title,
            "telegram_chat_id": str(card.get("telegram_chat_id") or card.get("chat_id") or (meta or {}).get("telegram_chat_id") or ""),
            "telegram_thread_id": str(card.get("telegram_thread_id") or card.get("thread_id") or (meta or {}).get("telegram_thread_id") or ""),
            "status": "active",
        })
        if card_uses_lifecycle_v3(orphan):
            run_key = str(orphan.get("origin_run_id") or orphan.get("ledger_run_id") or f"orphan:{key}")
            result = queue_lifecycle_terminal_fallback(
                run_key,
                orphan,
                orphan,
                terminal_status="expired",
                issue=summary,
                next_step="Start the task again if it is still needed.",
                dry_run=dry_run,
            )
            if not dry_run:
                state.setdefault("active_cards", {})[run_key] = orphan
                with fast_ack_state_lock():
                    latest = load_json(STATE_PATH, {})
                    if not isinstance(latest, dict):
                        latest = {}
                    latest.setdefault("active_cards", {})[run_key] = copy.deepcopy(orphan)
                    save_json(STATE_PATH, latest)
            reconciled.append({"event": f"orphan-card:{key}", "result": result})
            continue
        cmd = [
            "python3",
            str(WORK_CARD_SCRIPT),
            "done",
            "--key",
            key,
            "--title",
            title,
            "--model",
            str(card.get("model") or DEFAULT_MODEL),
            "--route",
            str(card.get("route") or DEFAULT_ROUTE),
            "--done",
            summary,
            "--blocker",
            "None",
            "--no-final-summary",
        ]
        result = {"ok": True, "dry_run": True} if dry_run else run_cmd(with_work_card_target(cmd, meta))
        reconciled.append({"event": f"orphan-card:{key}", "result": result})
    return reconciled


def terminal_request_meta(args: argparse.Namespace) -> dict[str, Any]:
    meta = parse_telegram_target_from_key(str(getattr(args, "session_key", "") or ""))
    if getattr(args, "chat_id", ""):
        meta["telegram_chat_id"] = str(args.chat_id)
    if getattr(args, "thread_id", ""):
        meta["telegram_thread_id"] = str(args.thread_id)
    return meta


def select_terminal_card(
    state: dict[str, Any],
    args: argparse.Namespace,
    meta: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    active = state.setdefault("active_cards", {})
    if not isinstance(active, dict):
        active = {}
        state["active_cards"] = active
    run_id = str(getattr(args, "run_id", "") or "")
    if run_id and isinstance(active.get(run_id), dict):
        return run_id, active[run_id]
    if run_id:
        # A concrete OpenCLAW turn must never borrow an older same-topic card.
        # Let the bounded finalize retry wait for the watcher to correlate the
        # exact run instead of authorizing against stale terminal history.
        return None

    session_id = str(getattr(args, "session_id", "") or "")
    session_key = str(getattr(args, "session_key", "") or "")
    expected_chat = str(normalize_telegram_chat_id(meta.get("telegram_chat_id")) or "")
    expected_thread = str(meta.get("telegram_thread_id") or "")
    candidates: list[tuple[dt.datetime, str, dict[str, Any]]] = []
    for key, card in active.items():
        if not isinstance(card, dict):
            continue
        if str(card.get("status") or "").lower() in TERMINAL_CARD_STATUSES:
            continue
        card_chat = str(normalize_telegram_chat_id(card.get("telegram_chat_id")) or "")
        card_thread = str(card.get("telegram_thread_id") or "")
        exact_session = bool(session_id and card_session_id(card) == session_id)
        exact_key = bool(session_key and str(card.get("telegram_session_key") or "") == session_key)
        same_origin = bool(
            expected_chat
            and card_chat == expected_chat
            and (not expected_thread or card_thread == expected_thread)
        )
        if not (exact_session or exact_key or same_origin):
            continue
        candidates.append((card_started_at(card) or dt.datetime.min.replace(tzinfo=dt.timezone.utc), str(key), card))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, key, card = candidates[0]
        return key, card

    # The watcher can lag the model by one poll. Adopt the newest visible,
    # interpreted card in this exact origin so final delivery never overtakes it.
    visible = interpreted_work_card_candidates(
        meta,
        max_age_seconds=INTERPRETED_CARD_ADOPTION_WINDOW_SECONDS,
    )
    if not visible:
        return None
    visible_key, work_card = visible[0]
    stable = run_id or session_id or f"terminal-card:{visible_key}"
    active[stable] = {
        "key": visible_key,
        "objective": str(work_card.get("title") or "").strip(),
        "model": str(work_card.get("model") or DEFAULT_MODEL),
        "route": str(work_card.get("route") or DEFAULT_ROUTE),
        "session_id": session_id,
        "telegram_session_key": session_key,
        "telegram_chat_id": expected_chat,
        "telegram_thread_id": expected_thread,
        "header_message_id": positive_telegram_message_id(work_card.get("header_message_id")),
        "live_message_id": positive_telegram_message_id(work_card.get("message_id")),
        "card_start_ok": bool(work_card.get("message_id")),
        "header_required": str(work_card.get("surface_contract") or "") != "live-only-v2",
        "surface_contract": str(work_card.get("surface_contract") or "header-live-v1"),
        "objective_interpreted": True,
        "requires_objective_interpretation": False,
        "started_at": str(work_card.get("started_at") or work_card.get("updated_at") or utc_now()),
        "last_card_update_at": str(work_card.get("updated_at") or utc_now()),
        "status": "active",
    }
    return stable, active[stable]


def terminal_action_fields(status: str) -> tuple[str, str, str]:
    return {
        "done": ("done", "Work complete; final summary follows", "None"),
        "paused": ("pause", "Work paused; final summary explains what remains", "See final summary"),
        "failed": ("fail", "Work stopped; final summary explains the issue", "See final summary"),
        "expired": ("fail", "Work expired; final summary explains the recovery state", "See final summary"),
    }.get(status, ("pause", "Work paused; final summary explains what remains", "See final summary"))


def terminal_execution_evidence_hash(
    run_key: str,
    card: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    bound = {
        "runKey": str(run_key),
        "cardKey": str(card.get("key") or ""),
        "jobId": str(card.get("job_id") or ""),
        "workId": str(card.get("work_id") or ""),
        "ledgerRunId": str(card.get("ledger_run_id") or ""),
        "originClaimHash": str(card.get("origin_claim_hash") or ""),
        "provider": str(evidence.get("provider") or ""),
        "model": str(evidence.get("model") or ""),
        "worker": str(evidence.get("worker") or ""),
        "host": str(evidence.get("host") or ""),
        "routeId": str(evidence.get("routeId") or ""),
    }
    return hashlib.sha256(
        json.dumps(bound, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validated_terminal_execution_evidence(
    run_key: str,
    card: dict[str, Any],
) -> dict[str, Any]:
    evidence = card.get("terminal_execution_evidence")
    if not isinstance(evidence, dict):
        return {}
    values = {
        key: _safe_progress_fragment(evidence.get(key))
        for key in ("provider", "model", "worker", "host", "routeId")
    }
    if any(not value or value.lower() in {"unknown", "unverified", "pending"} for value in values.values()):
        return {}
    expected = terminal_execution_evidence_hash(run_key, card, values)
    if not secrets_compare_digest(str(evidence.get("evidenceHash") or ""), expected):
        return {}
    return {**values, "evidenceHash": expected, "verifiedAt": str(evidence.get("verifiedAt") or "")}


def secrets_compare_digest(left: str, right: str) -> bool:
    """Keep the optional evidence receipt comparison constant-time."""
    try:
        import hmac

        return hmac.compare_digest(left, right)
    except Exception:
        return False


def live_terminal_execution_evidence(
    run_key: str,
    card: dict[str, Any],
) -> dict[str, Any]:
    if not card.get("coordinator_owned") or not str(card.get("job_id") or ""):
        return {}
    job = coordinator_job_snapshot(str(card.get("job_id") or ""))
    if not job or not _progress_job_matches_card(job, card, run_key):
        return {}
    actual = job.get("actual") if isinstance(job.get("actual"), dict) else {}
    route = job.get("route") if isinstance(job.get("route"), dict) else {}
    if not (bool(actual.get("executionVerified")) and bool(actual.get("modelVerified"))):
        return {}
    evidence = {
        "provider": _safe_progress_fragment(actual.get("actualProvider")),
        "model": _safe_progress_fragment(actual.get("actualModel")),
        "worker": _safe_progress_fragment(actual.get("actualWorker")),
        "host": _safe_progress_fragment(actual.get("actualHost")),
        "routeId": _safe_progress_fragment(route.get("routeId")),
        "verifiedAt": utc_now(),
    }
    if any(
        not evidence[key] or str(evidence[key]).lower() in {"unknown", "unverified", "pending"}
        for key in ("provider", "model", "worker", "host", "routeId")
    ):
        return {}
    evidence["evidenceHash"] = terminal_execution_evidence_hash(run_key, card, evidence)
    return evidence


def ensure_terminal_route_truth(run_key: str, card: dict[str, Any]) -> bool:
    """Freeze verified coordinator runtime facts; never promote a route plan."""
    if not card.get("coordinator_owned"):
        return True
    evidence = validated_terminal_execution_evidence(run_key, card)
    if not evidence:
        evidence = live_terminal_execution_evidence(run_key, card)
    if not evidence:
        card["terminal_route_blocked_at"] = card.get("terminal_route_blocked_at") or utc_now()
        card["terminal_route_incident"] = "verified-runtime-evidence-unavailable"
        return False
    route_label = (
        f"verified route={evidence['routeId']}; provider={evidence['provider']}; "
        f"worker={evidence['worker']}; host={evidence['host']}"
    )
    card.update({
        "runtime_model": evidence["model"],
        "route": route_label,
        "route_verified": True,
        "terminal_execution_evidence": evidence,
    })
    with fast_ack_state_lock():
        state = load_json(STATE_PATH, {})
        active = state.get("active_cards") if isinstance(state, dict) else {}
        current = active.get(run_key) if isinstance(active, dict) else None
        if (
            isinstance(current, dict)
            and str(current.get("key") or "") == str(card.get("key") or "")
            and str(current.get("work_id") or "") == str(card.get("work_id") or "")
        ):
            current.update({
                "runtime_model": evidence["model"],
                "route": route_label,
                "route_verified": True,
                "terminal_execution_evidence": evidence,
            })
            current.pop("terminal_route_incident", None)
            save_json(STATE_PATH, state)
    return True


def terminal_visibility_event_id(
    agent: str,
    work_id: str,
    run_id: str,
    card_key: str,
    status: str,
) -> str:
    _ = status
    material = f"{agent}\0{work_id}\0{run_id}\0{card_key}\0terminal".encode("utf-8")
    return f"telegram-terminal-{agent}-{hashlib.sha256(material).hexdigest()[:32]}"


def terminal_visibility_outbox_path(event_id: str) -> Path:
    digest = hashlib.sha256(str(event_id).encode("utf-8")).hexdigest()
    root = TERMINAL_VISIBILITY_OUTBOX_DIR
    if root == DEFAULT_TERMINAL_VISIBILITY_OUTBOX_DIR and STATE_PATH.parent != root.parent:
        root = STATE_PATH.parent / "terminal-visibility-outbox"
    return root / f"{digest}.json"


def terminal_visibility_age_seconds(record: dict[str, Any]) -> float:
    created = parse_utc(record.get("createdAt"))
    if not created:
        return float(TERMINAL_VISIBILITY_MAX_AGE_SECONDS + 1)
    return max(0.0, (dt.datetime.now(dt.timezone.utc) - created).total_seconds())


def queue_terminal_visibility(
    run_key: str,
    card_key: str,
    card: dict[str, Any],
    status: str,
) -> tuple[Path, dict[str, Any]]:
    """Persist only dashboard-safe terminal publication facts at mode 0600."""
    work_id = str(card.get("work_id") or "")
    ledger_run_id = str(card.get("ledger_run_id") or "")
    origin_claim_hash = str(card.get("origin_claim_hash") or "")
    if not work_id or not ledger_run_id:
        work_id, ledger_run_id, derived_claim = telegram_work_identity(card_key, run_key)
        origin_claim_hash = origin_claim_hash or derived_claim
        card.update({
            "work_id": work_id,
            "ledger_run_id": ledger_run_id,
            "origin_claim_hash": origin_claim_hash,
        })
    event_id = terminal_visibility_event_id("josh2", work_id, ledger_run_id, card_key, status)
    path = terminal_visibility_outbox_path(event_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    existing = load_json(path, {})
    if not isinstance(existing, dict):
        existing = {}
    model_id = str(card.get("runtime_model") or "") if card.get("coordinator_owned") else str(
        card.get("runtime_model") or card.get("model") or DEFAULT_MODEL
    )
    route_verified = bool(card.get("route_verified")) if card.get("coordinator_owned") else True
    record = {
        "version": 1,
        "eventId": event_id,
        "agent": "josh2",
        "workId": work_id,
        "runId": ledger_run_id,
        "cardKeyHash": hashlib.sha256(card_key.encode("utf-8")).hexdigest(),
        "terminalStatus": str(status or "done").lower(),
        "modelId": _safe_progress_fragment(model_id),
        "routeVerified": route_verified,
        "originClaimHash": origin_claim_hash,
        "attempts": int(existing.get("attempts") or 0),
        "createdAt": str(existing.get("createdAt") or utc_now()),
        "updatedAt": utc_now(),
        "lastAttemptAt": str(existing.get("lastAttemptAt") or ""),
        "acceptedAt": str(existing.get("acceptedAt") or ""),
        "blockedAt": str(existing.get("blockedAt") or ""),
        "incident": existing.get("incident") if isinstance(existing.get("incident"), dict) else {},
    }
    # A pending receipt may be upgraded from unverified to verified, but an
    # accepted immutable event is never rewritten.
    if record["acceptedAt"]:
        record = existing
    save_json(path, record)
    return path, record


def mark_terminal_visibility_blocked(
    path: Path,
    record: dict[str, Any],
    code: str,
) -> None:
    age = int(min(86400, terminal_visibility_age_seconds(record)))
    record.update({
        "blockedAt": str(record.get("blockedAt") or utc_now()),
        "updatedAt": utc_now(),
        "incident": {
            "status": "blocked",
            "code": _safe_progress_fragment(code, 80),
            "ageSeconds": age,
        },
    })
    save_json(path, record)


def terminal_outbox_path(run_key: str, card_key: str) -> Path:
    digest = hashlib.sha256(f"{run_key}\0{card_key}".encode("utf-8")).hexdigest()
    return TERMINAL_OUTBOX_DIR / f"{digest}.json"


def queue_terminal_final(
    run_key: str,
    card_key: str,
    card: dict[str, Any],
    meta: dict[str, Any],
    terminal_status: str,
    final_summary: str,
) -> Path:
    """Persist one private, origin-scoped final before any Telegram I/O."""
    path = terminal_outbox_path(run_key, card_key)
    existing = load_json(path, {})
    if not isinstance(existing, dict):
        existing = {}
    save_json(path, {
        "version": 1,
        "run_key": run_key,
        "card_key": card_key,
        "objective": str(card.get("objective") or "").strip(),
        "model": str(card.get("model") or DEFAULT_MODEL),
        "route": str(card.get("route") or DEFAULT_ROUTE),
        "telegram_chat_id": str(meta.get("telegram_chat_id") or ""),
        "telegram_thread_id": str(meta.get("telegram_thread_id") or ""),
        "terminal_status": terminal_status,
        "final_summary": final_summary,
        "work_id": str(card.get("work_id") or card.get("gateway_work_id") or ""),
        "ledger_run_id": str(card.get("ledger_run_id") or ""),
        "origin_claim_hash": str(card.get("origin_claim_hash") or ""),
        "runtime_model": str(card.get("runtime_model") or card.get("model") or DEFAULT_MODEL),
        "coordinator_owned": bool(card.get("coordinator_owned")),
        "job_id": str(card.get("job_id") or ""),
        "terminal_execution_evidence": card.get("terminal_execution_evidence")
        if isinstance(card.get("terminal_execution_evidence"), dict) else {},
        "route_verified": bool(card.get("route_verified")),
        "lifecycle_version": int(card.get("lifecycle_version") or 0),
        "lifecycle_writer_enabled": bool(card.get("lifecycle_writer_enabled")),
        "delivery_tier": int(card.get("delivery_tier") or 0),
        "no_card_required": bool(card.get("no_card_required")),
        "attempts": int(existing.get("attempts") or 0),
        "created_at": str(existing.get("created_at") or utc_now()),
        "updated_at": utc_now(),
        "next_attempt_at": str(existing.get("next_attempt_at") or ""),
        "escalated_at": str(existing.get("escalated_at") or ""),
    })
    return path


def card_uses_lifecycle_v3(card: dict[str, Any]) -> bool:
    return int(card.get("lifecycle_version") or 0) >= 3


def recover_card_lifecycle_identity(card: dict[str, Any]) -> dict[str, Any]:
    """Recover a lost watcher identity from the private lifecycle journal."""
    if card_uses_lifecycle_v3(card) and card.get("work_id"):
        return card
    try:
        lifecycle = gateway_lifecycle()
    except Exception:  # noqa: BLE001 - legacy orphan cleanup has no journal
        return card
    card_key = str(card.get("key") or "")
    if lifecycle is None or not card_key:
        return card
    try:
        with lifecycle.connect() as db:
            row = db.execute(
                "SELECT work_id FROM work_receipts WHERE origin_key=? ORDER BY created_at DESC LIMIT 1",
                (card_key,),
            ).fetchone()
        receipt = lifecycle.read_work(str(row["work_id"])) if row else None
    except Exception:  # noqa: BLE001 - orphan cleanup must remain bounded
        receipt = None
    if not receipt or int(receipt.get("lifecycleVersion") or 0) < 3:
        return card
    card.update({
        "work_id": str(receipt.get("workId") or ""),
        "ledger_run_id": str(receipt.get("runId") or ""),
        "lifecycle_version": int(receipt.get("lifecycleVersion") or 0),
        "delivery_tier": int(receipt.get("deliveryTier") or 0),
        "lifecycle_writer_enabled": bool(receipt.get("writerEnabled")),
        "no_card_required": int(receipt.get("deliveryTier") or 0) in {1, 2},
    })
    return card


def build_terminal_fallback_final(
    card: dict[str, Any],
    *,
    issue: str,
    next_step: str,
) -> str:
    """Build a private, dashboard-safe terminal result for adapter recovery."""
    lines = [
        f"Model: {card.get('runtime_model') or card.get('model') or DEFAULT_MODEL}",
        f"Route: {card.get('route') or DEFAULT_ROUTE}",
        "",
        "Complete: No - the task stopped before a verified result was delivered.",
        "",
        "What was done:",
        "- Preserved the existing Telegram task identity and visible surface.",
        "- Recorded the failure through the canonical terminal lifecycle.",
        "- Prevented an unreceipted fallback from sending a second final.",
        "",
        "Issues:",
        f"- {issue}",
        "",
        "Appropriate next steps:",
        f"- {next_step}",
        "",
        "Approval needed:",
        "n/a",
    ]
    return f"<pre>{html.escape(chr(10).join(lines))}</pre>"


def queue_lifecycle_terminal_fallback(
    run_key: str,
    card: dict[str, Any],
    meta: dict[str, Any],
    *,
    terminal_status: str,
    issue: str,
    next_step: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Queue a v3 fallback; recovery owns commit, intents, and Telegram I/O."""
    card_key = str(card.get("key") or "")
    if not card_uses_lifecycle_v3(card) or not card_key:
        return {"ok": False, "status": "legacy-or-missing-card"}
    if not dry_run:
        queue_terminal_final(
            str(run_key),
            card_key,
            card,
            meta,
            terminal_status,
            build_terminal_fallback_final(card, issue=issue, next_step=next_step),
        )
    now = utc_now()
    card.update({
        "status": "awaiting-final-gate",
        "final_delivery_status": "terminal-fallback-queued",
        "terminal_fallback_queued_at": now,
        "last_card_update_at": now,
    })
    return {
        "ok": True,
        "status": "terminal-fallback-queued",
        "dry_run": dry_run,
    }


@contextmanager
def private_terminal_final_file(final_summary: str):
    TERMINAL_OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    final_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=TERMINAL_OUTBOX_DIR,
            prefix=".terminal-final-",
            suffix=".html",
            delete=False,
        ) as handle:
            handle.write(final_summary)
            handle.flush()
            os.fsync(handle.fileno())
            final_path = Path(handle.name)
        os.chmod(final_path, 0o600)
        yield final_path
    finally:
        if final_path:
            final_path.unlink(missing_ok=True)


def lifecycle_outcome_for_status(status: str) -> str:
    return {
        "done": "succeeded",
        "failed": "failed",
        "cancelled": "cancelled",
        "expired": "expired",
        "superseded": "superseded",
    }.get(str(status or "").lower(), "partial")


def prepare_lifecycle_terminal(
    card: dict[str, Any],
    *,
    terminal_status: str,
    final_summary: str,
) -> dict[str, Any]:
    """Commit the v3 outcome/outbox before any final Telegram API call."""
    work_id = str(card.get("work_id") or card.get("gateway_work_id") or "")
    # Terminal visibility assigns a Control Tower work ID to legacy cards as
    # well.  Treat the ID as a lifecycle journal key only when the card also
    # carries the explicit v3 marker.
    if not card_uses_lifecycle_v3(card) or not work_id:
        return {"managed": False}
    lifecycle = gateway_lifecycle()
    if lifecycle is None:
        return {"managed": False}
    receipt = lifecycle.read_work(work_id)
    if not receipt:
        return {"managed": False}
    # Awaiting approval/input is explicitly nonterminal.  Keep the lifecycle
    # open and let the bound follow-up continue the same work identity.
    if str(terminal_status or "").lower() == "paused":
        if receipt.get("phase") == "terminal":
            raise LifecycleError("paused-work-already-terminal")
        phase = str(receipt.get("phase") or "")
        if phase == "received":
            receipt = lifecycle.transition(
                work_id,
                "classified",
                expected_sequence=int(receipt["sequence"]),
                fencing_epoch=int(receipt["fencingEpoch"]),
            )
            phase = "classified"
        if phase == "classified":
            receipt = lifecycle.transition(
                work_id,
                "acknowledged",
                expected_sequence=int(receipt["sequence"]),
                fencing_epoch=int(receipt["fencingEpoch"]),
            )
            phase = "acknowledged"
        if phase in {"acknowledged", "working", "verifying"}:
            receipt = lifecycle.transition(
                work_id,
                "awaiting_input",
                expected_sequence=int(receipt["sequence"]),
                fencing_epoch=int(receipt["fencingEpoch"]),
                safe_payload={"status": "awaiting_input"},
            )
        return {
            "managed": True,
            "nonterminal": True,
            "lifecycle": lifecycle,
            "receipt": receipt,
        }
    if receipt.get("phase") != "terminal":
        phase = str(receipt.get("phase") or "")
        if phase == "classified":
            receipt = lifecycle.transition(
                work_id,
                "acknowledged",
                expected_sequence=int(receipt["sequence"]),
                fencing_epoch=int(receipt["fencingEpoch"]),
            )
            phase = "acknowledged"
        if phase == "acknowledged":
            receipt = lifecycle.transition(
                work_id,
                "working",
                expected_sequence=int(receipt["sequence"]),
                fencing_epoch=int(receipt["fencingEpoch"]),
            )
            phase = "working"
        if phase in {"working", "awaiting_input"}:
            receipt = lifecycle.transition(
                work_id,
                "verifying",
                expected_sequence=int(receipt["sequence"]),
                fencing_epoch=int(receipt["fencingEpoch"]),
                safe_payload={"status": "verifying"},
            )
    private_payload = {
        "finalHtml": final_summary,
        "cardKey": str(card.get("key") or ""),
        "terminalStatus": str(terminal_status or "done"),
    }
    # Also call commit on terminal re-entry: its payload hash check prevents a
    # retry from replacing the already-authoritative private final.
    lifecycle.commit_terminal(
        work_id,
        lifecycle_outcome_for_status(terminal_status),
        expected_sequence=int(receipt["sequence"]),
        fencing_epoch=int(receipt["fencingEpoch"]),
        private_payload=private_payload,
    )
    receipt = lifecycle.read_work(work_id) or receipt
    writer = bool(receipt.get("writerEnabled"))
    if not writer:
        pinned_writer = bool(receipt.get("writerAuthorityAtStart"))
        return {
            "managed": True,
            "shadow": bool(receipt.get("shadowOnly")),
            "writer": False,
            "killed": pinned_writer,
            "lifecycle": lifecycle,
            "receipt": receipt,
        }
    # The durable outbox is claimed before the final-effect reservation.  A
    # crash at either point leaves `sending`, which recovery fences instead of
    # guessing whether Telegram accepted the request.
    claim = lifecycle.claim_terminal_delivery(work_id)
    state = str(claim.get("state") or "")
    if not claim.get("allowed"):
        return {
            "managed": True,
            "writer": True,
            "lifecycle": lifecycle,
            "receipt": receipt,
            "claim": claim,
            "allowed": False,
            "already_delivered": state == "delivered",
            "fenced": state in {"sending", "indeterminate"},
            "state": state,
        }
    card_edit_claim: dict[str, Any] = {}
    if int(receipt.get("deliveryTier") or 0) == 3 and not card.get("no_card_required"):
        card_edit_claim = lifecycle.claim_effect(
            work_id,
            "card_edit",
            sequence=int(receipt["sequence"]),
            fencing_epoch=int(receipt["fencingEpoch"]),
        )
        card_edit_state = str(card_edit_claim.get("state") or "")
        if not card_edit_claim.get("allowed"):
            lifecycle.finish_terminal_delivery(work_id, "indeterminate")
            return {
                "managed": True,
                "writer": True,
                "lifecycle": lifecycle,
                "receipt": lifecycle.read_work(work_id) or receipt,
                "claim": claim,
                "card_edit_claim": card_edit_claim,
                "allowed": False,
                "fenced": card_edit_state in {"sending", "indeterminate"},
                "state": card_edit_state,
            }
    effect_claim = lifecycle.claim_effect(
        work_id,
        "final",
        sequence=int(receipt["sequence"]),
        fencing_epoch=int(receipt["fencingEpoch"]),
    )
    effect_state = str(effect_claim.get("state") or "")
    if not effect_claim.get("allowed"):
        card_edit_key = str(card_edit_claim.get("idempotencyKey") or "")
        if card_edit_key and card_edit_claim.get("allowed"):
            lifecycle.finish_effect(
                card_edit_key,
                state="dead_letter",
                error_class="terminal-final-effect-fenced",
            )
        if effect_state == "delivered":
            lifecycle.finish_terminal_delivery(work_id, "delivered")
        else:
            lifecycle.finish_terminal_delivery(work_id, "indeterminate")
        return {
            "managed": True,
            "writer": True,
            "lifecycle": lifecycle,
            "receipt": lifecycle.read_work(work_id) or receipt,
            "claim": claim,
            "card_edit_claim": card_edit_claim,
            "effect_claim": effect_claim,
            "allowed": False,
            "already_delivered": effect_state == "delivered",
            "fenced": effect_state in {"sending", "indeterminate"},
            "state": effect_state,
        }
    committed_payload = claim.get("payload") if isinstance(claim.get("payload"), dict) else private_payload
    return {
        "managed": True,
        "writer": True,
        "lifecycle": lifecycle,
        "receipt": receipt,
        "claim": claim,
        "card_edit_claim": card_edit_claim,
        "effect_claim": effect_claim,
        "allowed": True,
        "already_delivered": False,
        "fenced": False,
        "state": state,
        "final_summary": str(committed_payload.get("finalHtml") or ""),
    }


def finish_lifecycle_terminal(prepared: dict[str, Any], *, state: str) -> None:
    if prepared.get("shadow"):
        lifecycle = prepared.get("lifecycle")
        receipt = prepared.get("receipt") or {}
        if lifecycle is not None and receipt.get("workId"):
            lifecycle.finish_shadow_sample(
                str(receipt["workId"]),
                delivered=state == "delivered",
            )
        return
    if not prepared.get("writer") or not prepared.get("allowed"):
        return
    lifecycle = prepared.get("lifecycle")
    receipt = prepared.get("receipt") or {}
    if lifecycle is not None and receipt.get("workId"):
        for claim_name, error_prefix in (
            ("card_edit_claim", "telegram-card-edit"),
            ("effect_claim", "telegram-final"),
        ):
            effect_claim = prepared.get(claim_name) or {}
            effect_key = str(effect_claim.get("idempotencyKey") or "")
            if not effect_key or not effect_claim.get("allowed"):
                continue
            lifecycle.finish_effect(
                effect_key,
                state=state,
                private_receipt="telegram-confirmed" if state == "delivered" else "",
                error_class=f"{error_prefix}-receipt-missing" if state == "indeterminate" else "",
            )
        lifecycle.finish_terminal_delivery(str(receipt["workId"]), state)


def send_gateway_final_without_card(
    final_summary: str,
    *,
    meta: dict[str, Any],
    timeout: int = 10,
) -> dict[str, Any]:
    """Trusted final-only adapter for Tier 1/2 work."""
    if not API_BASE or build_payload is None:
        return {"ok": False, "error": "telegram-adapter-unavailable"}
    payload = apply_telegram_target(build_payload(final_summary, None, silent=True), meta)
    payload["parse_mode"] = "HTML"
    payload["disable_web_page_preview"] = True
    return api_post("sendMessage", payload, timeout=timeout)


def terminal_work_card_command(
    card_key: str,
    card: dict[str, Any],
    meta: dict[str, Any],
    terminal_status: str,
    final_path: Path | None = None,
) -> list[str]:
    terminal_action, terminal_done, terminal_blocker = terminal_action_fields(terminal_status)
    cmd = [
        "python3",
        str(WORK_CARD_SCRIPT),
        terminal_action,
        "--key",
        card_key,
        "--title",
        str(card.get("objective") or "").strip(),
        "--model",
        str(card.get("runtime_model") or card.get("model") or DEFAULT_MODEL),
        "--route",
        str(card.get("route") or DEFAULT_ROUTE),
        "--done",
        terminal_done,
        "--blocker",
        terminal_blocker,
        "--timeout",
        "6",
    ]
    if final_path:
        cmd.extend(["--final-text-file", str(final_path)])
    else:
        cmd.append("--no-final-summary")
    return with_work_card_target(cmd, meta)


def build_stale_gate_recovery_final(card: dict[str, Any]) -> str:
    def wrapped(line: str, indent: str = "   ") -> list[str]:
        return textwrap.wrap(
            line,
            width=38,
            subsequent_indent=indent,
            break_long_words=True,
            break_on_hyphens=False,
        ) or [""]

    def bullet(line: str) -> list[str]:
        return textwrap.wrap(
            f"- {line}",
            width=38,
            subsequent_indent="  ",
            break_long_words=True,
            break_on_hyphens=False,
        )

    model = str(card.get("model") or DEFAULT_MODEL)
    route = str(card.get("route") or "Josh 2.0 Inbox")
    lines = [
        *wrapped(f"Model: {model} | Route: {route} | Why: delivery recovery"),
        "",
        *wrapped("Complete: No - detailed result unavailable."),
        "",
        "What was done:",
        *bullet("The agent run reached final review."),
        *bullet("The existing live card was preserved."),
        *bullet("Automatic recovery closed the stale gate."),
        "",
        "Issues:",
        *bullet("The original final was not retained."),
        "",
        "Appropriate next steps:",
        *wrapped("Review the task only if detail is needed."),
        "",
        "Approval needed:",
        "n/a",
    ]
    return f"<pre>{html.escape(chr(10).join(lines))}</pre>"


def queue_stale_final_gate_recovery(state: dict[str, Any], dry_run: bool = False) -> list[dict[str, Any]]:
    active = state.get("active_cards") if isinstance(state.get("active_cards"), dict) else {}
    now = dt.datetime.now(dt.timezone.utc)
    queued: list[dict[str, Any]] = []
    for run_key, card in active.items():
        if not isinstance(card, dict) or str(card.get("status") or "").lower() != "awaiting-final-gate":
            continue
        last = parse_utc(card.get("last_card_update_at") or card.get("last_progress_at"))
        if not last or (now - last).total_seconds() <= STALE_FINAL_GATE_SECONDS:
            continue
        card_key = str(card.get("key") or "")
        if not card_key:
            continue
        if card.get("no_card_required"):
            # A missing model-final callback is not permission to create a
            # Tier 1/2 card or synthesize a second terminal message.
            card["terminal_recovery_blocked_at"] = card.get("terminal_recovery_blocked_at") or utc_now()
            queued.append({
                "event": f"stale-final-gate:{run_key}",
                "result": {"ok": True, "status": "no-card-awaiting-final", "dry_run": dry_run},
            })
            continue
        receipt = work_card_state_receipt(card_key)
        if receipt["final_message_id"]:
            status = receipt["status"].lower() if receipt["status"].lower() in TERMINAL_CARD_STATUSES else "done"
            card.update({"status": status, "ended_at": utc_now(), "last_card_update_at": utc_now()})
            publish_terminal_once(str(run_key), card_key, status)
            queued.append({"event": f"stale-final-gate:{run_key}", "result": {"ok": True, "status": "already-delivered"}})
            continue
        outbox = terminal_outbox_path(str(run_key), card_key)
        if outbox.exists():
            continue
        meta = {
            "telegram_chat_id": str(card.get("telegram_chat_id") or ""),
            "telegram_thread_id": str(card.get("telegram_thread_id") or ""),
        }
        if not dry_run:
            queue_terminal_final(
                str(run_key),
                card_key,
                card,
                meta,
                "paused",
                build_stale_gate_recovery_final(card),
            )
            card["terminal_recovery_queued_at"] = utc_now()
        queued.append({"event": f"stale-final-gate:{run_key}", "result": {"ok": True, "status": "recovery-queued", "dry_run": dry_run}})
    return queued


def reconcile_stale_terminal_closes(state: dict[str, Any], dry_run: bool = False) -> list[dict[str, Any]]:
    """End abandoned UI close leases without retrying an uncertain final.

    The v3 lifecycle permanently fences an indeterminate Telegram send.  The
    UI lease is a separate state machine, however, and a process can die after
    claiming it.  Leaving that lease in ``closing-before-final`` makes every
    later poll skip the same live card forever.  Reconcile only the existing
    card surface to an honest failure state; never send or requeue a final.
    """
    active = state.get("active_cards") if isinstance(state.get("active_cards"), dict) else {}
    now = dt.datetime.now(dt.timezone.utc)
    reconciled: list[dict[str, Any]] = []
    for run_key, card in active.items():
        if not isinstance(card, dict) or str(card.get("status") or "").lower() != "closing-before-final":
            continue
        started = parse_utc(card.get("terminal_close_started_at"))
        if started and (now - started).total_seconds() <= TERMINAL_CLOSE_LEASE_SECONDS:
            continue
        card_key = str(card.get("key") or "")
        if not card_key:
            continue
        receipt = work_card_state_receipt(card_key)
        if receipt["final_message_id"]:
            status = receipt["status"].lower() if receipt["status"].lower() in TERMINAL_CARD_STATUSES else "done"
            ended_at = utc_now()
            if not dry_run:
                card.update({"status": status, "ended_at": ended_at, "last_card_update_at": ended_at})
                card.pop("terminal_close_started_at", None)
                publish_terminal_once(str(run_key), card_key, status)
            reconciled.append({
                "event": f"stale-terminal-close:{run_key}",
                "result": {"ok": True, "status": "already-delivered", "dry_run": dry_run},
            })
            continue

        # Editing the existing card is idempotent and does not create a final
        # Telegram message.  It is therefore safe even when the final send is
        # indeterminate, while any resend remains fenced by the lifecycle.
        result: dict[str, Any] = {"ok": True}
        if not dry_run and not card.get("no_card_required"):
            meta = {
                "telegram_chat_id": str(card.get("telegram_chat_id") or ""),
                "telegram_thread_id": str(card.get("telegram_thread_id") or ""),
            }
            command = [
                "python3",
                str(WORK_CARD_SCRIPT),
                "fail",
                "--key",
                card_key,
                "--title",
                str(card.get("objective") or "").strip(),
                "--model",
                str(card.get("runtime_model") or card.get("model") or DEFAULT_MODEL),
                "--route",
                str(card.get("route") or DEFAULT_ROUTE),
                "--done",
                "Work completed; final delivery receipt unavailable",
                "--blocker",
                "Final delivery could not be confirmed",
                "--timeout",
                "6",
                "--no-final-summary",
            ]
            result = run_cmd(with_work_card_target(command, meta), timeout=10)
        if not dry_run and (bool(result.get("ok")) or card.get("no_card_required")):
            ended_at = utc_now()
            card.update({
                "status": "failed",
                "ended_at": ended_at,
                "last_progress_at": ended_at,
                "last_card_update_at": ended_at,
                "final_contract_status": "delivery_indeterminate",
                "final_delivery_status": "indeterminate",
                "terminal_delivery_state": "indeterminate",
                "terminal_close_reconciled_at": ended_at,
            })
            card.pop("terminal_close_started_at", None)
            publish_terminal_once(str(run_key), card_key, "failed")
        elif not dry_run:
            # Keep the lease retryable when even the idempotent card edit did
            # not receive a receipt.  The next poll may retry the same edit,
            # while the final-message send remains permanently fenced.
            card.update({
                "final_contract_status": "delivery_indeterminate",
                "final_delivery_status": "indeterminate",
                "terminal_delivery_state": "indeterminate",
                "terminal_card_reconcile_error": "existing-card-edit-unconfirmed",
                "terminal_close_started_at": utc_now(),
            })
        reconciled.append({
            "event": f"stale-terminal-close:{run_key}",
            "result": {
                "ok": bool(result.get("ok")),
                "status": "delivery-indeterminate-needs-attention",
                "dry_run": dry_run,
            },
        })
    return reconciled


def recover_terminal_final_outbox(state: dict[str, Any], dry_run: bool = False) -> list[dict[str, Any]]:
    if not TERMINAL_OUTBOX_DIR.exists():
        return []
    active = state.get("active_cards") if isinstance(state.get("active_cards"), dict) else {}
    recovered: list[dict[str, Any]] = []
    for path in sorted(TERMINAL_OUTBOX_DIR.glob("*.json")):
        record = load_json(path, {})
        if not isinstance(record, dict):
            path.unlink(missing_ok=True)
            continue
        run_key = str(record.get("run_key") or "")
        card_key = str(record.get("card_key") or "")
        final_summary = str(record.get("final_summary") or "").strip()
        if not run_key or not card_key or not final_summary:
            path.unlink(missing_ok=True)
            continue
        next_attempt = parse_utc(record.get("next_attempt_at"))
        if next_attempt and dt.datetime.now(dt.timezone.utc) < next_attempt:
            continue
        card = active.get(run_key) if isinstance(active.get(run_key), dict) else {
            "key": card_key,
            "objective": record.get("objective"),
            "model": record.get("model"),
            "runtime_model": record.get("runtime_model"),
            "route": record.get("route"),
            "work_id": record.get("work_id"),
            "ledger_run_id": record.get("ledger_run_id"),
            "origin_claim_hash": record.get("origin_claim_hash"),
            "lifecycle_version": record.get("lifecycle_version"),
            "lifecycle_writer_enabled": bool(record.get("lifecycle_writer_enabled")),
            "delivery_tier": int(record.get("delivery_tier") or 0),
            "coordinator_owned": bool(record.get("coordinator_owned")),
            "job_id": str(record.get("job_id") or ""),
            "terminal_execution_evidence": record.get("terminal_execution_evidence")
            if isinstance(record.get("terminal_execution_evidence"), dict) else {},
            "route_verified": bool(record.get("route_verified")),
            "no_card_required": bool(record.get("no_card_required")),
        }
        if str(card.get("key") or "") != card_key:
            continue
        receipt = work_card_state_receipt(card_key)
        if receipt["final_message_id"]:
            status = receipt["status"].lower() if receipt["status"].lower() in TERMINAL_CARD_STATUSES else str(record.get("terminal_status") or "paused")
            if run_key in active:
                ended_at = utc_now()
                active[run_key].update({"status": status, "ended_at": ended_at, "last_card_update_at": ended_at})
            publish_terminal_once(run_key, card_key, status)
            path.unlink(missing_ok=True)
            recovered.append({"event": f"terminal-outbox:{run_key}", "result": {"ok": True, "status": "already-delivered"}})
            continue
        if dry_run:
            recovered.append({"event": f"terminal-outbox:{run_key}", "result": {"ok": True, "status": "retry-planned", "dry_run": True}})
            continue
        recovery_terminal_status = str(record.get("terminal_status") or "paused").lower()
        if recovery_terminal_status != "paused" and not publish_terminal_once(
            run_key,
            card_key,
            recovery_terminal_status,
            card_snapshot=card,
        ):
            _visibility_path, visibility_record = queue_terminal_visibility(
                run_key,
                card_key,
                card,
                recovery_terminal_status,
            )
            blocked = bool(visibility_record.get("blockedAt"))
            record.update({
                "last_error": "terminal-visibility-publication-blocked" if blocked else "terminal-visibility-publication-pending",
                "visibility_blocked_at": str(visibility_record.get("blockedAt") or ""),
                "visibility_event_id": str(visibility_record.get("eventId") or ""),
                "updated_at": utc_now(),
            })
            save_json(path, record)
            recovered.append({
                "event": f"terminal-outbox:{run_key}",
                "result": {
                    "ok": False,
                    "status": "terminal-visibility-blocked" if blocked else "terminal-visibility-pending",
                },
            })
            continue
        meta = {
            "telegram_chat_id": str(record.get("telegram_chat_id") or ""),
            "telegram_thread_id": str(record.get("telegram_thread_id") or ""),
        }
        terminal_status = str(record.get("terminal_status") or "paused").lower()
        prepared: dict[str, Any] = {}
        lifecycle_managed = card_uses_lifecycle_v3(card)
        work_id = str(card.get("work_id") or card.get("gateway_work_id") or "")
        if lifecycle_managed and not work_id:
            record.update({
                "lifecycle_fenced_at": record.get("lifecycle_fenced_at") or utc_now(),
                "last_error": "v3-work-identity-missing",
                "updated_at": utc_now(),
            })
            save_json(path, record)
            recovered.append({
                "event": f"terminal-outbox:{run_key}",
                "result": {"ok": False, "status": "lifecycle-final-fenced"},
            })
            continue
        if work_id:
            try:
                prepared = prepare_lifecycle_terminal(
                    card,
                    terminal_status=terminal_status,
                    final_summary=final_summary,
                )
            except Exception as exc:  # noqa: BLE001 - never bypass a v3 conflict
                record.update({
                    "lifecycle_fenced_at": record.get("lifecycle_fenced_at") or utc_now(),
                    "last_error": type(exc).__name__,
                    "updated_at": utc_now(),
                })
                save_json(path, record)
                recovered.append({
                    "event": f"terminal-outbox:{run_key}",
                    "result": {"ok": False, "status": "lifecycle-final-fenced"},
                })
                continue
        if lifecycle_managed and not prepared.get("managed"):
            record.update({
                "lifecycle_fenced_at": record.get("lifecycle_fenced_at") or utc_now(),
                "last_error": "v3-lifecycle-receipt-unavailable",
                "updated_at": utc_now(),
            })
            save_json(path, record)
            recovered.append({
                "event": f"terminal-outbox:{run_key}",
                "result": {"ok": False, "status": "lifecycle-final-fenced"},
            })
            continue
        if prepared.get("already_delivered"):
            status = terminal_status if terminal_status in TERMINAL_CARD_STATUSES else "done"
            if run_key in active:
                ended_at = utc_now()
                active[run_key].update({"status": status, "ended_at": ended_at, "last_card_update_at": ended_at})
            publish_terminal_once(run_key, card_key, status)
            path.unlink(missing_ok=True)
            recovered.append({"event": f"terminal-outbox:{run_key}", "result": {"ok": True, "status": "already-delivered"}})
            continue
        if prepared.get("killed") or prepared.get("fenced") or (prepared.get("writer") and not prepared.get("allowed")):
            record.update({
                "lifecycle_fenced_at": record.get("lifecycle_fenced_at") or utc_now(),
                "last_error": "indeterminate-final-delivery-fenced",
                "updated_at": utc_now(),
            })
            save_json(path, record)
            recovered.append({
                "event": f"terminal-outbox:{run_key}",
                "result": {"ok": False, "status": "final-delivery-indeterminate"},
            })
            continue
        if prepared.get("nonterminal"):
            # Paused/awaiting-input is deliberately not a terminal outcome.
            # Discard the stale fallback without invoking either terminal
            # helper, regardless of whether this delivery tier has a card.
            path.unlink(missing_ok=True)
            recovered.append({
                "event": f"terminal-outbox:{run_key}",
                "result": {"ok": True, "status": "nonterminal-paused"},
            })
            continue
        if prepared.get("shadow") and receipt.get("final_delivery_status") == "indeterminate":
            finish_lifecycle_terminal(prepared, state="indeterminate")
            record.update({
                "lifecycle_fenced_at": record.get("lifecycle_fenced_at") or utc_now(),
                "last_error": "shadow-final-delivery-indeterminate",
                "updated_at": utc_now(),
            })
            save_json(path, record)
            recovered.append({
                "event": f"terminal-outbox:{run_key}",
                "result": {"ok": False, "status": "final-delivery-indeterminate"},
            })
            continue
        authoritative_final = str(prepared.get("final_summary") or final_summary)
        if card.get("no_card_required"):
            if not prepared.get("writer"):
                record.update({
                    "lifecycle_fenced_at": record.get("lifecycle_fenced_at") or utc_now(),
                    "last_error": "no-card-lifecycle-unavailable",
                    "updated_at": utc_now(),
                })
                save_json(path, record)
                recovered.append({
                    "event": f"terminal-outbox:{run_key}",
                    "result": {"ok": False, "status": "no-card-recovery-fenced"},
                })
                continue
            result = send_gateway_final_without_card(authoritative_final, meta=meta)
            delivered = bool(
                result.get("ok")
                and positive_telegram_message_id((result.get("result") or {}).get("message_id"))
            )
            finish_lifecycle_terminal(prepared, state="delivered" if delivered else "indeterminate")
            if delivered:
                ended_at = utc_now()
                if run_key in active:
                    active[run_key].update({"status": "done", "ended_at": ended_at, "last_card_update_at": ended_at})
                publish_terminal_once(run_key, card_key, "done")
                path.unlink(missing_ok=True)
                recovered.append({"event": f"terminal-outbox:{run_key}", "result": {"ok": True, "status": "delivered"}})
            else:
                record.update({
                    "lifecycle_fenced_at": utc_now(),
                    "last_error": "indeterminate-final-delivery-fenced",
                    "updated_at": utc_now(),
                })
                save_json(path, record)
                recovered.append({
                    "event": f"terminal-outbox:{run_key}",
                    "result": {"ok": False, "status": "final-delivery-indeterminate"},
                })
            continue
        try:
            with private_terminal_final_file(authoritative_final) as final_path:
                result = run_cmd(
                    terminal_work_card_command(card_key, card, meta, terminal_status, final_path),
                    timeout=10,
                )
        except subprocess.TimeoutExpired:
            result = {"ok": False, "returncode": -1, "stderr": "terminal outbox retry timed out"}
        persisted = work_card_state_receipt(card_key)
        if persisted["final_message_id"] and persisted["status"].lower() in TERMINAL_CARD_STATUSES:
            finish_lifecycle_terminal(prepared, state="delivered")
            ended_at = utc_now()
            if run_key in active:
                active[run_key].update({
                    "status": persisted["status"].lower(),
                    "ended_at": ended_at,
                    "last_progress_at": ended_at,
                    "last_card_update_at": ended_at,
                    "terminal_closed_before_final_at": ended_at,
                })
            publish_terminal_once(run_key, card_key, persisted["status"].lower())
            path.unlink(missing_ok=True)
            recovered.append({"event": f"terminal-outbox:{run_key}", "result": {"ok": True, "status": "delivered"}})
            continue
        if prepared.get("writer"):
            # The helper may have reached Telegram even without a durable
            # message receipt.  Fence the v3 outbox permanently; do not fall
            # through to the legacy timed retry loop.
            finish_lifecycle_terminal(prepared, state="indeterminate")
            record.update({
                "lifecycle_fenced_at": utc_now(),
                "last_error": "indeterminate-final-delivery-fenced",
                "updated_at": utc_now(),
            })
            save_json(path, record)
            recovered.append({
                "event": f"terminal-outbox:{run_key}",
                "result": {"ok": False, "status": "final-delivery-indeterminate"},
            })
            continue
        attempts = int(record.get("attempts") or 0) + 1
        delay = min(300, 2 ** min(attempts, 8))
        record.update({
            "attempts": attempts,
            "last_attempt_at": utc_now(),
            "next_attempt_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delay)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "last_error": str(result.get("stderr") or "terminal delivery retry failed")[:240],
            "updated_at": utc_now(),
        })
        if attempts >= TERMINAL_OUTBOX_MAX_ATTEMPTS and not record.get("escalated_at"):
            record["escalated_at"] = utc_now()
            if prepared.get("shadow"):
                finish_lifecycle_terminal(prepared, state="dead_letter")
                record["shadow_sample_state"] = "unclean"
            publish_josh(
                str(card.get("objective") or "Telegram final delivery"),
                "blocked",
                "Automatic final delivery retries are still failing; the private final remains queued.",
            )
        save_json(path, record)
        recovered.append({"event": f"terminal-outbox:{run_key}", "result": {"ok": False, "status": "retry-queued", "attempts": attempts}})
    return recovered


def persist_terminal_card_state(run_key: str, card_key: str, status: str = "done") -> None:
    with fast_ack_state_lock():
        latest = load_json(STATE_PATH, {})
        if not isinstance(latest, dict):
            latest = {}
        active = latest.setdefault("active_cards", {})
        card = active.get(run_key) if isinstance(active, dict) else None
        if not isinstance(card, dict) or str(card.get("key") or "") != card_key:
            return
        ended_at = utc_now()
        card.update({
            "status": status,
            "ended_at": ended_at,
            "last_progress_at": ended_at,
            "last_card_update_at": ended_at,
            "terminal_closed_before_final_at": ended_at,
        })
        save_json(STATE_PATH, latest)


def publish_terminal_once(
    run_key: str,
    card_key: str,
    status: str,
    *,
    card_snapshot: dict[str, Any] | None = None,
) -> bool:
    """Accept one durable terminal work event before Telegram final delivery."""
    snapshot: dict[str, Any] = {}
    with fast_ack_state_lock():
        latest = load_json(STATE_PATH, {})
        active = latest.get("active_cards") if isinstance(latest, dict) else {}
        card = active.get(run_key) if isinstance(active, dict) else None
        if not isinstance(card, dict) or str(card.get("key") or "") != card_key:
            card = card_snapshot
        if not isinstance(card, dict) or str(card.get("key") or "") != card_key:
            return False
        snapshot = dict(card)

    route_ready = ensure_terminal_route_truth(run_key, snapshot)
    if route_ready and isinstance(card_snapshot, dict):
        card_snapshot.update({
            "runtime_model": snapshot.get("runtime_model"),
            "route": snapshot.get("route"),
            "route_verified": snapshot.get("route_verified"),
            "terminal_execution_evidence": snapshot.get("terminal_execution_evidence"),
        })
    path, record = queue_terminal_visibility(run_key, card_key, snapshot, status)
    if isinstance(card_snapshot, dict):
        card_snapshot.update({
            "work_id": str(record.get("workId") or ""),
            "ledger_run_id": str(record.get("runId") or ""),
            "origin_claim_hash": str(record.get("originClaimHash") or ""),
        })
    if not route_ready:
        mark_terminal_visibility_blocked(path, record, "terminal-route-unverified")
        with fast_ack_state_lock():
            latest = load_json(STATE_PATH, {})
            active = latest.get("active_cards") if isinstance(latest, dict) else {}
            card = active.get(run_key) if isinstance(active, dict) else None
            if isinstance(card, dict) and str(card.get("key") or "") == card_key:
                card["terminal_visibility_blocked_at"] = card.get("terminal_visibility_blocked_at") or utc_now()
                card["terminal_visibility_incident"] = "terminal-route-unverified"
                save_json(STATE_PATH, latest)
        return False
    # Rebuild the pending record after route verification so only actual
    # provider/model evidence can become the accepted terminal event.
    path, record = queue_terminal_visibility(run_key, card_key, snapshot, status)
    if record.get("acceptedAt"):
        published = True
    elif (
        int(record.get("attempts") or 0) >= TERMINAL_VISIBILITY_MAX_ATTEMPTS
        or terminal_visibility_age_seconds(record) > TERMINAL_VISIBILITY_MAX_AGE_SECONDS
    ):
        mark_terminal_visibility_blocked(path, record, "terminal-visibility-publication-stale")
        published = False
    else:
        canonical_status = {
            "failed": "error",
            "error": "error",
            "blocked": "blocked",
            "paused": "cancelled",
            "cancelled": "cancelled",
            "expired": "cancelled",
        }.get(str(status or "").lower(), "done")
        published = publish_josh(
            "Josh 2.0 Telegram task",
            canonical_status,
            "Terminal outcome accepted by the canonical local work ledger before Telegram delivery.",
            work_id=str(record.get("workId") or ""),
            run_id=str(record.get("runId") or ""),
            phase=canonical_status,
            model_id=str(record.get("modelId") or ""),
            route_verified=bool(record.get("routeVerified")),
            origin_claim_hash=str(record.get("originClaimHash") or ""),
            brain_feed=False,
            work_event="terminal",
            event_id=str(record.get("eventId") or ""),
        )
        latest_record = load_json(path, record)
        if not isinstance(latest_record, dict):
            latest_record = record
        if latest_record.get("acceptedAt"):
            published = True
        latest_record.update({
            "attempts": int(latest_record.get("attempts") or 0) + 1,
            "lastAttemptAt": utc_now(),
            "updatedAt": utc_now(),
        })
        if published:
            latest_record["acceptedAt"] = str(latest_record.get("acceptedAt") or utc_now())
            latest_record["incident"] = {}
            latest_record["blockedAt"] = ""
            save_json(path, latest_record)
        elif (
            int(latest_record.get("attempts") or 0) >= TERMINAL_VISIBILITY_MAX_ATTEMPTS
            or terminal_visibility_age_seconds(latest_record) > TERMINAL_VISIBILITY_MAX_AGE_SECONDS
        ):
            mark_terminal_visibility_blocked(
                path,
                latest_record,
                "terminal-visibility-publication-stale",
            )
        else:
            save_json(path, latest_record)

    with fast_ack_state_lock():
        latest = load_json(STATE_PATH, {})
        active = latest.get("active_cards") if isinstance(latest, dict) else {}
        card = active.get(run_key) if isinstance(active, dict) else None
        if isinstance(card, dict) and str(card.get("key") or "") == card_key:
            card.update({
                "work_id": str(record.get("workId") or card.get("work_id") or ""),
                "ledger_run_id": str(record.get("runId") or card.get("ledger_run_id") or ""),
                "origin_claim_hash": str(record.get("originClaimHash") or card.get("origin_claim_hash") or ""),
            })
            if published:
                card["ledger_terminal_published_at"] = utc_now()
                card["terminal_visibility_event_id"] = str(record.get("eventId") or "")
                card.pop("terminal_visibility_incident", None)
                card.pop("terminal_visibility_blocked_at", None)
            else:
                current_record = load_json(path, record)
                if isinstance(current_record, dict) and current_record.get("blockedAt"):
                    card["terminal_visibility_blocked_at"] = str(current_record.get("blockedAt"))
                    card["terminal_visibility_incident"] = str(
                        (current_record.get("incident") or {}).get("code") or "terminal-visibility-pending"
                    )
            save_json(STATE_PATH, latest)
        elif isinstance(card_snapshot, dict) and str(card_snapshot.get("key") or "") == card_key:
            if published:
                card_snapshot["ledger_terminal_published_at"] = utc_now()
                card_snapshot["terminal_visibility_event_id"] = str(record.get("eventId") or "")
    return published


def claim_terminal_card_close(run_key: str, card_key: str) -> str:
    """Atomically fence the poller before the pre-final Telegram edit."""
    with fast_ack_state_lock():
        latest = load_json(STATE_PATH, {})
        active = latest.get("active_cards") if isinstance(latest, dict) else {}
        card = active.get(run_key) if isinstance(active, dict) else None
        if not isinstance(card, dict) or str(card.get("key") or "") != card_key:
            return "missing"
        status = str(card.get("status") or "").lower()
        if status in TERMINAL_CARD_STATUSES:
            return "terminal"
        if status == "closing-before-final":
            started = parse_utc(card.get("terminal_close_started_at"))
            age = (dt.datetime.now(dt.timezone.utc) - started).total_seconds() if started else None
            if age is not None and age <= TERMINAL_CLOSE_LEASE_SECONDS:
                return "closing"
            # A helper can die after fencing the poller but before releasing
            # the lease. Reclaim only an expired/malformed close claim so the
            # exact run can finish without operator intervention.
            card["terminal_close_recovered_at"] = utc_now()
        card["status"] = "closing-before-final"
        card["terminal_close_started_at"] = utc_now()
        save_json(STATE_PATH, latest)
        return "claimed"


def release_terminal_card_close(run_key: str, card_key: str) -> None:
    with fast_ack_state_lock():
        latest = load_json(STATE_PATH, {})
        active = latest.get("active_cards") if isinstance(latest, dict) else {}
        card = active.get(run_key) if isinstance(active, dict) else None
        if (
            isinstance(card, dict)
            and str(card.get("key") or "") == card_key
            and str(card.get("status") or "") == "closing-before-final"
        ):
            card["status"] = "active"
            card.pop("terminal_close_started_at", None)
            save_json(STATE_PATH, latest)


def is_native_fallback_placeholder(card: dict[str, Any] | None) -> bool:
    """Prove that a pending run never acquired a coordinator-owned surface.

    A native OpenCLAW fallback may finalize only when the exact-run placeholder
    has no durable worker or Telegram-card evidence.  Any ownership or surface
    receipt keeps the transactional close gate fail-closed.
    """
    if not isinstance(card, dict):
        return False
    return bool(
        card.get("requires_objective_interpretation")
        and str(card.get("status") or "").lower() in {
            "pending-interpretation",
            "awaiting-objective-interpretation",
        }
        and not card.get("coordinator_owned")
        and not str(card.get("job_id") or "").strip()
        and not card.get("card_start_ok")
        and not positive_telegram_message_id(card.get("header_message_id"))
        and not positive_telegram_message_id(card.get("live_message_id"))
    )


def close_before_final(args: argparse.Namespace) -> dict[str, Any]:
    """Close the existing live card before OpenCLAW may deliver its final reply."""
    final_summary = sys.stdin.read().strip() if getattr(args, "final_from_stdin", False) else ""
    if getattr(args, "final_from_stdin", False) and not final_summary:
        return {"ok": False, "status": "missing-final-summary", "card_closed": False}
    meta = terminal_request_meta(args)
    if not exact_control_center_inbox(meta):
        return {"ok": True, "status": "not-applicable", "card_closed": False}

    with fast_ack_state_lock():
        state = load_json(STATE_PATH, {})
        if not isinstance(state, dict):
            state = {}
        adopt_interpreted_work_cards(state, meta=meta)
        selected = select_terminal_card(state, args, meta)
        if selected and is_native_fallback_placeholder(selected[1]):
            _run_key, placeholder = selected
            placeholder["no_card_required"] = True
            placeholder["status"] = "done"
            placeholder["ended_at"] = utc_now()
            placeholder["last_card_update_at"] = placeholder["ended_at"]
            placeholder["native_fallback_finalized_at"] = placeholder["ended_at"]
        save_json(STATE_PATH, state)

    if not selected:
        return {
            "ok": False,
            "status": "run-card-not-ready" if getattr(args, "run_id", "") else "no-active-card",
            "card_closed": False,
        }
    run_key, card = selected
    if card.get("no_card_required"):
        if final_summary:
            requested_status = str(getattr(args, "terminal_status", "done") or "done").lower()
            outbox_path: Path | None = None
            if requested_status != "paused":
                try:
                    outbox_path = queue_terminal_final(
                        run_key,
                        str(card.get("key") or ""),
                        card,
                        meta,
                        requested_status,
                        final_summary,
                    )
                except Exception:
                    return {
                        "ok": False,
                        "status": "terminal-outbox-unavailable",
                        "card_closed": False,
                        "suppress_native_final": True,
                    }
                if not publish_terminal_once(
                    run_key,
                    str(card.get("key") or ""),
                    requested_status,
                    card_snapshot=card,
                ):
                    _visibility_path, visibility_record = queue_terminal_visibility(
                        run_key,
                        str(card.get("key") or ""),
                        card,
                        requested_status,
                    )
                    return {
                        "ok": False,
                        "status": "terminal-visibility-blocked"
                        if visibility_record.get("blockedAt") else "terminal-visibility-pending",
                        "card_closed": False,
                        "suppress_native_final": True,
                        "retry_queued": True,
                    }
                queue_terminal_final(
                    run_key,
                    str(card.get("key") or ""),
                    card,
                    meta,
                    requested_status,
                    final_summary,
                )
            try:
                prepared = prepare_lifecycle_terminal(
                    card,
                    terminal_status=requested_status,
                    final_summary=final_summary,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "status": "terminal-outbox-unavailable",
                    "card_closed": False,
                    "error": type(exc).__name__,
                }
            if card_uses_lifecycle_v3(card) and not prepared.get("managed"):
                return {
                    "ok": False,
                    "status": "terminal-outbox-unavailable",
                    "card_closed": False,
                    "suppress_native_final": True,
                }
            if prepared.get("nonterminal"):
                return {
                    "ok": True,
                    "status": "no-card-required",
                    "card_closed": False,
                }
            if prepared.get("killed"):
                return {
                    "ok": True,
                    "status": "lifecycle-writer-disabled",
                    "card_closed": False,
                    "suppress_native_final": True,
                }
            if not prepared.get("writer"):
                return {
                    "ok": True,
                    "status": "no-card-required",
                    "card_closed": False,
                }
            if prepared.get("already_delivered"):
                return {
                    "ok": True,
                    "status": "final-already-delivered",
                    "card_closed": False,
                    "suppress_native_final": True,
                }
            if prepared.get("fenced") or not prepared.get("allowed"):
                return {
                    "ok": True,
                    "status": "final-delivery-indeterminate",
                    "card_closed": False,
                    "suppress_native_final": True,
                }
            authoritative_final = str(prepared.get("final_summary") or final_summary)
            result = send_gateway_final_without_card(authoritative_final, meta=meta)
            delivered = bool(
                result.get("ok")
                and positive_telegram_message_id((result.get("result") or {}).get("message_id"))
            )
            finish_lifecycle_terminal(
                prepared,
                state="delivered" if delivered else "indeterminate",
            )
            if delivered:
                if outbox_path:
                    outbox_path.unlink(missing_ok=True)
                persist_terminal_card_state(run_key, str(card.get("key") or ""), "done")
                publish_terminal_once(run_key, str(card.get("key") or ""), "done")
                return {
                    "ok": True,
                    "status": "closed-and-final-delivered",
                    "card_closed": False,
                    "suppress_native_final": True,
                    "terminal_status": "done",
                }
            return {
                "ok": True,
                "status": "final-delivery-indeterminate",
                "card_closed": False,
                "suppress_native_final": True,
            }
        return {
            "ok": True,
            "status": "no-card-required",
            "card_closed": False,
        }
    card_key = str(card.get("key") or "")
    if card.get("requires_objective_interpretation"):
        return {
            "ok": False,
            "status": "awaiting-objective-card",
            "card_closed": False,
            "reason": "The agent has not created an interpreted live work card.",
        }
    if not card_key:
        return {"ok": False, "status": "missing-card-key", "card_closed": False}

    receipt = work_card_state_receipt(card_key)
    receipt_terminal_status = receipt["status"].lower() if receipt["status"].lower() in TERMINAL_CARD_STATUSES else ""
    if receipt_terminal_status and not card_uses_lifecycle_v3(card):
        persisted_status = receipt_terminal_status
        persist_terminal_card_state(run_key, card_key, persisted_status)
        publish_terminal_once(run_key, card_key, persisted_status)
        if receipt["final_message_id"]:
            return {
                "ok": True,
                "status": "final-already-delivered",
                "card_closed": True,
                "suppress_native_final": True,
                "card_key": card_key,
                "live_message_id": receipt["live_message_id"],
                "final_message_id": receipt["final_message_id"],
            }
        if not final_summary:
            return {
                "ok": True,
                "status": "already-terminal",
                "card_closed": True,
                "card_key": card_key,
                "live_message_id": receipt["live_message_id"],
                "terminal_status": persisted_status,
            }
    if not receipt["live_message_id"] or (receipt["header_required"] and not receipt["header_message_id"]):
        return {
            "ok": False,
            "status": "incomplete-card-surface",
            "card_closed": False,
            "card_key": card_key,
        }

    objective = str(card.get("objective") or "").strip()
    if not objective or objective.lower() in {"josh 2.0 telegram task", "telegram task"}:
        return {
            "ok": False,
            "status": "awaiting-objective-card",
            "card_closed": False,
            "card_key": card_key,
        }

    terminal_status = receipt_terminal_status or str(getattr(args, "terminal_status", "done") or "done").lower()
    claim_status = claim_terminal_card_close(run_key, card_key)
    if claim_status == "terminal":
        persisted = work_card_state_receipt(card_key)
        if persisted["status"].lower() in TERMINAL_CARD_STATUSES:
            persist_terminal_card_state(run_key, card_key, persisted["status"].lower())
            publish_terminal_once(run_key, card_key, persisted["status"].lower())
            if final_summary and not persisted["final_message_id"]:
                # The card-only close succeeded in an earlier attempt. It is
                # already fenced from the poller, so retry only final delivery.
                claim_status = "terminal-final-retry"
            else:
                return {
                    "ok": True,
                    "status": "final-already-delivered" if persisted["final_message_id"] else "already-terminal",
                    "card_closed": True,
                    "suppress_native_final": bool(persisted["final_message_id"]),
                    "card_key": card_key,
                    "live_message_id": persisted["live_message_id"],
                    "final_message_id": persisted["final_message_id"],
                    "terminal_status": persisted["status"].lower(),
                }
    if claim_status not in {"claimed", "terminal-final-retry"}:
        return {
            "ok": False,
            "status": "terminal-close-in-progress" if claim_status == "closing" else "run-card-not-ready",
            "card_closed": False,
            "card_key": card_key,
        }
    prepared: dict[str, Any] = {}
    authoritative_final = final_summary
    outbox_path: Path | None = None
    if final_summary:
        if terminal_status != "paused":
            try:
                outbox_path = queue_terminal_final(
                    run_key,
                    card_key,
                    card,
                    meta,
                    terminal_status,
                    final_summary,
                )
            except Exception:
                release_terminal_card_close(run_key, card_key)
                return {
                    "ok": False,
                    "status": "terminal-outbox-unavailable",
                    "card_closed": False,
                    "card_key": card_key,
                }
            if not publish_terminal_once(
                run_key,
                card_key,
                terminal_status,
                card_snapshot=card,
            ):
                release_terminal_card_close(run_key, card_key)
                _visibility_path, visibility_record = queue_terminal_visibility(
                    run_key,
                    card_key,
                    card,
                    terminal_status,
                )
                return {
                    "ok": False,
                    "status": "terminal-visibility-blocked"
                    if visibility_record.get("blockedAt") else "terminal-visibility-pending",
                    "card_closed": False,
                    "suppress_native_final": True,
                    "retry_queued": True,
                    "card_key": card_key,
                }
            queue_terminal_final(
                run_key,
                card_key,
                card,
                meta,
                terminal_status,
                final_summary,
            )
        try:
            prepared = prepare_lifecycle_terminal(
                card,
                terminal_status=terminal_status,
                final_summary=final_summary,
            )
        except Exception as exc:  # noqa: BLE001
            release_terminal_card_close(run_key, card_key)
            return {
                "ok": False,
                "status": "terminal-outbox-unavailable",
                "card_closed": False,
                "card_key": card_key,
                "error": type(exc).__name__,
            }
        if card_uses_lifecycle_v3(card) and not prepared.get("managed"):
            release_terminal_card_close(run_key, card_key)
            return {
                "ok": False,
                "status": "terminal-outbox-unavailable",
                "card_closed": False,
                "suppress_native_final": True,
                "card_key": card_key,
            }
        if prepared.get("nonterminal"):
            release_terminal_card_close(run_key, card_key)
            return {
                "ok": True,
                "status": "awaiting-input",
                "card_closed": False,
                "suppress_native_final": True,
                "card_key": card_key,
            }
        if prepared.get("already_delivered"):
            release_terminal_card_close(run_key, card_key)
            return {
                "ok": True,
                "status": "final-already-delivered",
                "card_closed": True,
                "suppress_native_final": True,
                "card_key": card_key,
            }
        if prepared.get("killed") or prepared.get("fenced") or (prepared.get("writer") and not prepared.get("allowed")):
            # The lifecycle fence protects Telegram side effects, but it must
            # not strand the UI-side close lease. Leaving this record in
            # ``closing-before-final`` makes every heartbeat and recovery pass
            # skip the card until the lease expires, which is the frozen 50%
            # Inbox card observed in production.
            release_terminal_card_close(run_key, card_key)
            return {
                "ok": True,
                "status": "lifecycle-writer-disabled" if prepared.get("killed") else "final-delivery-indeterminate",
                "card_closed": False,
                "suppress_native_final": True,
                "card_key": card_key,
            }
        authoritative_final = str(prepared.get("final_summary") or final_summary)
    try:
        if final_summary:
            with private_terminal_final_file(authoritative_final) as final_path:
                result = run_cmd(
                    terminal_work_card_command(card_key, card, meta, terminal_status, final_path),
                    timeout=10,
                )
        else:
            result = run_cmd(
                terminal_work_card_command(card_key, card, meta, terminal_status),
                timeout=10,
            )
    except subprocess.TimeoutExpired:
        result = {"ok": False, "returncode": -1, "stderr": "terminal card edit timed out"}
    persisted = work_card_state_receipt(card_key)
    final_missing = bool(final_summary and not persisted["final_message_id"])
    delivered = bool(
        final_summary
        and persisted["final_message_id"]
        and persisted["status"].lower() in TERMINAL_CARD_STATUSES
    )
    if (
        persisted["status"].lower() not in TERMINAL_CARD_STATUSES
        or (not final_summary and not result.get("ok"))
        or (final_summary and final_missing)
    ):
        if final_summary and prepared.get("writer"):
            finish_lifecycle_terminal(prepared, state="indeterminate")
            release_terminal_card_close(run_key, card_key)
            return {
                "ok": True,
                "status": "final-delivery-indeterminate",
                "card_closed": persisted["status"].lower() in TERMINAL_CARD_STATUSES,
                "suppress_native_final": True,
                "card_key": card_key,
                "live_message_id": persisted["live_message_id"],
            }
        if (
            final_summary
            and prepared.get("shadow")
            and persisted.get("final_delivery_status") == "indeterminate"
        ):
            finish_lifecycle_terminal(prepared, state="indeterminate")
            release_terminal_card_close(run_key, card_key)
            return {
                "ok": True,
                "status": "final-delivery-indeterminate",
                "card_closed": persisted["status"].lower() in TERMINAL_CARD_STATUSES,
                "suppress_native_final": True,
                "card_key": card_key,
                "live_message_id": persisted["live_message_id"],
            }
        release_terminal_card_close(run_key, card_key)
        if final_summary and outbox_path and outbox_path.exists():
            return {
                "ok": True,
                "status": "final-queued-for-retry",
                "card_closed": persisted["status"].lower() in TERMINAL_CARD_STATUSES,
                "suppress_native_final": True,
                "retry_queued": True,
                "card_key": card_key,
                "live_message_id": persisted["live_message_id"],
            }
        return {
            "ok": False,
            "status": "terminal-final-delivery-failed" if final_missing else "terminal-card-edit-failed",
            "card_closed": False,
            "card_key": card_key,
            "helper_returncode": result.get("returncode"),
        }
    if delivered and outbox_path:
        outbox_path.unlink(missing_ok=True)
    if delivered:
        finish_lifecycle_terminal(prepared, state="delivered")
    persisted_status = persisted["status"].lower()
    persist_terminal_card_state(run_key, card_key, persisted_status)
    publish_terminal_once(run_key, card_key, persisted_status)
    if final_summary:
        return {
            "ok": True,
            "status": "closed-and-final-delivered",
            "card_closed": True,
            "suppress_native_final": True,
            "card_key": card_key,
            "live_message_id": persisted["live_message_id"],
            "final_message_id": persisted["final_message_id"],
            "terminal_status": persisted_status,
        }
    return {
        "ok": True,
        "status": "closed",
        "card_closed": True,
        "card_key": card_key,
        "live_message_id": persisted["live_message_id"],
        "terminal_status": persisted_status,
    }


def claim_inbox(args: argparse.Namespace) -> dict[str, Any]:
    prompt = sys.stdin.read()
    meta = {
        "telegram_chat_id": str(args.chat_id or CONTROL_CENTER_CHAT_ID),
        "telegram_thread_id": str(args.thread_id or "1"),
        "telegram_session_key": str(args.session_key or ""),
    }
    stable = str(args.run_id or f"message:{args.message_id}")
    session_id = hashlib.sha1(str(args.session_key or stable).encode("utf-8")).hexdigest()[:20]
    event = {
        "session_id": session_id,
        "ts": utc_now(),
        "run_id": stable,
        "message_id": str(args.message_id or ""),
        "prompt": prompt,
    }
    effect_protocol = telegram_effect_protocol(args)
    ack = send_ack(
        event,
        model=DEFAULT_MODEL,
        dry_run=args.dry_run,
        meta=meta,
        effect_protocol=effect_protocol,
    )
    if not ack.get("ok"):
        if not args.dry_run:
            if str(ack.get("status") or "") == "surface-failed":
                publish_josh(
                    "Inbox task surface needs retry",
                    "error",
                    "The reaction completed, but the header/live-card receipt was incomplete; no worker was queued and native fallback remains available.",
                )
            else:
                publish_josh(
                    "Inbox acknowledgement needs retry",
                    "error",
                    "Required eyes reaction failed; no header or card was created and native fallback remains available.",
                )
        return {
            "ok": False,
            "status": str(ack.get("status") or "reaction-failed"),
            "reaction_ok": bool(ack.get("reaction_ok")),
            "key": ack.get("key"),
            "card_start_ok": bool(ack.get("card_start_ok")),
            "header_message_id": str(ack.get("header_message_id") or ""),
            "live_message_id": str(ack.get("live_message_id") or ""),
            "surface_indeterminate": bool(ack.get("surface_indeterminate")),
        }

    no_card_required = bool(ack.get("no_card_required"))
    if exact_control_center_inbox(meta) and not args.dry_run and not no_card_required:
        header_message_id = positive_telegram_message_id(ack.get("header_message_id"))
        live_message_id = positive_telegram_message_id(ack.get("live_message_id"))
        header_required = receipt_requires_header(ack)
        if not ack.get("card_start_ok") or not live_message_id or (header_required and not header_message_id):
            publish_josh(
                "Inbox task surface needs retry",
                "error",
                "The helper did not prove the required Topic 1 live-card receipt; no worker was queued and native fallback remains available.",
            )
            return {
                "ok": False,
                "status": "surface-failed",
                "reaction_ok": bool(ack.get("reaction_ok")),
                "key": ack.get("key"),
                "card_start_ok": False,
                "header_message_id": header_message_id,
                "live_message_id": live_message_id,
                "header_required": header_required,
                "surface_contract": str(ack.get("surface_contract") or ("header-live-v1" if header_required else "live-only-v2")),
                "surface_indeterminate": bool(ack.get("surface_indeterminate")),
            }

    cmd = [
        sys.executable,
        str(COORDINATOR_SCRIPT),
        "submit",
        "--privacy", classify_privacy(prompt),
        "--origin-run-id", stable,
        "--message-id", str(args.message_id or ""),
        "--card-key", str(ack.get("key") or ""),
        "--chat-id", str(meta["telegram_chat_id"]),
        "--thread-id", str(meta["telegram_thread_id"]),
    ]
    route_plan = ack.get("route_plan")
    if isinstance(route_plan, dict) and route_plan.get("routeId"):
        cmd.extend(["--route-plan-json", json.dumps(route_plan, separators=(",", ":"), sort_keys=True)])
    if ack.get("work_id"):
        cmd.extend(["--work-id", str(ack["work_id"])])
    if ack.get("ledger_run_id"):
        cmd.extend(["--work-run-id", str(ack["ledger_run_id"])])
    if ack.get("origin_claim_hash"):
        cmd.extend(["--origin-claim-hash", str(ack["origin_claim_hash"])])
    if args.dry_run:
        cmd.append("--dry-run")

    def queue_failure_receipt(error_name: str) -> dict[str, Any]:
        update_telegram_effect(
            effect_protocol,
            state="indeterminate",
            stage="coordinator-submit",
            header_message_id=str(ack.get("header_message_id") or ""),
            live_message_id=str(ack.get("live_message_id") or ""),
        )
        failure_card = {
            "key": str(ack.get("key") or stable),
            "objective": str(ack.get("objective") or "Inbox worker queue failed"),
            "model": str(ack.get("model") or "unverified"),
            "runtime_model": str(ack.get("runtime_model") or ack.get("model") or "unverified"),
            "route": str(ack.get("route") or "Inbox coordinator dispatch"),
            "session_id": session_id,
            "telegram_session_key": str(args.session_key or ""),
            "telegram_chat_id": str(meta["telegram_chat_id"]),
            "telegram_thread_id": str(meta["telegram_thread_id"]),
            "work_id": str(ack.get("work_id") or ""),
            "ledger_run_id": str(ack.get("ledger_run_id") or ""),
            "origin_claim_hash": str(ack.get("origin_claim_hash") or ""),
            "no_card_required": no_card_required,
            "delivery_tier": int(ack.get("delivery_tier") or 0),
            "lifecycle_version": int(ack.get("lifecycle_version") or 0),
            "lifecycle_writer_enabled": bool(ack.get("lifecycle_writer_enabled")),
            "coordinator_owned": True,
            "status": "active",
            "started_at": str(ack.get("last_card_update_at") or utc_now()),
            "last_card_update_at": str(ack.get("last_card_update_at") or utc_now()),
        }
        fallback_result: dict[str, Any] = {}
        if card_uses_lifecycle_v3(failure_card):
            fallback_result = queue_lifecycle_terminal_fallback(
                stable,
                failure_card,
                meta,
                terminal_status="failed",
                issue="The asynchronous worker could not be queued.",
                next_step="Retry after the coordinator service is healthy.",
                dry_run=args.dry_run,
            )
            if not args.dry_run:
                persist_claim_state(stable, failure_card, {
                    "run_id": stable,
                    "message_id": str(args.message_id or ""),
                    "job_id": "",
                    "reaction_ok": bool(ack.get("reaction_ok")),
                    "card_start_ok": bool(ack.get("card_start_ok")),
                    "no_card_required": no_card_required,
                    "delivery_tier": int(ack.get("delivery_tier") or 0),
                    "lifecycle_version": int(ack.get("lifecycle_version") or 0),
                    "terminal_fallback_queued": True,
                })
        elif not args.dry_run and not no_card_required:
            run_cmd(with_work_card_target([
                sys.executable,
                str(WORK_CARD_SCRIPT),
                "fail",
                "--key", str(ack.get("key") or stable),
                "--model", "unverified",
                "--route", "Inbox coordinator dispatch",
                "--done", "Acknowledged the Inbox request|Stopped before model execution",
                "--blocker", "The asynchronous worker could not be queued",
                "--next", "Retry after the coordinator service is healthy",
            ], meta))
            publish_josh("Inbox worker queue failed", "error", "The request was acknowledged but no worker was queued.")
        # Preserve every proven Telegram effect. The plugin uses these durable
        # receipts to avoid opening a second handler after a visible card exists.
        return {
            "ok": False,
            "status": "queue-failed",
            "error": error_name,
            "reaction_ok": bool(ack.get("reaction_ok")),
            "card_start_ok": bool(ack.get("card_start_ok")),
            "header_message_id": str(ack.get("header_message_id") or ""),
            "live_message_id": str(ack.get("live_message_id") or ""),
            "header_required": receipt_requires_header(ack),
            "surface_contract": str(ack.get("surface_contract") or ("header-live-v1" if receipt_requires_header(ack) else "live-only-v2")),
            "job_id": "",
            "key": ack.get("key"),
            "terminal_fallback_queued": fallback_result.get("ok", False),
        }

    try:
        submitted = run_cmd(cmd, timeout=30, input_text=prompt)
    except subprocess.TimeoutExpired:
        return queue_failure_receipt("coordinator_submit_timeout")
    if not submitted.get("ok") or not submitted.get("stdout"):
        return queue_failure_receipt("coordinator_submit_failed")

    try:
        envelope = json.loads(str(submitted["stdout"]))
    except Exception:
        return queue_failure_receipt("coordinator_receipt_invalid_json")
    if not isinstance(envelope, dict):
        return queue_failure_receipt("coordinator_receipt_not_object")
    job = envelope.get("job")
    if not isinstance(job, dict) or not str(job.get("jobId") or "").strip():
        return queue_failure_receipt("coordinator_receipt_missing_job")
    route = envelope.get("route")
    if not isinstance(route, dict):
        route = {}
    active_card = {
        "key": ack.get("key"),
        "objective": ack.get("objective"),
        "model": ack.get("model"),
        "runtime_model": ack.get("runtime_model"),
        "route": ack.get("route"),
        "session_id": session_id,
        "message_id": str(args.message_id or ""),
        "job_id": str((job or {}).get("jobId") or ""),
        "work_id": str(ack.get("work_id") or ""),
        "ledger_run_id": str(ack.get("ledger_run_id") or ""),
        "origin_claim_hash": str(ack.get("origin_claim_hash") or ""),
        "coordinator_owned": True,
        "telegram_chat_id": str(meta["telegram_chat_id"]),
        "telegram_thread_id": str(meta["telegram_thread_id"]),
        "reaction_ok": bool(ack.get("reaction_ok")),
        "card_start_ok": bool(ack.get("card_start_ok")),
        "header_message_id": str(ack.get("header_message_id") or ""),
        "live_message_id": str(ack.get("live_message_id") or ""),
        "header_required": receipt_requires_header(ack),
        "surface_contract": str(ack.get("surface_contract") or ("header-live-v1" if receipt_requires_header(ack) else "live-only-v2")),
        "no_card_required": no_card_required,
        "delivery_tier": int(ack.get("delivery_tier") or 0),
        "lifecycle_version": int(ack.get("lifecycle_version") or 0),
        "lifecycle_sequence": int(ack.get("lifecycle_sequence") or 0),
        "fencing_epoch": int(ack.get("fencing_epoch") or 0),
        "lifecycle_writer_enabled": bool(ack.get("lifecycle_writer_enabled")),
        "lifecycle_shadow": bool(ack.get("lifecycle_shadow")),
        "started_at": ack.get("last_card_update_at"),
        "last_progress_at": ack.get("last_card_update_at"),
        "last_card_update_at": ack.get("last_card_update_at"),
        "status": "active",
    }
    last_claim = {
        "run_id": stable,
        "message_id": str(args.message_id or ""),
        "job_id": str((job or {}).get("jobId") or ""),
        "route_id": str((route or {}).get("routeId") or ""),
        "reaction_ok": bool(ack.get("reaction_ok")),
        "card_start_ok": bool(ack.get("card_start_ok")),
        "header_message_id": str(ack.get("header_message_id") or ""),
        "live_message_id": str(ack.get("live_message_id") or ""),
        "header_required": receipt_requires_header(ack),
        "surface_contract": str(ack.get("surface_contract") or ("header-live-v1" if receipt_requires_header(ack) else "live-only-v2")),
        "no_card_required": no_card_required,
        "delivery_tier": int(ack.get("delivery_tier") or 0),
        "lifecycle_version": int(ack.get("lifecycle_version") or 0),
    }
    if not args.dry_run:
        persist_claim_state(stable, active_card, last_claim)
    update_telegram_effect(
        effect_protocol,
        state="queued",
        stage="coordinator-queued",
        reaction_ok=bool(ack.get("reaction_ok")),
        header_message_id=str(ack.get("header_message_id") or ""),
        live_message_id=str(ack.get("live_message_id") or ""),
    )
    return {
        "ok": True,
        "status": "queued",
        "reaction_ok": bool(ack.get("reaction_ok")),
        "card_start_ok": bool(ack.get("card_start_ok")),
        "header_message_id": str(ack.get("header_message_id") or ""),
        "live_message_id": str(ack.get("live_message_id") or ""),
        "header_required": receipt_requires_header(ack),
        "surface_contract": str(ack.get("surface_contract") or ("header-live-v1" if receipt_requires_header(ack) else "live-only-v2")),
        "no_card_required": no_card_required,
        "delivery_tier": int(ack.get("delivery_tier") or 0),
        "lifecycle_version": int(ack.get("lifecycle_version") or 0),
        "job_id": str((job or {}).get("jobId") or ""),
        "route_id": str((route or {}).get("routeId") or ""),
        "deduplicated": bool(envelope.get("deduplicated")) if isinstance(envelope, dict) else False,
    }


def coordinator_maintenance() -> None:
    if not COORDINATOR_SCRIPT.exists():
        return
    for extra in (["recover"], ["cleanup", "--max-age-seconds", str(24 * 60 * 60)]):
        try:
            subprocess.run(
                [sys.executable, str(COORDINATOR_SCRIPT), *extra],
                cwd=WORKSPACE,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
        except Exception:
            pass


def poll_once(dry_run: bool = False) -> dict[str, Any]:
    state, base_state = load_fast_ack_state_snapshot()
    acked = set(state.get("acked_prompt_events") or [])
    metas = session_metadatas()
    if not metas:
        state["last_checked_at"] = utc_now()
        state["direct_session_id"] = ""
        state["owned_session_ids"] = []
        state["model"] = DEFAULT_MODEL
        state["last_result"] = {"ok": False, "status": "no-direct-session"}
        state["status"] = "no-direct-session"
        if not dry_run:
            merge_poll_state(state, base_state)
        return {"ok": False, "status": "no-direct-session"}

    sent: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    session_ids: list[str] = []
    state.setdefault("active_cards", {})
    first_bootstrap = not acked and not state.get("last_checked_at")
    for meta in metas:
        session_id = str(meta.get("sessionId") or "")
        if not session_id:
            continue
        session_ids.append(session_id)
        model = str(meta.get("model") or DEFAULT_MODEL)
        events = recent_prompt_events(session_id, meta=meta)
        for event in events:
            event_id = f"{event['session_id']}:{event['ts']}"
            if event_id in acked:
                continue
            if internal_replay_prompt(event.get("prompt") or ""):
                acked.add(event_id)
                continue
            if JAIMES_MENTION_RE.search(event.get("prompt") or ""):
                # JAIMES scans every authorized Control Center topic and owns
                # directly mentioned turns through the shared origin claim.
                acked.add(event_id)
                continue
            if should_skip_stale_prompt_event(event["ts"], first_bootstrap):
                acked.add(event_id)
                continue
            result = send_ack(event, model=model, dry_run=dry_run, meta=meta)
            if result.get("ok"):
                acked.add(event_id)
                if result.get("run_id"):
                    state["active_cards"][result["run_id"]] = {
                        "key": result.get("key"),
                        "work_id": result.get("work_id"),
                        "ledger_run_id": result.get("ledger_run_id"),
                        "origin_claim_hash": result.get("origin_claim_hash"),
                        "objective": result.get("objective"),
                        "model": result.get("model"),
                        "runtime_model": result.get("runtime_model"),
                        "route": result.get("route"),
                        "ack_message_id": result.get("ack_message_id"),
                        "card_start_ok": bool(result.get("card_start_ok")),
                        "header_message_id": str(result.get("header_message_id") or ""),
                        "live_message_id": str(result.get("live_message_id") or ""),
                        "session_id": session_id,
                        "telegram_chat_id": str(meta.get("telegram_chat_id") or ""),
                        "telegram_thread_id": str(meta.get("telegram_thread_id") or ""),
                        "telegram_session_key": str(meta.get("telegram_session_key") or ""),
                        "requires_objective_interpretation": bool(result.get("requires_objective_interpretation")),
                        "no_card_required": bool(result.get("no_card_required")),
                        "surface_contract": str(result.get("surface_contract") or ""),
                        "delivery_tier": int(result.get("delivery_tier") or 0),
                        "lifecycle_version": int(result.get("lifecycle_version") or 0),
                        "lifecycle_sequence": int(result.get("lifecycle_sequence") or 0),
                        "fencing_epoch": int(result.get("fencing_epoch") or 0),
                        "lifecycle_writer_enabled": bool(result.get("lifecycle_writer_enabled")),
                        "lifecycle_shadow": bool(result.get("lifecycle_shadow")),
                        "started_at": result.get("last_card_update_at"),
                        "last_progress_at": result.get("last_card_update_at"),
                        "last_card_update_at": result.get("last_card_update_at"),
                        "status": (
                            "active" if result.get("no_card_required") and result.get("lifecycle_writer_enabled")
                            else "done" if result.get("no_card_required")
                            else "pending-interpretation" if result.get("requires_objective_interpretation")
                            else "active"
                        ),
                    }
                sent.append({"event": event_id, "result": result})
            else:
                sent.append({"event": event_id, "result": result})
                break
        updates.extend(update_active_cards(state, session_id, dry_run=dry_run, meta=meta))
        orphan_updates = reconcile_orphan_work_cards(state, dry_run=dry_run, meta=meta)
        if orphan_updates:
            updates.extend(orphan_updates)

    state["acked_prompt_events"] = sorted(acked)[-200:]
    state["last_checked_at"] = utc_now()
    state["direct_session_id"] = session_ids[0] if session_ids else ""
    state["owned_session_ids"] = session_ids
    state["model"] = str(metas[0].get("model") or DEFAULT_MODEL)
    state["status"] = "ok"
    state.pop("last_error", None)
    state.pop("last_error_at", None)
    if sent:
        state["last_sent_at"] = utc_now()
        state["last_result"] = sent[-1]["result"]
        if sent[-1]["result"].get("ack_message_id"):
            state["latest_pending_ack"] = {
                "message_id": sent[-1]["result"].get("ack_message_id"),
                "key": sent[-1]["result"].get("key"),
                "event": sent[-1]["event"],
                "created_at": utc_now(),
                "model": sent[-1]["result"].get("model") or DEFAULT_MODEL,
            }
        else:
            state.pop("latest_pending_ack", None)
    stale_gate_updates = queue_stale_final_gate_recovery(state, dry_run=dry_run)
    terminal_outbox_updates = recover_terminal_final_outbox(state, dry_run=dry_run)
    stale_close_updates = reconcile_stale_terminal_closes(state, dry_run=dry_run)
    updates = [*stale_gate_updates, *terminal_outbox_updates, *stale_close_updates, *updates]
    pruned_terminal_cards = prune_terminal_cards(state)
    if not dry_run:
        merge_poll_state(state, base_state)
    return {
        "ok": True,
        "session_id": session_ids[0] if session_ids else "",
        "session_ids": session_ids,
        "sent": sent,
        "updates": updates,
        "pruned_terminal_cards": pruned_terminal_cards,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one poll and exit.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--claim-inbox", action="store_true", help="Claim one Inbox event from stdin and queue its worker.")
    parser.add_argument("--progress-event-json-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--close-before-final", action="store_true", help="Close the origin live card before native final delivery.")
    parser.add_argument("--final-from-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--message-id", default="")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--thread-id", default="")
    parser.add_argument("--session-key", default="")
    parser.add_argument("--terminal-status", choices=("done", "paused", "failed"), default="done")
    parser.add_argument("--effect-path", default="")
    parser.add_argument("--cancel-path", default="")
    parser.add_argument("--surface-deadline-ms", type=int, default=0)
    args = parser.parse_args()

    if args.progress_event_json_stdin:
        result = progress_event_from_stdin()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 4

    if args.close_before_final:
        result = close_before_final(args)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 3

    if args.claim_inbox:
        print(json.dumps(claim_inbox(args), indent=2))
        return 0

    if args.once:
        print(json.dumps(poll_once(dry_run=args.dry_run), indent=2))
        return 0

    coordinator_maintenance()
    while True:
        try:
            poll_once()
        except Exception as exc:  # noqa: BLE001 - keep watcher alive
            record_fast_ack_error(type(exc).__name__)
        time.sleep(max(0.5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
