#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
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
            "terminalIssueCount": 0,
            "strandedLifecycleCount": 0,
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


def test_indeterminate_terminal_receipt_degrades_semantic_health_without_restart_loop():
    probe = healthy_probe()
    probe["fastAckState"]["terminalIssueCount"] = 1

    status, issues, recovery_targets = health.evaluate(probe)

    assert status == "unhealthy"
    assert issues == ["A JAIMES Telegram final response has an unresolved delivery receipt."]
    assert recovery_targets == set()


def test_cardless_multi_step_receipt_degrades_semantic_health_without_restart_loop():
    probe = healthy_probe()
    probe["fastAckState"]["strandedLifecycleCount"] = 1

    status, issues, recovery_targets = health.evaluate(probe)

    assert status == "unhealthy"
    assert issues == ["A JAIMES Telegram multi-step request is stranded without a managed card."]
    assert recovery_targets == set()


def test_old_indeterminate_terminal_receipt_remains_unhealthy_until_receipt_backed_resolution():
    source = MODULE_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"def unresolved_terminal_issue\(card, cards\):\n(.*?)\n\npayload =",
        source,
        flags=re.S,
    )
    assert match
    namespace: dict = {}
    exec("def unresolved_terminal_issue(card, cards):\n" + match.group(1), namespace)
    unresolved_terminal_issue = namespace["unresolved_terminal_issue"]
    old = health.iso(health.utc_now() - health.dt.timedelta(days=2))
    unresolved = {
        "status": "failed",
        "ended_at": old,
        "final_contract_status": "delivery_indeterminate",
        "final_message_id": None,
    }
    assert unresolved_terminal_issue(unresolved, {}) is True

    resolved = {
        **unresolved,
        "final_message_id": "12345",
        "terminal_delivery_state": "delivered",
    }
    assert unresolved_terminal_issue(resolved, {}) is False


def test_indeterminate_incident_clears_only_for_matching_receipt_backed_recovery():
    source = MODULE_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"def unresolved_terminal_issue\(card, cards\):\n(.*?)\n\npayload =",
        source,
        flags=re.S,
    )
    assert match
    namespace: dict = {}
    exec("def unresolved_terminal_issue(card, cards):\n" + match.group(1), namespace)
    unresolved_terminal_issue = namespace["unresolved_terminal_issue"]
    incident = {
        "final_contract_status": "delivery_indeterminate",
        "recovered_by_work_id": "work-recovery",
        "recovery_final_message_id": "4113",
    }
    cards = {
        "recovery": {
            "work_id": "work-recovery",
            "final_message_id": "4113",
            "status": "done",
        }
    }
    assert unresolved_terminal_issue(incident, cards) is False
    cards["recovery"]["final_message_id"] = "different"
    assert unresolved_terminal_issue(incident, cards) is True


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


def test_remote_runs_multiline_script_via_stdin_instead_of_ssh_command_argv():
    script = "set -e\nlaunchctl kickstart -k gui/$(id -u)/example.service"
    completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

    with patch.object(health, "run", return_value=completed) as run:
        assert health.remote(script, timeout=17) is completed

    command = run.call_args.args[0]
    assert command[-3:] == ["/bin/zsh", "-s", "--"]
    assert script not in command
    assert run.call_args.kwargs == {"timeout": 17, "input_text": script}


def test_recovery_suppresses_all_child_output_but_keeps_safe_status_and_returncode():
    fake_key = "UNIT_FAKE_CREDENTIAL_NAME"
    fake_value = "unit-test-secret-9f8d"
    completed = subprocess.CompletedProcess(
        [],
        19,
        stdout=f"{fake_key}={fake_value}\n",
        stderr=f"credential {fake_value}\n",
    )

    with patch.object(health, "remote", return_value=completed):
        result = health.recover({"gateway"})

    serialized = json.dumps(result)
    assert result["ok"] is False
    assert result["returncode"] == 19
    assert result["capturedOutputSuppressed"] is True
    assert "stdout" not in result
    assert "stderr" not in result
    assert fake_key not in serialized
    assert fake_value not in serialized


def test_probe_failures_never_echo_remote_stdout_stderr_or_parse_payload():
    fake_key = "UNIT_FAKE_REMOTE_ENV_KEY"
    fake_value = "unit-test-secret-c781"
    results = (
        subprocess.CompletedProcess(
            [],
            23,
            stdout=f"{fake_key}={fake_value}\n",
            stderr=f"credential={fake_value}\n",
        ),
        subprocess.CompletedProcess(
            [],
            0,
            stdout=f"not-json {fake_key}={fake_value}\n",
            stderr="",
        ),
    )

    for completed in results:
        with patch.object(health, "remote", return_value=completed):
            probe = health.probe_jaimes()
        serialized = json.dumps(probe)
        assert probe["ok"] is False
        assert probe["probeReturncode"] == completed.returncode
        assert fake_key not in serialized
        assert fake_value not in serialized


def test_recovery_output_and_untrusted_probe_fields_never_reach_state_stdout_log_or_publish(
    tmp_path,
    capsys,
):
    fake_key = "UNIT_FAKE_ENV_DUMP_KEY"
    fake_value = "unit-test-private-value-4c2a"
    state_path = tmp_path / "jaimes-telegram-health.json"
    lock_path = tmp_path / "jaimes-telegram-health.lock"
    log_path = tmp_path / "jaimes-telegram-health.log"
    state_path.write_text(
        json.dumps({"status": "unhealthy", "failureStreak": 1}),
        encoding="utf-8",
    )
    unhealthy = healthy_probe()
    unhealthy["gateway"]["gateway_state"] = "stopped"
    unhealthy["fastAckState"]["identity"].update({fake_key: fake_value})
    unhealthy["brainFeed"].update({"auth": fake_value, "objective": f"{fake_key}={fake_value}"})
    unhealthy["cua"].update({"version": fake_value, "update": {fake_key: fake_value}})
    completed = subprocess.CompletedProcess(
        [],
        0,
        stdout=f"{fake_key}={fake_value}\n",
        stderr=f"credential={fake_value}\n",
    )

    with patch.object(health, "STATE_PATH", state_path), \
         patch.object(health, "LOCK_DIR", tmp_path), \
         patch.object(health, "LOCK_PATH", lock_path), \
         patch.object(health, "LOG_DIR", tmp_path), \
         patch.object(health, "LOG_PATH", log_path), \
         patch.object(health, "probe_jaimes", side_effect=[unhealthy, healthy_probe()]), \
         patch.object(health, "remote", return_value=completed), \
         patch.object(health, "brain_feed_needs_reconcile", return_value=False), \
         patch.object(health, "heartbeat"), \
         patch.object(health, "publish") as publish, \
         patch.object(health.sys, "argv", ["jaimes_telegram_health.py"]):
        assert health.main() == 0

    stdout = capsys.readouterr().out
    persisted = state_path.read_text(encoding="utf-8")
    logged = log_path.read_text(encoding="utf-8")
    shared = repr(publish.call_args_list)
    combined = "\n".join((stdout, persisted, logged, shared))
    state = json.loads(persisted)
    assert state["recovery"]["ok"] is True
    assert state["recovery"]["returncode"] == 0
    assert state["recovery"]["capturedOutputSuppressed"] is True
    assert fake_key not in combined
    assert fake_value not in combined
