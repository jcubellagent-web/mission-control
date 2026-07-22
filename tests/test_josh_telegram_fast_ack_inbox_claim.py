import argparse
import datetime as dt
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "josh_telegram_fast_ack.py"
SPEC = importlib.util.spec_from_file_location("josh_telegram_fast_ack", MODULE_PATH)
watcher = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = watcher
SPEC.loader.exec_module(watcher)


@pytest.fixture(autouse=True)
def isolate_gateway_lifecycle_store(monkeypatch, tmp_path):
    """Keep deterministic test work IDs out of the real private journal."""
    rollout_path = tmp_path / "telegram-lifecycle-rollout.json"
    rollout_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "writerLifecycleVersion": 3,
                "readerLifecycleVersions": [2, 3],
                "masterState": "off",
                "globalKillSwitch": False,
                "hosts": {"josh2": True, "jaimes": True},
                "shadowMinimumPerOwner": 20,
                "brainFixtureMinimum": 20,
                "rollback": {
                    "newWorkToLegacy": False,
                    "drainExistingVersionedWork": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        watcher,
        "LIFECYCLE_PRIVATE_ROOT",
        tmp_path / "telegram-lifecycle",
    )
    monkeypatch.setattr(watcher, "LIFECYCLE_ROLLOUT_PATH", rollout_path)
    monkeypatch.setattr(watcher, "_GATEWAY_LIFECYCLE", None)
    yield
    watcher._GATEWAY_LIFECYCLE = None


def test_publish_josh_retries_until_canonical_work_receipt_is_accepted():
    failed = subprocess.CompletedProcess(["publish"], 1, stdout="", stderr="temporary")
    accepted = subprocess.CompletedProcess(
        ["publish"], 0,
        stdout=json.dumps({"ok": True, "workLedger": {"accepted": True}}),
        stderr="",
    )
    with patch.object(watcher.subprocess, "run", side_effect=[failed, failed, accepted]) as run, \
         patch.object(watcher.time, "sleep"):
        ok = watcher.publish_josh(
            "Visible task", "active", "Working",
            work_id="work-1", run_id="run-1", work_event="start",
        )

    assert ok is True
    assert run.call_count == 3
    assert all(call.kwargs["capture_output"] is True for call in run.call_args_list)


def test_architecture_review_keeps_private_work_phrase_dashboard_safe():
    prompt = (
        "Assess whether our model routing is resilient and whether private work and "
        "execution are routed appropriately. Make no changes.\n"
        "Return three findings, the verified model and authentication route actually "
        "used, any fallback that occurred, and a final conclusion of functioning or "
        "needs attention."
    )
    assert watcher.classify_privacy(prompt) == "dashboard-safe"
    assert watcher.objective_from_prompt(prompt) == (
        "Assess whether our model routing is resilient and whether private work"
    )


def test_real_private_content_still_stays_on_the_sensitive_lane():
    assert watcher.classify_privacy("Review this private email account login failure.") == "sensitive-account"


def test_send_ack_fails_closed_when_canonical_work_visibility_cannot_publish():
    event = {
        "session_id": "session",
        "ts": "2026-07-15T04:23:21Z",
        "run_id": "before-dispatch:1",
        "message_id": "",
        "prompt": "Inspect the current Control Tower state",
    }
    with patch.object(watcher, "fast_ack_enabled", return_value=True), \
         patch.object(watcher, "live_cards_enabled", return_value=False), \
         patch.object(watcher, "send_chat_action"), \
         patch.object(watcher, "send_message_draft"), \
         patch.object(watcher, "send_prompt_reaction", return_value=True), \
         patch.object(watcher, "publish_josh", return_value=False):
        result = watcher.send_ack(
            event,
            model=watcher.DEFAULT_MODEL,
            dry_run=False,
            meta={"telegram_chat_id": "-100", "telegram_thread_id": "1"},
        )

    assert result["ok"] is False
    assert result["status"] == "visibility-failed"
    assert result["visibility_publish_ok"] is False


