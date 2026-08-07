from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_update_module():
    path = ROOT / "scripts" / "update_mission_control.py"
    spec = importlib.util.spec_from_file_location("update_mission_control_finops_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_all_subscription_variants_roll_into_fixed_provider_cost() -> None:
    module = load_update_module()
    budgets = {
        "providers": [
            {"id": "codex", "budgetType": "subscription", "fixedMonthlyUsd": 200},
            {"id": "gemini", "budgetType": "subscription", "monthlyCapUsd": 19.99},
            {"id": "xai", "budgetType": "premium-subscription", "fixedMonthlyUsd": 30},
            {"id": "ollama", "budgetType": "annual-subscription", "fixedMonthlyUsd": 16.67},
        ]
    }

    rows = module.build_provider_usage_breakdown([], budgets)
    fixed = {row["id"]: row["fixedMonthlyUsd"] for row in rows}

    assert fixed == {"codex": 200.0, "gemini": 19.99, "xai": 30.0, "ollama": 16.67}


def test_shared_finops_contract_excludes_account_email() -> None:
    update_source = (ROOT / "scripts" / "update_mission_control.py").read_text(encoding="utf-8")
    ui_source = (ROOT / "v2-react" / "src" / "main.tsx").read_text(encoding="utf-8")
    types_source = (ROOT / "v2-react" / "src" / "types.ts").read_text(encoding="utf-8")

    assert '"accountEmail": usage.get(' not in update_source
    assert "provider?.accountEmail" not in ui_source
    assert "accountEmail?: string" not in types_source


def test_ui_recognizes_qualified_subscription_types() -> None:
    ui_source = (ROOT / "v2-react" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert '.toLowerCase().includes("subscription")' in ui_source
    assert 'billingMode || "").toLowerCase().includes("subscription")' in ui_source


def test_provider_breakdown_projects_recent_activity_without_content() -> None:
    module = load_update_module()
    rows = module.build_provider_usage_breakdown([{
        "name": "glm-5.2:cloud",
        "source": "ollama",
        "callsWeekly": 4,
        "callsLast5m": 2,
        "callsLast30m": 3,
        "callsLast2h": 4,
        "lastActivityAt": "2026-08-06T01:00:00Z",
    }], {"providers": [{"id": "ollama", "budgetType": "annual-subscription", "fixedMonthlyUsd": 16.67}]})

    ollama = next(row for row in rows if row["id"] == "ollama")
    assert ollama["callsLast5m"] == 2
    assert ollama["callsLast30m"] == 3
    assert ollama["callsLast2h"] == 4
    assert ollama["lastActivityAt"] == "2026-08-06T01:00:00Z"


def test_ui_heat_uses_live_request_windows_and_jobs_consume_live_leases() -> None:
    ui_source = (ROOT / "v2-react" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert "callsLast5m" in ui_source
    assert "providerLiveActivity" in ui_source
    assert "calls30m - calls5m" in ui_source
    assert "calls2h - calls30m" in ui_source
    assert "costPressure" not in ui_source
    assert "Math.exp(-(ageMinutes - 2) / 18)" not in ui_source
    assert "activeWorks={state.workHot?.activeWorks}" in ui_source
    assert "timeValue(work.leaseUntil) > clockNow.getTime()" in ui_source
    assert "is-live-runtime" in ui_source


def test_live_work_rotation_counter_has_prominent_visual_hierarchy() -> None:
    css = (ROOT / "v2-react" / "src" / "styles.css").read_text(encoding="utf-8")
    counter = css.split(".brain-hero.is-flight-deck .agent-rotation-counter", 1)[1].split("}", 1)[0]

    assert "font-size: 20px" in counter
    assert "min-height: 34px" in counter
    assert "border: 2px" in counter
    assert "align-self: stretch" in counter
    assert "width: 100%" in counter
    stack = css.split(".brain-hero.is-flight-deck .agent-activity-stack", 1)[1].split("}", 1)[0]
    assert "align-items: stretch" in stack
