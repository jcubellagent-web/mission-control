import argparse
import copy
import datetime as dt
import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "josh_telegram_fast_ack.py"
SPEC = importlib.util.spec_from_file_location("josh_telegram_fast_ack_terminal", MODULE_PATH)
watcher = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = watcher
SPEC.loader.exec_module(watcher)


def utc_now(offset_seconds: int = 0) -> str:
    value = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=offset_seconds)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def pending_card(started_at: str) -> dict:
    return {
        "key": "fast-ack-run-pending",
        "objective": "",
        "model": "",
        "route": "",
        "session_id": "session-1",
        "telegram_chat_id": watcher.CONTROL_CENTER_CHAT_ID,
        "telegram_thread_id": "1",
        "telegram_session_key": "agent:main:telegram:group:-1003589561528:topic:1",
        "requires_objective_interpretation": True,
        "started_at": started_at,
        "last_card_update_at": started_at,
        "status": "pending-interpretation",
    }


def visible_card(started_at: str, status: str = "running") -> dict:
    return {
        "chat_id": watcher.CONTROL_CENTER_CHAT_ID,
        "thread_id": "1",
        "title": "Close every shared source lease safely",
        "model": "openai/gpt-5.6-terra",
        "route": "Josh 2.0 native execution",
        "header_message_id": 3812,
        "message_id": 3813,
        "final_message_id": None,
        "started_at": started_at,
        "updated_at": started_at,
        "status": status,
    }


def terminal_args() -> argparse.Namespace:
    return argparse.Namespace(
        run_id="run-1",
        session_id="session-1",
        session_key="agent:main:telegram:group:-1003589561528:topic:1",
        chat_id=watcher.CONTROL_CENTER_CHAT_ID,
        thread_id="1",
        terminal_status="done",
        final_from_stdin=False,
    )


CANONICAL_FINAL = """<pre>Model: openai/gpt-5.6-terra
   | Route: Josh 2.0 Inbox
   | Why: verified execution

Complete: Yes - objective complete.

What was done:
- Closed the existing live card.
- Verified final delivery ordering.
- Preserved one final response.

Issues:
n/a

Appropriate next steps:
No action needed.

Approval needed:
n/a</pre>"""


def test_pending_run_waits_for_interpretation_without_creating_generic_card(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {"cards": {}})
    started = utc_now()
    state = {"active_cards": {"run-1": pending_card(started)}}
    event = {
        "event_id": "tool-1",
        "run_id": "run-1",
        "type": "tool.completed",
        "ts": started,
        "summary": "Inspected the shared edit guard",
    }
    with patch.object(watcher, "live_cards_enabled", return_value=True), \
         patch.object(watcher, "recent_progress_events", return_value=[event]), \
         patch.object(watcher, "run_cmd") as run_cmd:
        updates = watcher.update_active_cards(state, "session-1", meta={
            "telegram_chat_id": watcher.CONTROL_CENTER_CHAT_ID,
            "telegram_thread_id": "1",
        })
    assert updates == []
    assert "tool-1" not in state.get("processed_progress_events", [])
    run_cmd.assert_not_called()


def test_pending_run_adopts_interpreted_card_and_replays_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now()
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {
        "cards": {"shared-edit-closure": visible_card(started)},
    })
    state = {"active_cards": {"run-1": pending_card(started)}}
    event = {
        "event_id": "tool-2",
        "run_id": "run-1",
        "type": "tool.completed",
        "ts": started,
        "summary": "Verified the lifecycle tests",
    }
    with patch.object(watcher, "live_cards_enabled", return_value=True), \
         patch.object(watcher, "recent_progress_events", return_value=[event]), \
         patch.object(watcher, "run_cmd", return_value={"ok": True, "stdout": "{}"}) as run_cmd, \
         patch.object(watcher, "publish_josh"), \
         patch.object(watcher, "send_chat_action"):
        updates = watcher.update_active_cards(state, "session-1", meta={
            "telegram_chat_id": watcher.CONTROL_CENTER_CHAT_ID,
            "telegram_thread_id": "1",
        })
    card = state["active_cards"]["run-1"]
    assert card["key"] == "shared-edit-closure"
    assert card["objective"] == "Close every shared source lease safely"
    assert card["requires_objective_interpretation"] is False
    command = run_cmd.call_args.args[0]
    assert command[command.index("--key") + 1] == "shared-edit-closure"
    assert "fast-ack-run-pending" not in command
    assert updates[0]["event"] == "tool-2"