def test_send_ack_uses_prompt_reaction_without_message_id_and_does_not_fail_claim():
    event = {"session_id": "session", "ts": "2026-07-15T03:42:21Z", "run_id": "before-dispatch:1", "message_id": "", "prompt": "private request"}
    with patch.object(watcher, "fast_ack_enabled", return_value=True), patch.object(watcher, "send_chat_action"), patch.object(watcher, "send_message_draft"), patch.object(watcher, "send_prompt_reaction", return_value=False) as prompt_reaction, patch.object(watcher, "publish_josh"):
        result = watcher.send_ack(event, model=watcher.DEFAULT_MODEL, dry_run=False, meta={"telegram_chat_id": "-100", "telegram_thread_id": "1"})
    prompt_reaction.assert_called_once_with("private request", meta={"telegram_chat_id": "-100", "telegram_thread_id": "1"})
    assert result["ok"] is True
    assert result["reaction_ok"] is False


def test_send_ack_starts_card_with_workspace_helper_and_returns_receipt():
    event = {"session_id": "session", "ts": "2026-07-15T04:23:21Z", "run_id": "before-dispatch:1", "message_id": "", "prompt": "Fix a multi-step Inbox task"}
    card_receipt = '{"ok": true, "header_message_id": 443, "message_id": 444}'
    with patch.object(watcher, "fast_ack_enabled", return_value=True), patch.object(watcher, "live_cards_enabled", return_value=True), patch.object(watcher, "send_chat_action"), patch.object(watcher, "send_message_draft"), patch.object(watcher, "send_prompt_reaction", return_value=True), patch.object(watcher, "auto_route_for_prompt", return_value={"model": "planned model", "route": "planned route", "route_plan": {"routeId": "luna"}}), patch.object(watcher, "skill_for_prompt", return_value={"id": "", "label": "", "reason": ""}), patch.object(watcher, "publish_josh"), patch.object(watcher, "run_cmd", return_value={"ok": True, "stdout": card_receipt}) as run_cmd:
        result = watcher.send_ack(event, model=watcher.DEFAULT_MODEL, dry_run=False, meta={"telegram_chat_id": watcher.CONTROL_CENTER_CHAT_ID, "telegram_thread_id": "1"})
    work_card_call = next(call for call in run_cmd.call_args_list if str(watcher.WORK_CARD_SCRIPT) in call.args[0])
    command = work_card_call.args[0]
    assert command[1] == str(watcher.WORK_CARD_SCRIPT)
    assert command[command.index("--thread-id") + 1] == "1"
    assert command[command.index("--timeout") + 1] == "6"
    assert work_card_call.kwargs["timeout"] == 25
    assert watcher.SEND_REPLY_SCRIPT == watcher.WORK_CARD_SCRIPT.with_name("send_josh_reply.py")
    assert result["card_start_ok"] is True
    assert result["header_message_id"] == "443"
    assert result["live_message_id"] == "444"
    assert result["card_start_attempts"] == 1
    assert result["card_start_receipt"] == card_receipt


def test_send_ack_exact_reply_tier_two_keeps_coordinator_lifecycle():
    event = {
        "session_id": "session",
        "ts": "2026-07-21T14:07:36Z",
        "run_id": "telegram-message:-1003589561528:1:4121",
        "message_id": "4121",
        "prompt": 'Canary Inbox: reply exactly "Inbox receipt confirmed"',
    }
    gateway = {
        "writer": True,
        "shadow": False,
        "receipt": {
            "workId": "work-inbox-canary",
            "lifecycleVersion": 3,
            "deliveryTier": 2,
            "classifierReason": "exact-reply",
            "sequence": 1,
            "fencingEpoch": 1,
        },
    }
    with patch.object(watcher, "begin_gateway_lifecycle", return_value=gateway), \
         patch.object(watcher, "advance_gateway_phase"), \
         patch.object(watcher, "set_gateway_worker_route"), \
         patch.object(watcher, "claim_gateway_effect", return_value={"allowed": True, "idempotencyKey": "reaction"}), \
         patch.object(watcher, "finish_gateway_effect"), \
         patch.object(watcher, "gateway_public_fields", return_value={
             "lifecycle_version": 3,
             "delivery_tier": 2,
             "lifecycle_writer_enabled": True,
         }), \
         patch.object(watcher, "objective_from_prompt", return_value=event["prompt"]), \
         patch.object(watcher, "semantic_reinterpretation", return_value=""), \
         patch.object(watcher, "place_inbox_reaction", return_value=True), \
         patch.object(watcher, "auto_route_for_prompt", return_value={
             "model": "planned model",
             "route": "planned route",
             "route_plan": {"routeId": "luna"},
         }), \
         patch.object(watcher, "skill_for_prompt", return_value={"id": "", "label": "", "reason": ""}), \
         patch.object(watcher, "publish_josh", return_value=True), \
         patch.object(watcher, "run_work_card_start") as card_start:
        result = watcher.send_ack(
            event,
            model=watcher.DEFAULT_MODEL,
            dry_run=False,
            meta={
                "telegram_chat_id": watcher.CONTROL_CENTER_CHAT_ID,
                "telegram_thread_id": "1",
            },
        )

    assert result["ok"] is True
    assert result["objective"] == "Respond to the current Telegram message"
    assert result["no_card_required"] is True
    assert result["delivery_tier"] == 2
    assert result["surface_contract"] == "tier-2-final-v3"
    card_start.assert_not_called()


