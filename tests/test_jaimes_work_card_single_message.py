#!/usr/bin/env python3
import datetime as dt
import fcntl
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest


TEST_DIR = Path(__file__).resolve().parent
STAGED_MODULE_PATH = TEST_DIR / "jaimes_work_card.py"
MODULE_PATH = (
    STAGED_MODULE_PATH
    if STAGED_MODULE_PATH.exists()
    else Path(__file__).resolve().parents[1] / "scripts" / "jaimes_work_card.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("jaimes_work_card_single_message", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


card = load_module()


def test_default_state_path_is_absolute_and_workspace_scoped():
    assert card.STATE_PATH.is_absolute()
    assert card.STATE_PATH == card.ROOT.parent / "memory" / "jaimes_work_cards.json"


def test_missing_local_telegram_credential_is_definitive_before_network_io():
    result = {"ok": False, "error": "JAIMES Telegram token or target chat is unavailable"}
    assert card.delivery_indeterminate(result) is False


def test_brain_feed_publish_uses_canonical_josh_ledger_with_identity():
    args = SimpleNamespace(
        no_brain_feed=False,
        dry_run=False,
        title="Lifecycle canary",
        key="lifecycle-canary",
        now="Verifying final delivery",
        next="",
        blocker="",
    )
    completed = subprocess.CompletedProcess(args=[], returncode=0)
    with patch.object(card.subprocess, "run", return_value=completed) as run:
        assert card.publish_brain_feed(
            args,
            "done",
            work_id="work-20260718",
            run_id="run-20260718",
        ) is True

    command = run.call_args.args[0]
    assert command[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4"]
    assert command[5] == card.CONTROL_TOWER_SSH_HOST
    remote_command = command[6]
    assert card.CONTROL_TOWER_REMOTE_ROOT in remote_command
    assert f"{card.CONTROL_TOWER_REMOTE_ROOT}/scripts/agent_publish.py" in remote_command
    assert "--work-id work-20260718" in remote_command
    assert "--run-id run-20260718" in remote_command
    assert "--status done" in remote_command
    assert str(card.ROOT / "scripts" / "agent_publish.py") not in command


def test_brain_feed_publish_never_falls_back_to_local_ledger_on_remote_failure():
    args = SimpleNamespace(
        no_brain_feed=False,
        dry_run=False,
        title="Lifecycle canary",
        key="lifecycle-canary",
        now="Publishing",
        next="",
        blocker="",
    )
    completed = subprocess.CompletedProcess(args=[], returncode=255)
    with patch.object(card.subprocess, "run", return_value=completed) as run:
        assert card.publish_brain_feed(args, "running") is False
    assert run.call_count == 1
    assert run.call_args.args[0][0] == "ssh"


def test_brain_feed_publish_rejects_a_partial_task_identity():
    args = SimpleNamespace(
        no_brain_feed=False,
        dry_run=False,
        title="Lifecycle canary",
        key="lifecycle-canary",
        now="Publishing",
        next="",
        blocker="",
    )
    with patch.object(card.subprocess, "run") as run:
        assert card.publish_brain_feed(args, "running", work_id="work-only") is False
        assert card.publish_brain_feed(args, "running", run_id="run-only") is False
    run.assert_not_called()


def test_ack_message_is_adopted_instead_of_sending_duplicate():
    args = SimpleNamespace(
        key="single-card", title="Fix duplicate cards", model="model", route="route",
        now="Working", done="Received task", next="Verify", blocker="None", eta="",
        ack_message_id="100", separate_message=False, chat_id="-1003589561528", thread_id="17",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=False,
        no_final_summary=True, timeout=15, dry_run=False, no_brain_feed=False,
    )
    saved = {}
    with patch.object(card, "load_state", return_value={"cards": {}}), \
         patch.object(card, "save_state", side_effect=lambda state: saved.update(state)), \
         patch.object(card, "edit_rich_card", return_value={"ok": True}) as edit, \
         patch.object(card, "send_rich_message") as send, \
         patch.object(card, "edit_objective_message", return_value={"ok": True}) as edit_objective, \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(args, "running") == 0
    edit.assert_called_once()
    assert edit.call_args.args[0] == "100"
    send.assert_not_called()
    edit_objective.assert_not_called()
    assert saved["cards"]["single-card"]["message_id"] == "100"
    assert saved["cards"]["single-card"]["retention"] == "persistent-edit-only"


def test_pending_ack_is_claimed_only_for_matching_origin(tmp_path):
    state_path = tmp_path / "ack.json"
    state_path.write_text('{"latest_pending_ack":{"message_id":"100","telegram_chat_id":"-1003589561528","telegram_thread_id":"17"}}')
    with patch.object(card, "ACK_STATE_PATH", state_path):
        assert card.claim_pending_ack("matching-card", "-1003589561528", "17") == "100"
    saved = card.load_json_file(state_path, {})["latest_pending_ack"]
    assert saved["claimed_by"] == "matching-card"


def test_pending_ack_claim_preserves_concurrent_fast_ack_state(tmp_path):
    ack_path = tmp_path / "jaimes_fast_ack_state.json"
    ready_path = tmp_path / "claim-ready"
    card.save_json_file(ack_path, {
        "latest_pending_ack": {
            "message_id": "100",
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "1",
        },
        "active_cards": {"before": {"status": "active"}},
        "acked_prompt_events": ["before-event"],
    })
    lock_path = ack_path.with_suffix(ack_path.suffix + ".lock")
    worker_code = """
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("jaimes_card_claimant", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.ACK_STATE_PATH = Path(sys.argv[2])
Path(sys.argv[3]).write_text("ready", encoding="utf-8")
print(module.claim_pending_ack("current-card", "-1003589561528", "1"))
"""

    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        process = subprocess.Popen(
            [sys.executable, "-c", worker_code, str(MODULE_PATH), str(ack_path), str(ready_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5
        while not ready_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready_path.exists()
        card.save_json_file(ack_path, {
            "latest_pending_ack": {
                "message_id": "100",
                "telegram_chat_id": "-1003589561528",
                "telegram_thread_id": "1",
            },
            "last_claim": {"run_id": "new-run"},
            "active_cards": {
                "before": {"status": "active"},
                "new-run": {"status": "active"},
            },
            "acked_prompt_events": ["before-event", "new-event"],
            "processed_progress_events": ["progress-event"],
        })

    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, (stdout, stderr)
    assert stdout.strip() == "100"
    final = card.load_json_file(ack_path, {})
    assert final["latest_pending_ack"]["claimed_by"] == "current-card"
    assert final["last_claim"] == {"run_id": "new-run"}
    assert set(final["active_cards"]) == {"before", "new-run"}
    assert final["acked_prompt_events"] == ["before-event", "new-event"]
    assert final["processed_progress_events"] == ["progress-event"]


def test_pending_ack_from_another_topic_is_not_claimed(tmp_path):
    state_path = tmp_path / "ack.json"
    state_path.write_text('{"latest_pending_ack":{"message_id":"100","telegram_chat_id":"-1003589561528","telegram_thread_id":"19"}}')
    with patch.object(card, "ACK_STATE_PATH", state_path):
        assert card.claim_pending_ack("topic-17-card", "-1003589561528", "17") == ""
    saved = card.load_json_file(state_path, {})["latest_pending_ack"]
    assert "claimed_by" not in saved


def test_unscoped_pending_ack_is_not_claimed(tmp_path):
    state_path = tmp_path / "ack.json"
    state_path.write_text('{"latest_pending_ack":{"message_id":"100"}}')
    with patch.object(card, "ACK_STATE_PATH", state_path):
        assert card.claim_pending_ack("safe-card", "-1003589561528", "17") == ""


def test_inbox_separate_message_never_adopts_prior_pending_ack(tmp_path):
    state_path = tmp_path / "cards.json"
    ack_path = tmp_path / "jaimes_fast_ack_state.json"
    card.save_json_file(ack_path, {
        "latest_pending_ack": {
            "message_id": "900",
            "telegram_chat_id": "-1003589561528",
            "telegram_thread_id": "1",
            "key": "prior-task",
        },
        "active_cards": {"prior-run": {"key": "prior-task", "status": "active"}},
    })
    args = SimpleNamespace(
        key="current-task", title="Create current Topic 1 surfaces", model="model", route="route",
        now="Working", done="Received task", next="Verify", blocker="None", eta="",
        ack_message_id="", separate_message=True, chat_id="-1003589561528", thread_id="1",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=False,
        no_final_summary=True, timeout=15, dry_run=False, no_brain_feed=False,
    )
    responses = iter([
        {"ok": True, "result": {"message_id": 901}},
        {"ok": True, "result": {"message_id": 902}},
    ])

    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "ACK_STATE_PATH", ack_path), \
         patch.object(card, "claim_pending_ack", side_effect=AssertionError("prior ack must not be claimed")), \
         patch.object(card, "send_card", side_effect=lambda *args, **kwargs: next(responses)) as send, \
         patch.object(card, "edit_card", side_effect=AssertionError("prior ack must not be edited")), \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(args, "running") == 0

    saved = card.load_json_file(state_path, {})["cards"]["current-task"]
    assert send.call_count == 2
    assert saved["header_message_id"] == 901
    assert saved["message_id"] == 902
    assert card.load_json_file(ack_path, {})["latest_pending_ack"]["message_id"] == "900"


def test_objective_and_live_card_are_separate_messages():
    args = SimpleNamespace(
        key="three-bubble", title="Restore middle card", model="model", route="route",
        now="Working", done="Received task", next="Verify", blocker="None", eta="",
        ack_message_id="100", separate_message=True, chat_id="-1003589561528", thread_id="17",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=False,
        no_final_summary=True, timeout=15, dry_run=False, no_brain_feed=False,
    )
    saved = {}
    with patch.object(card, "load_state", return_value={"cards": {}}), \
         patch.object(card, "save_state", side_effect=lambda state: saved.update(state)), \
         patch.object(card, "edit_rich_card") as edit, \
         patch.object(card, "send_rich_message", return_value={"ok": True, "result": {"message_id": 101}}) as send, \
         patch.object(card, "edit_objective_message") as edit_objective, \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(args, "running") == 0
    send.assert_called_once()
    edit.assert_not_called()
    edit_objective.assert_called_once()
    assert saved["cards"]["three-bubble"]["ack_message_id"] == "100"
    assert saved["cards"]["three-bubble"]["message_id"] == 101


def test_inbox_header_precedes_live_card_and_is_retry_safe(tmp_path):
    state_path = tmp_path / "cards.json"
    args = SimpleNamespace(
        key="inbox-header", title="Please verify the Inbox routing and card lifecycle",
        model="planned provider=codex; model=gpt-5.6-luna; worker=jaimes-hermes; host=jaimes",
        route="route=luna; owner=josh2; reason=bounded Inbox coordination; fallback=none",
        now="Objective and route confirmed", done="Received task", next="Verify", blocker="None", eta="",
        ack_message_id="", separate_message=False, chat_id="-1003589561528", thread_id="1",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=False,
        no_final_summary=True, timeout=15, dry_run=False, no_brain_feed=False,
    )
    calls = []
    responses = iter([
        {"ok": True, "result": {"message_id": 101}},
        {"ok": False, "error": "HTTP error 400: rejected before delivery"},
        {"ok": True, "result": {"message_id": 202}},
    ])

    def fake_send(text, *args, **kwargs):
        calls.append("header" if text.startswith("<pre>TASK HEADER") else "live")
        return next(responses)

    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "send_card", side_effect=fake_send), \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(args, "running") == 1
        partial = card.load_state()["cards"]["inbox-header"]
        assert partial["header_message_id"] == 101
        assert partial["message_id"] is None

        assert card.upsert_card(args, "running") == 0
        final = card.load_state()["cards"]["inbox-header"]

    assert calls == ["header", "live", "live"]
    assert final["header_message_id"] == 101
    assert final["message_id"] == 202


def test_inbox_indeterminate_header_send_is_quarantined(tmp_path):
    state_path = tmp_path / "cards.json"
    args = SimpleNamespace(
        key="ambiguous-header", title="Fence an ambiguous Topic 1 header", model="model", route="route",
        now="Working", done="Received task", next="Verify", blocker="None", eta="",
        ack_message_id="", separate_message=True, chat_id="-1003589561528", thread_id="1",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=False,
        no_final_summary=True, timeout=15, dry_run=False, no_brain_feed=False,
    )
    sends = []

    def ambiguous_header(*args, **kwargs):
        sends.append("header")
        return {"ok": False, "error": "timed out after request write"}

    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "send_card", side_effect=ambiguous_header), \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(args, "running") == 1
        partial = card.load_state()["cards"]["ambiguous-header"]
        assert partial["header_delivery_status"] == "indeterminate"
        assert partial["header_message_id"] is None
        assert card.upsert_card(args, "running") == 1

    assert sends == ["header"]


def test_inbox_indeterminate_live_send_is_quarantined_after_header_checkpoint(tmp_path):
    state_path = tmp_path / "cards.json"
    args = SimpleNamespace(
        key="ambiguous-live", title="Fence an ambiguous Topic 1 live card", model="model", route="route",
        now="Working", done="Received task", next="Verify", blocker="None", eta="",
        ack_message_id="", separate_message=True, chat_id="-1003589561528", thread_id="1",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=False,
        no_final_summary=True, timeout=15, dry_run=False, no_brain_feed=False,
    )
    sends = []

    def send_surface(text, *args, **kwargs):
        if not sends:
            sends.append("header")
            return {"ok": True, "result": {"message_id": 101}}
        sends.append("live")
        return {"ok": False, "error": "connection reset after request write"}

    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "send_card", side_effect=send_surface), \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(args, "running") == 1
        partial = card.load_state()["cards"]["ambiguous-live"]
        assert partial["header_message_id"] == 101
        assert partial["message_id"] is None
        assert partial["live_delivery_status"] == "indeterminate"
        assert card.upsert_card(args, "running") == 1

    assert sends == ["header", "live"]


def test_topic17_indeterminate_live_send_is_durably_quarantined(tmp_path):
    state_path = tmp_path / "cards.json"
    args = SimpleNamespace(
        key="topic17-ambiguous-live", title="Restore JAIMES live updates",
        model="model", route="route", now="Checking Telegram delivery",
        done="Received task", next="Verify", blocker="None", eta="",
        ack_message_id="", separate_message=False,
        chat_id="-1003589561528", thread_id="17",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=False,
        no_final_summary=True, timeout=8, dry_run=False, no_brain_feed=True,
    )
    sends = []

    def ambiguous_live(*_args, **_kwargs):
        sends.append("live")
        return {"ok": False, "error": "timed out after request write"}

    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "claim_pending_ack", return_value=""), \
         patch.object(card, "send_rich_message", side_effect=ambiguous_live), \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(args, "running") == 1
        partial = card.load_state()["cards"]["topic17-ambiguous-live"]
        assert partial["live_delivery_status"] == "indeterminate"
        assert partial["message_id"] is None
        assert partial["chat_id"] == "-1003589561528"
        assert partial["thread_id"] == "17"
        assert card.upsert_card(args, "running") == 1

    assert sends == ["live"]


