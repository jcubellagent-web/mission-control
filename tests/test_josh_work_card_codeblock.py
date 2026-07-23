#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "josh_work_card.py"
spec = importlib.util.spec_from_file_location("josh_work_card", MODULE_PATH)
card = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(card)


def plain_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return card.html.unescape(value)


def test_card_state_writes_are_private(tmp_path, monkeypatch):
    state_path = tmp_path / "josh_work_cards.json"
    state_path.write_text("{}\n", encoding="utf-8")
    state_path.chmod(0o644)
    monkeypatch.setattr(card, "STATE_PATH", state_path)

    card.save_state({"cards": {"one": {"status": "running"}}})

    assert state_path.stat().st_mode & 0o777 == 0o600


def test_duplicate_no_summary_terminal_close_is_a_noop(tmp_path, monkeypatch):
    state_path = tmp_path / "josh_work_cards.json"
    monkeypatch.setattr(card, "STATE_PATH", state_path)
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "josh_work_cards.lock")
    card.save_state({
        "cards": {
            "terminal-once": {
                "status": "done",
                "header_message_id": 101,
                "message_id": 102,
                "final_message_id": None,
            }
        }
    })
    args = argparse.Namespace(key="terminal-once", no_final_summary=True)

    assert card.upsert_card(args, "done") == 0
    assert card.load_state()["cards"]["terminal-once"]["message_id"] == 102


def test_pending_ack_claim_uses_shared_lock_and_preserves_concurrent_state(tmp_path, monkeypatch):
    ack_path = tmp_path / "fast_ack_state.json"
    ready_path = tmp_path / "claim-ready"
    monkeypatch.setattr(card, "ACK_STATE_PATH", ack_path)
    card.save_ack_state({
        "latest_pending_ack": {"message_id": "777", "key": "pending-card"},
        "active_cards": {"before": {"status": "active"}},
        "acked_prompt_events": ["before-event"],
    })
    lock_path = ack_path.with_suffix(ack_path.suffix + ".lock")
    worker_code = """
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("work_card_claimant", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.ACK_STATE_PATH = Path(sys.argv[2])
Path(sys.argv[3]).write_text("ready", encoding="utf-8")
print(module.claim_pending_ack("concurrent-card"))
"""

    process = None
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
        card.save_ack_state({
            "latest_pending_ack": {"message_id": "777", "key": "pending-card"},
            "last_claim": {"run_id": "new-run", "job_id": "new-job"},
            "active_cards": {
                "before": {"status": "active"},
                "new-run": {"status": "active"},
            },
            "acked_prompt_events": ["before-event", "new-event"],
            "processed_progress_events": ["progress-event"],
        })

    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, (stdout, stderr)
    assert stdout.strip() == "777"
    final = card.load_json(ack_path, {})
    assert final["latest_pending_ack"]["claimed_by"] == "concurrent-card"
    assert final["latest_pending_ack"]["message_id"] == "777"
    assert final["last_claim"] == {"run_id": "new-run", "job_id": "new-job"}
    assert set(final["active_cards"]) == {"before", "new-run"}
    assert final["acked_prompt_events"] == ["before-event", "new-event"]
    assert final["processed_progress_events"] == ["progress-event"]


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
    assert len(body.splitlines()) <= 22
    assert "JOSH 2.0 ·" in body
    assert "Now" in body
    assert "action:" not in body.lower()
    assert "terminal" not in body.lower()
    assert "fixed-width renderer" not in body


def test_final_summary_is_polished_proportional_html():
    rendered = card.build_completion_summary(
        title="Render final response summaries in fixed-width Telegram code blocks",
        status="done",
        model="google/gemini-2.5-flash",
        done=["Updated every agent renderer and verified the deployed mirrors"],
        next_step="No action needed.",
        blocker="None",
    )
    assert rendered.startswith("<b>JOSH 2.0 ·")
    assert not rendered.startswith("<pre>")
    body = plain_html(rendered)
    assert "What was done:" in body
    assert "Approval needed:" in body
    assert "• " in rendered


def test_weak_generated_success_is_downgraded_without_inventing_results():
    rendered = card.build_completion_summary(
        title="Evaluate Agent Robinhood for safe Robinhood trading support",
        status="done",
        model="unverified",
        route="unverified",
        now="Assessment complete",
        done=["Reviewing product claims, code, and trade risks"],
        next_step="No action needed.",
        blocker="None",
    )
    body = plain_html(rendered)
    assert "Complete: No" in body
    assert "Detailed findings were not" in body
    assert "Missing facts were not inferred" in body
    assert "Closed out:" not in body
    assert "Verified the worker execution" not in body
    assert "Prepared the result" not in body


