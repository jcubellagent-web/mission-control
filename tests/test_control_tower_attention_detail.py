from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_mission_control.py"
SPEC = importlib.util.spec_from_file_location("update_mission_control_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
mission_control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mission_control)


def layer_with_blocked_task(task: dict) -> dict:
    return {
        "counts": {},
        "blockedEvents": [],
        "attentionHandoffs": [],
        "tasks": {"blocked": [task], "approvalNeeded": []},
    }


def test_blocked_task_attention_prefers_summary_over_status() -> None:
    item = mission_control.shared_layer_attention_item(
        layer_with_blocked_task(
            {
                "title": "Repair Telegram delivery",
                "status": "blocked",
                "privacy": "dashboard-safe",
                "summary": "Waiting for the verified gateway listener to return.",
            }
        )
    )

    assert item["title"] == "Repair Telegram delivery"
    assert item["detail"] == "Waiting for the verified gateway listener to return."


def test_blocked_task_attention_uses_latest_note_when_summary_is_empty() -> None:
    item = mission_control.shared_layer_attention_item(
        layer_with_blocked_task(
            {
                "title": "Repair Telegram delivery",
                "status": "blocked",
                "privacy": "dashboard-safe",
                "notes": [
                    {"note": "Newest actionable blocker."},
                    {"note": "Older blocker."},
                ],
            }
        )
    )

    assert item["detail"] == "Newest actionable blocker."


def test_blocked_task_attention_prefers_newest_note_over_retained_summary() -> None:
    item = mission_control.shared_layer_attention_item(
        layer_with_blocked_task(
            {
                "title": "Repair Telegram delivery",
                "status": "blocked",
                "privacy": "dashboard-safe",
                "summary": "Old blocker from the first attempt.",
                "notes": [
                    {"status": "blocked", "note": "Current blocker from the retry."},
                    {"status": "blocked", "note": "Old blocker from the first attempt."},
                ],
            }
        )
    )

    assert item["detail"] == "Current blocker from the retry."


def test_blocked_private_task_attention_never_publishes_summary_or_note() -> None:
    for privacy in ("sensitive-account", "agent-private", None):
        item = mission_control.shared_layer_attention_item(
            layer_with_blocked_task(
                {
                    "title": "Private account task",
                    "status": "blocked",
                    "privacy": privacy,
                    "summary": "Synthetic private summary SECRET_VALUE",
                    "notes": [{"note": "Synthetic private note SECRET_VALUE"}],
                }
            )
        )

        assert item["detail"] == "A private shared task is blocked; review it in the secure task queue."
        assert "SECRET_VALUE" not in item["detail"]