def test_topic17_heartbeat_edits_same_card_without_polluting_work_log(tmp_path):
    state_path = tmp_path / "cards.json"
    common = {
        "key": "topic17-heartbeat",
        "title": "Restore JAIMES live updates",
        "model": "model",
        "route": "route",
        "next": "Verify",
        "blocker": "None",
        "eta": "",
        "ack_message_id": "",
        "separate_message": False,
        "chat_id": "-1003589561528",
        "thread_id": "17",
        "buttons": None,
        "buttons_file": None,
        "routing_buttons": False,
        "approval_buttons": False,
        "no_buttons": True,
        "final_summary": False,
        "no_final_summary": True,
        "timeout": 8,
        "dry_run": False,
        "no_brain_feed": True,
    }
    start_args = SimpleNamespace(
        **common,
        now="Checking Telegram delivery",
        done="Received task",
    )
    heartbeat_args = SimpleNamespace(
        **common,
        now="Checking Telegram delivery",
        done="",
    )

    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "claim_pending_ack", return_value=""), \
         patch.object(card, "send_rich_message", return_value={
             "ok": True, "result": {"message_id": 777}, "native_rich_message": True
         }) as send, \
         patch.object(card, "edit_rich_card", return_value={"ok": True}) as edit, \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(start_args, "running") == 0
        before = card.load_state()["cards"]["topic17-heartbeat"]
        assert before["work_log"] == ["Received task"]
        assert card.upsert_card(heartbeat_args, "running") == 0

    saved = card.load_json_file(state_path, {})["cards"]["topic17-heartbeat"]
    assert send.call_count == 1
    assert edit.call_count == 1
    assert edit.call_args.args[0] == 777
    rendered = card.html.unescape(edit.call_args.args[1])
    assert "Telegram delivery" in rendered
    assert "Still working" not in rendered
    assert saved["message_id"] == 777
    assert saved["work_log"] == ["Received task"]
    assert saved["current_step"] == "Checking Telegram delivery"


