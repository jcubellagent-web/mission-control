from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "update_mission_control.py"
SPEC = importlib.util.spec_from_file_location("update_mission_control_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def test_live_projection_keeps_only_rendered_hot_path_fields() -> None:
    cron_row = {
        "name": "Deep release QA",
        "status": "error",
        "runStatus": "missed",
        "qaJobId": "nightly-control-tower-suite",
        "qaMeta": True,
        "auditBlob": "full history remains in dashboard-data.json",
    }
    today_row = {field: f"today-{field}" for field in subject.TODAY_JOB_LIVE_FIELDS}
    today_row.update({
        "occurrenceId": "qa@2026-07-18T0247",
        "name": "Deep release QA",
        "outcome": "broken",
        "runStatus": "failed",
        "evidence": {"status": "failed"},
        "auditBlob": "full history remains in dashboard-data.json",
    })
    dashboard = {
        "generatedAt": "2026-07-18T16:00:00Z",
        "crons": [cron_row],
        "todayJobs": [today_row],
        "sharedOperatingLayer": {
            "status": "ok",
            "updatedAt": "2026-07-18T16:00:00Z",
            "counts": {"open": 0},
            "openHandoffs": [],
            "auditTrail": ["not rendered"],
        },
        "runtimeLayout": {
            "ok": True,
            "status": "ok",
            "checkedAt": "2026-07-18T16:00:00Z",
            "summary": "fits",
            "measurements": {"large": "not rendered"},
        },
        "capabilityInventory": {
            "updatedAt": "2026-07-18T16:00:00Z",
            "nodes": [{
                "id": "josh2",
                "name": "Josh 2.0",
                "host": "josh2",
                "agent": "josh2",
                "openclawCli": {"available": True, "version": "private detail"},
                "hermesCli": {"available": False, "version": "private detail"},
                "geminiCli": {"available": True, "version": "private detail"},
                "codexCli": {"available": True, "version": "private detail"},
                "inventory": ["not rendered"],
            }],
        },
    }

    live = subject.build_live_dashboard(dashboard)

    assert "crons" not in live
    assert set(live["todayJobs"][0]) == set(subject.TODAY_JOB_LIVE_FIELDS)
    assert set(live["sharedOperatingLayer"]) == set(subject.SHARED_OPERATING_LAYER_LIVE_FIELDS)
    assert set(live["runtimeLayout"]) == set(subject.RUNTIME_LAYOUT_LIVE_FIELDS)
    assert set(live["capabilityInventory"]["nodes"][0]) == set(subject.CAPABILITY_NODE_IDENTITY_FIELDS) | set(subject.CAPABILITY_NODE_RUNTIME_FIELDS)
    assert live["capabilityInventory"]["nodes"][0]["openclawCli"] == {"available": True}
    assert live["capabilityInventory"]["nodes"][0]["hermesCli"] == {"available": False}
    assert live["capabilityInventory"]["nodes"][0]["geminiCli"] == {"available": True}
    assert live["capabilityInventory"]["nodes"][0]["codexCli"] == {"available": True}
    assert dashboard["crons"][0]["auditBlob"].startswith("full history")
    assert dashboard["todayJobs"][0]["auditBlob"].startswith("full history")


def test_live_projection_stays_below_hot_path_budget_without_mutating_full_data() -> None:
    large_text = "x" * 2_000
    dashboard = {
        "generatedAt": "2026-07-18T16:00:00Z",
        "modelUsage": {"ledger": "m" * 32_000},
        "crons": [{
            "name": f"Job {index}",
            "agent": "JOSH 2.0",
            "status": "ok",
            "runStatus": "done",
            "description": "Scheduled check",
            "schedule": "Every 5 min",
            "source": "launchd",
            "auditBlob": large_text,
        } for index in range(120)],
        "todayJobs": [{
            "occurrenceId": f"job-{index}",
            "name": f"Occurrence {index}",
            "owner": "JOSH 2.0",
            "outcome": "complete",
            "runStatus": "done",
            "scheduledAt": "2026-07-18T12:00:00-04:00",
            "evidence": {"status": "done"},
            "auditBlob": large_text,
        } for index in range(140)],
        "sharedOperatingLayer": {
            "status": "ok",
            "counts": {"open": 0},
            "openHandoffs": [],
            "history": [large_text] * 80,
        },
        "runtimeLayout": {
            "ok": True,
            "status": "ok",
            "summary": "fits",
            "measurements": [large_text] * 30,
        },
        "capabilityInventory": {
            "updatedAt": "2026-07-18T16:00:00Z",
            "nodes": [{
                "id": f"node-{index}",
                "openclawCli": {"available": True, "detail": large_text},
                "inventory": large_text,
            } for index in range(20)],
        },
    }

    full_size = len((json.dumps(dashboard, indent=2, ensure_ascii=True, default=str) + "\n").encode("utf-8"))
    live = subject.build_live_dashboard(dashboard)
    live_size = len((json.dumps(live, indent=2, ensure_ascii=True, default=str) + "\n").encode("utf-8"))

    assert full_size > 500_000
    assert live_size <= 225_000
    assert dashboard["sharedOperatingLayer"]["history"]


def test_qa_action_metadata_only_marks_pure_aggregate_alerts() -> None:
    meta = {"name": "Deep release QA", "qaJobId": "nightly-control-tower-suite", "qaMeta": True}
    operational = {"name": "Telegram delivery", "qaMeta": False}

    assert subject._qa_action_metadata([meta]) == {
        "qaJobIds": ["nightly-control-tower-suite"],
        "qaMetaOnly": True,
    }
    assert subject._qa_action_metadata([meta, operational])["qaMetaOnly"] is False


def test_personal_codex_preserves_explicit_action_classification() -> None:
    normalized = subject.normalize_personal_codex({
        "actionRequired": [
            {
                "title": "Re-run validation",
                "priority": "medium",
                "kind": "system",
                "requiresApproval": False,
                "detail": "A local check needs another pass.",
            },
            {
                "title": "Approve private account change",
                "priority": "high",
                "kind": "approval",
                "requiresApproval": True,
            },
        ],
    }, "2026-07-18T16:00:00Z")

    assert normalized["actionRequired"][0]["kind"] == "system"
    assert normalized["actionRequired"][0]["requiresApproval"] is False
    assert normalized["actionRequired"][0]["detail"] == "A local check needs another pass."
    assert normalized["actionRequired"][1]["kind"] == "approval"
    assert normalized["actionRequired"][1]["requiresApproval"] is True
