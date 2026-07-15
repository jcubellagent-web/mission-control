#!/usr/bin/env python3
"""Run low-noise recurring QA for the Josh 2.0 Telegram Inbox contract.

The scheduled lane is deterministic and never writes to Telegram. The live mode
remains an explicitly confirmed manual release tool; recurring production writes
are intentionally disabled because Bot API sends cannot be made idempotent after
an ambiguous network result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "telegram_response_contract_stress.py"
SLO_PATH = ROOT / "config" / "ecosystem-qa-slo.json"
STATE_PATH = Path(os.environ.get("TELEGRAM_INBOX_QA_STATE", str(ROOT / "data" / "telegram-inbox-qa.json")))
LOCK_PATH = STATE_PATH.with_suffix(STATE_PATH.suffix + ".lock")
PRIVATE_CANARY_DIR = Path.home() / ".openclaw" / "private" / "telegram-response-canary"
PRIVATE_CLEANUP_PATH = Path(
    os.environ.get(
        "TELEGRAM_INBOX_QA_PRIVATE_CLEANUP",
        str(PRIVATE_CANARY_DIR / "pending.json"),
    )
)
LIVE_LOCK_PATH = PRIVATE_CANARY_DIR / "live.lock"
OPT_IN_PATH = Path.home() / ".openclaw" / "state" / "telegram-recurring-canary-approved.json"
WORK_CARD_SCRIPT = Path.home() / ".openclaw" / "workspace" / "scripts" / "josh_work_card.py"
FAST_ACK_STATE = Path.home() / ".openclaw" / "telegram" / "fast_ack_state.json"
FAST_ACK_LOCK = FAST_ACK_STATE.with_suffix(FAST_ACK_STATE.suffix + ".lock")
WORK_CARD_STATE = Path.home() / ".openclaw" / "workspace" / "memory" / "josh_work_cards.json"
WORK_CARD_LOCK = WORK_CARD_STATE.with_suffix(WORK_CARD_STATE.suffix + ".lock")
COORDINATOR_STATE = Path.home() / ".openclaw" / "private" / "inbox-coordinator" / "jobs.json"
COORDINATOR_LOCK = COORDINATOR_STATE.with_name("jobs.lock")
CHANGE_LOCK = Path.home() / ".openclaw" / "state" / "control-tower-change-lock.json"
PRODUCTION_CHAT_ID = "-1003589561528"
PRODUCTION_THREAD_ID = "1"
TERMINAL_CARD_STATES = {"done", "failed", "paused", "retired", "cancelled"}
TERMINAL_JOB_STATES = {"done", "failed", "cancelled"}
DEFAULT_HISTORY_LIMIT = 768
DEFAULT_ROLLING_WINDOW = 30
DEFAULT_MINIMUM_ROLLING_SAMPLES = 20
STRESS_RETENTION_DAYS = 7
LIVE_RETENTION_DAYS = 30
MAX_STRESS_SAMPLES = 512
MAX_LIVE_SAMPLES = 256
SKIP_PRECONDITION_EXIT = 75
#JAIMES: Recurring QA is local-only; manual live canaries remain double-gated and persist only dashboard-safe metrics.


def iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write(path: Path, payload: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_private(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    atomic_write(path, payload, mode=0o600)


def read_locked_json(path: Path, lock_path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
    except (BlockingIOError, OSError, ValueError, json.JSONDecodeError):
        return None


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def lease_active(now: dt.datetime | None = None) -> bool:
    lease = read_json(CHANGE_LOCK, {})
    try:
        expires = dt.datetime.fromisoformat(str(lease.get("expiresAt") or "").replace("Z", "+00:00"))
        return expires > (now or dt.datetime.now(dt.timezone.utc))
    except (TypeError, ValueError):
        return False


def _nonterminal_count(container: Any, terminal: set[str]) -> int:
    values = container.values() if isinstance(container, dict) else container if isinstance(container, list) else []
    count = 0
    for value in values:
        if not isinstance(value, dict):
            continue
        status = str(value.get("status") or "").strip().lower()
        if status not in terminal:
            count += 1
    return count


def _indeterminate_count(container: Any) -> int:
    values = container.values() if isinstance(container, dict) else container if isinstance(container, list) else []
    return sum(
        1
        for value in values
        if isinstance(value, dict)
        and str(value.get("deliveryState") or value.get("delivery_state") or "").strip().lower() == "indeterminate"
    )


def recurring_canary_enabled() -> bool:
    if os.environ.get("JOSH_TELEGRAM_RECURRING_CANARY") == "1":
        return True
    approval = read_json(OPT_IN_PATH, {})
    return bool(approval.get("enabled") is True and approval.get("chatId") == PRODUCTION_CHAT_ID and str(approval.get("threadId")) == PRODUCTION_THREAD_ID)


def live_busy_reasons() -> list[str]:
    reasons: list[str] = []
    if lease_active():
        reasons.append("shared change lease is active")
    ack = read_locked_json(FAST_ACK_STATE, FAST_ACK_LOCK)
    if ack is None:
        reasons.append("Inbox fast-ack state is unavailable or locked")
    else:
        if ack.get("pending_objective"):
            reasons.append("Inbox objective is pending")
        if _nonterminal_count(ack.get("active_cards"), TERMINAL_CARD_STATES):
            reasons.append("Inbox fast-ack card is active")
        if _indeterminate_count(ack.get("active_cards")):
            reasons.append("Inbox fast-ack receipt is indeterminate")
    cards = read_locked_json(WORK_CARD_STATE, WORK_CARD_LOCK)
    if cards is None:
        reasons.append("Inbox work-card state is unavailable or locked")
    else:
        if _nonterminal_count(cards.get("cards"), TERMINAL_CARD_STATES):
            reasons.append("Inbox work card is active")
        if _indeterminate_count(cards.get("cards")):
            reasons.append("Inbox work-card receipt is indeterminate")
    jobs = read_locked_json(COORDINATOR_STATE, COORDINATOR_LOCK)
    if jobs is None:
        reasons.append("Inbox coordinator state is unavailable or locked")
    elif _nonterminal_count(jobs.get("jobs"), TERMINAL_JOB_STATES):
        reasons.append("Inbox worker job is active")
    if WORK_CARD_SCRIPT.exists():
        try:
            work_card = load_module("telegram_work_card_busy_guard", WORK_CARD_SCRIPT)
            cooldown = work_card.telegram_cooldown_active() if hasattr(work_card, "telegram_cooldown_active") else None
            if cooldown:
                reasons.append("Telegram transport cooldown is active")
        except Exception:
            reasons.append("Telegram helper health is unavailable")
    return reasons


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile_value * len(ordered)) - 1))
    return round(ordered[index], 1)


def finite_metric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return round(number, 1) if math.isfinite(number) and number >= 0 else None


def inbox_slo_config() -> dict[str, Any]:
    config = read_json(SLO_PATH, {})
    return config.get("telegramInbox") if isinstance(config.get("telegramInbox"), dict) else {}


def slo_thresholds() -> dict[str, float]:
    inbox = inbox_slo_config()
    return {
        "eyes": float(inbox.get("eyesReactionP95Seconds") or 2) * 1000,
        "header": float(inbox.get("objectiveHeaderP95Seconds") or 5) * 1000,
        "liveCard": float(inbox.get("liveWorkCardP95Seconds") or 8) * 1000,
        "final": float(inbox.get("structuredFinalCanaryP95Seconds") or 45) * 1000,
    }


def sanitize_result(payload: dict[str, Any], mode: str, checked_at: str | None = None) -> dict[str, Any]:
    stress = payload.get("stress") if isinstance(payload.get("stress"), dict) else {}
    transport = payload.get("transport") if isinstance(payload.get("transport"), dict) else None
    sample: dict[str, Any] = {
        "checkedAt": checked_at or iso(),
        "mode": mode,
        "role": str(payload.get("role") or "josh2"),
        "ok": bool(payload.get("ok")),
        "status": "ok" if payload.get("ok") else "failed",
        "stress": {
            "ok": bool(stress.get("ok")),
            "iterations": int(stress.get("iterations") or 0),
            "renderedCards": int(stress.get("renderedCards") or 0),
            "problemCount": len(stress.get("problems") or []),
            "durationMs": finite_metric(payload.get("monitorDurationMs")),
        },
        "problemCount": len(payload.get("problems") or []),
    }
    if transport is not None:
        timing = transport.get("timing") if isinstance(transport.get("timing"), dict) else {}
        cumulative = timing.get("cumulativeMs") if isinstance(timing.get("cumulativeMs"), dict) else {}
        cleanup = transport.get("cleanup") if isinstance(transport.get("cleanup"), dict) else {}
        final = transport.get("final") if isinstance(transport.get("final"), dict) else {}
        sample["transport"] = {
            "ok": bool(transport.get("ok")),
            "scope": "synthetic response timing after canary anchor receipt",
            "renderer": str(transport.get("renderer") or "unknown")[:24],
            "setupMs": finite_metric(timing.get("setupMs")),
            "latencyMs": {
                stage: finite_metric(cumulative[stage])
                for stage in ("eyes", "header", "liveCard", "final")
                if finite_metric(cumulative.get(stage)) is not None
            },
            "milestoneEdits": int(transport.get("milestoneEdits") or 0),
            "terminalLiveCard100Percent": bool((timing.get("checks") or {}).get("terminalLiveCard100Percent")),
            "exactlyOneFinal": bool(final.get("exactlyOne")),
            "cleanup": {
                "attempted": int(cleanup.get("attempted") or 0),
                "deleted": int(cleanup.get("deleted") or 0),
                "failedCount": len(cleanup.get("failedIds") or []),
                "indeterminateCount": len(cleanup.get("indeterminateStages") or []),
            },
            "elapsedMs": finite_metric(transport.get("elapsedMs")),
            "failureCount": len(transport.get("failures") or []),
        }
    return sample


def sample_violations(sample: dict[str, Any], thresholds: dict[str, float]) -> list[str]:
    transport = sample.get("transport") if isinstance(sample.get("transport"), dict) else None
    if sample.get("mode") != "live" or transport is None:
        return []
    violations: list[str] = []
    latency = transport.get("latencyMs") if isinstance(transport.get("latencyMs"), dict) else {}
    for stage, limit in thresholds.items():
        value = latency.get(stage)
        if not isinstance(value, (int, float)):
            violations.append(f"{stage} latency missing")
        elif float(value) > limit:
            violations.append(f"{stage} latency exceeded {int(limit)} ms")
    ordered = [latency.get(stage) for stage in ("eyes", "header", "liveCard", "final")]
    if all(isinstance(value, (int, float)) for value in ordered) and ordered != sorted(ordered):
        violations.append("synthetic cumulative timing was not monotonic")
    if not transport.get("terminalLiveCard100Percent"):
        violations.append("terminal live card was not verified at 100 percent")
    if not transport.get("exactlyOneFinal"):
        violations.append("exactly one final was not verified")
    cleanup = transport.get("cleanup") if isinstance(transport.get("cleanup"), dict) else {}
    if int(cleanup.get("failedCount") or 0) or int(cleanup.get("indeterminateCount") or 0):
        violations.append("temporary canary cleanup was incomplete or indeterminate")
    return violations


def rolling_latency(history: list[dict[str, Any]], window: int, thresholds: dict[str, float]) -> dict[str, Any]:
    live = [row for row in history if row.get("mode") == "live" and isinstance(row.get("transport"), dict)][-window:]
    stages: dict[str, Any] = {}
    for stage, limit in thresholds.items():
        values = [
            float(row["transport"]["latencyMs"][stage])
            for row in live
            if isinstance((row["transport"].get("latencyMs") or {}).get(stage), (int, float))
        ]
        stages[stage] = {
            "samples": len(values),
            "p50Ms": percentile(values, 0.50),
            "p95Ms": percentile(values, 0.95),
            "maxMs": round(max(values), 1) if values else None,
            "sloMs": int(limit),
        }
    return {
        "scope": "synthetic canary timing; not real inbound-path latency",
        "window": window,
        "liveSamples": len(live),
        "stages": stages,
    }


def prune_history(history: list[dict[str, Any]], now: dt.datetime | None = None) -> list[dict[str, Any]]:
    current = now or dt.datetime.now(dt.timezone.utc)
    by_mode: dict[str, list[tuple[dt.datetime, dict[str, Any]]]] = {"stress": [], "live": []}
    for row in history:
        if not isinstance(row, dict) or row.get("mode") not in by_mode:
            continue
        try:
            checked = dt.datetime.fromisoformat(str(row.get("checkedAt") or "").replace("Z", "+00:00"))
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=dt.timezone.utc)
            checked = checked.astimezone(dt.timezone.utc)
        except (TypeError, ValueError):
            continue
        if checked > current + dt.timedelta(minutes=5):
            continue
        retention = STRESS_RETENTION_DAYS if row["mode"] == "stress" else LIVE_RETENTION_DAYS
        if current - checked <= dt.timedelta(days=retention):
            by_mode[row["mode"]].append((checked, row))
    by_mode["stress"] = sorted(by_mode["stress"], key=lambda item: item[0])[-MAX_STRESS_SAMPLES:]
    by_mode["live"] = sorted(by_mode["live"], key=lambda item: item[0])[-MAX_LIVE_SAMPLES:]
    combined = by_mode["stress"] + by_mode["live"]
    return [row for _checked, row in sorted(combined, key=lambda item: item[0])]


def rolling_violations(rolling: dict[str, Any], minimum_samples: int) -> list[str]:
    violations: list[str] = []
    for stage, metrics in (rolling.get("stages") or {}).items():
        if int(metrics.get("samples") or 0) < minimum_samples:
            continue
        p95 = metrics.get("p95Ms")
        limit = metrics.get("sloMs")
        if isinstance(p95, (int, float)) and isinstance(limit, (int, float)) and p95 > limit:
            violations.append(f"rolling synthetic {stage} p95 exceeded {int(limit)} ms")
    return violations


def rolling_contract_stress(history: list[dict[str, Any]], window: int, minimum_samples: int) -> dict[str, Any]:
    policy = inbox_slo_config()
    limit = int(policy.get("contractStressP95Ms") or 2_000)
    minimum = int(policy.get("contractStressMinimumSamples") or minimum_samples)
    values = [
        finite_metric((row.get("stress") or {}).get("durationMs"))
        for row in history
        if row.get("mode") == "stress" and isinstance(row.get("stress"), dict)
    ]
    values = [value for value in values if value is not None][-max(1, window):]
    p95 = percentile(values, 0.95)
    status = "warming_up" if len(values) < max(1, minimum) else ("attention" if p95 is not None and p95 > limit else "ok")
    return {
        "scope": "local deterministic render and response-contract stress; no Telegram messages",
        "samples": len(values),
        "minimumSamples": max(1, minimum),
        "p50Ms": percentile(values, 0.50),
        "p95Ms": p95,
        "maxMs": round(max(values), 1) if values else None,
        "sloMs": limit,
        "status": status,
    }


def dashboard_safe_sample(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict) or row.get("mode") not in {"stress", "live"}:
        return None
    stress = row.get("stress") if isinstance(row.get("stress"), dict) else {}
    output: dict[str, Any] = {
        "checkedAt": str(row.get("checkedAt") or "")[:40],
        "mode": str(row.get("mode")),
        "role": "josh2",
        "ok": bool(row.get("ok")),
        "status": str(row.get("status") or "unknown")[:40],
        "stress": {
            "ok": bool(stress.get("ok")),
            "iterations": max(0, int(stress.get("iterations") or 0)),
            "renderedCards": max(0, int(stress.get("renderedCards") or 0)),
            "problemCount": max(0, int(stress.get("problemCount") or 0)),
            "durationMs": finite_metric(stress.get("durationMs")),
        },
        "problemCount": max(0, int(row.get("problemCount") or 0)),
    }
    transport = row.get("transport") if isinstance(row.get("transport"), dict) else None
    if transport is not None:
        latency = transport.get("latencyMs") if isinstance(transport.get("latencyMs"), dict) else {}
        cleanup = transport.get("cleanup") if isinstance(transport.get("cleanup"), dict) else {}
        output["transport"] = {
            "ok": bool(transport.get("ok")),
            "scope": "synthetic response timing after canary anchor receipt",
            "renderer": str(transport.get("renderer") or "unknown")[:24],
            "setupMs": finite_metric(transport.get("setupMs")),
            "latencyMs": {
                stage: metric
                for stage in ("eyes", "header", "liveCard", "final")
                if (metric := finite_metric(latency.get(stage))) is not None
            },
            "milestoneEdits": max(0, int(transport.get("milestoneEdits") or 0)),
            "terminalLiveCard100Percent": bool(transport.get("terminalLiveCard100Percent")),
            "exactlyOneFinal": bool(transport.get("exactlyOneFinal")),
            "cleanup": {
                key: max(0, int(cleanup.get(key) or 0))
                for key in ("attempted", "deleted", "failedCount", "indeterminateCount")
            },
            "elapsedMs": finite_metric(transport.get("elapsedMs")),
            "failureCount": max(0, int(transport.get("failureCount") or 0)),
        }
    return output


def dashboard_safe_lane(row: Any) -> dict[str, Any]:
    lane = row if isinstance(row, dict) else {}
    output = {
        "consecutiveFailures": max(0, int(lane.get("consecutiveFailures") or 0)),
        "alertAfterFailures": max(1, int(lane.get("alertAfterFailures") or 1)),
        "alertActive": bool(lane.get("alertActive")),
    }
    for key in ("lastSuccessAt", "lastFailureAt"):
        if lane.get(key):
            output[key] = str(lane[key])[:40]
    last_sample = dashboard_safe_sample(lane.get("lastSample"))
    if last_sample:
        output["lastSample"] = last_sample
    return output


def cleanup_ledger_from_result(payload: dict[str, Any]) -> dict[str, Any] | None:
    transport = payload.get("transport") if isinstance(payload.get("transport"), dict) else None
    cleanup = transport.get("cleanup") if isinstance(transport, dict) and isinstance(transport.get("cleanup"), dict) else None
    if cleanup is None:
        return None
    return {
        "version": 1,
        "updatedAt": iso(),
        "chatId": PRODUCTION_CHAT_ID,
        "threadId": PRODUCTION_THREAD_ID,
        "messageIds": list(dict.fromkeys(str(value) for value in cleanup.get("failedIds") or [] if str(value).isdigit())),
        "indeterminateStages": [str(value)[:80] for value in cleanup.get("indeterminateStages") or []],
    }


def retry_private_cleanup() -> tuple[bool, dict[str, int]]:
    if not PRIVATE_CLEANUP_PATH.exists():
        return True, {"attempted": 0, "deleted": 0, "remaining": 0, "unknown": 0, "invalid": 0}
    try:
        ledger = json.loads(PRIVATE_CLEANUP_PATH.read_text(encoding="utf-8"))
        valid = (
            isinstance(ledger, dict)
            and ledger.get("version") == 1
            and ledger.get("chatId") == PRODUCTION_CHAT_ID
            and str(ledger.get("threadId")) == PRODUCTION_THREAD_ID
            and isinstance(ledger.get("messageIds"), list)
            and isinstance(ledger.get("indeterminateStages"), list)
        )
        if not valid:
            raise ValueError("invalid private cleanup ledger schema")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False, {"attempted": 0, "deleted": 0, "remaining": 0, "unknown": 0, "invalid": 1}
    ids = [str(value) for value in ledger.get("messageIds") or [] if str(value).isdigit()]
    unknown = [str(value) for value in ledger.get("indeterminateStages") or []]
    if not ids and not unknown:
        PRIVATE_CLEANUP_PATH.unlink(missing_ok=True)
        return True, {"attempted": 0, "deleted": 0, "remaining": 0, "unknown": 0, "invalid": 0}
    if not WORK_CARD_SCRIPT.exists():
        return False, {"attempted": 0, "deleted": 0, "remaining": len(ids), "unknown": len(unknown), "invalid": 0}
    runner = load_module("telegram_response_contract_cleanup", RUNNER)
    work_card = load_module("telegram_work_card_cleanup", WORK_CARD_SCRIPT)
    result = runner.cleanup_messages(work_card, PRODUCTION_CHAT_ID, ids, indeterminate_stages=unknown)
    remaining = [str(value) for value in result.get("failedIds") or []]
    next_ledger = {
        "version": 1,
        "updatedAt": iso(),
        "chatId": PRODUCTION_CHAT_ID,
        "threadId": PRODUCTION_THREAD_ID,
        "messageIds": remaining,
        "indeterminateStages": unknown,
    }
    if remaining or unknown:
        atomic_write_private(PRIVATE_CLEANUP_PATH, next_ledger)
    else:
        PRIVATE_CLEANUP_PATH.unlink(missing_ok=True)
    return not remaining and not unknown, {
        "attempted": int(result.get("attempted") or 0),
        "deleted": int(result.get("deleted") or 0),
        "remaining": len(remaining),
        "unknown": len(unknown),
        "invalid": 0,
    }


def run_harness(mode: str, iterations: int, timeout: int) -> tuple[dict[str, Any], int]:
    started = time.monotonic()
    command = [
        sys.executable,
        str(RUNNER),
        "--role",
        "josh2",
        "--iterations",
        str(max(1, iterations)),
    ]
    if mode == "live":
        command.extend([
            "--live",
            "--chat-id",
            PRODUCTION_CHAT_ID,
            "--thread-id",
            PRODUCTION_THREAD_ID,
            "--confirm-production-canary",
        ])
    environment = os.environ.copy()
    if mode == "live":
        environment["TELEGRAM_CANARY_CLEANUP_JOURNAL"] = str(PRIVATE_CLEANUP_PATH)
    try:
        process = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return {
            "role": "josh2",
            "ok": False,
            "problems": ["QA harness timed out"],
            "stress": {},
            "monitorDurationMs": round((time.monotonic() - started) * 1000, 1),
        }, 124
    try:
        payload = json.loads(process.stdout)
    except Exception:
        payload = {"role": "josh2", "ok": False, "problems": ["QA harness returned invalid output"], "stress": {}}
    payload["monitorDurationMs"] = round((time.monotonic() - started) * 1000, 1)
    return payload, process.returncode


def update_state(
    sample: dict[str, Any],
    *,
    alert_after_failures: int,
    history_limit: int,
    rolling_window: int,
    minimum_rolling_samples: int,
) -> tuple[dict[str, Any], bool]:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = read_json(STATE_PATH, {"version": 1, "lanes": {}, "history": []})
        history = state.get("history") if isinstance(state.get("history"), list) else []
        history = [safe for row in history if (safe := dashboard_safe_sample(row)) is not None]
        thresholds = slo_thresholds()
        violations = sample_violations(sample, thresholds)
        sample["violations"] = violations
        if violations:
            sample["ok"] = False
            sample["status"] = "failed"
        history.append(sample)
        history = prune_history(history)[-max(1, history_limit):]
        rolling = rolling_latency(history, max(1, rolling_window), thresholds)
        contract_rolling = rolling_contract_stress(history, max(1, rolling_window), minimum_rolling_samples)
        rolling_slo_failures = rolling_violations(rolling, max(1, minimum_rolling_samples))
        if sample.get("mode") == "live" and rolling_slo_failures:
            sample["rollingViolations"] = rolling_slo_failures
            sample["ok"] = False
            sample["status"] = "failed"
        if sample.get("mode") == "stress" and contract_rolling.get("status") == "attention":
            sample["rollingViolations"] = [
                f"rolling contract stress p95 exceeded {int(contract_rolling['sloMs'])} ms"
            ]
            sample["ok"] = False
            sample["status"] = "failed"
        if history:
            history[-1] = dashboard_safe_sample(sample) or {}

        prior_lanes = state.get("lanes") if isinstance(state.get("lanes"), dict) else {}
        lanes = {
            mode: dashboard_safe_lane(value)
            for mode, value in prior_lanes.items()
            if mode in {"stress", "live"}
        }
        lane = lanes.get(sample["mode"]) if isinstance(lanes.get(sample["mode"]), dict) else {}
        skipped = str(sample.get("status") or "").startswith("skipped")
        prior_failures = int(lane.get("consecutiveFailures") or 0)
        if skipped:
            consecutive = prior_failures
        elif sample.get("ok"):
            consecutive = 0
        else:
            consecutive = prior_failures + 1
        cleanup = (sample.get("transport") or {}).get("cleanup") if isinstance(sample.get("transport"), dict) else {}
        cleanup_actionable = bool(int((cleanup or {}).get("failedCount") or 0) or int((cleanup or {}).get("indeterminateCount") or 0))
        actionable = cleanup_actionable or consecutive >= max(1, alert_after_failures)
        if skipped and lane.get("alertActive"):
            actionable = True
        lane.update({
            "lastSample": sample,
            "consecutiveFailures": consecutive,
            "alertAfterFailures": max(1, alert_after_failures),
            "alertActive": actionable,
        })
        if not skipped and sample.get("ok"):
            lane["lastSuccessAt"] = sample["checkedAt"]
        elif not skipped:
            lane["lastFailureAt"] = sample["checkedAt"]
        lanes[sample["mode"]] = lane

        overall_attention = any(bool(value.get("alertActive")) for value in lanes.values() if isinstance(value, dict))
        degraded = any(int(value.get("consecutiveFailures") or 0) for value in lanes.values() if isinstance(value, dict))
        output = {
            "version": 1,
            "updatedAt": sample["checkedAt"],
            "status": "attention" if overall_attention else ("degraded" if degraded else "ok"),
            "summary": (
                "Telegram Inbox QA needs attention."
                if overall_attention
                else (
                    "Telegram Inbox QA is degraded; an automatic retry is scheduled."
                    if degraded
                    else "Telegram Inbox contract QA is healthy; recurring production-write canaries are disabled for safety."
                )
            ),
            "privacy": {
                "dashboardSafe": True,
                "messageIdsIncluded": False,
                "rawPromptsIncluded": False,
            },
            "coverage": {
                "contractStress": "every 30 minutes",
                "runtimeHealth": "every 5 minutes",
                "liveTransport": "passive evidence from real Inbox work",
                "recurringProductionWrites": False,
            },
            "lanes": lanes,
            "rolling": {
                "syntheticTransport": rolling,
                "contractStress": contract_rolling,
            },
            "history": history,
        }
        atomic_write(STATE_PATH, output, mode=0o644)
    return output, actionable


def safe_output(state: dict[str, Any], sample: dict[str, Any], actionable: bool) -> dict[str, Any]:
    lane = (state.get("lanes") or {}).get(sample.get("mode"), {})
    transport = sample.get("transport") if isinstance(sample.get("transport"), dict) else {}
    return {
        "ok": bool(sample.get("ok")),
        "status": sample.get("status"),
        "alertActionable": actionable,
        "checkedAt": sample.get("checkedAt"),
        "mode": sample.get("mode"),
        "summary": state.get("summary"),
        "consecutiveFailures": int(lane.get("consecutiveFailures") or 0),
        "alertAfterFailures": int(lane.get("alertAfterFailures") or 1),
        "alertActive": bool(lane.get("alertActive")),
        "stress": sample.get("stress"),
        "latencyMs": transport.get("latencyMs") if transport else None,
        "cleanup": transport.get("cleanup") if transport else None,
        "violations": sample.get("violations") or [],
        "rollingViolations": sample.get("rollingViolations") or [],
        "scope": "dashboard-safe synthetic QA; no prompts, message IDs, or private Telegram content",
    }


def skipped_output(checked_at: str, summary: str) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "skipped_precondition",
        "checkedAt": checked_at,
        "mode": "live",
        "summary": summary,
        "scope": "dashboard-safe synthetic QA; no prompts, message IDs, or private Telegram content",
    }


def main() -> int:
    policy = inbox_slo_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("stress", "live"), required=True)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--alert-after-failures", type=int, default=2)
    parser.add_argument("--history-limit", type=int, default=DEFAULT_HISTORY_LIMIT)
    parser.add_argument(
        "--rolling-window",
        type=int,
        default=int(policy.get("syntheticCanaryRollingWindowSamples") or DEFAULT_ROLLING_WINDOW),
    )
    parser.add_argument(
        "--minimum-rolling-samples",
        type=int,
        default=int(policy.get("syntheticCanaryMinimumSamples") or DEFAULT_MINIMUM_ROLLING_SAMPLES),
    )
    parser.add_argument(
        "--confirm-production-canary",
        action="store_true",
        help="explicitly allow the scheduled live canary in production Inbox topic 1",
    )
    args = parser.parse_args()

    checked_at = iso()
    live_lock = None
    if args.mode == "live":
        if not args.confirm_production_canary:
            print(json.dumps({
                "ok": False,
                "status": "refused",
                "checkedAt": checked_at,
                "mode": "live",
                "summary": "Production canary confirmation is required.",
            }, indent=2))
            return 2
        if not recurring_canary_enabled():
            print(json.dumps(skipped_output(checked_at, "Recurring production canary is not enabled on this host."), indent=2))
            return SKIP_PRECONDITION_EXIT
        PRIVATE_CANARY_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(PRIVATE_CANARY_DIR, 0o700)
        live_lock = LIVE_LOCK_PATH.open("a+", encoding="utf-8")
        try:
            fcntl.flock(live_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps(skipped_output(checked_at, "Another production canary is already running."), indent=2))
            return SKIP_PRECONDITION_EXIT
        cleanup_ok, cleanup = retry_private_cleanup()
        if not cleanup_ok:
            sample = {
                "checkedAt": checked_at,
                "mode": "live",
                "role": "josh2",
                "ok": False,
                "status": "failed_cleanup",
                "stress": {"ok": True, "iterations": 0, "renderedCards": 0, "problemCount": 0},
                "problemCount": 1,
                "transport": {
                    "ok": False,
                    "scope": "synthetic response timing after canary anchor receipt",
                    "renderer": "none",
                    "latencyMs": {},
                    "terminalLiveCard100Percent": False,
                    "exactlyOneFinal": False,
                    "cleanup": {
                        "attempted": cleanup["attempted"],
                        "deleted": cleanup["deleted"],
                        "failedCount": cleanup["remaining"],
                        "indeterminateCount": cleanup["unknown"] + cleanup.get("invalid", 0),
                    },
                },
            }
            state, actionable = update_state(
                sample,
                alert_after_failures=1,
                history_limit=args.history_limit,
                rolling_window=args.rolling_window,
                minimum_rolling_samples=args.minimum_rolling_samples,
            )
            print(json.dumps(safe_output(state, sample, actionable), indent=2))
            return 1

        busy = live_busy_reasons()
        hard_preconditions = [reason for reason in busy if "indeterminate" in reason.lower()]
        if hard_preconditions:
            sample = {
                "checkedAt": checked_at,
                "mode": "live",
                "role": "josh2",
                "ok": False,
                "status": "failed_precondition",
                "stress": {"ok": True, "iterations": 0, "renderedCards": 0, "problemCount": 0},
                "problemCount": len(hard_preconditions),
            }
            state, actionable = update_state(
                sample,
                alert_after_failures=1,
                history_limit=args.history_limit,
                rolling_window=args.rolling_window,
                minimum_rolling_samples=args.minimum_rolling_samples,
            )
            print(json.dumps(safe_output(state, sample, actionable), indent=2))
            return 1
        if busy:
            sample = {
                "checkedAt": checked_at,
                "mode": "live",
                "role": "josh2",
                "ok": True,
                "status": "skipped_busy",
                "stress": {"ok": True, "iterations": 0, "renderedCards": 0, "problemCount": 0},
                "problemCount": 0,
                "skipReasonCount": len(busy),
            }
            state, actionable = update_state(
                sample,
                alert_after_failures=args.alert_after_failures,
                history_limit=args.history_limit,
                rolling_window=args.rolling_window,
                minimum_rolling_samples=args.minimum_rolling_samples,
            )
            print(json.dumps(safe_output(state, sample, actionable), indent=2))
            return SKIP_PRECONDITION_EXIT

    payload, runner_returncode = run_harness(args.mode, args.iterations, max(30, args.timeout_seconds))
    sample = sanitize_result(payload, args.mode, checked_at=checked_at)
    if runner_returncode and sample.get("ok"):
        sample["ok"] = False
        sample["status"] = "failed"
        sample["problemCount"] = max(1, int(sample.get("problemCount") or 0))

    if args.mode == "live":
        ledger = cleanup_ledger_from_result(payload)
        if ledger and (ledger["messageIds"] or ledger["indeterminateStages"]):
            atomic_write_private(PRIVATE_CLEANUP_PATH, ledger)
        elif ledger is not None:
            PRIVATE_CLEANUP_PATH.unlink(missing_ok=True)
        if PRIVATE_CLEANUP_PATH.exists():
            cleanup_ok, cleanup = retry_private_cleanup()
            if not cleanup_ok:
                transport = sample.get("transport") if isinstance(sample.get("transport"), dict) else {}
                transport.update({
                    "ok": False,
                    "scope": "synthetic response timing after canary anchor receipt",
                    "renderer": str(transport.get("renderer") or "none")[:24],
                    "latencyMs": transport.get("latencyMs") if isinstance(transport.get("latencyMs"), dict) else {},
                    "terminalLiveCard100Percent": bool(transport.get("terminalLiveCard100Percent")),
                    "exactlyOneFinal": bool(transport.get("exactlyOneFinal")),
                    "cleanup": {
                        "attempted": cleanup["attempted"],
                        "deleted": cleanup["deleted"],
                        "failedCount": cleanup["remaining"],
                        "indeterminateCount": cleanup["unknown"] + cleanup.get("invalid", 0),
                    },
                })
                sample["transport"] = transport
                sample["ok"] = False
                sample["status"] = "failed_cleanup"
                sample["problemCount"] = max(1, int(sample.get("problemCount") or 0))

    state, actionable = update_state(
        sample,
        alert_after_failures=args.alert_after_failures,
        history_limit=args.history_limit,
        rolling_window=args.rolling_window,
        minimum_rolling_samples=args.minimum_rolling_samples,
    )
    print(json.dumps(safe_output(state, sample, actionable), indent=2))
    return 1 if actionable else 0


if __name__ == "__main__":
    raise SystemExit(main())