def test_sequential_topic17_tasks_send_fresh_cards_and_preserve_prior_task(tmp_path):
    state_path = tmp_path / "cards.json"
    ack_path = tmp_path / "jaimes_fast_ack_state.json"
    common = {
        "model": "model",
        "route": "route",
        "now": "Checking Telegram delivery",
        "done": "Received task",
        "next": "Verify",
        "blocker": "None",
        "eta": "",
        "ack_message_id": "",
        "separate_message": True,
        "chat_id": "-1003589561528",
        "thread_id": "17",
        "buttons": None,
        "buttons_file": None,
        "routing_buttons": False,
        "approval_buttons": False,
        "no_buttons": True,
        "final_summary": False,
        "no_final_summary": True,
        "timeout": 8,
        "dry_run": False,
        "no_brain_feed": True,
    }
    task_a = SimpleNamespace(
        **common,
        key="topic17-task-a",
        title="Verify the first JAIMES task",
    )
    task_b = SimpleNamespace(
        **common,
        key="topic17-task-b",
        title="Verify the second JAIMES task",
    )
    sends = iter([
        {"ok": True, "result": {"message_id": 801}},
        {"ok": True, "result": {"message_id": 802}},
    ])

    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "ACK_STATE_PATH", ack_path), \
         patch.object(card, "claim_pending_ack", side_effect=AssertionError(
             "separate Topic 17 tasks must not claim the prior pending acknowledgement"
         )), \
         patch.object(card, "send_rich_message", side_effect=lambda *_args, **_kwargs: next(sends)) as send, \
         patch.object(card, "edit_rich_card", side_effect=AssertionError(
             "task B must not edit task A's card"
         )), \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(task_a, "running") == 0
        card.save_json_file(ack_path, {
            "latest_pending_ack": {
                "message_id": "801",
                "key": "topic17-task-a",
                "telegram_chat_id": "-1003589561528",
                "telegram_thread_id": "17",
            }
        })
        assert card.upsert_card(task_b, "running") == 0

    saved_cards = card.load_json_file(state_path, {})["cards"]
    assert set(saved_cards) == {"topic17-task-a", "topic17-task-b"}
    assert saved_cards["topic17-task-a"]["message_id"] == 801
    assert saved_cards["topic17-task-b"]["message_id"] == 802
    assert send.call_count == 2
    pending = card.load_json_file(ack_path, {})["latest_pending_ack"]
    assert pending["message_id"] == "801"
    assert pending["key"] == "topic17-task-a"
    assert "claimed_by" not in pending


