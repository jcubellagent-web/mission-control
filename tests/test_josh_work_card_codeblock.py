#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import re
import tempfile

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "josh_work_card.py"
spec = importlib.util.spec_from_file_location("josh_work_card", MODULE_PATH)
card = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(card)


def test_card_state_writes_are_private(tmp_path, monkeypatch):
    state_path = tmp_path / "josh_work_cards.json"
    state_path.write_text("{}\n", encoding="utf-8")
    state_path.chmod(0o644)
    monkeypatch.setattr(card, "STATE_PATH", state_path)

    card.save_state({"cards": {"one": {"status": "running"}}})

    assert state_path.stat().st_mode & 0o777 == 0o600


def test_ecosystem_code_block_geometry():
    assert card.CARD_WRAP_WIDTH == 38
    assert card.CARD_CONTINUATION_INDENT == "   "
    assert card.CARD_BULLET_INDENT == "  "


def test_emoji_rows_use_three_space_continuation():
    rows = card.hanging_status_lines(
        "✅ action: terminal — running a bounded system operation"
    )
    assert rows == [
        "✅ action: terminal — running a",
        "   bounded system operation",
    ]


def test_plain_bullets_use_two_space_continuation():
    rows = card.hanging_bullet_lines(
        "Keep working and update this card when the phase changes."
    )
    assert rows == [
        "- Keep working and update this card",
        "  when the phase changes.",
    ]


def test_preformatted_rows_remain_idempotent():
    row = "✅ action: patch — updating the shared renderer"
    assert card.live_line(row) == row


def test_live_card_is_html_preformatted_and_bounded():
    rendered = card.build_card(
        title="Standardize live work cards",
        status="running",
        model="google/gemini-2.5-flash",
        now="Action: terminal — running a bounded system operation",
        done=["✅ action: patch — updated the fixed-width renderer"],
        next_step="No action needed.",
        blocker="None",
    )
    assert rendered.startswith("<pre>") and rendered.endswith("</pre>")
    body = card.html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>"))
    assert max(map(card.display_width, body.splitlines())) <= card.CARD_WRAP_WIDTH
    assert "\n✅ action: patch — updated the\n   fixed-width renderer\n" in rendered


def test_final_summary_is_html_preformatted_and_bounded():
    rendered = card.build_completion_summary(
        title="Render final response summaries in fixed-width Telegram code blocks",
        status="done",
        model="google/gemini-2.5-flash",
        done=["Updated every agent renderer and verified the deployed mirrors"],
        next_step="No action needed.",
        blocker="None",
    )
    assert rendered.startswith("<pre>") and rendered.endswith("</pre>")
    body = card.html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>"))
    assert max(map(card.display_width, body.splitlines())) <= card.CARD_WRAP_WIDTH
    assert "What was done:" in body
    assert "Approval needed:" in body


def test_live_progress_has_a_ten_cell_visual_bar():
    lines = card.progress_lines(
        ["Received Telegram task", "Objective determined: Inbox health check", "Asynchronous worker started"],
        "running",
    )
    rendered = "\n".join(lines)
    assert re.search(r"[█░]{10}", rendered)
    assert "%" in rendered
    assert "stage" in rendered


def test_progress_is_based_on_verified_milestones_not_update_volume():
    route = "planned provider=codex; model=gpt-5.6-luna; worker=josh2-codex-luna; host=josh2"
    early = card.progress_phase(
        ["Received Telegram task", "Objective determined: Inbox health check"],
        "running",
        route=route,
    )
    noisy = card.progress_phase(
        ["Received Telegram task", "Objective determined: Inbox health check"] + [f"Update {i}" for i in range(20)],
        "running",
        route=route,
    )
    assert early == noisy
    assert early[0] == 50


