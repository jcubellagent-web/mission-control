#!/usr/bin/env python3
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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
         patch.object(card, "edit_objective_message", return_value={"ok": True}), \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(args, "running") == 0
    edit.assert_called_once()
    assert edit.call_args.args[0] == "100"
    send.assert_not_called()
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
    edit_objective.assert_not_called()
    assert saved["cards"]["three-bubble"]["ack_message_id"] == "100"
    assert saved["cards"]["three-bubble"]["message_id"] == 101


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