def test_finished_assessment_stays_complete_when_the_finding_is_negative():
    rendered = card.build_completion_summary(
        title="Evaluate Agent Robinhood for safe Robinhood trading support",
        status="done",
        model="openai/gpt-5.6-terra",
        route="route=direct; reason=verified read-only assessment",
        done=["Confirmed the signal source is read-only; brokerage automation is unsupported."],
        next_step="Do not connect brokerage credentials.",
        blocker="Brokerage trading support is unsupported.",
    )
    body = plain_html(rendered)
    assert "Complete: Yes" in body
    assert "brokerage automation is" in body
    assert "Issues:" in body


def test_finished_telegram_health_assessment_accepts_negative_operational_findings():
    rendered = card.build_completion_summary(
        title="Assess Telegram health read-only",
        status="done",
        model="openai/gpt-5.6-luna",
        route="route=luna; reason=read-only health/status check",
        done=[
            "The local gateway is running and listening on port 18790, but the sandbox could not probe loopback.",
            "The inspected launchd domain has no registered Telegram fast-ack entry.",
            "The available Telegram logs are empty and last modified May 5.",
        ],
        next_step="Use the host-native read-only probe for current service state.",
        blocker="Sandbox-local service checks are unverified.",
    )
    body = plain_html(rendered)
    assert "Complete: Yes" in body
    assert "running and listening" in body
    assert "has no registered" in body
    assert "empty and last modified" in body


def test_negative_operational_findings_cannot_claim_no_issues_or_no_action():
    rendered = card.build_completion_summary(
        title="Assess Telegram health read-only",
        status="done",
        model="openai/gpt-5.6-luna",
        route="route=luna; reason=read-only health/status check",
        done=[
            "The local gateway service is not running at its configured endpoint.",
            "The Telegram Fast Ack watcher service is stopped in the launchd runtime.",
            "The available Telegram delivery logs are empty and stale on the service host.",
        ],
        next_step="No action needed.",
        blocker="None",
    )
    body = plain_html(rendered)
    assert "Complete: No" in body
    assert "A reported risk or limitation was not included under Issues." in body
    assert "did not capture enough concrete findings" not in body


def test_generic_state_words_do_not_substitute_for_operational_findings():
    rendered = card.build_completion_summary(
        title="Assess Telegram health read-only",
        status="done",
        model="openai/gpt-5.6-luna",
        route="route=luna; reason=read-only health/status check",
        done=[
            "The gateway health assessment remains active while the requested work is being discussed.",
            "The service status review is running while the requested work remains pending.",
            "The runtime report was last modified May 5 while the request remained pending.",
        ],
        next_step="No action needed.",
        blocker="None",
    )
    assert "Complete: No" in plain_html(rendered)


def test_process_prefixed_operational_findings_are_preserved():
    rendered = card.build_completion_summary(
        title="Assess Telegram health read-only",
        status="done",
        model="openai/gpt-5.6-luna",
        route="route=luna; reason=read-only health/status check",
        done=[
            "Checked the gateway service is running and listening on its configured endpoint.",
            "Checked the Fast Ack service is registered and active in the launchd runtime.",
            "Confirmed the host health check passed with no remaining issues.",
        ],
        next_step="No action needed.",
        blocker="None",
    )
    body = plain_html(rendered)
    assert "Complete: Yes" in body
    assert "Checked the gateway service is running" in body


def test_no_missing_helpers_is_not_misclassified_as_an_operational_risk():
    rendered = card.build_completion_summary(
        title="Assess Telegram health read-only",
        status="done",
        model="openai/gpt-5.6-luna",
        route="route=luna; reason=read-only health/status check",
        done=[
            "The gateway service has no remaining issues after its current health check.",
            "The runtime has no missing helpers in the canonical Telegram delivery path.",
            "There are no service failures in the current host snapshot.",
        ],
        next_step="No action needed.",
        blocker="None",
    )
    assert "Complete: Yes" in plain_html(rendered)


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


def test_compact_progress_ignores_a_large_raw_tool_burst():
    route = "route=terra; owner=josh2; reason=trusted execution"
    baseline = card.compact_phase(
        ["Received Telegram task", "Objective determined: Verify Inbox routing"],
        "running",
        route=route,
    )
    noisy = card.compact_phase(
        ["Received Telegram task", "Objective determined: Verify Inbox routing"]
        + [f"tool: read_file - /Users/josh2.0/private/file-{index}.py" for index in range(40)],
        "running",
        route=route,
    )
    assert baseline == noisy == ("Routed", 3)


