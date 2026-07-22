from __future__ import annotations

import html
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import jaimes_telegram_fast_ack as watcher
import jaimes_work_card as card
import objective_quality


def card_args(*, action: str, key: str) -> SimpleNamespace:
    return SimpleNamespace(
        action=action,
        key=key,
        title="Verify Telegram delivery",
        model="openai-codex/gpt-5.6-sol",
        route="JAIMES verified execution",
        now="Verifying the delivery result",
        done="",
        next="",
        blocker="None",
        eta="",
        ack_message_id="",
        separate_message=False,
        chat_id="-1003589561528",
        thread_id="17",
        work_id="",
        run_id="",
        task_started_at="",
        final_message_id="",
        final_delivery_verified_by="",
        buttons=None,
        buttons_file=None,
        routing_buttons=False,
        approval_buttons=False,
        no_buttons=True,
        final_summary=False,
        no_final_summary=True,
        timeout=15,
        dry_run=False,
        no_brain_feed=True,
    )


def test_transport_rows_are_removed_without_losing_same_line_imperative() -> None:
    prompt = (
        "[J|6218150306]\n"
        "TEST ID: TG-E2E-20260718-B Run a safe concurrency and delivery canary"
    )
    assert objective_quality.current_request_text(prompt) == (
        "Run a safe concurrency and delivery canary"
    )
    objective = objective_quality.semantic_reinterpretation(prompt)
    assert objective.startswith("Execute ")
    assert "TEST ID" not in objective
    assert "6218150306" not in objective
    assert not objective_quality.objective_is_near_copy(prompt, objective)


def test_context_before_imperative_does_not_hide_actionable_request() -> None:
    prompt = "Background context only. Please verify the Topic 17 completion receipt."
    assert objective_quality.semantic_reinterpretation(prompt).startswith("Confirm ")


def test_model_routing_output_contract_has_same_safe_objective_as_inbox() -> None:
    prompt = (
        "Assess whether our model routing is resilient and whether private work and "
        "execution are routed appropriately. Make no changes.\n"
        "Return three findings, the verified model and authentication route actually "
        "used, any fallback that occurred, and a final conclusion of functioning or "
        "needs attention."
    )
    objective = watcher.objective_from_prompt(prompt)
    if objective_quality.objective_is_near_copy(prompt, objective):
        objective = objective_quality.semantic_reinterpretation(prompt)
    assert objective == "Assess model-routing resilience and private-execution boundaries"
    assert "needs attention" not in objective.lower()


def test_hermes_sender_attribution_never_reaches_visible_objective() -> None:
    prompt = "[J|private-transport-id] testing"
    assert watcher.clean_prompt(prompt) == "testing"
    objective = watcher.objective_from_prompt(prompt)
    if objective_quality.objective_is_near_copy(prompt, objective):
        objective = objective_quality.semantic_reinterpretation(prompt)
    assert objective == objective_quality.GENERIC_CONNECTIVITY_OBJECTIVE
    assert "private-transport-id" not in objective


def test_only_start_may_create_a_live_card(tmp_path: Path) -> None:
    state_path = tmp_path / "cards.json"
    state_path.write_text('{"cards":{}}', encoding="utf-8")
    args = card_args(action="update", key="missing-card")
    with patch.object(card, "STATE_PATH", state_path), \
         patch.object(card, "LOCK_PATH", tmp_path / "cards.lock"), \
         patch.object(card, "send_card") as send, \
         patch.object(card, "edit_card") as edit:
        assert card.upsert_card(args, "running") == 1
    send.assert_not_called()
    edit.assert_not_called()
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"cards": {}}


def test_card_is_compact_semantic_and_omits_empty_sections() -> None:
    rendered = card.build_card(
        title="Verify Topic 17 delivery",
        status="running",
        model="openai-codex/gpt-5.6-sol",
        now="Tool: search_files — tracing the delivery result",
        done=[
            "Skill applied: telegram-task-flow",
            "Action completed: patch — corrected card ownership",
            "Tool result: read_file — confirmed one card owner",
            "Brain Feed — publishing current phase",
            "Verification passed: terminal — 300/300 tasks returned unique hashes",
            "Decision: keep one editable card",
        ],
        next_step="Keep working and update this card",
        blocker="None",
        updated="20:44 EDT",
    )
    body = html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>"))
    lowered = body.lower()
    assert "tool:" not in lowered
    assert "action:" not in lowered
    assert "brain feed" not in lowered
    assert "read_file" not in lowered
    assert "blocker" not in lowered
    assert "next" not in lowered
    assert "Done" in body
    milestone_rows = [line for line in body.splitlines() if line.startswith(("✓ ", "… "))]
    assert len(milestone_rows) <= 3
    assert len(body.splitlines()) <= 22
    assert max(map(len, body.splitlines())) <= card.CARD_WRAP_WIDTH
    assert len(body) < 900