def test_model_completion_defers_terminal_edit_to_pre_final_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now()
    active = pending_card(started)
    active.update({
        "key": "interpreted-card",
        "objective": "Finish one Telegram lifecycle",
        "requires_objective_interpretation": False,
        "status": "active",
    })
    state = {"active_cards": {"run-1": active}}
    with patch.object(watcher, "live_cards_enabled", return_value=True), \
         patch.object(watcher, "recent_progress_events", return_value=[{
             "event_id": "model-1",
             "run_id": "run-1",
             "type": "model.completed",
             "ts": started,
             "summary": "Model completed",
             "final_text": "untrusted final",
         }]), \
         patch.object(watcher, "run_cmd") as run_cmd, \
         patch.object(watcher, "publish_josh") as publish:
        updates = watcher.update_active_cards(state, "session-1", meta={
            "telegram_chat_id": watcher.CONTROL_CENTER_CHAT_ID,
            "telegram_thread_id": "1",
        })
    assert updates == [{"event": "model-1", "result": {"ok": True, "deferred_to_pre_final_gate": True}}]
    assert state["active_cards"]["run-1"]["status"] == "awaiting-final-gate"
    run_cmd.assert_not_called()
    publish.assert_not_called()


def test_orphan_reconciler_adopts_manual_card_instead_of_closing_it(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now(-30)
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {
        "cards": {"shared-edit-closure": visible_card(started)},
    })
    state = {"active_cards": {"run-1": pending_card(started)}}
    with patch.object(watcher, "run_cmd") as run_cmd:
        reconciled = watcher.reconcile_orphan_work_cards(state, dry_run=False, meta={
            "telegram_chat_id": watcher.CONTROL_CENTER_CHAT_ID,
            "telegram_thread_id": "1",
        })
    assert reconciled == []
    assert state["active_cards"]["run-1"]["key"] == "shared-edit-closure"
    run_cmd.assert_not_called()


def test_manual_card_is_never_false_closed_from_missing_watcher_state(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now(-(watcher.MAX_ACTIVE_CARD_SECONDS + 60))
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {
        "cards": {"manual-interpreted-card": visible_card(started)},
    })
    with patch.object(watcher, "run_cmd") as run_cmd:
        reconciled = watcher.reconcile_orphan_work_cards({"active_cards": {}}, dry_run=False, meta={
            "telegram_chat_id": watcher.CONTROL_CENTER_CHAT_ID,
            "telegram_thread_id": "1",
        })
    assert reconciled == []
    run_cmd.assert_not_called()


def test_poll_preserves_pending_interpretation_flag(monkeypatch):
    started = utc_now()
    captured = {}
    monkeypatch.setattr(watcher, "load_fast_ack_state_snapshot", lambda: ({}, {}))
    monkeypatch.setattr(watcher, "session_metadata", lambda: {
        "sessionId": "session-1",
        "model": "openai/gpt-5.6-terra",
        "telegram_chat_id": watcher.CONTROL_CENTER_CHAT_ID,
        "telegram_thread_id": "1",
        "telegram_session_key": "agent:main:telegram:group:-1003589561528:topic:1",
    })
    monkeypatch.setattr(watcher, "recent_prompt_events", lambda *args, **kwargs: [{
        "session_id": "session-1",
        "ts": started,
        "run_id": "run-1",
        "prompt": "raw prompt",
    }])
    monkeypatch.setattr(watcher, "send_ack", lambda *args, **kwargs: {
        "ok": True,
        "status": "awaiting-objective-interpretation",
        "key": "fast-ack-run-pending",
        "objective": "",
        "run_id": "run-1",
        "requires_objective_interpretation": True,
        "last_card_update_at": started,
    })

    def capture_state(state, *args, **kwargs):
        captured.update(copy.deepcopy(state))
        return []

    monkeypatch.setattr(watcher, "update_active_cards", capture_state)
    monkeypatch.setattr(watcher, "reconcile_orphan_work_cards", lambda *args, **kwargs: [])
    watcher.poll_once(dry_run=True)
    card = captured["active_cards"]["run-1"]
    assert card["requires_objective_interpretation"] is True
    assert card["status"] == "pending-interpretation"
    assert card["telegram_thread_id"] == "1"


