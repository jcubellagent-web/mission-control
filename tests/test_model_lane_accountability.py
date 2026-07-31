from __future__ import annotations

import argparse
import datetime as dt
import json

from scripts import agent_route
from scripts import update_mission_control as dashboard


def test_route_telemetry_records_glm_eligibility_and_bypass(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_route, "ROUTE_TELEMETRY_PATH", tmp_path / "routes.jsonl")
    monkeypatch.setattr(agent_route, "ollama_live_allowance_status", lambda: (True, "Ollama live allowance has 99.7% remaining"))
    args = argparse.Namespace(
        task_type="technical-analysis", capability=[], privacy="dashboard-safe", requester="joshex",
        priority="normal", title="Synthetic", objective="Synthetic", queue_duration_ms=None,
        memory_duration_ms=None, tool_duration_ms=None, model_duration_ms=None,
    )
    route = agent_route.annotate_glm_accountability(args, {"provider": "gemini", "model": "gemini-test", "reason": "manual comparator"}, False)
    result = {"agent": "joshex", "approval": "none", "needsApproval": False, "modelRoute": route}
    record = agent_route.append_route_telemetry(args, result, 3)
    assert record["glmEligible"] is True
    assert record["glmSelected"] is False
    assert record["glmBypassReason"] == "manual comparator"
    assert "Synthetic" not in (tmp_path / "routes.jsonl").read_text()


def test_receipts_reconcile_usage_and_latest_disposition(monkeypatch, tmp_path):
    now = dt.datetime(2026, 7, 31, 12, tzinfo=dt.timezone.utc)
    receipts = tmp_path / "receipts.jsonl"
    receipts.write_text("\n".join([
        json.dumps({"event": "execution", "receiptId": "r1", "recordedAt": "2026-07-31T10:00:00Z", "provider": "ollama", "model": "glm-5.2:cloud", "outcome": "success", "inputTokens": 100, "outputTokens": 50, "durationMs": 900, "canary": False, "integrationDisposition": "pending"}),
        json.dumps({"event": "disposition", "receiptId": "r1", "recordedAt": "2026-07-31T11:00:00Z", "integrationDisposition": "integrated", "integrationReasonCode": "used"}),
    ]) + "\n")
    monkeypatch.setattr(dashboard, "MODEL_LANE_RECEIPTS_PATH", receipts)
    summary = dashboard.build_model_lane_receipt_summary(now)
    assert summary["modelRows"][0]["totalTokens"] == 150
    assert summary["executions"][0]["integrationDisposition"] == "integrated"


def test_surplus_alert_is_watch_signal_not_action_required(monkeypatch, tmp_path):
    routes = tmp_path / "routes.jsonl"
    routes.write_text("\n".join(json.dumps({"timestamp": "2026-07-31T10:00:00Z", "glmEligible": True, "glmSelected": False}) for _ in range(5)) + "\n")
    monkeypatch.setattr(dashboard, "AGENT_ROUTE_TELEMETRY_PATH", routes)
    governance = dashboard.build_ollama_governance(
        {"executions": []},
        {"usageWindows": [{"label": "Weekly", "remainingPercent": 99.7}]},
        dt.datetime(2026, 7, 31, 12, tzinfo=dt.timezone.utc),
    )
    assert governance["surplusAlert"] is True
    assert governance["eligibleBypasses"] == 5
    assert governance["targets"]["coveragePct"] == 80