def test_live_only_v2_receipt_is_a_complete_topic_surface():
    receipt = watcher.parse_work_card_start_receipt(
        "missing-card-state",
        {
            "ok": True,
            "stdout": json.dumps({
                "ok": True,
                "header_required": False,
                "surface_contract": "live-only-v2",
                "header_message_id": None,
                "message_id": 444,
            }),
        },
    )
    assert receipt["surface_ok"] is True
    assert receipt["header_required"] is False
    assert receipt["surface_contract"] == "live-only-v2"
    assert receipt["header_message_id"] == ""
    assert receipt["live_message_id"] == "444"


def test_contradictory_or_unknown_surface_contracts_fail_closed():
    for payload in (
        {
            "header_required": False,
            "surface_contract": "header-live-v1",
            "message_id": 444,
        },
        {
            "header_required": True,
            "surface_contract": "live-only-v2",
            "message_id": 444,
        },
        {
            "header_required": False,
            "surface_contract": "future-contract",
            "message_id": 444,
        },
    ):
        receipt = watcher.parse_work_card_start_receipt(
            "missing-card-state",
            {"ok": True, "stdout": json.dumps({"ok": True, **payload})},
        )
        assert receipt["surface_ok"] is False


def test_reaction_happens_before_route_and_skill_probes():
    order = []
    event = {"session_id": "session", "ts": "2026-07-15T04:23:21Z", "run_id": "before-dispatch:1", "message_id": "42", "prompt": "Check the Inbox"}
    with patch.object(watcher, "fast_ack_enabled", return_value=True), patch.object(watcher, "live_cards_enabled", return_value=False), patch.object(watcher, "send_chat_action"), patch.object(watcher, "send_message_draft"), patch.object(watcher, "send_message_reaction", side_effect=lambda *args, **kwargs: order.append("eyes") or True), patch.object(watcher, "auto_route_for_prompt", side_effect=lambda *args, **kwargs: order.append("route") or {"model": "planned model", "route": "planned route"}), patch.object(watcher, "skill_for_prompt", side_effect=lambda *args, **kwargs: order.append("skill") or {"id": "", "label": "", "reason": ""}), patch.object(watcher, "publish_josh"):
        watcher.send_ack(event, model=watcher.DEFAULT_MODEL, dry_run=False, meta={"telegram_chat_id": "-100", "telegram_thread_id": "1"})
    assert order == ["eyes", "route", "skill"]


def test_missing_message_retry_reuses_run_scoped_card_key():
    event = {"session_id": "session", "ts": "2026-07-15T04:23:21Z", "run_id": "stable-run", "message_id": "", "prompt": "Check the Inbox"}
    common = [
        patch.object(watcher, "fast_ack_enabled", return_value=True),
        patch.object(watcher, "live_cards_enabled", return_value=False),
        patch.object(watcher, "send_chat_action"),
        patch.object(watcher, "send_message_draft"),
        patch.object(watcher, "send_prompt_reaction", return_value=True),
        patch.object(watcher, "auto_route_for_prompt", return_value={"model": "planned model", "route": "planned route"}),
        patch.object(watcher, "skill_for_prompt", return_value={"id": "", "label": "", "reason": ""}),
        patch.object(watcher, "publish_josh"),
    ]
    for item in common:
        item.start()
    try:
        first = watcher.send_ack(event, model=watcher.DEFAULT_MODEL, dry_run=False, meta={"telegram_chat_id": "-100", "telegram_thread_id": "1"})
        event["ts"] = "2026-07-15T04:23:22Z"
        second = watcher.send_ack(event, model=watcher.DEFAULT_MODEL, dry_run=False, meta={"telegram_chat_id": "-100", "telegram_thread_id": "1"})
    finally:
        for item in reversed(common):
            item.stop()
    assert first["key"] == second["key"]


