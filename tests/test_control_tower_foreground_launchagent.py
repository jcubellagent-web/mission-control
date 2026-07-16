from __future__ import annotations

import plistlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    with (ROOT / "launchd" / name).open("rb") as handle:
        return plistlib.load(handle)


def test_foreground_guard_launchagent_is_lightweight_and_frequent() -> None:
    payload = load("com.josh20.control-tower-foreground-guard.plist")

    assert payload["Label"] == "com.josh20.control-tower-foreground-guard"
    assert payload["ProgramArguments"] == [
        "/usr/bin/python3",
        "/Users/josh2.0/.openclaw/workspace/mission-control/scripts/control_tower_foreground.py",
        "ensure",
        "--repair",
    ]
    assert payload["RunAtLoad"] is True
    assert 15 <= payload["StartInterval"] <= 60
    assert "mission_control_kiosk_watchdog.py" not in " ".join(payload["ProgramArguments"])
    assert payload["StandardOutPath"] != payload["StandardErrorPath"]


def test_deep_watchdog_stays_on_slower_cadence() -> None:
    payload = load("com.josh20.mission-control-kiosk-watchdog.plist")

    assert payload["Label"] == "com.josh20.mission-control-kiosk-watchdog"
    assert "mission_control_kiosk_watchdog.py" in " ".join(payload["ProgramArguments"])
    assert payload["RunAtLoad"] is True
    assert payload["StartInterval"] >= 300