def test_inbox_live_receipt_survives_indeterminate_final_send_and_retry(tmp_path):
    state_path = tmp_path / "cards.json"
    start_args = SimpleNamespace(
        key="ambiguous-final", title="Checkpoint live before the final", model="model", route="route",
        now="Working", done="Received task", next="Verify", blocker="None", eta="",
        ack_message_id="", separate_message=True, chat_id="-1003589561528", thread_id="1",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=False,
        no_final_summary=True, timeout=15, dry_run=False, no_brain_feed=False,
    )
    done_args = SimpleNamespace(**{
        **start_args.__dict__,
        "now": "Finished and verified",
        "done": "Completed task|Verified result|Prepared final",
        "final_summary": True,
        "no_final_summary": False,
    })
    sends = iter([
        {"ok": True, "result": {"message_id": 101}},
        {"ok": True, "result": {"message_id": 102}},
    ])
    final_sends = []

    def ambiguous_final(*args, **kwargs):
        final_sends.append("final")
        return {"ok": False, "error": "timed out after request write"}

    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "send_card", side_effect=lambda *args, **kwargs: next(sends)), \
         patch.object(card, "edit_card", return_value={"ok": True}), \
         patch.object(card, "send_final_summary", side_effect=ambiguous_final), \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(start_args, "running") == 0
        assert card.upsert_card(done_args, "done") == 1
        partial = card.load_state()["cards"]["ambiguous-final"]
        assert partial["header_message_id"] == 101
        assert partial["message_id"] == 102
        assert partial["final_message_id"] is None
        assert partial["final_delivery_status"] == "indeterminate"
        assert card.upsert_card(done_args, "done") == 1

    assert final_sends == ["final"]