def test_claim_inbox_leaves_native_fallback_when_ack_reports_failure():
    args = argparse.Namespace(run_id="before-dispatch:1", message_id="", chat_id="-100", thread_id="1", session_key="session", dry_run=False)
    with patch("sys.stdin.read", return_value="private request"), patch.object(watcher, "send_ack", return_value={"ok": False, "status": "reaction-failed", "reaction_ok": False, "key": "card-1"}), patch.object(watcher, "run_cmd") as run_cmd, patch.object(watcher, "publish_josh"):
        result = watcher.claim_inbox(args)
    assert result["status"] == "reaction-failed"
    assert result["reaction_ok"] is False
    assert result["card_start_ok"] is False
    run_cmd.assert_not_called()


def test_claim_inbox_refuses_queue_without_topic_surface_receipts():
    args = argparse.Namespace(
        run_id="telegram-message:-1003589561528:1:902",
        message_id="902",
        chat_id=watcher.CONTROL_CENTER_CHAT_ID,
        thread_id="1",
        session_key="session",
        dry_run=False,
    )
    ack = {
        "ok": True,
        "reaction_ok": True,
        "card_start_ok": False,
        "header_message_id": "101",
        "live_message_id": "",
        "key": "card-902",
    }
    with patch("sys.stdin.read", return_value="private request"), patch.object(watcher, "send_ack", return_value=ack), patch.object(watcher, "run_cmd") as run_cmd, patch.object(watcher, "publish_josh"):
        result = watcher.claim_inbox(args)
    assert result == {
        "ok": False,
        "status": "surface-failed",
        "reaction_ok": True,
        "key": "card-902",
        "card_start_ok": False,
        "header_message_id": "101",
        "live_message_id": "",
        "header_required": True,
        "surface_contract": "header-live-v1",
        "surface_indeterminate": False,
    }
    run_cmd.assert_not_called()


def test_queue_failure_preserves_all_durable_surface_receipts():
    args = argparse.Namespace(
        run_id="telegram-message:-1003589561528:1:904",
        message_id="904",
        chat_id=watcher.CONTROL_CENTER_CHAT_ID,
        thread_id="1",
        session_key="session",
        dry_run=False,
    )
    ack = {
        "ok": True,
        "reaction_ok": True,
        "card_start_ok": True,
        "header_message_id": "401",
        "live_message_id": "402",
        "key": "card-904",
        "objective": "Queue a worker",
        "model": "model",
        "route": "route",
        "last_card_update_at": watcher.utc_now(),
    }

    def fail_submit(cmd, *args, **kwargs):
        if str(watcher.COORDINATOR_SCRIPT) in cmd:
            return {"ok": False, "stdout": "", "stderr": "queue unavailable"}
        return {"ok": True, "stdout": "{}", "stderr": ""}

    with patch("sys.stdin.read", return_value="private request"), \
         patch.object(watcher, "send_ack", return_value=ack), \
         patch.object(watcher, "run_cmd", side_effect=fail_submit), \
         patch.object(watcher, "publish_josh"), \
         patch.object(watcher, "persist_claim_state") as persist:
        result = watcher.claim_inbox(args)

    assert result == {
        "ok": False,
        "status": "queue-failed",
        "error": "coordinator_submit_failed",
        "reaction_ok": True,
        "card_start_ok": True,
        "header_message_id": "401",
        "live_message_id": "402",
        "header_required": True,
        "surface_contract": "header-live-v1",
        "job_id": "",
        "key": "card-904",
        "terminal_fallback_queued": False,
    }
    persist.assert_not_called()


def test_invalid_coordinator_envelope_never_claims_queued():
    args = argparse.Namespace(
        run_id="telegram-message:-1003589561528:1:905",
        message_id="905",
        chat_id=watcher.CONTROL_CENTER_CHAT_ID,
        thread_id="1",
        session_key="session",
        dry_run=False,
    )
    ack = {
        "ok": True,
        "reaction_ok": True,
        "card_start_ok": True,
        "header_message_id": "501",
        "live_message_id": "502",
        "key": "card-905",
        "objective": "Validate coordinator receipt",
        "model": "model",
        "route": "route",
        "last_card_update_at": watcher.utc_now(),
    }

    for stdout, expected_error in (
        ("not-json", "coordinator_receipt_invalid_json"),
        ('{"ok":true,"job":{}}', "coordinator_receipt_missing_job"),
    ):
        def invalid_receipt(cmd, *args, **kwargs):
            if str(watcher.COORDINATOR_SCRIPT) in cmd:
                return {"ok": True, "stdout": stdout, "stderr": ""}
            return {"ok": True, "stdout": "{}", "stderr": ""}

        with patch("sys.stdin.read", return_value="private request"), \
             patch.object(watcher, "send_ack", return_value=ack), \
             patch.object(watcher, "run_cmd", side_effect=invalid_receipt), \
             patch.object(watcher, "publish_josh"), \
             patch.object(watcher, "persist_claim_state") as persist:
            result = watcher.claim_inbox(args)

        assert result["ok"] is False
        assert result["status"] == "queue-failed"
        assert result["error"] == expected_error
        assert result["header_message_id"] == "501"
        assert result["live_message_id"] == "502"
        assert result["job_id"] == ""
        persist.assert_not_called()


