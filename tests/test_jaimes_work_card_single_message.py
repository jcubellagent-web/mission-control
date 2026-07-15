#!/usr/bin/env python3
import datetime as dt
import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "jaimes_work_card.py"
    spec = importlib.util.spec_from_file_location("jaimes_work_card_single_message", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


card = load_module()


def test_default_state_path_is_absolute_and_workspace_scoped():
    assert card.STATE_PATH.is_absolute()
    assert str(card.STATE_PATH).endswith("/.openclaw/workspace/memory/jaimes_work_cards.json")


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
         patch.object(card, "edit_card", return_value={"ok": True}) as edit, \
         patch.object(card, "send_card") as send, \
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
         patch.object(card, "edit_card") as edit, \
         patch.object(card, "send_card", return_value={"ok": True, "result": {"message_id": 101}}) as send, \
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
        {"ok": False, "error": "temporary live-card failure"},
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


def test_live_card_shows_skills_tools_actions_and_decisions():
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
    for expected in (
        "🔄 Now", "🧰 tool: search_files", "✅ Completed",
        "🧭 skill:", "🧠 decision:", "✅ tool:",
        "✅ action:", "✅ verify:", "⏭️ Next",
    ):
        assert expected in text


def test_complete_card_keeps_cumulative_major_steps_and_final_marker():
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
    for expected in (
        "🧭 skill: telegram-task-flow",
        "🧠 decision: preserve cumulative major-step history",
        "✅ tool: search_files",
        "✅ action: patch",
        "✅ verify: terminal",
        "🏁 final: summary sent",
    ):
        assert expected in normalized


def test_long_history_keeps_early_skill_and_decision_plus_recent_steps():
    steps = [
        "Skill applied: telegram-task-flow — workflow",
        "Decision: keep the cumulative ledger",
    ] + [f"Action completed: phase {i} — result" for i in range(1, 16)]
    lines = card.activity_lines(steps, fallback="none", limit=12)
    assert any("🧭 skill:" in line for line in lines)
    assert any("🧠 decision:" in line for line in lines)
    assert any("phase 15" in line for line in lines)
    assert len(lines) == 12


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


def test_preformatted_code_block_row_is_not_prefixed_with_bullet():
    row = "✅ action: terminal — running a bounded system operation"
    assert card.live_line(row) == row
    rendered = card.build_card(
        title="Fix code-block alignment",
        status="running",
        model="openai-codex/gpt-5.6-sol",
        now=row,
        done=[],
        next_step="No action needed.",
        blocker="None",
    )
    assert "\n✅ action: terminal — running a bounded\n   system operation\n" in rendered
    assert "• ✅ action" not in rendered


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
