#!/usr/bin/env python3
"""Probe canonical Josh 2.0 runtime services and perform bounded recovery."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
OUTPUT = ROOT / "data" / "ecosystem-runtime-probe.json"
JOSH_WORK_CARD_SCRIPT = WORKSPACE / "scripts" / "josh_work_card.py"
JOSH_SEND_REPLY_SCRIPT = JOSH_WORK_CARD_SCRIPT.with_name("send_josh_reply.py")
CANONICAL_WORK_CARD_SCRIPT = ROOT / "scripts" / "josh_work_card.py"
CANONICAL_FAST_ACK_SCRIPT = ROOT / "scripts" / "josh_telegram_fast_ack.py"
CANONICAL_FAST_ACK_LAUNCHER = ROOT / "scripts" / "jaimes_telegram_fast_ack_launcher.py"
FAST_ACK_STATE_PATH = Path.home() / ".openclaw" / "telegram" / "fast_ack_state.json"
INBOX_PLUGIN_SOURCE = ROOT / "plugins" / "inbox-coordinator" / "index.js"
OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
RECOVERY_COOLDOWN = dt.timedelta(minutes=15)
CLEAN_PROBES_TO_CLEAR = 2
FAST_ACK_MAX_STALE_SECONDS = 30
SERVICE_LABELS = {
    "controlTower": "com.josh20.mission-control-react-v2",
    "brainFeed": "com.josh20.brain-feed-server",
    "gateway": "ai.openclaw.gateway",
    "telegramFastAck": "com.josh20.telegram-fast-ack",
}


def probe_exit_code(checks: dict[str, Any]) -> int:
    """Separate real service outages from contract/freshness drift."""
    if any(not bool((checks.get(service) or {}).get("ok")) for service in SERVICE_LABELS):
        return 2
    if any(not bool(row.get("ok")) for row in checks.values() if isinstance(row, dict)):
        return 1
    return 0


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


def parse_ts(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write(path: Path, payload: Any) -> None:
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


def tcp_probe(port: int, timeout: float = 2.0) -> tuple[bool, float, str]:
    started = time.perf_counter()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True, round((time.perf_counter() - started) * 1000, 1), "listening"
    except OSError as exc:
        return False, round((time.perf_counter() - started) * 1000, 1), f"{type(exc).__name__}"


def http_json(url: str, timeout: float = 4.0) -> tuple[bool, float, Any, str]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read()
        return True, round((time.perf_counter() - started) * 1000, 1), json.loads(raw), "ok"
    except Exception as exc:
        return False, round((time.perf_counter() - started) * 1000, 1), None, f"{type(exc).__name__}"


def launchd_running(label: str) -> bool:
    proc = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )
    text = proc.stdout.lower()
    return proc.returncode == 0 and ("state = running" in text or "pid =" in text)


def launchd_snapshot(label: str) -> tuple[bool, str]:
    proc = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )
    text = proc.stdout
    running = proc.returncode == 0 and ("state = running" in text.lower() or "pid =" in text.lower())
    return running, text


def fast_ack_runtime_health(
    launchd_ok: bool,
    state: dict[str, Any],
    *,
    now: dt.datetime | None = None,
) -> tuple[bool, str, float | None]:
    """Require a fresh watcher heartbeat; a launchd crash loop is not healthy."""
    if not launchd_ok:
        return False, "launchd not running", None
    checked_at = parse_ts(state.get("last_checked_at")) if isinstance(state, dict) else None
    if checked_at is None:
        return False, "watcher heartbeat missing", None
    current = now or utc_now()
    age_seconds = max(0.0, (current - checked_at.astimezone(dt.timezone.utc)).total_seconds())
    status = str(state.get("status") or "").strip().lower()
    if age_seconds > FAST_ACK_MAX_STALE_SECONDS:
        return False, "watcher heartbeat stale", round(age_seconds, 1)
    if status not in {"ok", "no-direct-session"}:
        return False, f"watcher status {status or 'unknown'}", round(age_seconds, 1)
    return True, "launchd running; watcher heartbeat fresh", round(age_seconds, 1)


def configured_inbox_helper(config: dict[str, Any]) -> str:
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    entries = plugins.get("entries") if isinstance(plugins.get("entries"), dict) else {}
    entry = entries.get("inbox-coordinator") if isinstance(entries.get("inbox-coordinator"), dict) else {}
    plugin_config = entry.get("config") if isinstance(entry.get("config"), dict) else {}
    return str(plugin_config.get("helperPath") or entry.get("helperPath") or "").strip()


def inbox_plugin_enabled(config: dict[str, Any]) -> bool:
    """Match OpenCLAW's canonical explicit boolean plugin enablement."""
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    entries = plugins.get("entries") if isinstance(plugins.get("entries"), dict) else {}
    entry = entries.get("inbox-coordinator") if isinstance(entries.get("inbox-coordinator"), dict) else {}
    return entry.get("enabled") is True