def test_valid_coordinator_envelope_returns_verified_queue_receipt():
    args = argparse.Namespace(
        run_id="telegram-message:-1003589561528:1:906",
        message_id="906",
        chat_id=watcher.CONTROL_CENTER_CHAT_ID,
        thread_id="1",
        session_key="session",
        dry_run=False,
    )
    ack = {
        "ok": True,
        "reaction_ok": True,
        "card_start_ok": True,
        "header_message_id": "601",
        "live_message_id": "602",
        "key": "card-906",
        "objective": "Queue verified work",
        "model": "model",
        "route": "route",
        "route_plan": {"routeId": "luna"},
        "last_card_update_at": watcher.utc_now(),
    }
    envelope = {
        "job": {"jobId": "job-906"},
        "route": {"routeId": "luna"},
        "deduplicated": False,
    }
    with patch("sys.stdin.read", return_value="private request"), \
         patch.object(watcher, "send_ack", return_value=ack), \
         patch.object(watcher, "run_cmd", return_value={"ok": True, "stdout": json.dumps(envelope), "stderr": ""}), \
         patch.object(watcher, "persist_claim_state") as persist:
        result = watcher.claim_inbox(args)

    assert result == {
        "ok": True,
        "status": "queued",
        "reaction_ok": True,
        "card_start_ok": True,
        "header_message_id": "601",
        "live_message_id": "602",
        "header_required": True,
        "surface_contract": "header-live-v1",
        "no_card_required": False,
        "delivery_tier": 0,
        "lifecycle_version": 0,
        "job_id": "job-906",
        "route_id": "luna",
        "deduplicated": False,
    }
    persist.assert_called_once()


def test_claim_accepts_one_versioned_live_card_without_task_header():
    args = argparse.Namespace(
        run_id="telegram-message:-1003589561528:1:907",
        message_id="907",
        chat_id=watcher.CONTROL_CENTER_CHAT_ID,
        thread_id="1",
        session_key="session",
        dry_run=False,
    )
    ack = {
        "ok": True,
        "reaction_ok": True,
        "card_start_ok": True,
        "header_message_id": "",
        "live_message_id": "702",
        "header_required": False,
        "surface_contract": "live-only-v2",
        "key": "card-907",
        "objective": "Assess Telegram health read-only",
        "model": "model",
        "route": "route",
        "route_plan": {"routeId": "terra"},
        "last_card_update_at": watcher.utc_now(),
    }
    envelope = {"job": {"jobId": "job-907"}, "route": {"routeId": "terra"}}
    with patch("sys.stdin.read", return_value="Read-only Telegram health check"), \
         patch.object(watcher, "send_ack", return_value=ack), \
         patch.object(watcher, "run_cmd", return_value={"ok": True, "stdout": json.dumps(envelope), "stderr": ""}), \
         patch.object(watcher, "persist_claim_state") as persist:
        result = watcher.claim_inbox(args)

    assert result["ok"] is True
    assert result["header_message_id"] == ""
    assert result["live_message_id"] == "702"
    assert result["header_required"] is False
    assert result["surface_contract"] == "live-only-v2"
    persisted_card = persist.call_args.args[1]
    assert persisted_card["header_required"] is False


