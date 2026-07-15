from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mission_control_runtime_layout_check.py"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "control-tower-live.ci.json"
WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "mission-control-regression.yml"
SPEC = importlib.util.spec_from_file_location("mission_control_runtime_layout_check", MODULE_PATH)
assert SPEC and SPEC.loader
runtime_layout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_layout)


class FakeChromium:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def launch(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakePlaywright:
    def __init__(self, outcomes: list[object]) -> None:
        self.chromium = FakeChromium(outcomes)


def missing_browser_error() -> RuntimeError:
    return RuntimeError(
        "BrowserType.launch: Executable doesn't exist. "
        "Please run the following command to download new browsers: playwright install"
    )


def test_ci_live_data_fixture_satisfies_canonical_contract() -> None:
    result = runtime_layout.check_control_tower_json(FIXTURE_PATH)

    assert result["name"] == "live-data-json"
    assert result["state"] == "pass"
    assert runtime_layout.internal_text_leaks(FIXTURE_PATH.read_text()) == []


def test_release_workflow_stages_and_validates_same_live_data_fixture() -> None:
    workflow = WORKFLOW_PATH.read_text()

    assert "cp tests/fixtures/control-tower-live.ci.json data/control-tower-live.json" in workflow
    assert "--data data/control-tower-live.json" in workflow


@pytest.mark.parametrize("missing_field", ["runtimeLayout", "sourceUpdatedAt"])
def test_ci_live_data_fixture_keeps_required_fields_strict(
    missing_field: str,
    tmp_path: Path,
) -> None:
    payload = json.loads(FIXTURE_PATH.read_text())
    payload.pop(missing_field)
    candidate = tmp_path / "control-tower-live.json"
    candidate.write_text(json.dumps(payload))

    result = runtime_layout.check_control_tower_json(candidate)

    assert result["state"] == "fail"
    assert missing_field in result["detail"]


def test_uses_bundled_playwright_browser_without_fallback() -> None:
    browser = object()
    playwright = FakePlaywright([browser])

    launched, evidence = runtime_layout.launch_playwright_browser(playwright)

    assert launched is browser
    assert evidence == {
        "engine": "playwright-chromium",
        "browser": "bundled Chromium",
        "browserFallback": {"used": False},
    }
    assert playwright.chromium.calls == [{"headless": True}]


def test_missing_bundled_browser_retries_supported_chrome_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTROL_TOWER_BROWSER", raising=False)
    browser = object()
    playwright = FakePlaywright([missing_browser_error(), browser])

    launched, evidence = runtime_layout.launch_playwright_browser(playwright)

    assert launched is browser
    assert evidence["browser"] == "Google Chrome"
    assert evidence["browserFallback"] == {
        "used": True,
        "from": "bundled Chromium",
        "to": "Chrome channel",
        "reason": "Playwright-managed browser executable was not installed",
    }
    assert playwright.chromium.calls == [
        {"headless": True},
        {"headless": True, "channel": "chrome"},
    ]


def test_channel_failure_retries_detected_browser_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTROL_TOWER_BROWSER", raising=False)
    executable = "/Applications/Chromium.app/Contents/MacOS/Chromium"
    monkeypatch.setattr(runtime_layout, "browser_candidates", lambda: [executable])
    browser = object()
    playwright = FakePlaywright([
        missing_browser_error(),
        RuntimeError("Chrome channel is not installed"),
        browser,
    ])

    launched, evidence = runtime_layout.launch_playwright_browser(playwright)

    assert launched is browser
    assert evidence["browser"] == "Chromium"
    assert evidence["browserFallback"]["to"] == "installed executable"
    assert playwright.chromium.calls[-1] == {"headless": True, "executable_path": executable}


def test_non_installation_launch_failure_remains_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTROL_TOWER_BROWSER", raising=False)
    failure = RuntimeError("BrowserType.launch: browser process crashed")
    playwright = FakePlaywright([failure])

    with pytest.raises(RuntimeError, match="browser process crashed"):
        runtime_layout.launch_playwright_browser(playwright)

    assert playwright.chromium.calls == [{"headless": True}]


def test_missing_bundled_and_system_browsers_remains_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTROL_TOWER_BROWSER", raising=False)
    monkeypatch.setattr(runtime_layout, "browser_candidates", lambda: [])
    playwright = FakePlaywright([
        missing_browser_error(),
        RuntimeError("Chrome channel is not installed"),
    ])

    with pytest.raises(RuntimeError, match="installed-browser fallback failed"):
        runtime_layout.launch_playwright_browser(playwright)

    assert playwright.chromium.calls == [
        {"headless": True},
        {"headless": True, "channel": "chrome"},
    ]
