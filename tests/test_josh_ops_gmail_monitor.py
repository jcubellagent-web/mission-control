from __future__ import annotations

import importlib.util
import plistlib
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "josh_ops_gmail_monitor.py"
SPEC = importlib.util.spec_from_file_location("josh_ops_gmail_monitor", MODULE_PATH)
assert SPEC and SPEC.loader
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


def test_command_is_read_only_and_counts_only() -> None:
    command = monitor.build_command("shared@example.com", 25)
    assert "--readonly" in command
    assert "--gmail-no-send" in command
    assert "--no-input" in command
    assert "--select=id" in command
    assert "mark-read" not in command


def test_result_count_accepts_supported_gog_shapes() -> None:
    assert monitor.result_count([{"id": "a"}, {"id": "b"}]) == 2
    assert monitor.result_count({"threads": [{"id": "a"}]}) == 1
    assert monitor.result_count({"unexpected": "shape"}) == 0


def test_safe_error_reason_never_returns_raw_error_text() -> None:
    raw = "invalid_grant for private-user@example.com token=secret"
    assert monitor.safe_error_reason(raw) == "authentication_refresh_required"
    assert raw not in monitor.safe_error_reason(raw)


def test_canonical_launch_agent_uses_new_read_only_monitor() -> None:
    plist_path = Path(__file__).resolve().parents[1] / "launchd" / "com.josh20.ops-gmail-monitor.plist"
    payload = plistlib.loads(plist_path.read_bytes())
    assert payload["ProgramArguments"] == [
        "/usr/bin/python3",
        "/Users/josh2.0/.openclaw/workspace/mission-control/scripts/josh_ops_gmail_monitor.py",
    ]
    assert payload["StartCalendarInterval"] == {"Hour": 7, "Minute": 25}