def test_card_start_retries_once_only_after_durable_header(monkeypatch, tmp_path):
    state_path = tmp_path / "work-cards.json"
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", state_path)
    event = {
        "session_id": "session",
        "ts": "2026-07-15T04:23:21Z",
        "run_id": "telegram-message:-1003589561528:1:902",
        "message_id": "902",
        "prompt": "Verify a retry-safe Inbox card",
    }
    expected_key = "fast-ack-telegram--1003589561528-1-message-902"
    calls = []

    def run_card(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if len(calls) == 1:
            state_path.write_text(json.dumps({
                "cards": {
                    expected_key: {
                        "status": "running",
                        "header_message_id": 101,
                        "message_id": None,
                    }
                }
            }), encoding="utf-8")
            return {"ok": False, "stdout": "", "stderr": '{"ok":false,"action":"sent"}'}
        return {"ok": True, "stdout": '{"ok":true,"header_message_id":101,"message_id":102}', "stderr": ""}

    monkeypatch.setattr(watcher, "send_message_reaction", lambda *args, **kwargs: True)
    monkeypatch.setattr(watcher, "send_chat_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(watcher, "send_message_draft", lambda *args, **kwargs: None)
    monkeypatch.setattr(watcher, "auto_route_for_prompt", lambda *args, **kwargs: {"model": "planned model", "route": "planned route", "route_plan": {"routeId": "luna"}})
    monkeypatch.setattr(watcher, "skill_for_prompt", lambda *args, **kwargs: {"id": "", "label": "", "reason": ""})
    monkeypatch.setattr(watcher, "publish_josh", lambda *args, **kwargs: True)
    monkeypatch.setattr(watcher, "run_cmd", run_card)

    result = watcher.send_ack(event, watcher.DEFAULT_MODEL, meta={
        "telegram_chat_id": watcher.CONTROL_CENTER_CHAT_ID,
        "telegram_thread_id": "1",
    })

    assert result["ok"] is True
    assert result["card_start_attempts"] == 2
    assert result["header_message_id"] == "101"
    assert result["live_message_id"] == "102"
    assert len(calls) == 2
    assert all(cmd[cmd.index("--key") + 1] == expected_key for cmd in calls)


def test_card_start_does_not_retry_an_indeterminate_live_send(monkeypatch, tmp_path):
    state_path = tmp_path / "work-cards.json"
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", state_path)
    expected_key = "fast-ack-telegram--1003589561528-1-message-903"
    calls = []

    def ambiguous_card(cmd, *args, **kwargs):
        calls.append(list(cmd))
        state_path.write_text(json.dumps({
            "cards": {
                expected_key: {
                    "status": "running",
                    "header_message_id": 201,
                    "message_id": None,
                    "live_delivery_status": "indeterminate",
                }
            }
        }), encoding="utf-8")
        return {"ok": False, "stdout": "", "stderr": "timed out after request write"}

    monkeypatch.setattr(watcher, "send_message_reaction", lambda *args, **kwargs: True)
    monkeypatch.setattr(watcher, "send_chat_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(watcher, "send_message_draft", lambda *args, **kwargs: None)
    monkeypatch.setattr(watcher, "auto_route_for_prompt", lambda *args, **kwargs: {"model": "planned model", "route": "planned route", "route_plan": {"routeId": "luna"}})
    monkeypatch.setattr(watcher, "skill_for_prompt", lambda *args, **kwargs: {"id": "", "label": "", "reason": ""})
    monkeypatch.setattr(watcher, "run_cmd", ambiguous_card)

    result = watcher.send_ack({
        "session_id": "session",
        "ts": "2026-07-15T04:23:21Z",
        "run_id": "telegram-message:-1003589561528:1:903",
        "message_id": "903",
        "prompt": "Verify ambiguous delivery handling",
    }, watcher.DEFAULT_MODEL, meta={
        "telegram_chat_id": watcher.CONTROL_CENTER_CHAT_ID,
        "telegram_thread_id": "1",
    })

    assert result["ok"] is False
    assert result["status"] == "surface-failed"
    assert result["card_start_attempts"] == 1
    assert result["header_message_id"] == "201"
    assert result["live_message_id"] == ""
    assert len(calls) == 1


def test_stale_poller_merge_preserves_concurrent_claim(monkeypatch, tmp_path):
    state_path = tmp_path / "fast-ack.json"
    monkeypatch.setattr(watcher, "STATE_PATH", state_path)
    watcher.save_json(state_path, {
        "status": "ok",
        "active_cards": {"old": {"status": "active", "last_card_update_at": "old"}},
        "acked_prompt_events": ["old-event"],
    })
    candidate, base = watcher.load_fast_ack_state_snapshot()
    candidate["last_checked_at"] = "2026-07-15T12:00:00Z"
    candidate["active_cards"]["old"]["last_card_update_at"] = "poll-update"

    claim = {"key": "card-new", "status": "active", "coordinator_owned": True}
    worker = threading.Thread(target=watcher.persist_claim_state, args=(
        "new-run",
        claim,
        {"run_id": "new-run", "job_id": "job-new", "header_message_id": "301", "live_message_id": "302"},
    ))
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()

    merged = watcher.merge_poll_state(candidate, base)
    assert merged["active_cards"]["new-run"] == claim
    assert merged["active_cards"]["old"]["last_card_update_at"] == "poll-update"
    assert merged["last_claim"]["job_id"] == "job-new"
    assert merged["acked_prompt_events"] == ["old-event"]


def test_concurrent_claim_writers_preserve_both_cards(monkeypatch, tmp_path):
    state_path = tmp_path / "fast-ack.json"
    monkeypatch.setattr(watcher, "STATE_PATH", state_path)
    watcher.save_json(state_path, {"active_cards": {}})
    barrier = threading.Barrier(3)

    def write_claim(run_id):
        barrier.wait(timeout=2)
        watcher.persist_claim_state(run_id, {"key": f"card-{run_id}", "status": "active"}, {"run_id": run_id})

    workers = [threading.Thread(target=write_claim, args=(run_id,)) for run_id in ("one", "two")]
    for worker in workers:
        worker.start()
    barrier.wait(timeout=2)
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    state = watcher.load_json(state_path, {})
    assert set(state["active_cards"]) == {"one", "two"}


def test_cross_process_claim_stress_preserves_every_card(tmp_path):
    state_path = tmp_path / "fast-ack.json"
    watcher.save_json(state_path, {"active_cards": {}})
    writer_code = """
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("fast_ack_writer", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.STATE_PATH = Path(sys.argv[2])
prefix = sys.argv[3]
for index in range(int(sys.argv[4])):
    run_id = f"{prefix}-{index}"
    module.persist_claim_state(
        run_id,
        {"key": f"card-{run_id}", "status": "active"},
        {"run_id": run_id},
    )
"""
    writer_count = 8
    cards_per_writer = 12
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                writer_code,
                str(MODULE_PATH),
                str(state_path),
                f"writer-{writer}",
                str(cards_per_writer),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for writer in range(writer_count)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)

    state = watcher.load_json(state_path, {})
    cards = state.get("active_cards") or {}
    assert len(cards) == writer_count * cards_per_writer
    assert set(cards) == {
        f"writer-{writer}-{index}"
        for writer in range(writer_count)
        for index in range(cards_per_writer)
    }


def test_coordinator_worker_gets_heartbeat_while_running():
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=watcher.HEARTBEAT_SECONDS + 5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state = {
        "active_cards": {
            "run-1": {
                "key": "card-1",
                "objective": "Long Inbox worker",
                "model": "codex/gpt-5.6-luna",
                "route": "route=luna; owner=josh2",
                "session_id": "session",
                "job_id": "job-1",
                "coordinator_owned": True,
                "started_at": old,
                "last_card_update_at": old,
                "status": "active",
            }
        }
    }
    with patch.object(watcher, "live_cards_enabled", return_value=True), patch.object(watcher, "recent_progress_events", return_value=[]), patch.object(watcher, "coordinator_job_status", return_value="running"):
        updates = watcher.update_active_cards(state, "session", dry_run=True, meta={"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"})
    assert len(updates) == 1
    assert updates[0]["event"].startswith("heartbeat:run-1:")
    assert state["active_cards"]["run-1"]["last_card_update_at"] != old


def test_coordinator_worker_heartbeat_cannot_claim_verified_route():
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=watcher.HEARTBEAT_SECONDS + 5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state = {
        "active_cards": {
            "run-1": {
                "key": "card-1",
                "objective": "Long Inbox worker",
                "model": "codex/gpt-5.6-luna",
                "route": "route=luna; owner=josh2",
                "job_id": "job-1",
                "coordinator_owned": True,
                "work_id": "work-1",
                "ledger_run_id": "ledger-run-1",
                "origin_claim_hash": "a" * 64,
                "started_at": old,
                "last_card_update_at": old,
                "status": "active",
            }
        }
    }
    published = []
    with patch.object(watcher, "recent_progress_events", return_value=[]), \
         patch.object(watcher, "coordinator_job_snapshot", return_value={"status": "running"}), \
         patch.object(watcher, "run_cmd", return_value={"ok": True}), \
         patch.object(watcher, "publish_josh", side_effect=lambda *args, **kwargs: published.append((args, kwargs)) or True):
        watcher.update_active_cards(state, "session", dry_run=False, meta={"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"})
    assert published
    assert published[-1][1]["work_event"] == "heartbeat"
    assert published[-1][1]["route_verified"] is False
    assert published[-1][1]["brain_feed"] is True


def test_failed_coordinator_card_is_terminal_and_not_refreshed():
    old = "2026-07-01T00:00:00Z"
    state = {"active_cards": {"run-1": {"key": "card-1", "status": "failed", "last_card_update_at": old}}}
    with patch.object(watcher, "live_cards_enabled", return_value=True), patch.object(watcher, "recent_progress_events", return_value=[]), patch.object(watcher, "coordinator_job_status") as status:
        updates = watcher.update_active_cards(state, "session", dry_run=True, meta={"telegram_chat_id": "-1003589561528", "telegram_thread_id": "1"})
    assert updates == []
    assert state["active_cards"]["run-1"]["last_card_update_at"] == old
    status.assert_not_called()


def test_terminal_card_history_is_bounded():
    state = {"active_cards": {f"run-{index}": {"status": "done", "ended_at": f"2026-07-01T00:{index % 60:02d}:00Z"} for index in range(120)}}
    removed = watcher.prune_terminal_cards(state, keep=10)
    assert removed == 110
    assert len(state["active_cards"]) == 10


def test_effect_protocol_checkpoints_only_before_irreversible_surface(tmp_path):
    effect_path = tmp_path / "claim.effects.json"
    cancel_path = tmp_path / "claim.cancel.json"
    protocol = {"effect": effect_path, "cancel": cancel_path}

    assert watcher.telegram_claim_not_cancelled(protocol) is True
    assert not effect_path.exists(), "eyes-only acknowledgement must not fence native fallback"
    assert watcher.begin_telegram_surface(protocol, "header-live-card") is True
    effect = json.loads(effect_path.read_text(encoding="utf-8"))
    assert effect["state"] == "attempting"
    assert effect["stage"] == "header-live-card"
    assert "prompt" not in effect


def test_effect_protocol_honors_timeout_cancellation_before_surface(tmp_path):
    effect_path = tmp_path / "claim.effects.json"
    cancel_path = tmp_path / "claim.cancel.json"
    cancel_path.write_text('{"version":1,"state":"cancelled-before-surface"}\n', encoding="utf-8")
    protocol = {"effect": effect_path, "cancel": cancel_path}

    assert watcher.telegram_claim_not_cancelled(protocol) is False
    assert watcher.begin_telegram_surface(protocol, "header-live-card") is False
    assert not effect_path.exists()


def test_coordinator_submit_timeout_returns_durable_receipt(monkeypatch, tmp_path):
    args = argparse.Namespace(
        run_id="telegram-message:-1003589561528:1:990",
        message_id="990",
        chat_id=watcher.CONTROL_CENTER_CHAT_ID,
        thread_id="1",
        session_key="session",
        dry_run=False,
        effect_path=str(tmp_path / "claim.effects.json"),
        cancel_path=str(tmp_path / "claim.cancel.json"),
    )
    ack = {
        "ok": True,
        "reaction_ok": True,
        "card_start_ok": True,
        "header_message_id": "901",
        "live_message_id": "902",
        "key": "card-990",
        "objective": "Exercise timeout receipt",
        "model": "model",
        "route": "route",
        "last_card_update_at": watcher.utc_now(),
    }

    def timeout_submit(cmd, *unused_args, **unused_kwargs):
        if str(watcher.COORDINATOR_SCRIPT) in cmd:
            raise subprocess.TimeoutExpired(cmd, 30)
        return {"ok": True, "stdout": "{}", "stderr": ""}

    monkeypatch.setattr("sys.stdin.read", lambda: "private request")
    monkeypatch.setattr(watcher, "send_ack", lambda *args, **kwargs: ack)
    monkeypatch.setattr(watcher, "run_cmd", timeout_submit)
    monkeypatch.setattr(watcher, "publish_josh", lambda *args, **kwargs: True)
    result = watcher.claim_inbox(args)

    assert result["ok"] is False
    assert result["status"] == "queue-failed"
    assert result["error"] == "coordinator_submit_timeout"
    assert result["header_message_id"] == "901"
    assert result["live_message_id"] == "902"
    effect = json.loads(Path(args.effect_path).read_text(encoding="utf-8"))
    assert effect["state"] == "indeterminate"