def test_inbox_indeterminate_final_edit_never_falls_back_to_new_send(tmp_path):
    state_path = tmp_path / "cards.json"
    card.save_json_file(state_path, {"cards": {
        "edit-final": {
            "title": "Retry one known final",
            "header_message_id": 101,
            "message_id": 102,
            "final_message_id": 103,
            "status": "done",
            "done": ["Completed task", "Verified result", "Prepared final"],
            "work_log": ["Completed task", "Verified result", "Prepared final"],
            "route": "route",
            "model": "model",
            "chat_id": "-1003589561528",
            "thread_id": "1",
        }
    }})
    args = SimpleNamespace(
        key="edit-final", title="Retry one known final", model="model", route="route",
        now="Finished and verified", done="", next="No action needed", blocker="None", eta="",
        ack_message_id="", separate_message=True, chat_id="-1003589561528", thread_id="1",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=True,
        no_final_summary=False, timeout=15, dry_run=False, no_brain_feed=False,
    )

    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "edit_card", return_value={"ok": True}), \
         patch.object(card, "edit_final_summary", return_value={"ok": False, "error": "timed out after request write"}) as edit_final, \
         patch.object(card, "send_final_summary") as send_final, \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(args, "done") == 1

    edit_final.assert_called_once()
    send_final.assert_not_called()
    saved = card.load_json_file(state_path, {})["cards"]["edit-final"]
    assert saved["message_id"] == 102
    assert saved["final_message_id"] == 103


def test_inbox_task_header_is_bounded_and_names_agent_and_models():
    rendered = card.build_task_header(
        title="Please confirm whether JAIMES should investigate the Inbox failure",
        model="planned provider=codex; model=gpt-5.6-terra; worker=jaimes-hermes; host=jaimes",
        route="route=terra; owner=josh2; reason=multi-step repair; fallback=none",
    )
    assert rendered.startswith("<pre>") and rendered.endswith("</pre>")
    body = card.html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>"))
    assert max(map(len, body.splitlines())) <= card.CARD_WRAP_WIDTH
    assert "TASK HEADER" in body
    assert "│Objective│" in body
    assert "│Agent    │ JAIMES system" in body
    assert "codex/gpt-5.6-terra" in body.replace("\n", "")


def test_initial_live_progress_reflects_verified_routing_milestones():
    initial = card.progress_lines([
        "Received Telegram task",
        "Objective determined: Verify Inbox routing",
        "Model selected: codex/gpt-5.6-luna",
        "Skill selected: telegram-task-flow",
    ], "running", planned_steps=4)[0]
    noisy = card.progress_lines([
        "Received Telegram task",
        "Objective determined: Verify Inbox routing",
        "Model selected: codex/gpt-5.6-luna",
        "Skill selected: telegram-task-flow",
    ] * 5, "running", planned_steps=4)[0]
    assert "█████░░░░░ 50%" in initial
    assert noisy == initial


def test_live_card_delete_is_blocked_without_explicit_override():
    with patch.dict(card.os.environ, {"JAIMES_ALLOW_EXPLICIT_CARD_DELETE": "0"}), \
         patch.object(card.urllib.request, "urlopen") as urlopen:
        result = card.api_call("deleteMessage", {"chat_id": -1003589561528, "message_id": 100})
    assert result["ok"] is False
    assert "retention policy" in result["error"]
    urlopen.assert_not_called()


