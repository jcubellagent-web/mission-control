from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import agent_publish


def test_routine_monitoring_is_an_operational_observation() -> None:
    assert agent_publish.decision_kind("Crypto monitor: no trade") == (
        "operational-observation", "legacy-inference"
    )


def test_explicit_governance_wins_over_title_inference() -> None:
    assert agent_publish.decision_kind("Health check policy", "governance") == (
        "governance", "explicit"
    )