def plugin_uses_canonical_helper_default(source: str) -> bool:
    return '"mission-control", "scripts", "josh_telegram_fast_ack.py"' in source


def launchd_uses_canonical_inbox_helper(snapshot: str) -> bool:
    """Accept direct execution or the canonical Josh-owned credential wrapper."""
    direct = str(CANONICAL_FAST_ACK_SCRIPT) in snapshot
    wrapped = (
        str(CANONICAL_FAST_ACK_LAUNCHER) in snapshot
        and "TELEGRAM_FAST_ACK_OWNER => josh2" in snapshot
    )
    return direct or wrapped


def collect(base_url: str) -> dict[str, Any]:
    root_ok, root_ms, _, root_detail = http_json(base_url.rstrip("/") + "/data/control-tower-live.json")
    live = _ if isinstance(_, dict) else {}
    brain_ok, brain_ms, brain_detail = tcp_probe(8765)
    gateway_ok, gateway_ms, gateway_detail = tcp_probe(18790)
    fast_ack_launchd_ok, fast_ack_launch = launchd_snapshot(SERVICE_LABELS["telegramFastAck"])
    fast_ack_ok, fast_ack_detail, fast_ack_age = fast_ack_runtime_health(
        fast_ack_launchd_ok,
        read_json(FAST_ACK_STATE_PATH, {}),
    )
    telegram_helper_missing = [
        path.name
        for path in (JOSH_WORK_CARD_SCRIPT, JOSH_SEND_REPLY_SCRIPT, CANONICAL_WORK_CARD_SCRIPT)
        if not path.is_file()
    ]
    work_card_synced = bool(
        not telegram_helper_missing
        and JOSH_WORK_CARD_SCRIPT.read_bytes() == CANONICAL_WORK_CARD_SCRIPT.read_bytes()
    )
    telegram_helper_ok = not telegram_helper_missing and work_card_synced
    openclaw_config = read_json(OPENCLAW_CONFIG, {})
    parsed_config = openclaw_config if isinstance(openclaw_config, dict) else {}
    configured_helper = configured_inbox_helper(parsed_config)
    plugin_enabled = inbox_plugin_enabled(parsed_config)
    plugin_source = INBOX_PLUGIN_SOURCE.read_text(encoding="utf-8") if INBOX_PLUGIN_SOURCE.is_file() else ""
    expected_helper = CANONICAL_FAST_ACK_SCRIPT.resolve()
    effective_helper = Path(configured_helper).expanduser().resolve() if configured_helper else expected_helper
    default_contract_ok = bool(configured_helper) or plugin_uses_canonical_helper_default(plugin_source)
    claim_helper_ok = (
        plugin_enabled
        and CANONICAL_FAST_ACK_SCRIPT.is_file()
        and effective_helper == expected_helper
        and default_contract_ok
        and launchd_uses_canonical_inbox_helper(fast_ack_launch)
    )
    source_stamp = parse_ts(live.get("sourceUpdatedAt"))
    source_age = round((utc_now() - source_stamp.astimezone(dt.timezone.utc)).total_seconds() / 60, 1) if source_stamp else None
    checks = {
        "controlTower": {"ok": root_ok, "latencyMs": root_ms, "detail": root_detail},
        "brainFeed": {"ok": brain_ok, "latencyMs": brain_ms, "detail": brain_detail},
        "gateway": {"ok": gateway_ok, "latencyMs": gateway_ms, "detail": gateway_detail},
        "telegramFastAck": {
            "ok": fast_ack_ok,
            "heartbeatAgeSeconds": fast_ack_age,
            "detail": fast_ack_detail,
        },
        "telegramWorkCardHelper": {
            "ok": telegram_helper_ok,
            "detail": (
                "runtime work-card helper matches canonical source"
                if telegram_helper_ok
                else f"missing: {', '.join(telegram_helper_missing)}"
                if telegram_helper_missing
                else "runtime work-card helper differs from canonical source"
            ),
        },
        "telegramInboxClaimHelper": {
            "ok": claim_helper_ok,
            "detail": "canonical helper selected by plugin and launchd" if claim_helper_ok else "plugin or launchd is not using the canonical Inbox helper",
        },
        "sourceFreshness": {"ok": source_age is not None and source_age <= 5, "ageMinutes": source_age, "detail": "sourceUpdatedAt"},
    }
    return {"checks": checks, "ok": all(row["ok"] for row in checks.values())}


def recovery_due(previous: dict[str, Any], service: str, now: dt.datetime) -> bool:
    recoveries = previous.get("recoveries") if isinstance(previous.get("recoveries"), dict) else {}
    row = recoveries.get(service) if isinstance(recoveries.get(service), dict) else {}
    last = parse_ts(row.get("lastAttemptAt"))
    attempts = int(row.get("attemptsSinceHealthy") or 0)
    return attempts < 3 and (not last or now - last.astimezone(dt.timezone.utc) >= RECOVERY_COOLDOWN)


