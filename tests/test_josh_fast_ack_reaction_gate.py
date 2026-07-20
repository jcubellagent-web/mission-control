from __future__ import annotations

import argparse
import importlib.util
import io
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "josh_telegram_fast_ack.py"
SPEC = importlib.util.spec_from_file_location("josh_telegram_fast_ack_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
watcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watcher)


def event() -> dict[str, str]:
    return {
        "session_id": "session-1",
        "run_id": "message:902",
        "message_id": "902",
        "ts": "2026-07-15T00:00:00Z",
        "prompt": "Please verify the Inbox workflow.",
    }


def inbox_meta() -> dict[str, str]:
    return {
        "telegram_chat_id": watcher.CONTROL_CENTER_CHAT_ID,
        "telegram_thread_id": "1",
        "telegram_session_key": "agent:main:telegram:group:test:topic:1",
    }


def test_inbox_health_request_is_not_misclassified_as_gmail() -> None:
    prompt = "Please run a quick Inbox health check and confirm model routing, Brain Feed, and gateway status."
    assert watcher.objective_from_prompt(prompt) == "Verify Inbox routing and health"


def test_trailing_no_change_constraint_cannot_replace_health_objective() -> None:
    prompt = (
        "Read-only acceptance check: assess current Telegram health and give me three concrete findings. "
        "Make no changes."
    )
    assert watcher.current_request_text(prompt).startswith("assess current Telegram health")
    assert watcher.current_request_text(prompt) != "Make no changes"
    assert watcher.objective_from_prompt(prompt) == "Assess Telegram health read-only"


def test_gmail_objective_requires_email_context() -> None:
    assert watcher.objective_from_prompt("Please triage my Gmail inbox") == "Triage Gmail inbox"
    assert watcher.objective_from_prompt("Please review the Inbox workflow") != "Triage Gmail inbox"


def test_unmatched_objective_is_operator_paraphrase() -> None:
    prompt = "Could you examine this unusual delegated task and make the results easy to use?"
    objective = watcher.objective_from_prompt(prompt)
    assert objective.startswith("Assess ")
    assert objective != prompt.rstrip("?")
    assert len(objective.split()) <= watcher.OBJECTIVE_MAX_WORDS


def test_objective_copy_complaint_maps_to_interpreted_intent() -> None:
    prompt = "The Telegram and Control Tower objective is just a copy of my message. Interpret it in your own words first."
    assert watcher.objective_from_prompt(prompt) == "Make agent task objectives reflect interpreted intent"


