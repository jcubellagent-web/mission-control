from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mission_control_visual_canaries.py"
SPEC = importlib.util.spec_from_file_location("mission_control_visual_canaries_policy", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_noncritical_source_drift_is_advisory() -> None:
    result = MODULE.summarize_canary_checks([
        MODULE.status(False, "Historical UI shape", "source pattern moved", severity="high"),
        MODULE.status(True, "Live runtime", "verified", severity="critical"),
    ])

    assert result["ok"] is True
    assert result["status"] == "advisory"
    assert len(result["advisory"]) == 1
    assert result["blocking"] == []


def test_critical_canary_still_fails_release_health() -> None:
    result = MODULE.summarize_canary_checks([
        MODULE.status(False, "Live runtime", "browser render failed", severity="critical"),
    ])

    assert result["ok"] is False
    assert result["status"] == "attention"
    assert len(result["blocking"]) == 1