def test_live_card_hides_runtime_plumbing_and_keeps_semantic_milestones():
    text = card.build_card(
        title="Improve live-card transparency",
        status="running",
        model="openai-codex/gpt-5.6-sol",
        now="Tool: search_files — tracing live-card rendering",
        done=[
            "Skill applied: telegram-task-flow — workflow guidance",
            "Decision: preserve one card while increasing operator detail",
            "Tool result: read_file — inspected the renderer",
            "Action completed: patch — updated the card format",
            "Verification passed: terminal — regression checks",
        ],
        next_step="Reload and verify the watcher",
        blocker="None",
    )
    for expected in ("Now", "Done", "updated the card format", "regression checks", "Next"):
        assert expected in text
    for hidden in ("search_files", "read_file", "tool:", "action:", "skill:"):
        assert hidden not in text.lower()


def test_complete_card_consolidates_history_and_keeps_final_marker():
    steps = [
        "Skill applied: telegram-task-flow — loaded the workflow",
        "Decision: preserve cumulative major-step history",
        "Tool result: search_files — located the completion path",
        "Action completed: patch — fixed state continuity",
        "Verification passed: terminal — regression checks",
        "summary sent",
    ]
    text = card.build_card(
        title="Preserve cumulative completion history",
        status="done",
        model="openai-codex/gpt-5.6-sol",
        now="summary sent",
        done=steps,
        next_step="No action needed.",
        blocker="None",
    )
    normalized = " ".join(text.split())
    for expected in ("earlier checks", "regression checks", "Summary ready"):
        assert expected in normalized
    for hidden in ("skill:", "tool:", "action:", "verify:", "search_files"):
        assert hidden not in normalized.lower()


def test_long_history_is_consolidated_to_three_semantic_rows():
    steps = [
        "Skill applied: telegram-task-flow — workflow",
        "Decision: keep the cumulative ledger",
    ] + [f"Action completed: phase {i} — completed phase {i}" for i in range(1, 16)]
    lines = card.activity_lines(steps, fallback="none", limit=12)
    assert any("earlier checks" in line for line in lines)
    assert any("phase 15" in line for line in lines)
    assert len(lines) == 3
    assert all("action:" not in line.lower() and "skill:" not in line.lower() for line in lines)


def test_emoji_rows_use_code_block_hanging_indent():
    wrapped = card.hanging_wrap(
        "✅ tool: web_search — researching "
        "site:inspect.aisi.org.uk custom eval task datasets and scorers"
    )
    rows = wrapped.splitlines()
    assert rows[0] == "✅ tool: web_search — researching"
    assert rows[1] == "   site:inspect.aisi.org.uk custom"
    assert all(row.startswith("   ") for row in rows[1:])
    assert card.CARD_WRAP_WIDTH == 38
    assert card.CARD_CONTINUATION_INDENT == "   "
    assert max(map(len, rows)) <= card.CARD_WRAP_WIDTH


def test_preformatted_runtime_row_is_humanized_without_internal_label():
    row = "✅ action: terminal — running a bounded system operation"
    assert card.live_line(row) == "- a bounded system operation"
    rendered = card.build_card(
        title="Fix code-block alignment",
        status="running",
        model="openai-codex/gpt-5.6-sol",
        now=row,
        done=[],
        next_step="No action needed.",
        blocker="None",
    )
    assert "a bounded system operation" in rendered
    assert "action:" not in rendered.lower()


def test_dash_rows_use_two_space_hanging_indent():
    wrapped = card.hanging_wrap(
        "- Wait for the active shared source lease to finish before applying "
        "the verified renderer patch."
    )
    rows = wrapped.splitlines()
    assert len(rows) > 1
    assert all(row.startswith("  ") for row in rows[1:])
    assert max(map(len, rows)) <= card.CARD_WRAP_WIDTH


def test_live_card_objective_is_bounded_to_mobile_width():
    rendered = card.build_card(
        title="Investigate and repair every lingering Telegram Inbox workflow regression",
        status="running",
        model="gpt-5.6-sol",
        now="Verifying the live renderer",
        done=["Received task"],
        next_step="Finish verification",
        blocker="None",
    )
    body = card.html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>"))
    assert max(map(len, body.splitlines())) <= card.CARD_WRAP_WIDTH


def test_final_summary_is_html_preformatted_and_bounded():
    rendered = card.build_completion_summary(
        title="Render final response summaries in fixed-width Telegram code blocks",
        status="done",
        model="openai-codex/gpt-5.6-sol",
        done=["Updated every agent renderer and verified the deployed mirrors"],
        next_step="No action needed.",
        blocker="None",
    )
    assert rendered.startswith("<pre>") and rendered.endswith("</pre>")
    body = card.html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>"))
    assert max(map(len, body.splitlines())) <= card.CARD_WRAP_WIDTH
    assert "What was done:" in body
    assert "Approval needed:" in body