def test_terminal_needs_attention_closes_delivery_lifecycle_at_100_percent():
    items = [
        "Received Telegram task",
        "Objective and runbook confirmed",
        "Route selected: Josh 2.0 system",
        "Worker started verified execution",
        "Model execution verified; formatting final result",
        "Structured issue summary prepared",
    ]
    route = "route=terra; reason=trusted execution; worker=josh2-codex-terra"

    assert card.is_complete_status("failed") is False
    assert card.is_terminal_lifecycle_status("failed") is True
    assert card.milestone_count(items, "failed", route=route) == 6
    assert card.progress_phase(items, "failed", route=route) == (100, "delivery complete · needs attention")

    legacy = card.build_card(
        title="Verify the new JOSHeX changes work as intended",
        status="failed",
        model="provider=codex; model=gpt-5.6-terra; worker=josh2-codex-terra; host=josh2",
        route=route,
        done=items,
        blocker="The requested behavior still needs attention",
    )
    rich = card.build_rich_card(
        title="Verify the new JOSHeX changes work as intended",
        status="failed",
        model="provider=codex; model=gpt-5.6-terra; worker=josh2-codex-terra; host=josh2",
        route=route,
        done=items,
    )

    assert "Progress [██████████] 6/6" in card.html.unescape(legacy)
    assert "JOSH 2.0 · NEEDS ATTENTION" in rich
    assert "██████████ 100% · stage 6/6" in rich
    assert rich.count(" checked") == 6


def test_worker_visibility_exposes_owner_and_delegated_worker_cleanly():
    model = "planned provider=xai; model=grok-4; worker=jaimes-grok-public; host=jaimes"
    route = "route=grok; owner=josh2; reason=current events"
    rows = card.worker_visibility_lines(model, route, "running")
    assert rows[0] == "Josh 2.0 · owner/coordinator"
    assert rows[1] == "↳ JAIMES / Grok · xai/grok-4 · planned"
    assert "jaimes-grok-public" not in " ".join(rows)


def test_started_worker_is_active_and_heartbeats_are_aggregated():
    model = "planned provider=codex; model=gpt-5.6-terra; worker=josh2-codex-terra; host=josh2"
    route = "planned route=terra; reason=trusted execution; fallback=none"
    items = [
        "Asynchronous worker started",
        "Still working; waiting for next model/tool update (21:51:20 EDT)",
        "Still working; waiting for next model/tool update (21:51:40 EDT)",
    ]
    rows = card.worker_visibility_lines(model, route, "running", items)
    assert rows[1].endswith("· active")
    assert card.heartbeat_activity_text(items) == (
        "Worker active · heartbeat 2 confirmed at 21:51:40 EDT."
    )
    rendered = card.build_rich_card(
        title="Verify Inbox end-to-end reliability",
        status="running",
        model=model,
        route=route,
        now=items[-1],
        done=items[:-1],
    )
    assert "Working through the task" not in rendered
    assert "Recent activity (1)" in rendered
    assert "heartbeat 2 confirmed at 21:51:40 EDT" in rendered


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
    assert rendered.startswith("<h3>")
    assert not rendered.startswith("<pre>")
    assert "<p><b>Objective</b><br>" in rendered
    assert "<code>codex/gpt-5.6-luna</code>" in rendered
    assert "<blockquote><b>Now</b><br>" in rendered
    assert rendered.count('type="checkbox"') == 6
    assert "<details><summary>Recent activity" in rendered
    assert "<footer>elapsed 1m 15s" in rendered
    assert "Asynchronous worker" not in rendered
    assert re.search(r"[█░]{10}", rendered)


def test_rich_cards_default_only_to_control_center_inbox(monkeypatch):
    monkeypatch.delenv(card.RICH_CARD_ENV, raising=False)
    assert card.rich_cards_enabled("-1003589561528", "1")
    assert card.rich_cards_enabled("telegram:-1003589561528", "1")
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


def test_task_header_is_opt_in_for_diagnostics(monkeypatch):
    monkeypatch.delenv(card.TASK_HEADER_ENV, raising=False)
    assert not card.task_headers_enabled("-1003589561528", "1")
    assert not card.task_headers_enabled("telegram:-1003589561528", "1")
    assert card.is_inbox_topic("telegram:-1003589561528", "1")
    assert not card.task_headers_enabled("-1003589561528", "17")
    monkeypatch.setenv(card.TASK_HEADER_ENV, "1")
    assert card.task_headers_enabled("-1003589561528", "1")