def test_near_copy_gate_defers_visible_surfaces(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(watcher, "send_chat_action", lambda *args, **kwargs: calls.append("typing"))
    monkeypatch.setattr(watcher, "send_message_draft", lambda *args, **kwargs: calls.append("draft"))
    monkeypatch.setattr(watcher, "objective_from_prompt", lambda _prompt: "Inspect JOSHeX after the update")
    monkeypatch.setattr(watcher, "objective_is_near_copy", lambda _prompt, _objective: True)
    monkeypatch.setattr(watcher, "semantic_reinterpretation", lambda _prompt: "")
    monkeypatch.setattr(watcher, "auto_route_for_prompt", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must wait before routing")))
    monkeypatch.setattr(watcher, "publish_josh", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not publish")))
    result = watcher.send_ack(event(), watcher.DEFAULT_MODEL, dry_run=True, meta=inbox_meta())
    assert result["status"] == "awaiting-objective-interpretation"
    assert result["objective"] == ""
    assert result["requires_objective_interpretation"] is True


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Testing the new JOSHeX changes", "Verify the new JOSHeX changes work as intended"),
        ("Please test the new JAIMES changes", "Verify the new JAIMES changes work as intended"),
        ("Please inspect JOSHeX after the update", "Inspect JOSHeX after the update"),
        ("Please review JAIMES response formatting", "Audit JAIMES response formatting"),
        (
            "Please sync Josh 2.0 and JAIMES shared state",
            "Synchronize Josh 2.0 and JAIMES shared state",
        ),
        (
            "Could you please test the new JOSHeX changes?",
            "Verify the new JOSHeX changes work as intended",
        ),
        (
            "I want you to test the new JOSHeX changes",
            "Verify the new JOSHeX changes work as intended",
        ),
        (
            "Please verify the new JOSHeX changes",
            "Verify the new JOSHeX changes work as intended",
        ),
        ("Please test Josh 2.0", "Verify Josh 2.0 operates correctly"),
        (
            "Please test the new JOSHeX changes across Telegram and Control Tower",
            "Verify new JOSHeX changes across Telegram and Control Tower work as intended",
        ),
        (
            "Why did the JAIMES response change?",
            "Investigate why the JAIMES response changed",
        ),
        (
            "What did the update change in JOSHeX?",
            "Explain what the update changed in JOSHeX",
        ),
        (
            "Are the new JOSHeX changes working?",
            "Verify whether the new JOSHeX changes work as intended",
        ),
        (
            "Is the new JOSHeX behavior correct?",
            "Verify whether the new JOSHeX behavior works as intended",
        ),
        (
            "Does the new JOSHeX workflow work?",
            "Verify whether the new JOSHeX workflow works as intended",
        ),
        (
            "Please test the new JOSHeX changes so that the task header stays specific",
            "Verify the new JOSHeX changes so that the task header stays specific",
        ),
        (
            "Could you please test the new JOSHeX changes so I can trust the header?",
            "Verify the new JOSHeX changes so I can trust the header",
        ),
        (
            "Please validate the new JOSHeX changes and make sure the live card completes",
            "Verify the new JOSHeX changes and make sure the live card completes",
        ),
        (
            "Was the new JOSHeX behavior correct?",
            "Verify whether the new JOSHeX behavior works as intended",
        ),
        (
            "Have the new JOSHeX changes worked?",
            "Verify whether the new JOSHeX changes work as intended",
        ),
    ],
)
def test_explicit_action_and_target_outrank_agent_topic_buckets(prompt: str, expected: str) -> None:
    objective = watcher.objective_from_prompt(prompt)
    assert objective == expected
    assert objective != "Sync agent ecosystem state"
    assert len(objective) <= 80


def test_pasted_card_agent_names_do_not_override_current_request_objective() -> None:
    prompt = """Objective:
Sync agent ecosystem state
Model: codex/gpt-5.6-terra

Current user request: Please test the new JOSHeX changes
"""
    assert watcher.objective_from_prompt(prompt) == "Verify the new JOSHeX changes work as intended"


def test_generic_settings_text_does_not_invent_a_jaimes_objective() -> None:
    objective = watcher.objective_from_prompt("The settings changed unexpectedly")
    assert objective == "Handle settings changed unexpectedly"
    assert objective != "Tune JAIMES instruction-following settings"


def test_media_and_pasted_status_identifiers_do_not_leak_into_objective() -> None:
    prompt = """[media attached: media://inbound/private-card-id] (image/jpeg)
Objective:
Sync agent ecosystem state

Current user request: Could you please test the new JOSHeX changes?
"""
    objective = watcher.objective_from_prompt(prompt)
    assert objective == "Verify the new JOSHeX changes work as intended"
    assert "media" not in objective.lower()
    assert "private-card-id" not in objective


def patch_post_reaction_path(monkeypatch, calls: list[str], *, live_cards: bool) -> None:
    monkeypatch.setattr(watcher, "send_chat_action", lambda *args, **kwargs: calls.append("typing"))
    monkeypatch.setattr(watcher, "send_message_draft", lambda *args, **kwargs: calls.append("draft"))
    monkeypatch.setattr(watcher, "auto_route_for_prompt", lambda *args, **kwargs: calls.append("route") or {
        "model": "Gemini Flash",
        "route": "luna",
        "route_plan": {"routeId": "luna"},
    })
    monkeypatch.setattr(watcher, "skill_for_prompt", lambda *args, **kwargs: {"id": "", "label": ""})
    monkeypatch.setattr(watcher, "is_hold_request", lambda *args, **kwargs: False)
    monkeypatch.setattr(watcher, "live_cards_enabled", lambda *args, **kwargs: live_cards)
    monkeypatch.setattr(
        watcher,
        "run_cmd",
        lambda *args, **kwargs: calls.append("header-card") or {
            "ok": True,
            "stdout": '{"ok":true,"header_message_id":101,"message_id":102}',
        },
    )
    monkeypatch.setattr(watcher, "publish_josh", lambda *args, **kwargs: calls.append("publish") or True)


def test_exact_inbox_reacts_before_typing_routing_and_card(monkeypatch) -> None:
    calls: list[str] = []
    outcomes = iter([False, True])

    def react(*args, **kwargs):
        calls.append("reaction")
        return next(outcomes)

    monkeypatch.setattr(watcher, "send_message_reaction", react)
    monkeypatch.setattr(watcher.time, "sleep", lambda *args, **kwargs: None)
    patch_post_reaction_path(monkeypatch, calls, live_cards=True)

    result = watcher.send_ack(event(), watcher.DEFAULT_MODEL, meta=inbox_meta())

    assert result["ok"] is True
    assert result["reaction_ok"] is True
    assert calls[:2] == ["reaction", "reaction"]
    assert calls.index("typing") > 1
    assert calls.index("route") > 1
    assert calls.index("header-card") > 1


def test_exact_inbox_reaction_failure_emits_nothing_else(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        watcher,
        "send_message_reaction",
        lambda *args, **kwargs: calls.append("reaction") or False,
    )
    monkeypatch.setattr(watcher.time, "sleep", lambda *args, **kwargs: None)
    patch_post_reaction_path(monkeypatch, calls, live_cards=True)

    result = watcher.send_ack(event(), watcher.DEFAULT_MODEL, meta=inbox_meta())

    assert result["ok"] is False
    assert result["status"] == "reaction-failed"
    assert result["reaction_ok"] is False
    assert calls == ["reaction", "reaction"]


def test_non_inbox_reaction_remains_best_effort(monkeypatch) -> None:
    calls: list[str] = []
    meta = {**inbox_meta(), "telegram_thread_id": "17"}
    monkeypatch.setattr(
        watcher,
        "send_message_reaction",
        lambda *args, **kwargs: calls.append("reaction") or False,
    )
    patch_post_reaction_path(monkeypatch, calls, live_cards=False)

    result = watcher.send_ack(event(), watcher.DEFAULT_MODEL, meta=meta)

    assert result["ok"] is True
    assert result["reaction_ok"] is False
    assert calls[0] == "reaction"
    assert "typing" in calls
    assert "route" in calls


def test_claim_stops_before_coordinator_when_required_reaction_fails(monkeypatch) -> None:
    monkeypatch.setattr(watcher.sys, "stdin", io.StringIO("Do the Inbox work"))
    monkeypatch.setattr(watcher, "send_ack", lambda *args, **kwargs: {
        "ok": False,
        "status": "reaction-failed",
        "reaction_ok": False,
        "key": "message-key",
    })
    monkeypatch.setattr(
        watcher,
        "run_cmd",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("coordinator must not run")),
    )
    published: list[tuple] = []
    monkeypatch.setattr(watcher, "publish_josh", lambda *args, **kwargs: published.append(args))
    args = argparse.Namespace(
        chat_id=watcher.CONTROL_CENTER_CHAT_ID,
        thread_id="1",
        session_key="session-key",
        run_id="message:902",
        message_id="902",
        dry_run=False,
    )

    result = watcher.claim_inbox(args)

    assert result == {
        "ok": False,
        "status": "reaction-failed",
        "reaction_ok": False,
        "key": "message-key",
        "card_start_ok": False,
        "header_message_id": "",
        "live_message_id": "",
        "surface_indeterminate": False,
    }
    assert published