def test_worker_visibility_exposes_owner_and_delegated_worker_cleanly():
    model = "planned provider=xai; model=grok-4; worker=jaimes-grok-public; host=jaimes"
    route = "route=grok; owner=josh2; reason=current events"
    rows = card.worker_visibility_lines(model, route, "running")
    assert rows[0] == "Josh 2.0 · owner/coordinator"
    assert rows[1] == "↳ JAIMES / Grok · xai/grok-4 · planned"
    assert "jaimes-grok-public" not in " ".join(rows)


def test_rich_card_uses_native_blocks_and_collapsible_activity():
    rendered = card.build_rich_card(
        title="Verify Inbox routing",
        status="running",
        model="planned provider=codex; model=gpt-5.6-luna; worker=josh2-codex-luna; host=josh2",
        route="route=luna; owner=josh2; reason=fast Inbox coordination",
        now="Asynchronous worker started",
        done=["Received Telegram task", "Objective determined: Verify Inbox routing"],
        started_at="2026-07-15T05:00:00Z",
        updated="2026-07-15T05:01:15Z",
    )
    assert rendered.startswith("<h3>JOSH 2.0 · LIVE WORK</h3>")
    assert '<input type="checkbox" checked>' in rendered
    assert "<details><summary>Recent activity" in rendered
    assert "<footer>elapsed 1m 15s" in rendered
    assert re.search(r"[█░]{10}", rendered)


def test_rich_cards_default_only_to_control_center_inbox(monkeypatch):
    monkeypatch.delenv(card.RICH_CARD_ENV, raising=False)
    assert card.rich_cards_enabled("-1003589561528", "1")
    assert not card.rich_cards_enabled("-1003589561528", "17")
    assert not card.rich_cards_enabled("6218150306", "")


def test_task_header_is_a_bounded_fixed_width_table():
    rendered = card.build_task_header(
        title="Add a concise routing receipt before live work",
        model="planned provider=codex; model=gpt-5.6-luna; worker=josh2-codex-luna; host=josh2",
        route="route=luna; owner=josh2; reason=fast Inbox coordination; fallback=none",
    )
    assert rendered.startswith("<pre>") and rendered.endswith("</pre>")
    body = card.html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>"))
    assert max(map(card.display_width, body.splitlines())) <= card.CARD_WRAP_WIDTH
    assert "TASK HEADER" in body
    assert "│Objective│" in body
    assert "│Agent    │ Josh 2.0 system" in body
    assert "│Models   │ codex/gpt-5.6-luna" in body


def test_task_header_defaults_only_to_control_center_inbox(monkeypatch):
    monkeypatch.delenv(card.TASK_HEADER_ENV, raising=False)
    assert card.task_headers_enabled("-1003589561528", "1")
    assert not card.task_headers_enabled("-1003589561528", "17")


def test_header_is_persisted_before_live_send_and_not_duplicated_on_retry(monkeypatch, tmp_path):
    state_path = tmp_path / "cards.json"
    monkeypatch.setattr(card, "STATE_PATH", state_path)
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "cards.lock")
    monkeypatch.setattr(card, "publish_brain_feed", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "claim_pending_ack", lambda key: "")
    calls = []
    live_attempts = iter([
        # A definitive rejection is safe to retry. Ambiguous timeouts are
        # covered separately and must remain quarantined to avoid duplicates.
        {"ok": False, "error": "HTTP error 400: rejected before delivery"},
        {"ok": True, "native_rich_message": True, "result": {"message_id": 202}},
    ])

    def fake_send_card(text, buttons, timeout, chat_id=None, thread_id=None):
        calls.append("header")
        assert text.startswith("<pre>TASK HEADER")
        return {"ok": True, "result": {"message_id": 101}}

    def fake_send_rich(*args, **kwargs):
        calls.append("live")
        return next(live_attempts)

    monkeypatch.setattr(card, "send_card", fake_send_card)
    monkeypatch.setattr(card, "send_rich_message", fake_send_rich)
    args = argparse.Namespace(
        key="header-retry",
        title="Verify retry-safe task header",
        model="planned provider=codex; model=gpt-5.6-luna; worker=josh2-codex-luna; host=josh2",
        route="route=luna; owner=josh2; reason=fast Inbox coordination; fallback=none",
        now="Objective and runbook confirmed",
        done="Received Telegram task|Objective determined: Verify retry-safe task header",
        next="Continue automatically",
        blocker="None",
        eta="",
        ack_message_id="",
        buttons="",
        buttons_file="",
        routing_buttons=False,
        approval_buttons=False,
        no_buttons=True,
        no_final_summary=False,
        final_text_file="",
        timeout=15,
        chat_id="-1003589561528",
        thread_id="1",
        dry_run=False,
        no_brain_feed=True,
    )
    assert card.upsert_card(args, "running") == 1
    partial = card.load_state()["cards"]["header-retry"]
    assert partial["header_message_id"] == 101
    assert partial["message_id"] is None

    assert card.upsert_card(args, "running") == 0
    final = card.load_state()["cards"]["header-retry"]
    assert calls == ["header", "live", "live"]
    assert final["header_message_id"] == 101
    assert final["message_id"] == 202


