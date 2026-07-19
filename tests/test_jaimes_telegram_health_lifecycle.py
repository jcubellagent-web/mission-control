#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch


TEST_DIR = Path(__file__).resolve().parent
STAGED_MODULE_PATH = TEST_DIR / "jaimes_telegram_health.py"
MODULE_PATH = (
    STAGED_MODULE_PATH
    if STAGED_MODULE_PATH.exists()
    else Path(__file__).resolve().parents[1] / "scripts" / "jaimes_telegram_health.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("jaimes_telegram_health_lifecycle", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


health = load_module()


def healthy_probe() -> dict:
    return {
        "gateway": {
            "gateway_state": "running",
            "platforms": {"telegram": {"state": "connected"}},
        },
        "launchd": "state = running",
        "processes": "hermes gateway",
        "fastAckLaunchd": "state = running",
        "fastAckState": {
            "identity": {"ok": True},
            "lastCheckedAt": health.iso(),
            "lastSurfaceAt": health.iso(),
            "lastSurfaceOk": True,
            "surfaceIndeterminate": False,
            "activeCardCount": 1,
            "deliveryError": {},
        },
        "sessions": {
            "agent:main:telegram:group:-1003589561528:17": {
                "resume_pending": False,
            }
        },
        "brainFeed": {"updatedAt": health.iso(), "status": "active"},
        "cua": {
            "statusCode": 0,
            "status": "daemon is running",
            "permissions": {"accessibility": True, "screen_recording": True},
            "toolsCode": 0,
            "tools": "list_apps get_screen_size get_accessibility_tree",
            "screenProbe": {"returncode": 0},
        },
    }


def test_recent_managed_card_receipt_failure_degrades_health_without_restart_loop():
    probe = healthy_probe()
    probe["fastAckState"].update({
        "lastSurfaceOk": False,
        "deliveryError": {"at": health.iso(), "method": "editMessageText"},
    })

    status, issues, recovery_targets = health.evaluate(probe)

    assert status == "unhealthy"
    assert issues == ["A JAIMES Telegram card send or edit still lacks a confirmed receipt."]
    assert recovery_targets == set()


def test_missing_or_stale_fast_ack_poll_is_unhealthy_while_fresh_is_healthy():
    fresh_probe = healthy_probe()
    fresh_status, fresh_issues, fresh_targets = health.evaluate(fresh_probe)

    assert fresh_status == "ok"
    assert "JAIMES Telegram fast-ack has not completed a recent poll." not in fresh_issues
    assert "fast_ack" not in fresh_targets

    stale = (
        health.utc_now()
        - health.dt.timedelta(seconds=health.FAST_ACK_STALE_SECONDS + 1)
    )
    for last_checked_at in (None, health.iso(stale)):
        probe = healthy_probe()
        probe["fastAckState"]["lastCheckedAt"] = last_checked_at
        status, issues, recovery_targets = health.evaluate(probe)

        assert status == "unhealthy"
        assert "JAIMES Telegram fast-ack has not completed a recent poll." in issues
        assert recovery_targets == {"fast_ack"}


def test_health_dry_run_is_observational_and_writes_no_state_lock_log_or_publish(tmp_path, capsys):
    state_path = tmp_path / "jaimes-telegram-health.json"
    lock_path = tmp_path / "jaimes-telegram-health.lock"
    log_path = tmp_path / "jaimes-telegram-health.log"
    original = {
        "status": "ok",
        "failureStreak": 0,
        "lastPublishedAt": "2026-07-19T12:00:00Z",
        "lastHealthyAt": "2026-07-19T12:00:00Z",
    }
    state_path.write_text(json.dumps(original), encoding="utf-8")
    before = state_path.read_bytes()

    with patch.object(health, "STATE_PATH", state_path), \
         patch.object(health, "LOCK_PATH", lock_path), \
         patch.object(health, "LOG_PATH", log_path), \
         patch.object(health, "probe_jaimes", return_value=healthy_probe()), \
         patch.object(health, "lock_or_exit", side_effect=AssertionError("dry-run must not lock")), \
         patch.object(health, "brain_feed_needs_reconcile", side_effect=AssertionError("dry-run must not reconcile")), \
         patch.object(health, "reconcile_visibility", side_effect=AssertionError("dry-run must not repair")), \
         patch.object(health, "recover", side_effect=AssertionError("dry-run must not restart")), \
         patch.object(health, "heartbeat", side_effect=AssertionError("dry-run must not heartbeat")), \
         patch.object(health, "publish", side_effect=AssertionError("dry-run must not publish")), \
         patch.object(health, "write_json", side_effect=AssertionError("dry-run must not write")), \
         patch.object(health, "log", side_effect=AssertionError("dry-run must not log")), \
         patch.object(health.sys, "argv", ["jaimes_telegram_health.py", "--dry-run"]):
        assert health.main() == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert state_path.read_bytes() == before
    assert not lock_path.exists()
    assert not log_path.exists()