def test_stale_poll_merge_cannot_reopen_terminal_card(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "fast-ack.json")
    base = {"active_cards": {"run-1": {"key": "card-1", "status": "active", "last_card_update_at": "old"}}}
    candidate = copy.deepcopy(base)
    candidate["active_cards"]["run-1"]["last_card_update_at"] = "poll-update"
    latest = copy.deepcopy(base)
    latest["active_cards"]["run-1"].update({
        "status": "done",
        "ended_at": "2026-07-17T04:00:00Z",
        "last_card_update_at": "2026-07-17T04:00:00Z",
    })
    watcher.save_json(watcher.STATE_PATH, latest)
    merged = watcher.merge_poll_state(candidate, base)
    assert merged["active_cards"]["run-1"]["status"] == "done"
    assert merged["active_cards"]["run-1"]["ended_at"] == "2026-07-17T04:00:00Z"


def test_close_before_final_edits_same_card_then_marks_fast_ack_terminal(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "fast-ack.json")
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now(-20)
    active = pending_card(started)
    active.update({
        "key": "shared-edit-closure",
        "objective": "Close every shared source lease safely",
        "model": "openai/gpt-5.6-terra",
        "route": "Josh 2.0 native execution",
        "requires_objective_interpretation": False,
        "status": "active",
    })
    watcher.save_json(watcher.STATE_PATH, {"active_cards": {"run-1": active}})
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {
        "cards": {"shared-edit-closure": visible_card(started)},
    })
    calls = []

    def close_card(cmd, *args, **kwargs):
        calls.append(list(cmd))
        work_state = watcher.load_json(watcher.WORK_CARD_STATE_PATH, {})
        work_state["cards"]["shared-edit-closure"]["status"] = "done"
        work_state["cards"]["shared-edit-closure"]["updated_at"] = utc_now()
        watcher.save_json(watcher.WORK_CARD_STATE_PATH, work_state)
        return {"ok": True, "returncode": 0, "stdout": "{}", "stderr": ""}

    monkeypatch.setattr(watcher, "run_cmd", close_card)
    result = watcher.close_before_final(terminal_args())
    assert result["ok"] is True
    assert result["status"] == "closed"
    assert result["card_key"] == "shared-edit-closure"
    assert len(calls) == 1
    command = calls[0]
    assert command[2] == "done"
    assert command[command.index("--key") + 1] == "shared-edit-closure"
    assert "--no-final-summary" in command
    persisted = watcher.load_json(watcher.STATE_PATH, {})["active_cards"]["run-1"]
    assert persisted["status"] == "done"
    assert persisted["terminal_closed_before_final_at"]


def test_close_before_final_uses_paused_status_for_approval_required_result(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "fast-ack.json")
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now(-20)
    active = pending_card(started)
    active.update({
        "key": "approval-card",
        "objective": "Release verified source safely",
        "requires_objective_interpretation": False,
        "status": "active",
    })
    work_card = visible_card(started)
    work_card["title"] = "Release verified source safely"
    watcher.save_json(watcher.STATE_PATH, {"active_cards": {"run-1": active}})
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {"cards": {"approval-card": work_card}})
    calls = []

    def pause_card(cmd, *args, **kwargs):
        calls.append(list(cmd))
        work_state = watcher.load_json(watcher.WORK_CARD_STATE_PATH, {})
        work_state["cards"]["approval-card"]["status"] = "paused"
        watcher.save_json(watcher.WORK_CARD_STATE_PATH, work_state)
        return {"ok": True, "returncode": 0, "stdout": "{}", "stderr": ""}

    monkeypatch.setattr(watcher, "run_cmd", pause_card)
    args = terminal_args()
    args.terminal_status = "paused"
    result = watcher.close_before_final(args)
    assert result["terminal_status"] == "paused"
    assert calls[0][2] == "pause"
    assert watcher.load_json(watcher.STATE_PATH, {})["active_cards"]["run-1"]["status"] == "paused"


def test_close_before_final_delivers_private_canonical_final_then_suppresses_native(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "telegram" / "fast-ack.json")
    monkeypatch.setattr(watcher, "TERMINAL_OUTBOX_DIR", tmp_path / "telegram" / "terminal-final-outbox")
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now(-20)
    active = pending_card(started)
    active.update({
        "key": "transactional-final",
        "objective": "Deliver the final only after card closure",
        "requires_objective_interpretation": False,
        "status": "active",
    })
    work_card = visible_card(started)
    work_card["title"] = active["objective"]
    watcher.save_json(watcher.STATE_PATH, {"active_cards": {"run-1": active}})
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {"cards": {"transactional-final": work_card}})
    observed = {}

    def deliver_final(cmd, *args, **kwargs):
        observed["command"] = list(cmd)
        final_path = Path(cmd[cmd.index("--final-text-file") + 1])
        observed["final_path"] = final_path
        observed["final_text"] = final_path.read_text(encoding="utf-8")
        work_state = watcher.load_json(watcher.WORK_CARD_STATE_PATH, {})
        work_state["cards"]["transactional-final"].update({
            "status": "done",
            "final_message_id": 5001,
            "updated_at": utc_now(),
        })
        watcher.save_json(watcher.WORK_CARD_STATE_PATH, work_state)
        return {"ok": True, "returncode": 0, "stdout": "{}", "stderr": ""}

    monkeypatch.setattr(watcher, "run_cmd", deliver_final)
    monkeypatch.setattr(watcher.sys, "stdin", io.StringIO(CANONICAL_FINAL))
    args = terminal_args()
    args.final_from_stdin = True
    result = watcher.close_before_final(args)

    assert result["status"] == "closed-and-final-delivered"
    assert result["suppress_native_final"] is True
    assert result["final_message_id"] == "5001"
    assert observed["final_text"] == CANONICAL_FINAL
    assert "--no-final-summary" not in observed["command"]
    assert not observed["final_path"].exists()
    assert list(watcher.TERMINAL_OUTBOX_DIR.glob("*.json")) == []