def test_new_inbox_card_defaults_to_versioned_live_only_surface(monkeypatch, tmp_path):
    monkeypatch.delenv(card.TASK_HEADER_ENV, raising=False)
    monkeypatch.setattr(card, "STATE_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "cards.lock")
    monkeypatch.setattr(card, "publish_brain_feed", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "claim_pending_ack", lambda key: "")
    monkeypatch.setattr(
        card,
        "send_card",
        lambda *args, **kwargs: pytest.fail("the default Inbox flow must not send a task header"),
    )
    monkeypatch.setattr(
        card,
        "send_rich_message",
        lambda *args, **kwargs: {
            "ok": True,
            "native_rich_message": True,
            "result": {"message_id": 202},
        },
    )
    args = argparse.Namespace(
        key="live-only-default", title="Assess Telegram health read-only",
        model="provider=codex; model=gpt-5.6-terra; worker=josh2-codex-terra; host=josh2",
        route="route=terra; owner=josh2; reason=verified Telegram assessment; fallback=none",
        now="Telegram health verified", done="Objective determined: Assess Telegram health read-only",
        next="Continue automatically", blocker="None", eta="", ack_message_id="",
        buttons="", buttons_file="", routing_buttons=False, approval_buttons=False,
        no_buttons=True, no_final_summary=False, final_text_file="", timeout=15,
        chat_id="-1003589561528", thread_id="1", dry_run=False, no_brain_feed=True,
    )

    assert card.upsert_card(args, "running") == 0
    persisted = card.load_state()["cards"]["live-only-default"]
    assert persisted["header_message_id"] is None
    assert persisted["message_id"] == 202
    assert persisted["header_required"] is False
    assert persisted["surface_contract"] == "live-only-v2"


def test_header_is_persisted_before_live_send_and_not_duplicated_on_retry(monkeypatch, tmp_path):
    monkeypatch.setenv(card.TASK_HEADER_ENV, "1")
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


def test_indeterminate_header_send_is_quarantined_on_retry(monkeypatch, tmp_path):
    monkeypatch.setenv(card.TASK_HEADER_ENV, "1")
    monkeypatch.setattr(card, "STATE_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "cards.lock")
    monkeypatch.setattr(card, "publish_brain_feed", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "claim_pending_ack", lambda key: "")
    sends = []

    def ambiguous_header(*args, **kwargs):
        sends.append("header")
        return {
            "ok": False,
            "error": "timed out after request write",
            "delivery_indeterminate": True,
        }

    monkeypatch.setattr(card, "send_card", ambiguous_header)
    monkeypatch.setattr(
        card,
        "send_rich_message",
        lambda *args, **kwargs: pytest.fail("live card must not send after an ambiguous header"),
    )
    args = argparse.Namespace(
        key="indeterminate-header",
        title="Prevent duplicate task headers",
        model="planned provider=codex; model=gpt-5.6-luna; worker=josh2-codex-luna; host=josh2",
        route="route=luna; owner=josh2; reason=bounded Inbox coordination; fallback=none",
        now="Objective and route confirmed",
        done="Received Telegram task|Objective determined: Prevent duplicate task headers",
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
    partial = card.load_state()["cards"]["indeterminate-header"]
    assert partial["header_message_id"] is None
    assert partial["message_id"] is None
    assert partial["header_delivery_status"] == "indeterminate"

    assert card.upsert_card(args, "running") == 1
    assert sends == ["header"]


def test_header_success_without_receipt_is_quarantined_on_retry(monkeypatch, tmp_path):
    monkeypatch.setenv(card.TASK_HEADER_ENV, "1")
    monkeypatch.setattr(card, "STATE_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "cards.lock")
    monkeypatch.setattr(card, "publish_brain_feed", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "claim_pending_ack", lambda key: "")
    sends = []

    def missing_receipt(*args, **kwargs):
        sends.append("header")
        return {"ok": True, "result": {}}

    monkeypatch.setattr(card, "send_card", missing_receipt)
    args = argparse.Namespace(
        key="missing-header-receipt",
        title="Fence missing task-header receipts",
        model="planned provider=codex; model=gpt-5.6-luna; worker=josh2-codex-luna; host=josh2",
        route="route=luna; owner=josh2; reason=bounded Inbox coordination; fallback=none",
        now="Objective and route confirmed",
        done="Received Telegram task|Objective determined: Fence missing task-header receipts",
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
    assert card.load_state()["cards"]["missing-header-receipt"]["header_delivery_status"] == "indeterminate"
    assert card.upsert_card(args, "running") == 1
    assert sends == ["header"]


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


def test_live_success_without_receipt_is_quarantined_on_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(card, "STATE_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "cards.lock")
    monkeypatch.setattr(card, "publish_brain_feed", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "claim_pending_ack", lambda key: "")
    monkeypatch.setattr(card, "task_headers_enabled", lambda *args: False)
    monkeypatch.setattr(card, "rich_cards_enabled", lambda *args: True)
    sends = []

    def missing_receipt(*args, **kwargs):
        sends.append("live")
        return {"ok": True, "native_rich_message": True, "result": {}}

    monkeypatch.setattr(card, "send_rich_message", missing_receipt)
    args = argparse.Namespace(
        key="missing-live-receipt", title="Fence missing live receipts",
        model="codex/gpt-5.6-luna", route="route=luna", now="Starting",
        done="Received Telegram task", next="Continue", blocker="None", eta="",
        ack_message_id="", buttons="", buttons_file="", routing_buttons=False,
        approval_buttons=False, no_buttons=True, no_final_summary=False,
        final_text_file="", timeout=15, chat_id="-1003589561528", thread_id="1",
        dry_run=False, no_brain_feed=True,
    )
    assert card.upsert_card(args, "running") == 1
    assert card.load_state()["cards"]["missing-live-receipt"]["live_delivery_status"] == "indeterminate"
    assert card.upsert_card(args, "running") == 1
    assert sends == ["live"]


def test_final_success_without_receipt_is_quarantined_on_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(card, "STATE_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "cards.lock")
    monkeypatch.setattr(card, "publish_brain_feed", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "claim_pending_ack", lambda key: "")
    monkeypatch.setattr(card, "task_headers_enabled", lambda *args: False)
    monkeypatch.setattr(card, "rich_cards_enabled", lambda *args: True)
    calls = {"live": 0, "final": 0}

    def live(*args, **kwargs):
        calls["live"] += 1
        return {"ok": True, "native_rich_message": True, "result": {"message_id": 202}}

    def final(*args, **kwargs):
        calls["final"] += 1
        return {"ok": True, "result": {}}

    monkeypatch.setattr(card, "send_rich_message", live)
    monkeypatch.setattr(card, "edit_rich_card", live)
    monkeypatch.setattr(card, "send_final_summary", final)
    args = argparse.Namespace(
        key="missing-final-receipt", title="Fence missing final receipts",
        model="codex/gpt-5.6-luna", route="route=luna", now="Finished",
        done="Completed request|Verified result|Prepared final", next="No action needed.",
        blocker="None", eta="", ack_message_id="", buttons="", buttons_file="",
        routing_buttons=False, approval_buttons=False, no_buttons=True,
        no_final_summary=False, separate_final_summary=True, final_text_file="", timeout=15,
        chat_id="-1003589561528", thread_id="1", dry_run=False, no_brain_feed=True,
    )
    assert card.upsert_card(args, "done") == 1
    partial = card.load_state()["cards"]["missing-final-receipt"]
    assert partial["message_id"] == 202
    assert partial["final_delivery_status"] == "indeterminate"
    assert card.upsert_card(args, "done") == 1
    assert calls == {"live": 2, "final": 1}


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


def test_ambiguous_rich_edit_is_quarantined_without_legacy_retry(monkeypatch):
    calls = []

    def fake_api(method, payload, timeout=15):
        calls.append((method, payload))
        return {"ok": False, "error": "timed out waiting for response"}

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
    assert not result["ok"]
    assert result["native_rich_message"] is True
    assert result["delivery_indeterminate"] is True
    assert len(calls) == 1


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
            return {"ok": False, "error": "Bad Request: final payload rejected"}
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
        separate_final_summary=True,
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


@pytest.mark.parametrize("renderer", ["rich", "legacy"])
def test_needs_attention_card_reaches_100_before_structured_final(monkeypatch, tmp_path, renderer):
    monkeypatch.setattr(card, "STATE_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "cards.lock")
    monkeypatch.setattr(card, "publish_brain_feed", lambda *args, **kwargs: None)
    calls: list[tuple] = []
    card.save_state({
        "cards": {
            "failed-terminal-order": {
                "title": "Verify the new JOSHeX changes work as intended",
                "message_id": 202,
                "header_message_id": 201,
                "final_message_id": None,
                "ack_message_id": "",
                "status": "running",
                "started_at": "2026-07-15T19:18:59Z",
                "updated_at": "2026-07-15T19:19:20Z",
                "done": ["Received Telegram task"],
                "work_log": ["Received Telegram task"],
                "route": "route=terra; worker=josh2-codex-terra",
                "model": "provider=codex; model=gpt-5.6-terra; worker=josh2-codex-terra; host=josh2",
                "renderer": renderer,
                "chat_id": "-1003589561528",
                "thread_id": "1",
            }
        }
    })

    def edit_live(message_id, rich, legacy, *_args, **_kwargs):
        calls.append(("edit_live", str(message_id), rich, legacy))
        return {"ok": True, "native_rich_message": True, "result": {"message_id": int(message_id)}}

    def send_final(text, *_args, **_kwargs):
        calls.append(("send_final", text))
        return {"ok": True, "result": {"message_id": 303}}

    def edit_legacy(message_id, legacy, *_args, **_kwargs):
        calls.append(("edit_live", str(message_id), legacy))
        return {"ok": True, "result": {"message_id": int(message_id)}}

    monkeypatch.setattr(card, "edit_rich_card", edit_live)
    monkeypatch.setattr(card, "edit_card", edit_legacy)
    monkeypatch.setattr(card, "send_final_summary", send_final)
    args = argparse.Namespace(
        key="failed-terminal-order",
        title="Verify the new JOSHeX changes work as intended",
        model="provider=codex; model=gpt-5.6-terra; worker=josh2-codex-terra; host=josh2",
        route="route=terra; worker=josh2-codex-terra",
        now="Structured issue summary prepared",
        done="Worker execution verified|Objective was not completed|Structured issue summary prepared",
        next="Review the remaining issue.",
        blocker="The requested behavior still needs attention",
        eta="",
        ack_message_id="",
        buttons="",
        buttons_file="",
        routing_buttons=False,
        approval_buttons=False,
        no_buttons=True,
        no_final_summary=False,
        separate_final_summary=True,
        final_text_file="",
        timeout=15,
        chat_id="-1003589561528",
        thread_id="1",
        dry_run=False,
        no_brain_feed=True,
    )

    assert card.upsert_card(args, "failed") == 0
    assert [call[0] for call in calls] == ["edit_live", "send_final"]
    if renderer == "rich":
        assert "██████████ 100% · stage 6/6" in calls[0][2]
        assert calls[0][2].count(" checked") == 6
        assert "Progress [██████████] 6/6" in card.html.unescape(calls[0][3])
    else:
        assert "Progress [██████████] 6/6" in calls[0][2]
    persisted = card.load_state()["cards"]["failed-terminal-order"]
    assert persisted["status"] == "failed"
    assert persisted["message_id"] == 202
    assert persisted["final_message_id"] == 303
    assert persisted["header_required"] is True
    assert persisted["surface_contract"] == "header-live-v1"


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


def test_incomplete_coordinator_final_passes_real_terminal_file_contract_without_grader_prose():
    coordinator_path = Path(__file__).resolve().parents[1] / "scripts" / "inbox_coordinator.py"
    coordinator_spec = importlib.util.spec_from_file_location("inbox_coordinator_terminal_contract", coordinator_path)
    coordinator = importlib.util.module_from_spec(coordinator_spec)
    assert coordinator_spec and coordinator_spec.loader
    coordinator_spec.loader.exec_module(coordinator)
    weak_output = """Complete: Yes
What was done:
- Assessment complete.
- Reviewed the requested assessment.
- Prepared the result for delivery.
Issues:
- n/a
Appropriate next steps:
- No action needed.
Approval needed:
- n/a"""
    rendered = coordinator.render_final_html(
        {"routeId": "luna", "routingReason": "read-only health/status check"},
        {
            "executionVerified": True,
            "modelVerified": True,
            "actualProvider": "codex",
            "actualModel": "gpt-5.6-luna",
        },
        weak_output,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "final.html"
        path.write_text(rendered, encoding="utf-8")
        assert card.load_final_text_file(str(path)) == rendered
    lowered = plain_html(rendered).lower()
    assert "completion claim requires" not in lowered
    assert "supplied summary contained" not in lowered
    assert "did not include enough concrete" not in lowered


def test_final_text_file_accepts_canonical_structured_text():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "final.html"
        value = """<pre>Model: codex/gpt-5.6-luna
   | Route: Josh 2.0 Inbox
   | Why: verified assessment

Complete: Yes - assessment complete.

What was done:
- Confirmed Agent RH monitors only
  Robinhood Chain signals.
- Determined it cannot trade a
  Robinhood brokerage account.
- Identified credential and wallet
  access as avoidable risks.
- Recommended read-only signal use
  without credentials or wallets.

Issues:
- Connecting credentials would create
  avoidable account-control risk.

Appropriate next steps:
- Use Agent RH only as a read-only
  research signal.
- Do not connect brokerage, wallet,
  or trading credentials.

Approval needed:
- n/a</pre>"""
        path.write_text(value, encoding="utf-8")
        assert card.load_final_text_file(str(path)) == value


def test_final_text_file_accepts_polished_proportional_html():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "final.html"
        value = """<b>JOSH 2.0 · COMPLETE</b>
<code>Model: codex/gpt-5.6-terra | Route: Josh 2.0 Inbox | Why: verified Telegram assessment</code>

<blockquote><b>Complete:</b> Yes - Telegram assessment complete</blockquote>

<b>What was done:</b>
• Confirmed Telegram is connected to the verified Josh 2.0 gateway.
• Verified the native live card rendered all six progress stages.
• Confirmed every response-contract check passed with no remaining work.

<b>Issues:</b>
• None

<b>Appropriate next steps:</b>
• No action needed.

<b>Approval needed:</b>
• None"""
        path.write_text(value, encoding="utf-8")
        assert card.load_final_text_file(str(path)) == value


def test_final_text_file_rejects_weak_agent_rh_completion_card():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "final.html"
        value = """<pre>Model: unverified | Route: unverified
   | Why: reported work-card outcome

Complete: Yes - Evaluate Agent RH
   for safe trading support complete

What was done:
- Reviewing product claims, code, and
  trade risks.
- Read-only Robinhood Chain signal
  source; not brokerage automation.
- Assessment complete.

Issues:
- n/a

Appropriate next steps:
- No action needed.

Approval needed:
- n/a</pre>"""
        path.write_text(value, encoding="utf-8")
        with pytest.raises(SystemExit, match="not substantive"):
            card.load_final_text_file(str(path))


def test_final_text_file_accepts_concrete_topic17_repair_outcomes():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "final.html"
        value = """<pre>Model: codex/gpt-5.6-sol
   | Route: JAIMES execution
   | Why: origin-route repair

Complete: Yes - routing fixed

What was done:
- Missing topic metadata caused
  edits to enter the wrong chat.
- 26 misplaced card records were
  repaired without deleting history.
- Duplicate fast-ack cards were
  disabled; one owner remains.

Issues:
- n/a

Appropriate next steps:
- Keep a Topic 17 route canary.

Approval needed:
- n/a</pre>"""
        path.write_text(value, encoding="utf-8")
        assert card.load_final_text_file(str(path)) == value


def test_final_text_file_rejects_duplicate_why_header():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "final.html"
        value = """<pre>Model: codex/gpt-5.6-sol
   | Route: JAIMES execution
   | Why: primary | Why: duplicate

Complete: No - malformed header

What was done:
- Preserved the source response.
- Identified a duplicate field.
- Kept the result fail closed.

Issues:
- Header is malformed.

Appropriate next steps:
- Regenerate one verified header.

Approval needed:
- n/a</pre>"""
        path.write_text(value, encoding="utf-8")
        with pytest.raises(SystemExit, match="canonical ordered final contract"):
            card.load_final_text_file(str(path))


def test_topic1_bare_terminal_defers_then_substantive_final_uses_single_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(card, "STATE_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "cards.lock")
    monkeypatch.setattr(card, "publish_brain_feed", lambda *args, **kwargs: None)
    sends: list[str] = []
    edits: list[str] = []
    card.save_state({
        "cards": {
            "agent-rh-assessment": {
                "title": "Evaluate Agent Robinhood for safe trading support",
                "header_message_id": 201,
                "message_id": 202,
                "final_message_id": None,
                "ack_message_id": "",
                "status": "running",
                "started_at": "2026-07-18T16:30:00Z",
                "updated_at": "2026-07-18T16:31:00Z",
                "done": ["Reviewing product claims, code, and trade risks"],
                "work_log": ["Reviewing product claims, code, and trade risks"],
                "route": "route=luna; reason=Inbox assessment",
                "model": "codex/gpt-5.6-luna",
                "renderer": "legacy",
                "chat_id": "-1003589561528",
                "thread_id": "1",
            }
        }
    })

    def edit_live(message_id, *_args, **_kwargs):
        edits.append(f"live:{message_id}")
        return {"ok": True, "result": {"message_id": int(message_id)}}

    def send_final(text, *_args, **_kwargs):
        sends.append(text)
        return {"ok": True, "result": {"message_id": 303}}

    monkeypatch.setattr(card, "edit_card", edit_live)
    monkeypatch.setattr(card, "send_final_summary", send_final)
    args = argparse.Namespace(
        key="agent-rh-assessment",
        title="Evaluate Agent Robinhood for safe trading support",
        model="codex/gpt-5.6-luna",
        route="route=luna; reason=Inbox assessment",
        now="Assessment complete",
        done="Read-only Robinhood Chain signal source; not brokerage automation",
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
        separate_final_summary=False,
        final_text_file="",
        timeout=15,
        chat_id="-1003589561528",
        thread_id="1",
        dry_run=False,
        no_brain_feed=True,
    )

    assert card.upsert_card(args, "done") == 0
    deferred = card.load_state()["cards"]["agent-rh-assessment"]
    assert deferred["status"] == "done"
    assert deferred["final_message_id"] is None
    assert sends == []

    final_path = tmp_path / "substantive-final.html"
    final_path.write_text("""<pre>Model: codex/gpt-5.6-luna
   | Route: Josh 2.0 Inbox
   | Why: verified assessment

Complete: Yes - assessment complete.

What was done:
- Confirmed Agent RH monitors only
  Robinhood Chain signals.
- Determined it cannot trade a
  Robinhood brokerage account.
- Identified credential and wallet
  access as avoidable risks.
- Recommended read-only signal use
  without credentials or wallets.

Issues:
- Connecting credentials would create
  avoidable account-control risk.

Appropriate next steps:
- Use Agent RH only as a read-only
  research signal.
- Do not connect brokerage, wallet,
  or trading credentials.

Approval needed:
- n/a</pre>""", encoding="utf-8")
    args.final_text_file = str(final_path)
    assert card.upsert_card(args, "done") == 0
    delivered = card.load_state()["cards"]["agent-rh-assessment"]
    assert delivered["final_message_id"] == 303
    assert len(sends) == 1
    assert "Confirmed Agent RH monitors only" in sends[0]
    assert edits == ["live:202", "live:202"]


def test_unrelated_cards_do_not_hold_global_state_lock_across_network_io(monkeypatch, tmp_path):
    monkeypatch.setattr(card, "STATE_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "cards.lock")
    monkeypatch.setattr(card, "publish_brain_feed", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "claim_pending_ack", lambda key: "")
    monkeypatch.setattr(card, "task_headers_enabled", lambda *args: False)
    monkeypatch.setattr(card, "rich_cards_enabled", lambda *args: False)
    barrier = threading.Barrier(2, timeout=2)
    ids = iter((801, 802))

    def concurrent_send(*args, **kwargs):
        barrier.wait()
        return {"ok": True, "result": {"message_id": next(ids)}}

    monkeypatch.setattr(card, "send_card", concurrent_send)

    def args_for(key):
        return argparse.Namespace(
            key=key, title=f"Concurrent {key}", model="codex/gpt-5.6-luna",
            route="route=luna", now="Starting", done="Received Telegram task",
            next="Continue", blocker="None", eta="", ack_message_id="",
            buttons="", buttons_file="", routing_buttons=False,
            approval_buttons=False, no_buttons=True, no_final_summary=False,
            final_text_file="", timeout=15, chat_id="-1003589561528",
            thread_id="1", dry_run=False, no_brain_feed=True,
        )

    results = []
    threads = [
        threading.Thread(target=lambda key=key: results.append(card.upsert_card(args_for(key), "running")))
        for key in ("burst-a", "burst-b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()

    assert sorted(results) == [0, 0]
    saved = card.load_state()["cards"]
    assert set(saved) == {"burst-a", "burst-b"}
    assert {saved["burst-a"]["message_id"], saved["burst-b"]["message_id"]} == {801, 802}


def protocol_start_args(tmp_path, key="protocol-card"):
    return argparse.Namespace(
        key=key, title="Protocol card", model="codex/gpt-5.6-luna",
        route="route=luna", now="Starting", done="Received Telegram task",
        next="Continue", blocker="None", eta="", ack_message_id="",
        buttons="", buttons_file="", routing_buttons=False,
        approval_buttons=False, no_buttons=True, no_final_summary=False,
        final_text_file="", timeout=1, chat_id="-1003589561528",
        thread_id="1", dry_run=False, no_brain_feed=True,
        effect_path=str(tmp_path / "claim.effects.json"),
        cancel_path=str(tmp_path / "claim.cancel.json"),
        surface_deadline_ms=int(time.time() * 1000) + 5_000,
    )


def prepare_protocol_card(monkeypatch, tmp_path):
    monkeypatch.setattr(card, "STATE_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(card, "LOCK_PATH", tmp_path / "cards.lock")
    monkeypatch.setattr(card, "publish_brain_feed", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "claim_pending_ack", lambda key: "")
    monkeypatch.setattr(card, "task_headers_enabled", lambda *args: True)
    monkeypatch.setattr(card, "rich_cards_enabled", lambda *args: False)


def test_protocol_cancellation_prevents_header_send(monkeypatch, tmp_path):
    prepare_protocol_card(monkeypatch, tmp_path)
    args = protocol_start_args(tmp_path)
    Path(args.cancel_path).write_text('{"state":"cancelled-before-surface"}\n', encoding="utf-8")
    sends = []
    monkeypatch.setattr(card, "send_card", lambda *args, **kwargs: sends.append(args) or {"ok": True})
    assert card.upsert_card(args, "running") == 1
    assert sends == []
    assert not Path(args.effect_path).exists()


def test_protocol_checkpoints_attempt_before_header_and_live_send(monkeypatch, tmp_path):
    prepare_protocol_card(monkeypatch, tmp_path)
    args = protocol_start_args(tmp_path)
    observed = []

    def send_card(*unused_args, **unused_kwargs):
        effect = json.loads(Path(args.effect_path).read_text(encoding="utf-8"))
        observed.append((effect["state"], effect["stage"]))
        return {"ok": True, "result": {"message_id": 700 + len(observed)}}

    monkeypatch.setattr(card, "send_card", send_card)
    assert card.upsert_card(args, "running") == 0
    assert observed == [("attempting", "task-header-send"), ("attempting", "live-card-send")]
    effect = json.loads(Path(args.effect_path).read_text(encoding="utf-8"))
    assert effect["header_message_id"] == "701"
    assert effect["live_message_id"] == "702"


@pytest.mark.parametrize(
    ("result", "expected_state"),
    [
        ({"ok": False, "error": "HTTP Error 400: Bad Request"}, "failed-before-surface"),
        ({"ok": False, "error": "timed out after request write"}, "indeterminate"),
        ({"ok": True, "result": {}}, "indeterminate"),
    ],
)
def test_protocol_distinguishes_definitive_and_ambiguous_header_failures(
    monkeypatch, tmp_path, result, expected_state,
):
    prepare_protocol_card(monkeypatch, tmp_path)
    args = protocol_start_args(tmp_path)
    monkeypatch.setattr(card, "send_card", lambda *args, **kwargs: result)
    assert card.upsert_card(args, "running") == 1
    effect = json.loads(Path(args.effect_path).read_text(encoding="utf-8"))
    assert effect["state"] == expected_state


def test_protocol_refuses_new_surface_after_deadline(monkeypatch, tmp_path):
    prepare_protocol_card(monkeypatch, tmp_path)
    args = protocol_start_args(tmp_path)
    args.surface_deadline_ms = int(time.time() * 1000) - 1
    sends = []
    monkeypatch.setattr(card, "send_card", lambda *args, **kwargs: sends.append(args) or {"ok": True})
    assert card.upsert_card(args, "running") == 1
    assert sends == []
    assert not Path(args.effect_path).exists()
