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


def valid_kiosk_legibility_measurements() -> dict[str, object]:
    return {
        "pageOverflowX": 0,
        "pageOverflowY": 0,
        "liveWork": {
            "objectives": [{"fontSize": 24, "clipped": False}],
            "names": [{"fontSize": 17, "clipped": False}],
            "descriptions": [{"fontSize": 12.5, "clipped": False}],
            "secondary": [{"fontSize": 10.5, "clipped": False}],
        },
        "finops": {
            "bodyPresent": True,
            "bodyBottomDeadSpace": 9,
            "bodyBottomOvershoot": 0,
            "walletWidth": 224,
            "panelOverflowX": 0,
            "panelOverflowY": 0,
            "walletActionCount": 4,
            "visibleDetailFeeds": 0,
            "metricBandCount": 2,
            "metricCounts": [5, 4],
            "providerCount": 4,
            "providerGeometry": [
                {"provider": "codex", "width": 245, "height": 108, "overflowX": 0, "overflowY": 0, "routeColor": "#65D1D5"},
                {"provider": "antigravity", "width": 245, "height": 108, "overflowX": 0, "overflowY": 0, "routeColor": "#72D69A"},
                {"provider": "ollama", "width": 245, "height": 108, "overflowX": 0, "overflowY": 0, "routeColor": "#A8ABB3"},
                {"provider": "grok", "width": 245, "height": 108, "overflowX": 0, "overflowY": 0, "routeColor": "#1677FF"},
            ],
            "providerNames": [{"fontSize": 14, "clipped": False}],
            "providerBodies": [{"fontSize": 11, "clipped": False}],
            "providerMetadata": [{"fontSize": 10, "clipped": False}],
            "ledgerPresent": True,
            "ledgerOverflowX": 0,
            "ledgerOverflowY": 0,
            "ledgerRowCount": 9,
            "ledgerRowMinHeight": 22,
            "healthPresent": True,
            "healthCount": 4,
            "healthHeight": 76,
            "healthOverflowX": 0,
            "healthOverflowY": 0,
        },
    }


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


def test_playwright_probes_preserve_desktop_mobile_and_add_kiosk_1920() -> None:
    screenshot = Path("/tmp/control-tower-layout.png")

    probes = runtime_layout.playwright_probe_specs(screenshot)

    assert [(label, viewport) for label, viewport, _ in probes] == [
        ("desktop", {"width": 1440, "height": 1000}),
        ("mobile", {"width": 390, "height": 844}),
        ("kiosk-1920", {"width": 1920, "height": 1080}),
    ]
    assert [path.name for _, _, path in probes if path] == [
        "control-tower-layout.png",
        "control-tower-layout-mobile.png",
        "control-tower-layout-kiosk-1920.png",
    ]


def test_kiosk_legibility_accepts_exact_contract_boundaries() -> None:
    assert runtime_layout.validate_kiosk_legibility(valid_kiosk_legibility_measurements()) == []


def test_kiosk_legibility_reports_every_regression() -> None:
    measurements = valid_kiosk_legibility_measurements()
    measurements["pageOverflowX"] = 6
    measurements["pageOverflowY"] = 7
    live_work = measurements["liveWork"]
    assert isinstance(live_work, dict)
    live_work["objectives"] = [{"fontSize": 23.5, "clipped": True}]
    live_work["names"] = [{"fontSize": 16.5, "clipped": True}]
    live_work["descriptions"] = [{"fontSize": 12, "clipped": True}]
    live_work["secondary"] = [{"fontSize": 10, "clipped": True}]
    finops = measurements["finops"]
    assert isinstance(finops, dict)
    finops["bodyBottomDeadSpace"] = 11
    finops["bodyBottomOvershoot"] = 3
    finops["walletWidth"] = 240
    finops["panelOverflowX"] = 2
    finops["walletActionCount"] = 3
    finops["visibleDetailFeeds"] = 1
    finops["metricCounts"] = [5, 5]
    finops["providerCount"] = 3
    provider_geometry = finops["providerGeometry"]
    assert isinstance(provider_geometry, list)
    provider_geometry[0]["width"] = 244
    provider_geometry[0]["height"] = 107
    provider_geometry[0]["overflowX"] = 2
    provider_geometry[0]["routeColor"] = "#FFFFFF"
    finops["providerNames"] = [{"fontSize": 13.5, "clipped": True}]
    finops["providerBodies"] = [{"fontSize": 10.5, "clipped": True}]
    finops["providerMetadata"] = [{"fontSize": 9.5, "clipped": True}]
    finops["ledgerOverflowX"] = 2
    finops["ledgerOverflowY"] = 2
    finops["ledgerRowCount"] = 10
    finops["ledgerRowMinHeight"] = 21
    finops["healthCount"] = 3
    finops["healthHeight"] = 91
    finops["healthOverflowY"] = 2

    failures = runtime_layout.validate_kiosk_legibility(measurements)

    expected_fragments = (
        "horizontal page overflow",
        "vertical page overflow",
        "Live Work objective minimum font",
        "Live Work objective has 1 clipped",
        "Live Work name minimum font",
        "Live Work name has 1 clipped",
        "Live Work description minimum font",
        "Live Work description has 1 clipped",
        "Live Work secondary text minimum font",
        "Live Work secondary text has 1 clipped",
        "FinOps bottom dead space",
        "FinOps body overshoots",
        "FinOps wallet width",
        "FinOps panelOverflowX",
        "FinOps wallet action count",
        "FinOps overview exposes transaction/activity detail feeds",
        "FinOps metric hierarchy",
        "FinOps provider count",
        "codex card",
        "codex card content overflows",
        "codex route color",
        "FinOps provider name minimum font",
        "FinOps provider name has 1 clipped",
        "FinOps provider body minimum font",
        "FinOps provider body has 1 clipped",
        "FinOps provider metadata minimum font",
        "FinOps provider metadata has 1 clipped",
        "FinOps model ledger horizontal overflow",
        "FinOps model ledger vertical overflow",
        "FinOps model ledger renders 10 rows",
        "FinOps model ledger row height",
        "FinOps health rail has 3 cells",
        "FinOps health rail height",
        "FinOps health rail content overflows",
    )
    assert all(any(fragment in failure for failure in failures) for fragment in expected_fragments)
    assert len(failures) >= len(expected_fragments)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (("liveWork", "objectives"), "Live Work objective measurements are missing"),
        (("finops", "providerMetadata"), "FinOps provider metadata measurements are missing"),
        (("finops", "ledgerPresent"), "FinOps model ledger is missing"),
        (("finops", "healthPresent"), "FinOps health rail is missing"),
        (("finops", "providerGeometry"), "FinOps provider identities are incomplete"),
    ],
)
def test_kiosk_legibility_fails_closed_when_required_measurements_are_missing(
    path: tuple[str, str],
    expected: str,
) -> None:
    measurements = valid_kiosk_legibility_measurements()
    section = measurements[path[0]]
    assert isinstance(section, dict)
    section.pop(path[1])

    failures = runtime_layout.validate_kiosk_legibility(measurements)

    assert any(expected in failure for failure in failures)
