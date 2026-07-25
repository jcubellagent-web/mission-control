from __future__ import annotations

import plistlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_daily_active_canary_is_idle_guarded_and_failure_quiet() -> None:
    path = ROOT / "launchd" / "ai.control-tower.interaction-active-canary.plist"
    with path.open("rb") as handle:
        payload = plistlib.load(handle)
    args = payload["ProgramArguments"]
    assert payload["StartCalendarInterval"] == {"Hour": 4, "Minute": 20}
    assert "--active-canary" in args
    assert "--idle-only" in args
    assert "--refresh-dashboard" in args
    assert all("telegram" not in str(value).lower() for value in payload.values())


def test_installer_manages_passive_and_daily_canary_agents() -> None:
    source = (ROOT / "scripts" / "install_interaction_capability_watch.sh").read_text()
    assert "ai.control-tower.interaction-capabilities" in source
    assert "ai.control-tower.interaction-active-canary" in source
    assert "BACKUP_ROOT" in source