def test_failed_terminal_send_is_durably_queued_and_retried(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "telegram" / "fast-ack.json")
    monkeypatch.setattr(watcher, "TERMINAL_OUTBOX_DIR", tmp_path / "telegram" / "terminal-final-outbox")
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now(-20)
    active = pending_card(started)
    active.update({
        "key": "queued-final",
        "objective": "Retry a failed final without duplicates",
        "requires_objective_interpretation": False,
        "status": "active",
    })
    work_card = visible_card(started)
    work_card["title"] = active["objective"]
    watcher.save_json(watcher.STATE_PATH, {"active_cards": {"run-1": active}})
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {"cards": {"queued-final": work_card}})
    monkeypatch.setattr(watcher.sys, "stdin", io.StringIO(CANONICAL_FINAL))
    monkeypatch.setattr(watcher, "run_cmd", lambda *args, **kwargs: {
        "ok": False,
        "returncode": 1,
        "stdout": "",
        "stderr": "temporary Telegram failure",
    })
    args = terminal_args()
    args.final_from_stdin = True
    queued = watcher.close_before_final(args)

    assert queued["ok"] is True
    assert queued["status"] == "final-queued-for-retry"
    assert queued["suppress_native_final"] is True
    outboxes = list(watcher.TERMINAL_OUTBOX_DIR.glob("*.json"))
    assert len(outboxes) == 1
    assert outboxes[0].stat().st_mode & 0o777 == 0o600
    assert watcher.load_json(outboxes[0], {})["final_summary"] == CANONICAL_FINAL

    def recover_final(cmd, *args, **kwargs):
        work_state = watcher.load_json(watcher.WORK_CARD_STATE_PATH, {})
        work_state["cards"]["queued-final"].update({
            "status": "done",
            "final_message_id": 5002,
            "updated_at": utc_now(),
        })
        watcher.save_json(watcher.WORK_CARD_STATE_PATH, work_state)
        return {"ok": True, "returncode": 0, "stdout": "{}", "stderr": ""}

    monkeypatch.setattr(watcher, "run_cmd", recover_final)
    state = watcher.load_json(watcher.STATE_PATH, {})
    recovered = watcher.recover_terminal_final_outbox(state)
    assert recovered[0]["result"]["status"] == "delivered"
    assert state["active_cards"]["run-1"]["status"] == "done"
    assert list(watcher.TERMINAL_OUTBOX_DIR.glob("*.json")) == []


def test_stale_awaiting_final_gate_queues_transparent_recovery(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "TERMINAL_OUTBOX_DIR", tmp_path / "terminal-final-outbox")
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now(-(watcher.STALE_FINAL_GATE_SECONDS + 1))
    card = pending_card(started)
    card.update({
        "key": "stale-final-gate",
        "objective": "Recover a stale final gate",
        "model": "openai/gpt-5.6-terra",
        "route": "Josh 2.0 Inbox",
        "requires_objective_interpretation": False,
        "status": "awaiting-final-gate",
        "last_card_update_at": started,
    })
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {
        "cards": {"stale-final-gate": visible_card(started)},
    })
    state = {"active_cards": {"run-stale": card}}
    queued = watcher.queue_stale_final_gate_recovery(state)
    assert queued[0]["result"]["status"] == "recovery-queued"
    outbox = watcher.load_json(next(watcher.TERMINAL_OUTBOX_DIR.glob("*.json")), {})
    assert outbox["terminal_status"] == "paused"
    assert outbox["final_summary"].startswith("<pre>")
    plain = outbox["final_summary"][5:-6]
    assert all(len(line) <= 38 for line in plain.splitlines())


