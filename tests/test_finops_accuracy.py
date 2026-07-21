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
