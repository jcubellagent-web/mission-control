from __future__ import annotations

import argparse
import importlib.util
import io
from pathlib import Path


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


def test_gmail_objective_requires_email_context() -> None:
    assert watcher.objective_from_prompt("Please triage my Gmail inbox") == "Triage Gmail inbox"
    assert watcher.objective_from_prompt("Please review the Inbox workflow") != "Triage Gmail inbox"


def test_unmatched_objective_is_operator_paraphrase() -> None:
    prompt = "Could you examine this unusual delegated task and make the results easy to use?"
    objective = watcher.objective_from_prompt(prompt)
    assert objective.startswith("Assess ")
    assert objective != prompt.rstrip("?")
    assert len(objective.split()) <= 8


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
    monkeypatch.setattr(watcher, "run_cmd", lambda *args, **kwargs: calls.append("header-card") or {"ok": True})
    monkeypatch.setattr(watcher, "publish_josh", lambda *args, **kwargs: calls.append("publish"))


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
    }
    assert published