def test_stale_terminal_close_claim_is_recovered_without_operator_action(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "fast-ack.json")
    card = {
        "key": "card-1",
        "status": "closing-before-final",
        "terminal_close_started_at": utc_now(-(watcher.TERMINAL_CLOSE_LEASE_SECONDS + 1)),
    }
    watcher.save_json(watcher.STATE_PATH, {"active_cards": {"run-1": card}})
    assert watcher.claim_terminal_card_close("run-1", "card-1") == "claimed"
    recovered = watcher.load_json(watcher.STATE_PATH, {})["active_cards"]["run-1"]
    assert recovered["status"] == "closing-before-final"
    assert recovered["terminal_close_recovered_at"]


def test_close_before_final_allows_exact_native_fallback_without_durable_surface(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "fast-ack.json")
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now()
    watcher.save_json(watcher.STATE_PATH, {"active_cards": {"run-1": pending_card(started)}})
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {"cards": {}})
    with patch.object(watcher, "run_cmd") as run_cmd:
        result = watcher.close_before_final(terminal_args())
    assert result["ok"] is True
    assert result["status"] == "no-card-required"
    run_cmd.assert_not_called()
    persisted = watcher.load_json(watcher.STATE_PATH, {})["active_cards"]["run-1"]
    assert persisted["status"] == "done"
    assert persisted["no_card_required"] is True
    assert persisted["native_fallback_finalized_at"]


def test_close_before_final_keeps_uninterpreted_owned_or_visible_runs_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "fast-ack.json")
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {"cards": {}})
    durable_variants = (
        {"coordinator_owned": True, "job_id": "job-1"},
        {"card_start_ok": True},
        {"header_message_id": "4101"},
        {"live_message_id": "4102"},
    )
    for index, durable in enumerate(durable_variants):
        card = pending_card(utc_now())
        card.update(durable)
        watcher.save_json(watcher.STATE_PATH, {"active_cards": {"run-1": card}})
        with patch.object(watcher, "run_cmd") as run_cmd:
            result = watcher.close_before_final(terminal_args())
        assert result["ok"] is False, index
        assert result["status"] == "awaiting-objective-card", index
        assert not watcher.load_json(watcher.STATE_PATH, {})["active_cards"]["run-1"].get("no_card_required")
        run_cmd.assert_not_called()


def test_close_before_final_is_idempotent_for_terminal_card(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "fast-ack.json")
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now(-20)
    active = pending_card(started)
    active.update({
        "key": "shared-edit-closure",
        "objective": "Close every shared source lease safely",
        "requires_objective_interpretation": False,
        "status": "active",
    })
    watcher.save_json(watcher.STATE_PATH, {"active_cards": {"run-1": active}})
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {
        "cards": {"shared-edit-closure": visible_card(started, status="done")},
    })
    with patch.object(watcher, "run_cmd") as run_cmd:
        result = watcher.close_before_final(terminal_args())
    assert result["ok"] is True
    assert result["status"] == "already-terminal"
    run_cmd.assert_not_called()


def test_close_before_final_never_authorizes_against_stale_terminal_card(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "fast-ack.json")
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now(-60)
    old = pending_card(started)
    old.update({
        "key": "old-card",
        "objective": "Old objective",
        "requires_objective_interpretation": False,
        "status": "done",
    })
    watcher.save_json(watcher.STATE_PATH, {"active_cards": {"old-run": old}})
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {"cards": {"old-card": visible_card(started, status="done")}})
    result = watcher.close_before_final(terminal_args())
    assert result["ok"] is False
    assert result["status"] == "run-card-not-ready"


def test_existing_separate_final_suppresses_native_duplicate(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "fast-ack.json")
    monkeypatch.setattr(watcher, "WORK_CARD_STATE_PATH", tmp_path / "work-cards.json")
    started = utc_now(-20)
    active = pending_card(started)
    active.update({
        "key": "already-final",
        "objective": "Existing final",
        "requires_objective_interpretation": False,
        "status": "active",
    })
    work_card = visible_card(started, status="done")
    work_card["final_message_id"] = 4999
    watcher.save_json(watcher.STATE_PATH, {"active_cards": {"run-1": active}})
    watcher.save_json(watcher.WORK_CARD_STATE_PATH, {"cards": {"already-final": work_card}})
    result = watcher.close_before_final(terminal_args())
    assert result["status"] == "final-already-delivered"
    assert result["suppress_native_final"] is True
    assert result["final_message_id"] == "4999"
