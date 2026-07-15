import argparse
import datetime as dt
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "josh_telegram_fast_ack",
    Path(__file__).resolve().parents[1] / "scripts" / "josh_telegram_fast_ack.py",
)
watcher = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = watcher
SPEC.loader.exec_module(watcher)


def test_send_ack_uses_prompt_reaction_without_message_id_and_does_not_fail_claim():
    event = {"session_id": "session", "ts": "2026-07-15T03:42:21Z", "run_id": "before-dispatch:1", "message_id": "", "prompt": "private request"}
    with patch.object(watcher, "fast_ack_enabled", return_value=True), patch.object(watcher, "send_chat_action"), patch.object(watcher, "send_message_draft"), patch.object(watcher, "send_prompt_reaction", return_value=False) as prompt_reaction, patch.object(watcher, "publish_josh"):
        result = watcher.send_ack(event, model=watcher.DEFAULT_MODEL, dry_run=False, meta={"telegram_chat_id": "-100", "telegram_thread_id": "1"})
    prompt_reaction.assert_called_once_with("private request", meta={"telegram_chat_id": "-100", "telegram_thread_id": "1"})
    assert result["ok"] is True
    assert result["reaction_ok"] is False


def test_send_ack_starts_card_with_workspace_helper_and_returns_receipt():
    event = {"session_id": "session", "ts": "2026-07-15T04:23:21Z", "run_id": "before-dispatch:1", "message_id": "", "prompt": "Fix a multi-step Inbox task"}
    card_receipt = '{"ok": true, "action": "start", "message_id": 444}'
    with patch.object(watcher, "fast_ack_enabled", return_value=True), patch.object(watcher, "live_cards_enabled", return_value=True), patch.object(watcher, "send_chat_action"), patch.object(watcher, "send_message_draft"), patch.object(watcher, "send_prompt_reaction", return_value=True), patch.object(watcher, "publish_josh"), patch.object(watcher, "run_cmd", return_value={"ok": True, "stdout": card_receipt}) as run_cmd:
        result = watcher.send_ack(event, model=watcher.DEFAULT_MODEL, dry_run=False, meta={"telegram_chat_id": "-100", "telegram_thread_id": "1"})
    command = next(call.args[0] for call in run_cmd.call_args_list if str(watcher.WORK_CARD_SCRIPT) in call.args[0])
    assert command[1] == str(watcher.WORK_CARD_SCRIPT)
    assert command[command.index("--thread-id") + 1] == "1"
    assert watcher.SEND_REPLY_SCRIPT == watcher.WORK_CARD_SCRIPT.with_name("send_josh_reply.py")
    assert result["card_start_ok"] is True
    assert result["card_start_receipt"] == card_receipt


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
    run_cmd.assert_not_called()


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