def restart_service(service: str) -> dict[str, Any]:
    label = SERVICE_LABELS[service]
    proc = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    return {"service": service, "label": label, "returncode": proc.returncode, "ok": proc.returncode == 0, "detail": (proc.stderr or proc.stdout).strip()[:400]}


def next_service_failure_streaks(previous: dict[str, Any], checks: dict[str, Any]) -> dict[str, int]:
    prior = previous.get("serviceFailureStreaks") if isinstance(previous.get("serviceFailureStreaks"), dict) else {}
    return {
        service: 0 if bool((checks.get(service) or {}).get("ok")) else int(prior.get(service) or 0) + 1
        for service in SERVICE_LABELS
    }


def next_service_healthy_streaks(previous: dict[str, Any], checks: dict[str, Any]) -> dict[str, int]:
    prior = previous.get("serviceHealthyStreaks") if isinstance(previous.get("serviceHealthyStreaks"), dict) else {}
    return {
        service: min(CLEAN_PROBES_TO_CLEAR, int(prior.get(service) or 0) + 1)
        if bool((checks.get(service) or {}).get("ok"))
        else 0
        for service in SERVICE_LABELS
    }


def recoverable_services(
    previous: dict[str, Any],
    checks: dict[str, Any],
    streaks: dict[str, int],
    now: dt.datetime,
) -> list[str]:
    return [
        service
        for service in SERVICE_LABELS
        if not bool((checks.get(service) or {}).get("ok"))
        and int(streaks.get(service) or 0) >= 2
        and recovery_due(previous, service, now)
    ]


def clear_stably_healthy_recoveries(
    recoveries: dict[str, Any],
    checks: dict[str, Any],
    healthy_streaks: dict[str, int],
) -> dict[str, Any]:
    remaining = dict(recoveries)
    for service in SERVICE_LABELS:
        if (
            bool((checks.get(service) or {}).get("ok"))
            and int(healthy_streaks.get(service) or 0) >= CLEAN_PROBES_TO_CLEAR
        ):
            remaining.pop(service, None)
    return remaining


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("CONTROL_TOWER_BASE", "http://127.0.0.1:5174"))
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    now = utc_now()
    previous = read_json(OUTPUT, {})
    result = collect(args.base_url)
    streak = 0 if result["ok"] else int(previous.get("failureStreak") or 0) + 1
    service_streaks = next_service_failure_streaks(previous, result["checks"])
    service_healthy_streaks = next_service_healthy_streaks(previous, result["checks"])
    recoveries = previous.get("recoveries") if isinstance(previous.get("recoveries"), dict) else {}
    attempted = []
    initial_checks = result["checks"]
    if args.recover:
        for service in recoverable_services(previous, result["checks"], service_streaks, now):
            row = restart_service(service)
            attempted.append(row)
            previous_row = recoveries.get(service) if isinstance(recoveries.get(service), dict) else {}
            recoveries[service] = {
                "lastAttemptAt": iso(now),
                "attemptsSinceHealthy": int(previous_row.get("attemptsSinceHealthy") or 0) + 1,
                "lastResult": row,
            }
        if attempted:
            time.sleep(4)
            result = collect(args.base_url)
            if result["ok"]:
                streak = 0
            for row in attempted:
                service_healthy_streaks[str(row["service"])] = 0
    for service in SERVICE_LABELS:
        if bool((result["checks"].get(service) or {}).get("ok")):
            service_streaks[service] = 0
    recoveries = clear_stably_healthy_recoveries(recoveries, result["checks"], service_healthy_streaks)
    payload = {
        "checkedAt": iso(),
        "ok": result["ok"],
        "status": "ok" if result["ok"] else "attention",
        "failureStreak": streak,
        "serviceFailureStreaks": service_streaks,
        "serviceHealthyStreaks": service_healthy_streaks,
        "checks": result["checks"],
        "preRecoveryChecks": initial_checks if attempted else None,
        "recoveryAttempts": attempted,
        "recoveries": recoveries,
        "failedServices": [
            service
            for service in SERVICE_LABELS
            if not bool((result["checks"].get(service) or {}).get("ok"))
        ],
        "failedContracts": [
            name
            for name, row in result["checks"].items()
            if name not in SERVICE_LABELS and isinstance(row, dict) and not bool(row.get("ok"))
        ],
        "policy": "A service must fail two consecutive service-specific probes before one bounded restart; 15-minute cooldown; circuit opens after three attempts; two later scheduled clean probes clear recovery state; source staleness never triggers a restart.",
    }
    if not args.no_write:
        atomic_write(OUTPUT, payload)
    print(json.dumps(payload, indent=2))
    return probe_exit_code(result["checks"])


if __name__ == "__main__":
    raise SystemExit(main())