def test_jaimes_ops_defaults_to_inbox_style_native_blocks() -> None:
    assert card.rich_cards_enabled("-1003589561528", "17")
    assert not card.rich_cards_enabled("-1003589561528", "19")
    rendered = card.build_rich_card(
        title="Confirm JAIMES is responsive",
        status="running",
        model="openai-codex/gpt-5.6-sol",
        route="JAIMES verified execution",
        now="Running the requested check",
        done=[
            "Received Telegram task",
            "Objective determined",
            "Model selected: openai-codex/gpt-5.6-sol",
        ],
        started_at="2026-07-20T20:00:00Z",
        updated="2026-07-20T20:00:06Z",
    )
    for fragment in (
        "<h3>JAIMES · LIVE WORK</h3>",
        "<b>Objective</b>",
        "<h4>Progress</h4>",
        "<h4>Active work</h4>",
        "<details><summary>Recent activity",
        "stage 3/6",
    ):
        assert fragment in rendered
    assert "<pre>⚙️" not in rendered


def test_ambiguous_jaimes_rich_edit_never_rewrites_as_legacy(monkeypatch) -> None:
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
        thread_id="17",
    )
    assert not result["ok"]
    assert result["delivery_indeterminate"] is True
    assert result["native_rich_message"] is True
    assert len(calls) == 1


def test_successful_numeric_benchmark_remains_complete() -> None:
    complete, sections = watcher.parse_final_sections(
        """Complete: Yes
What was done:
- Completed 3 rounds with 100 parallel tasks each.
- Verified 300/300 aggregate outputs were unique.
- Failures: 0; maximum parallel activity: 100.
- Measured p95 task latency at 10.049 ms.
Issues:
n/a
Appropriate next steps:
No action needed.
Approval needed:
n/a"""
    )
    assert complete is True
    assert any("300/300" in item for item in sections["done"])
    assert sections["issues"] == []
    assert sections["next"] == ["No action needed."]


def test_quick_readiness_final_accepts_direct_result_without_weakening_tier_three() -> None:
    source = """Complete: Yes — the response system is functioning for this test.
What was done:
- Followed the requested section order and plain-text format.
- Omitted the prohibited Model line.
- Kept the response concise and structured.
Issues:
n/a
Appropriate next steps:
n/a
Approval needed:
n/a"""
    quick_complete, quick_sections = watcher.parse_final_sections(source, delivery_tier=2)
    strict_complete, _ = watcher.parse_final_sections(source, delivery_tier=3)
    assert quick_complete is True
    assert quick_sections["done"] == ["the response system is functioning for this test."]
    assert strict_complete is False
    assert watcher.terminal_outcome_for_response(source, delivery_tier=2) == "succeeded"
    assert watcher.terminal_outcome_for_response(source, delivery_tier=3) == "partial"


def test_quick_final_with_only_formatter_metadata_fails_closed() -> None:
    complete, _ = watcher.parse_final_sections(
        """Complete: Yes
What was done:
- Followed the requested format.
- Omitted the prohibited Model line.
Issues: n/a
Appropriate next steps: n/a
Approval needed: n/a""",
        delivery_tier=2,
    )
    assert complete is False


def test_structured_final_preserves_explicit_verified_why() -> None:
    rendered = watcher.structured_final_text(
        """Complete: Yes
What was done:
- Completed 3 rounds of 100 tasks.
- Verified 300/300 outputs were unique.
- Failures: 0.
Issues:
- n/a
Appropriate next steps:
- No action needed.
Approval needed:
- n/a""",
        objective="Run a safe concurrency and delivery canary",
        model="openai-codex/gpt-5.6-sol",
        route="JAIMES local execution",
        why="verified backend execution",
    )
    body = html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>"))
    assert "Why: verified backend execution" in body.replace("\n   ", " ")
    assert "verified JAIMES execution" not in body
