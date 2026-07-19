from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "jaimes_completion_evidence.py"
SPEC = importlib.util.spec_from_file_location("jaimes_completion_evidence", MODULE_PATH)
assert SPEC and SPEC.loader
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)
NOW = dt.datetime(2026, 7, 18, 21, 0, tzinfo=dt.timezone.utc)


def build(cards, active):
    return subject.build_completion_evidence({"cards": cards}, {"active_cards": active}, now=NOW)


def test_empty_evidence_is_watch_not_green():
    payload = build({}, {})
    assert payload["status"] == "watch"
    assert payload["ok"] is False
    assert payload["completedRuns"] == 0


def test_exact_current_run_delivery_is_ready_without_exposing_identifiers():
    cards = {"card-private-id": {
        "status": "done",
        "work_id": "work-private-id",
        "run_id": "run-private-id",
        "task_started_at": "2026-07-18T20:30:00Z",
        "final_message_id": "3914",
        "final_delivery_verified_by": "hermes-adapter-success",
        "final_delivery_confirmed_at": "2026-07-18T20:31:00Z",
    }}
    active = {"watcher-private-id": {
        "key": "card-private-id",
        "work_id": "work-private-id",
        "ledger_run_id": "run-private-id",
        "task_started_at": "2026-07-18T20:30:00Z",
        "final_evidence_status": "current",
    }}
    payload = build(cards, active)
    assert payload["status"] == "ok"
    assert payload["ok"] is True
    assert payload["finalMessagesLinked"] == payload["finalMessagesRequired"] == 1
    rendered = json.dumps(payload)
    for private_value in ("card-private-id", "work-private-id", "run-private-id", "3914"):
        assert private_value not in rendered


def test_legacy_or_unlinked_completion_stays_watch():
    payload = build({"legacy": {"status": "done", "updated_at": "2026-07-18T20:30:00Z"}}, {})
    assert payload["status"] == "watch"
    assert payload["completedRuns"] == 1
    assert payload["identityBoundRuns"] == 0
    assert "legacy-or-unbound-completions" in payload["issues"]


def test_mismatched_watcher_identity_is_attention():
    cards = {"card": {
        "status": "done",
        "work_id": "work-current",
        "run_id": "run-current",
        "task_started_at": "2026-07-18T20:30:00Z",
        "final_message_id": "3914",
        "final_delivery_verified_by": "hermes-adapter-success",
        "final_delivery_confirmed_at": "2026-07-18T20:31:00Z",
    }}
    active = {"watcher": {
        "key": "card",
        "work_id": "work-current",
        "ledger_run_id": "run-other",
        "task_started_at": "2026-07-18T20:30:00Z",
        "final_evidence_status": "current",
    }}
    payload = build(cards, active)
    assert payload["status"] == "attention"
    assert payload["mismatches"] == 1
    assert payload["ok"] is False


def test_old_completions_are_outside_the_live_window():
    payload = build({"old": {"status": "done", "updated_at": "2026-07-16T20:30:00Z"}}, {})
    assert payload["completedRuns"] == 0
    assert payload["status"] == "watch"
