from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_x_skill_and_runbook_define_the_same_resilient_ladder() -> None:
    skill = (ROOT / "agent-skills" / "x-trading-signal-search" / "SKILL.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "x-intelligence-runbook.md").read_text(encoding="utf-8")

    for text in (skill, runbook):
        lowered = text.lower()
        assert "grok" in lowered
        assert "authenticated x ui" in lowered
        assert "public-web" in lowered or "public web" in lowered
        assert "primary source" in lowered
        assert "session canary" in lowered


def test_machine_readable_x_state_keeps_api_and_subscription_separate() -> None:
    state = json.loads((ROOT / "config" / "x-intelligence-provider-state.json").read_text(encoding="utf-8"))
    watchlist = json.loads((ROOT / "config" / "x-intelligence-watchlist.json").read_text(encoding="utf-8"))

    assert state["xaiApi"]["enabled"] is False
    assert state["grokSubscription"]["enabled"] is True
    assert state["grokSubscription"]["allowanceSource"] == "data/modelUsage.json codexbarLimits.xai"
    assert state["grokSubscription"]["exhaustionFallback"] == "authenticated-x-ui"
    assert state["xSignal"]["routeOrder"] == [
        "grok-subscription", "authenticated-x-ui", "forwarded-x-links", "public-web-primary-sources"
    ]
    assert watchlist["policy"]["grok_exhaustion_fallback"] == "authenticated-x-ui"