def test_reconcile_retires_only_stale_unowned_cards_with_private_backup(tmp_path):
    now = dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc)
    stale = (now - dt.timedelta(hours=13)).isoformat().replace("+00:00", "Z")
    fresh = (now - dt.timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    state_path = tmp_path / "jaimes_work_cards.json"
    ack_path = tmp_path / "jaimes_fast_ack_state.json"
    original = {
        "store_version": 7,
        "cards": {
            "stale-running": {
                "status": "running", "updated_at": stale, "message_id": 101,
                "custom": {"history": ["accepted", "working"]},
            },
            "stale-active": {"status": "active", "updated_at": stale, "message_id": 102},
            "owned-stale": {"status": "running", "updated_at": stale, "message_id": 103},
            "fresh-running": {"status": "running", "updated_at": fresh, "message_id": 104},
            "already-done": {"status": "done", "updated_at": stale, "message_id": 105},
            "unknown-age": {"status": "running", "updated_at": "not-a-time", "message_id": 106},
        },
    }
    state_path.write_text(json.dumps(original), encoding="utf-8")
    state_path.chmod(0o644)
    ack_path.write_text(json.dumps({
        "active_cards": {
            "live-run": {"key": "owned-stale", "status": "active"},
            "old-run": {"key": "already-done", "status": "done"},
        },
    }), encoding="utf-8")

    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "ACK_STATE_PATH", ack_path), \
         patch.object(card, "api_call") as api_call, \
         patch.object(card, "publish_brain_feed") as publish:
        result = card.reconcile_work_cards(now=now)

    api_call.assert_not_called()
    publish.assert_not_called()
    assert result["retired_keys"] == ["stale-active", "stale-running"]
    assert result["telegram_messages_changed"] is False
    assert result["brain_feed_published"] is False
    assert result["skipped"] == {
        "fast_ack_active": 1,
        "fresh": 1,
        "invalid_updated_at": 1,
        "terminal": 1,
        "invalid_record": 0,
    }

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["store_version"] == 7
    assert saved["cards"]["stale-running"] == {
        "status": "retired",
        "previous_status": "running",
        "updated_at": stale,
        "message_id": 101,
        "custom": {"history": ["accepted", "working"]},
        "retired_at": "2026-07-15T12:00:00Z",
        "retired_reason": "stale-for-43200s-without-active-fast-ack-owner",
    }
    assert saved["cards"]["stale-active"]["previous_status"] == "active"
    assert saved["cards"]["owned-stale"]["status"] == "running"
    assert saved["cards"]["fresh-running"]["status"] == "running"
    assert saved["cards"]["unknown-age"]["status"] == "running"
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600

    backup_path = Path(result["backup"])
    assert backup_path.name == "jaimes_work_cards.json.20260715T120000Z.bak"
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
    assert json.loads(backup_path.read_text(encoding="utf-8")) == original


def test_reconcile_dry_run_reports_candidates_without_writing_or_backing_up(tmp_path):
    now = dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc)
    state_path = tmp_path / "cards.json"
    ack_path = tmp_path / "ack.json"
    state_path.write_text(json.dumps({
        "cards": {
            "threshold-card": {
                "status": "running",
                "updated_at": "2026-07-15T00:00:00Z",
                "message_id": 501,
            },
        },
    }), encoding="utf-8")
    state_path.chmod(0o644)
    ack_path.write_text("{}", encoding="utf-8")
    before = state_path.read_bytes()

    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "ACK_STATE_PATH", ack_path):
        result = card.reconcile_work_cards(dry_run=True, now=now)

    assert result["retired"] == 1
    assert result["retired_keys"] == ["threshold-card"]
    assert result["backup"] == ""
    assert state_path.read_bytes() == before
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o644
    assert list(tmp_path.glob("*.bak*")) == []


@pytest.mark.parametrize(
    ("filename", "state_payload", "ack_payload"),
    [
        ("cards.json", "{invalid", "{}"),
        ("ack.json", '{"cards":{}}', "{invalid"),
        ("cards-shape.json", '{"cards":[]}', "{}"),
        ("ack-shape.json", '{"cards":{}}', '{"active_cards":[]}'),
    ],
)
def test_reconcile_fails_closed_on_malformed_state(
    tmp_path, filename, state_payload, ack_payload
):
    state_path = tmp_path / "cards.json"
    ack_path = tmp_path / "ack.json"
    state_path.write_text(state_payload, encoding="utf-8")
    ack_path.write_text(ack_payload, encoding="utf-8")
    before = state_path.read_bytes()

    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "ACK_STATE_PATH", ack_path), \
         pytest.raises(RuntimeError):
        card.reconcile_work_cards()

    assert state_path.read_bytes() == before
    assert list(tmp_path.glob("*.bak*")) == []


def test_save_json_file_forces_private_permissions(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"old":true}', encoding="utf-8")
    path.chmod(0o666)
    card.save_json_file(path, {"private": True})
    assert json.loads(path.read_text(encoding="utf-8")) == {"private": True}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_reconcile_cli_uses_default_age_and_does_not_require_key(capsys):
    receipt = {
        "ok": True,
        "dry_run": True,
        "retired": 0,
        "telegram_messages_changed": False,
        "brain_feed_published": False,
    }
    with patch.object(sys, "argv", ["jaimes_work_card.py", "reconcile", "--dry-run"]), \
         patch.object(card, "reconcile_work_cards", return_value=receipt) as reconcile:
        assert card.main() == 0
    reconcile.assert_called_once_with(
        max_age_seconds=card.DEFAULT_RECONCILE_MAX_AGE_SECONDS,
        dry_run=True,
    )
    assert json.loads(capsys.readouterr().out) == receipt