def test_indeterminate_rich_send_is_quarantined_instead_of_duplicated(monkeypatch, tmp_path):
    monkeypatch.setattr(card, "STATE_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "cards.lock")
    monkeypatch.setattr(card, "publish_brain_feed", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "claim_pending_ack", lambda key: "")
    monkeypatch.setattr(card, "task_headers_enabled", lambda *args: False)
    monkeypatch.setattr(card, "rich_cards_enabled", lambda *args: True)
    sends = []

    def ambiguous_send(*args, **kwargs):
        sends.append("send")
        return {"ok": False, "error": "timed out after request write", "delivery_indeterminate": True}

    monkeypatch.setattr(card, "send_rich_message", ambiguous_send)
    args = argparse.Namespace(
        key="indeterminate-live",
        title="Prevent duplicate live cards",
        model="planned provider=codex; model=gpt-5.6-luna; worker=josh2-codex-luna; host=josh2",
        route="route=luna; owner=josh2; reason=bounded Inbox coordination; fallback=none",
        now="Objective and route confirmed",
        done="Received Telegram task|Objective determined: Prevent duplicate live cards",
        next="Continue automatically",
        blocker="None",
        eta="",
        ack_message_id="",
        buttons="",
        buttons_file="",
        routing_buttons=False,
        approval_buttons=False,
        no_buttons=True,
        no_final_summary=False,
        final_text_file="",
        timeout=15,
        chat_id="-1003589561528",
        thread_id="1",
        dry_run=False,
        no_brain_feed=True,
    )
    assert card.upsert_card(args, "running") == 1
    partial = card.load_state()["cards"]["indeterminate-live"]
    assert partial["live_delivery_status"] == "indeterminate"

    assert card.upsert_card(args, "running") == 1
    assert sends == ["send"]


def test_rich_edit_falls_back_to_legacy_html(monkeypatch):
    calls = []

    def fake_api(method, payload, timeout=15):
        calls.append((method, payload))
        if len(calls) == 1:
            return {"ok": False, "error": "rich unsupported"}
        return {"ok": True, "result": {"message_id": 42}}

    monkeypatch.setattr(card, "api_call", fake_api)
    result = card.edit_rich_card(
        42,
        "<h3>Live</h3>",
        "<pre>Live</pre>",
        None,
        15,
        chat_id="-1003589561528",
        thread_id="1",
    )
    assert result["ok"]
    assert result["native_rich_message"] is False
    assert calls[0][1]["rich_message"]["html"] == "<h3>Live</h3>"
    assert calls[1][1]["parse_mode"] == "HTML"


def test_live_receipt_is_checkpointed_before_final_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(card, "STATE_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "cards.lock")
    monkeypatch.setattr(card, "publish_brain_feed", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "claim_pending_ack", lambda key: "")
    monkeypatch.setattr(card, "task_headers_enabled", lambda *args: False)
    monkeypatch.setattr(card, "rich_cards_enabled", lambda *args: True)
    calls = {"send_live": 0, "edit_live": 0, "send_final": 0}

    def send_live(*args, **kwargs):
        calls["send_live"] += 1
        return {"ok": True, "native_rich_message": True, "result": {"message_id": 202}}

    def edit_live(*args, **kwargs):
        calls["edit_live"] += 1
        return {"ok": True, "native_rich_message": True, "result": {"message_id": 202}}

    def send_final(*args, **kwargs):
        calls["send_final"] += 1
        if calls["send_final"] == 1:
            return {"ok": False, "error": "temporary final delivery failure"}
        return {"ok": True, "result": {"message_id": 303}}

    monkeypatch.setattr(card, "send_rich_message", send_live)
    monkeypatch.setattr(card, "edit_rich_card", edit_live)
    monkeypatch.setattr(card, "send_final_summary", send_final)
    args = argparse.Namespace(
        key="final-retry",
        title="Verify final retry",
        model="provider=codex; model=gpt-5.6-luna; worker=josh2-codex-luna; host=josh2",
        route="route=luna; reason=fast Inbox coordination; fallback=none",
        now="Finished and verified",
        done="Completed the request|Verified the result|Prepared the final",
        next="No action needed.",
        blocker="None",
        eta="",
        ack_message_id="",
        buttons="",
        buttons_file="",
        routing_buttons=False,
        approval_buttons=False,
        no_buttons=True,
        no_final_summary=False,
        final_text_file="",
        timeout=15,
        chat_id="-1003589561528",
        thread_id="1",
        dry_run=False,
        no_brain_feed=True,
    )
    assert card.upsert_card(args, "done") == 1
    partial = card.load_state()["cards"]["final-retry"]
    assert partial["message_id"] == 202
    assert partial["final_message_id"] is None
    assert card.upsert_card(args, "done") == 0
    final = card.load_state()["cards"]["final-retry"]
    assert final["message_id"] == 202
    assert final["final_message_id"] == 303
    assert calls == {"send_live": 1, "edit_live": 1, "send_final": 2}


def test_ambiguous_rich_send_does_not_create_a_second_fallback(monkeypatch):
    calls = []

    def fake_api(method, payload, timeout=15):
        calls.append(method)
        return {"ok": False, "error": "timed out waiting for response"}

    monkeypatch.setattr(card, "api_call", fake_api)
    result = card.send_rich_message(
        "<h3>Live</h3>",
        "<pre>Live</pre>",
        15,
        chat_id="-1003589561528",
        thread_id="1",
    )
    assert result["ok"] is False
    assert calls == ["sendRichMessage"]


def test_verified_coordinator_model_is_disclosed_on_the_live_card():
    raw = "provider=codex; model=gpt-5.6-luna; worker=josh2-codex-luna; host=josh2"
    assert card.friendly_model_line(raw) == "codex/gpt-5.6-luna"
    assert card.resolve_auth_path(raw) == "subscription"


def test_telegram_not_modified_is_an_idempotent_success_signal():
    assert card.telegram_message_not_modified({"ok": False, "error": "Bad Request: message is not modified"})
    assert not card.telegram_message_not_modified({"ok": False, "error": "Bad Request: message to edit not found"})


def test_final_text_file_rejects_noncanonical_conversational_text():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "final.html"
        path.write_text("Inbox routing is healthy &amp; ready.", encoding="utf-8")
        with pytest.raises(SystemExit, match="canonical ordered final contract"):
            card.load_final_text_file(str(path))


def test_final_text_file_accepts_canonical_structured_text():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "final.html"
        value = """<pre>Model: codex/gpt-5.6-luna

Complete: Yes

What was done:
- Routed the Inbox request.
- Verified worker execution.
- Prepared the final result.

Issues:
- n/a

Appropriate next steps:
- No action needed.

Approval needed:
- n/a</pre>"""
        path.write_text(value, encoding="utf-8")
        assert card.load_final_text_file(str(path)) == value