def test_adapter_final_message_receipt_is_persisted_with_exact_task_identity(tmp_path):
    state_path = tmp_path / "cards.json"
    state_path.write_text(json.dumps({"cards": {"linked-card": {
        "title": "Verify Topic 17 delivery",
        "message_id": "3900",
        "ack_message_id": "3900",
        "status": "running",
        "work_log": ["Final summary validated; sending now"],
        "current_step": "Final summary validated; sending now",
        "model": "openai-codex/gpt-5.6-sol",
        "route": "JAIMES verified execution",
        "chat_id": "-1003589561528",
        "thread_id": "17",
        "work_id": "work-20260718",
        "run_id": "run-20260718",
        "task_started_at": "2026-07-18T19:39:36Z",
    }}}), encoding="utf-8")
    args = SimpleNamespace(
        key="linked-card", title="Verify Topic 17 delivery",
        model="openai-codex/gpt-5.6-sol", route="JAIMES verified execution",
        now="Final summary delivered", done="Final summary delivered",
        next="See the final summary for findings and next steps.", blocker="None", eta="",
        ack_message_id="", separate_message=False,
        chat_id="-1003589561528", thread_id="17",
        work_id="work-20260718", run_id="run-20260718",
        task_started_at="2026-07-18T19:39:36Z",
        final_message_id="3914", final_delivery_verified_by="hermes-adapter-success",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=False,
        no_final_summary=True, timeout=15, dry_run=False, no_brain_feed=False,
    )
    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "edit_card", return_value={"ok": True}), \
         patch.object(card, "send_card") as send, \
         patch.object(card, "publish_brain_feed", return_value=True) as publish:
        assert card.upsert_card(args, "done") == 0
    send.assert_not_called()
    publish.assert_called_once_with(
        args,
        "done",
        work_id="work-20260718",
        run_id="run-20260718",
    )

    saved = json.loads(state_path.read_text(encoding="utf-8"))["cards"]["linked-card"]
    assert saved["final_message_id"] == "3914"
    assert saved["final_delivery_verified_by"] == "hermes-adapter-success"
    assert saved["final_delivery_confirmed_at"].startswith("2026-")
    assert saved["work_id"] == "work-20260718"
    assert saved["run_id"] == "run-20260718"
    assert saved["task_started_at"] == "2026-07-18T19:39:36Z"


def test_existing_final_message_link_rejects_a_different_receipt(tmp_path):
    state_path = tmp_path / "cards.json"
    state_path.write_text(json.dumps({"cards": {"linked-card": {
        "title": "Verify Topic 17 delivery",
        "message_id": "3900",
        "final_message_id": "3914",
        "status": "done",
    }}}), encoding="utf-8")
    args = SimpleNamespace(
        key="linked-card", title="Verify Topic 17 delivery", model="model", route="route",
        now="Final summary delivered", done="Final summary delivered", next="", blocker="None", eta="",
        ack_message_id="", separate_message=False, chat_id="-1003589561528", thread_id="17",
        work_id="work-20260718", run_id="run-20260718",
        task_started_at="2026-07-18T19:39:36Z",
        final_message_id="3915", final_delivery_verified_by="hermes-adapter-success",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=False,
        no_final_summary=True, timeout=15, dry_run=False, no_brain_feed=False,
    )
    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "edit_card") as edit, \
         patch.object(card, "send_card") as send, \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(args, "done") == 1
    edit.assert_not_called()
    send.assert_not_called()
    saved = json.loads(state_path.read_text(encoding="utf-8"))["cards"]["linked-card"]
    assert saved["final_message_id"] == "3914"


def test_existing_task_identity_cannot_be_rebound(tmp_path):
    state_path = tmp_path / "cards.json"
    state_path.write_text(json.dumps({"cards": {"linked-card": {
        "title": "Verify Topic 17 delivery",
        "message_id": "3900",
        "status": "running",
        "work_id": "work-original",
        "run_id": "run-original",
        "task_started_at": "2026-07-18T19:39:36Z",
    }}}), encoding="utf-8")
    args = SimpleNamespace(
        key="linked-card", title="Verify Topic 17 delivery", model="model", route="route",
        now="Working", done="", next="", blocker="None", eta="",
        ack_message_id="", separate_message=False, chat_id="-1003589561528", thread_id="17",
        work_id="work-other", run_id="run-original",
        task_started_at="2026-07-18T19:39:36Z",
        final_message_id="", final_delivery_verified_by="",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=False,
        no_final_summary=True, timeout=15, dry_run=False, no_brain_feed=False,
    )
    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "edit_card") as edit, \
         patch.object(card, "send_card") as send, \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(args, "running") == 1
    edit.assert_not_called()
    send.assert_not_called()
    saved = json.loads(state_path.read_text(encoding="utf-8"))["cards"]["linked-card"]
    assert saved["work_id"] == "work-original"
