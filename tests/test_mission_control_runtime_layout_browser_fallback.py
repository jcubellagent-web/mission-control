from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mission_control_runtime_layout_check.py"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "control-tower-live.ci.json"
REGRESSION_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "dashboard-data.ci.json"
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
        "layout": {
            "liveWork": {"fullyInViewport": True},
            "todayJobs": {"fullyInViewport": True},
            "brainAtlas": {"fullyInViewport": True},
            "finops": {"fullyInViewport": True},
            "atlasFinopsTopDelta": 0,
            "atlasFinopsHeightDelta": 0,
            "jobsAboveFinopsGap": 7,
            "liveAboveAtlasGap": 7,
        },
        "memory": {
            "flowState": "live",
            "reducedMotion": False,
            "mapAnimationName": "none",
            "mapAnimated": False,
            "mapBoxShadow": "rgba(88, 238, 154, 0.06) 0px 0px 18px",
            "evidenceSource": "governed-memory-registry",
            "edges": [
                {
                    "agent": "josh2",
                    "operation": "retrieval",
                    "observedAt": "2026-01-01T00:00:00Z",
                    "evidenceValid": True,
                    "ageSeconds": 10,
                    "live": True,
                    "animationName": "memory-flow-travel",
                    "animated": True,
                    "strokeWidth": 4.4,
                    "strokeDasharray": "20px, 12px",
                    "strokeLinecap": "round",
                    "stroke": "rgba(101, 217, 255, 0.96)",
                    "filter": "none",
                },
                {
                    "operation": "used",
                    "observedAt": "",
                    "evidenceValid": False,
                    "ageSeconds": None,
                    "live": False,
                    "animationName": "none",
                    "animated": False,
                },
                {"agent": "joshex", "operation": "retrieval", "observedAt": "", "evidenceValid": False, "ageSeconds": None, "live": False, "animationName": "none", "animated": False},
                {"agent": "jaimes", "operation": "retrieval", "observedAt": "", "evidenceValid": False, "ageSeconds": None, "live": False, "animationName": "none", "animated": False},
                {"agent": "jain", "operation": "retrieval", "observedAt": "", "evidenceValid": False, "ageSeconds": None, "live": False, "animationName": "none", "animated": False},
            ],
            "liveEdgeCount": 1,
            "animatedEdgeCount": 1,
            "animatedInactiveCount": 0,
            "atlasAgentNodes": [
                {"agent": "joshex", "layer": "memory", "working": False, "workState": "quiet", "memoryState": "idle", "workClass": False, "memoryClass": False, "memoryReceiptVisible": False, "auraAnimationName": "none", "presenceAnimationName": "none", "memoryAnimationName": "none", "workAnimated": False, "memoryAnimated": False, "animated": False},
                {"agent": "josh2", "layer": "memory", "working": True, "workState": "working", "memoryState": "live", "workClass": True, "memoryClass": True, "memoryReceiptVisible": True, "auraAnimationName": "memory-agent-presence-halo", "presenceAnimationName": "memory-agent-presence-dot", "memoryAnimationName": "none", "memoryFilter": "none", "memoryStrokeWidth": 3.1, "workAnimated": True, "memoryAnimated": False, "animated": True},
                {"agent": "jaimes", "layer": "memory", "working": False, "workState": "quiet", "memoryState": "idle", "workClass": False, "memoryClass": False, "memoryReceiptVisible": False, "auraAnimationName": "none", "presenceAnimationName": "none", "memoryAnimationName": "none", "workAnimated": False, "memoryAnimated": False, "animated": False},
                {"agent": "jain", "layer": "memory", "working": False, "workState": "quiet", "memoryState": "idle", "workClass": False, "memoryClass": False, "memoryReceiptVisible": False, "auraAnimationName": "none", "presenceAnimationName": "none", "memoryAnimationName": "none", "workAnimated": False, "memoryAnimated": False, "animated": False},
            ],
            "liveWorkAgents": [
                {"agent": "joshex", "working": False, "modelFamily": "unverified", "modelVerified": False, "modelLabel": "Unverified model route pending", "modelChipFamily": "unverified", "modelChipVerified": False, "workerCount": 0, "visibleWorkerCount": 0, "workerFamilies": [], "workerLabels": [], "workerStaleStates": [], "workerOverflow": "", "headerOverflowX": 0, "headerOverflowY": 0},
                {"agent": "josh2", "working": True, "modelFamily": "codex", "modelVerified": True, "modelLabel": "GPT codex/gpt-5.6-terra", "modelChipFamily": "codex", "modelChipVerified": True, "workerCount": 1, "visibleWorkerCount": 1, "workerFamilies": ["antigravity"], "workerLabels": ["Worker · Gemini · gemini-3.1-pro · active"], "workerStaleStates": ["false"], "workerOverflow": "", "headerOverflowX": 0, "headerOverflowY": 0},
                {"agent": "jaimes", "working": False, "modelFamily": "unverified", "modelVerified": False, "modelLabel": "Unverified model route pending", "modelChipFamily": "unverified", "modelChipVerified": False, "workerCount": 0, "visibleWorkerCount": 0, "workerFamilies": [], "workerLabels": [], "workerStaleStates": [], "workerOverflow": "", "headerOverflowX": 0, "headerOverflowY": 0},
                {"agent": "jain", "working": False, "modelFamily": "unverified", "modelVerified": False, "modelLabel": "Unverified model route pending", "modelChipFamily": "unverified", "modelChipVerified": False, "workerCount": 0, "visibleWorkerCount": 0, "workerFamilies": [], "workerLabels": [], "workerStaleStates": [], "workerOverflow": "", "headerOverflowX": 0, "headerOverflowY": 0},
            ],
            "workingAgentCount": 1,
        },
        "brainAtlasView": {
            "active": "unified",
            "tone": "clear",
            "statusText": "1 working · Memory live · 2 exact receipts",
            "visiblePanelCount": 1,
            "legacyViewControlCount": 0,
            "layerCounts": {"memory": 1, "proof": 1},
            "proofState": "ready",
            "proofEmptyText": "",
            "proofRows": [
                {"agent": "josh2", "workLabel": "Refresh Control Tower health", "visibleWorkLabel": "Refresh Control Tower health", "receipt": "receipt-1", "receiptStatus": "done", "model": "codex/gpt-5.6-terra", "routeVerified": True, "declaredAnimated": False, "opaqueLabel": False, "clipped": False},
                {"agent": "jaimes", "workLabel": "Verify scheduled agent jobs", "visibleWorkLabel": "Verify scheduled agent jobs", "receipt": "receipt-2", "receiptStatus": "active", "model": "codex/gpt-5.6-sol", "routeVerified": True, "declaredAnimated": False, "opaqueLabel": False, "clipped": False},
            ],
            "proofEdges": [
                {"animationName": "none", "animated": False, "memoryFlowClass": False, "liveClass": False},
                {"animationName": "none", "animated": False, "memoryFlowClass": False, "liveClass": False},
            ],
        },
        "brainAtlasSections": {
            "unified": {
                "contained": True,
                "heading": "Live activity + exact proof",
                "description": "Governed memory moves on exact receipts; static proof is audit evidence, not private reasoning.",
                "headingFontSize": 12,
                "descriptionFontSize": 9.5,
                "headingClipped": False,
                "descriptionClipped": False,
                "labelledBy": "brain-atlas-unified-heading",
                "describedBy": "brain-atlas-unified-description",
                "labelledByTargetPresent": True,
                "height": 410,
                "graphHeight": 320,
                "horizontalFillRatio": 0.95,
                "graphKind": "svg",
                "overflowY": 0,
                "svgTitlePresent": True,
                "svgDescriptionPresent": True,
                "primaryGlyphHeights": [8.2],
                "secondaryGlyphHeights": [7.2],
                "layerGlyphHeights": [9.5],
                "nodeOverlapCount": 0,
                "htmlTextOverflowCount": 0,
                "svgTextOverflowCount": 0,
                "svgTextOverlapCount": 0,
            },
        },
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
            "walletWidth": 670,
            "panelOverflowX": 0,
            "panelOverflowY": 0,
            "walletActionCount": 4,
            "visibleDetailFeeds": 0,
            "metricBandCount": 1,
            "metricCounts": [5],
            "providerCount": 4,
            "providerGeometry": [
                {"provider": "codex", "width": 199, "height": 124, "overflowX": 0, "overflowY": 0, "routeColor": "#65D1D5"},
                {"provider": "antigravity", "width": 199, "height": 124, "overflowX": 0, "overflowY": 0, "routeColor": "#72D69A"},
                {"provider": "ollama", "width": 199, "height": 124, "overflowX": 0, "overflowY": 0, "routeColor": "#A8ABB3"},
                {"provider": "grok", "width": 199, "height": 124, "overflowX": 0, "overflowY": 0, "routeColor": "#1677FF"},
            ],
            "providerNames": [{"fontSize": 12, "clipped": False}],
            "providerBodies": [{"fontSize": 8, "clipped": False}],
            "providerMetadata": [{"fontSize": 8, "clipped": False}],
            "ledgerPresent": True,
            "ledgerOverflowX": 0,
            "ledgerOverflowY": 0,
            "ledgerRowCount": 9,
            "ledgerRowMinHeight": 22,
            "healthPresent": True,
            "healthCount": 4,
            "healthHeight": 56,
            "healthOverflowX": 0,
            "healthOverflowY": 0,
        },
        "todayJobs": {
            "rowCount": 139,
            "declaredRowCount": 139,
            "nonGreenRowCount": 94,
            "nonGreenSummaryCount": 3,
            "reasonTriggerCount": 97,
            "missingReasonCount": 0,
            "objectReasonCount": 0,
            "pendingSummaryReason": "85 scheduled later today · 1 running or active · 2 outcome unverified · 1 inside the grace window awaiting evidence. Open means no terminal result yet; it does not mean failed.",
            "nowMarkerPresent": True,
            "nowMarkerLabel": "Current time, 12:36 AM Eastern Time",
            "scrollOverflowY": 4000,
            "nowCenterDelta": 0,
            "followNowState": "centered",
            "directChildrenValid": True,
        },
    }


def test_ci_live_data_fixture_satisfies_canonical_contract() -> None:
    result = runtime_layout.check_control_tower_json(FIXTURE_PATH)

    assert result["name"] == "live-data-json"
    assert result["state"] == "pass"
    assert runtime_layout.internal_text_leaks(FIXTURE_PATH.read_text()) == []


def test_release_workflow_stages_and_validates_same_live_data_fixture() -> None:
    workflow = WORKFLOW_PATH.read_text()

    assert "cp tests/fixtures/dashboard-data.ci.json data/control-tower-live.json" in workflow
    assert "cp tests/fixtures/control-tower-hot.ci.json data/control-tower-hot.json" in workflow
    assert "--data data/control-tower-live.json" in workflow


def test_release_workflow_uses_deterministic_dashboard_fixture() -> None:
    workflow = WORKFLOW_PATH.read_text()
    payload = json.loads(REGRESSION_FIXTURE_PATH.read_text())

    assert "--dashboard-data tests/fixtures/dashboard-data.ci.json" in workflow
    assert isinstance(payload["todayJobs"], list)
    assert payload["todayJobsMeta"]["counts"] == {
        "complete": 3,
        "skipped": 2,
        "broken": 2,
        "pending": 17,
    }


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


def test_compact_live_json_accepts_today_jobs_without_crons(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE_PATH.read_text())
    payload.pop("crons")
    payload["todayJobs"] = [{"occurrenceId": "daily-qa@09:00", "name": "Daily QA"}]
    candidate = tmp_path / "control-tower-live.json"
    candidate.write_text(json.dumps(payload))

    result = runtime_layout.check_control_tower_json(candidate)

    assert result["state"] == "pass"
    assert result["evidence"]["scheduleSource"] == "todayJobs"
    assert result["evidence"]["scheduleRows"] == 1


def test_compact_live_json_requires_a_canonical_schedule_source(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE_PATH.read_text())
    payload.pop("crons")
    payload.pop("todayJobs", None)
    candidate = tmp_path / "control-tower-live.json"
    candidate.write_text(json.dumps(payload))

    result = runtime_layout.check_control_tower_json(candidate)

    assert result["state"] == "fail"
    assert "crons or todayJobs" in result["detail"]


def test_live_projection_rejects_ready_raw_atlas_downgrade(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE_PATH.read_text())
    payload["brainAtlas"] = {
        "status": "unavailable",
        "emptyReason": "generated-payload-invalid",
    }
    (tmp_path / "brain-atlas.json").write_text(json.dumps({"status": "ready"}))
    candidate = tmp_path / "control-tower-live.json"
    candidate.write_text(json.dumps(payload))

    result = runtime_layout.check_control_tower_json(candidate)

    assert result["state"] == "fail"
    assert "projection rejected a ready canonical graph" in result["detail"]


def test_full_dashboard_json_still_requires_crons(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE_PATH.read_text())
    payload.pop("crons")
    payload["todayJobs"] = []
    candidate = tmp_path / "dashboard-data.json"
    candidate.write_text(json.dumps(payload))

    result = runtime_layout.check_control_tower_json(candidate)

    assert result["state"] == "fail"
    assert "crons" in result["detail"]


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


def test_playwright_probes_cover_responsive_reference_and_reduced_motion_contracts() -> None:
    screenshot = Path("/tmp/control-tower-layout.png")

    probes = runtime_layout.playwright_probe_specs(screenshot)

    assert [(label, viewport) for label, viewport, _ in probes] == [
        ("desktop", {"width": 1440, "height": 1000}),
        ("mobile", {"width": 390, "height": 844}),
        ("kiosk-1920", {"width": 1920, "height": 1080}),
        ("reference-2048", {"width": 2048, "height": 1228}),
        ("kiosk-reduced-motion", {"width": 1920, "height": 1080}),
    ]
    assert [path.name for _, _, path in probes if path] == [
        "control-tower-layout.png",
        "control-tower-layout-mobile.png",
        "control-tower-layout-kiosk-1920.png",
        "control-tower-layout-reference-2048.png",
        "control-tower-layout-kiosk-reduced-motion.png",
    ]


def test_chromium_fallback_uses_isolated_mock_keychain() -> None:
    source = MODULE_PATH.read_text()
    command = source[source.index('"--headless=new"'):source.index('"--window-size=1440,1000"')]

    assert '"--use-mock-keychain"' in command
    assert 'f"--user-data-dir={profile}"' in command


def test_kiosk_legibility_accepts_exact_contract_boundaries() -> None:
    assert runtime_layout.validate_kiosk_legibility(valid_kiosk_legibility_measurements()) == []
    assert runtime_layout.validate_control_tower_layout(
        valid_kiosk_legibility_measurements(),
        label="reference-2048",
    ) == []


def test_layout_rejects_legacy_tabs_or_missing_simultaneous_layers() -> None:
    measurements = valid_kiosk_legibility_measurements()
    view = measurements["brainAtlasView"]
    assert isinstance(view, dict)
    view["active"] = "evidence"
    view["visiblePanelCount"] = 2
    view["legacyViewControlCount"] = 2
    view["layerCounts"] = {"memory": 1, "proof": 0}

    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any("active view is evidence (requires unified)" in failure for failure in failures)
    assert any("exactly one visible unified region" in failure for failure in failures)
    assert any("legacy Activity / Evidence view controls" in failure for failure in failures)
    assert any("exactly one visible proof layer" in failure for failure in failures)


def test_layout_rejects_broken_unified_atlas_status_contract() -> None:
    measurements = valid_kiosk_legibility_measurements()
    view = measurements["brainAtlasView"]
    assert isinstance(view, dict)
    view.update({"tone": "", "statusText": "Memory live"})

    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any("unified tone is missing or invalid" in failure for failure in failures)
    assert any("unified status does not summarize work and receipt state" in failure for failure in failures)


def test_layout_rejects_cramped_or_undocumented_brain_atlas_sections() -> None:
    measurements = valid_kiosk_legibility_measurements()
    sections = measurements["brainAtlasSections"]
    assert isinstance(sections, dict)
    unified = sections["unified"]
    assert isinstance(unified, dict)
    unified["description"] = ""
    unified["descriptionFontSize"] = 8
    unified["descriptionClipped"] = True
    unified["graphHeight"] = 104
    unified["primaryGlyphHeights"] = [6]
    unified["secondaryGlyphHeights"] = [5]
    unified["layerGlyphHeights"] = [7]
    unified["nodeOverlapCount"] = 3
    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any("purpose text is missing" in failure for failure in failures)
    assert any("purpose text is too small or unmeasured" in failure for failure in failures)
    assert any("heading or purpose text is clipped" in failure for failure in failures)
    assert any("unified graph height is 104px" in failure for failure in failures)
    assert any("unified primary labels render below 8px" in failure for failure in failures)
    assert any("unified layer labels render below 9px" in failure for failure in failures)
    assert any("3 overlapping same-layer node pair" in failure for failure in failures)


def test_layout_rejects_brain_atlas_html_and_svg_text_overflow() -> None:
    measurements = valid_kiosk_legibility_measurements()
    sections = measurements["brainAtlasSections"]
    assert isinstance(sections, dict)
    unified = sections["unified"]
    assert isinstance(unified, dict)
    unified.update({
        "htmlTextOverflowCount": 1,
        "svgTextOverflowCount": 2,
        "svgTextOverlapCount": 1,
    })

    failures = runtime_layout.validate_control_tower_layout(
        measurements, label="kiosk-1920"
    )

    assert any("1 overflowing HTML text container" in failure for failure in failures)
    assert any("2 overflowing SVG node text" in failure for failure in failures)
    assert any("1 overflowing SVG title/detail pair" in failure for failure in failures)


def test_layout_rejects_reversed_or_overflowing_brain_atlas_sections() -> None:
    measurements = valid_kiosk_legibility_measurements()
    sections = measurements["brainAtlasSections"]
    assert isinstance(sections, dict)
    unified = sections["unified"]
    assert isinstance(unified, dict)
    unified["contained"] = False
    unified["overflowY"] = 12
    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any("Live activity + exact proof region escapes its panel" in failure for failure in failures)
    assert any("Live activity + exact proof overflows vertically by 12px" in failure for failure in failures)


def test_layout_rejects_horizontally_letterboxed_brain_atlas_graph() -> None:
    measurements = valid_kiosk_legibility_measurements()
    sections = measurements["brainAtlasSections"]
    assert isinstance(sections, dict)
    unified = sections["unified"]
    assert isinstance(unified, dict)
    unified["horizontalFillRatio"] = 0.66

    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any("unified graph uses 66% of its horizontal map" in failure for failure in failures)


def test_horizontal_fill_probe_excludes_full_width_layer_artifacts() -> None:
    source = MODULE_PATH.read_text()
    atlas_probe = source[source.index("const atlasRegion"):source.index("const visibleAtlasRegions")]

    assert "querySelectorAll(fillAnchorSelector)" in atlas_probe
    assert ".memory-flow-node, .brain-atlas-proof-work, .brain-atlas-proof-receipt, .brain-atlas-proof-model" in atlas_probe
    assert "querySelectorAll('[data-atlas-layer]')" not in atlas_probe


def test_layout_requires_one_visible_unified_svg() -> None:
    measurements = valid_kiosk_legibility_measurements()
    sections = measurements["brainAtlasSections"]
    assert isinstance(sections, dict)
    unified = sections["unified"]
    assert isinstance(unified, dict)
    unified["graphKind"] = "empty"

    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any("unified graph must be one visible SVG" in failure for failure in failures)


def test_layout_rejects_missing_section_overflow_measurement() -> None:
    measurements = valid_kiosk_legibility_measurements()
    sections = measurements["brainAtlasSections"]
    assert isinstance(sections, dict)
    unified = sections["unified"]
    assert isinstance(unified, dict)
    unified.pop("overflowY")

    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any("Live activity + exact proof overflow measurement is missing" in failure for failure in failures)


def test_layout_rejects_unreadable_or_unverified_proof_rows() -> None:
    measurements = valid_kiosk_legibility_measurements()
    view = measurements["brainAtlasView"]
    assert isinstance(view, dict)
    rows = view["proofRows"]
    assert isinstance(rows, list)
    rows[0].update({
        "agent": "unknown",
        "workLabel": "Work deadbeef",
        "visibleWorkLabel": "Work deadbeef",
        "receipt": "",
        "receiptStatus": "",
        "model": "",
        "routeVerified": False,
        "declaredAnimated": True,
        "opaqueLabel": True,
        "clipped": True,
    })

    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    expected = (
        "unknown agent",
        "opaque work identifier",
        "lacks an exact receipt",
        "lacks an exact receipt status",
        "lacks a verified model",
        "lacks a verified route",
        "not declared static",
        "is clipped",
    )
    assert all(any(fragment in failure for failure in failures) for fragment in expected)


def test_layout_rejects_private_or_identifier_shaped_work_names() -> None:
    measurements = valid_kiosk_legibility_measurements()
    view = measurements["brainAtlasView"]
    assert isinstance(view, dict)
    rows = view["proofRows"]
    assert isinstance(rows, list)
    rows[0]["workLabel"] = "Review /Users/private/work-12345678"

    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any("exposes an unsafe work name" in failure for failure in failures)


@pytest.mark.parametrize(
    "unsafe_label",
    [
        "Open /help/etc/passwd",
        "Call 212-555-0199",
        "Review 123-45-6789",
        "Use sk-proj-abcdefghijklmnopqrstuvwxyz",
    ],
)
def test_layout_rejects_pii_path_and_secret_shaped_work_names(unsafe_label: str) -> None:
    measurements = valid_kiosk_legibility_measurements()
    view = measurements["brainAtlasView"]
    assert isinstance(view, dict)
    rows = view["proofRows"]
    assert isinstance(rows, list)
    rows[0]["workLabel"] = unsafe_label

    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any("exposes an unsafe work name" in failure for failure in failures)


def test_layout_allows_safe_slash_command_work_name() -> None:
    measurements = valid_kiosk_legibility_measurements()
    view = measurements["brainAtlasView"]
    assert isinstance(view, dict)
    rows = view["proofRows"]
    assert isinstance(rows, list)
    rows[0]["workLabel"] = "/new Telegram task"

    assert runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920") == []


def test_layout_rejects_work_name_over_shared_56_character_limit() -> None:
    measurements = valid_kiosk_legibility_measurements()
    view = measurements["brainAtlasView"]
    assert isinstance(view, dict)
    rows = view["proofRows"]
    assert isinstance(rows, list)
    rows[0]["workLabel"] = "x" * 57

    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any("lacks a concise work name" in failure for failure in failures)


@pytest.mark.parametrize("row_count", [0, 4])
def test_layout_limits_exact_proof_rows_to_a_readable_window(row_count: int) -> None:
    measurements = valid_kiosk_legibility_measurements()
    view = measurements["brainAtlasView"]
    assert isinstance(view, dict)
    rows = view["proofRows"]
    assert isinstance(rows, list)
    view["proofRows"] = [json.loads(json.dumps(rows[0])) for _ in range(row_count)]

    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any(f"renders {row_count} exact proof rows" in failure for failure in failures)


@pytest.mark.parametrize("proof_state", ["empty", "unavailable"])
def test_layout_accepts_truthful_empty_or_unavailable_proof_state(proof_state: str) -> None:
    measurements = valid_kiosk_legibility_measurements()
    view = measurements["brainAtlasView"]
    assert isinstance(view, dict)
    view["proofState"] = proof_state
    view["proofRows"] = []
    view["proofEdges"] = []
    view["proofEmptyText"] = "No exact proof paths in this window" if proof_state == "empty" else "Exact proof unavailable"
    view["statusText"] = (
        "0 working · Memory idle · No exact receipts in window"
        if proof_state == "empty"
        else "0 working · Memory telemetry unavailable · Source unavailable"
    )

    assert runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920") == []


def test_layout_rejects_animated_proof_edges_that_impersonate_memory() -> None:
    measurements = valid_kiosk_legibility_measurements()
    view = measurements["brainAtlasView"]
    assert isinstance(view, dict)
    edges = view["proofEdges"]
    assert isinstance(edges, list)
    edges[0].update({
        "animationName": "memory-flow-travel",
        "animated": True,
        "memoryFlowClass": True,
        "liveClass": True,
    })

    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any("proof edge 1 is animated" in failure for failure in failures)
    assert any("proof edge 1 impersonates live memory activity" in failure for failure in failures)


def test_layout_rejects_shared_agent_node_outside_memory_layer() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    nodes = memory["atlasAgentNodes"]
    assert isinstance(nodes, list)
    nodes[0]["layer"] = "proof"

    failures = runtime_layout.validate_control_tower_layout(measurements, label="kiosk-1920")

    assert any("shared agent node is outside the memory layer" in failure for failure in failures)


def test_layout_accepts_idle_atlas_only_when_no_path_is_live_or_animated() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    memory["flowState"] = "idle"
    edges = memory["edges"]
    assert isinstance(edges, list)
    for edge in edges:
        assert isinstance(edge, dict)
        edge["live"] = False
        edge["animationName"] = "none"
        edge["animated"] = False
    atlas_nodes = memory["atlasAgentNodes"]
    assert isinstance(atlas_nodes, list)
    for node in atlas_nodes:
        assert isinstance(node, dict)
        node["memoryState"] = "idle"
        node["memoryClass"] = False
        node["memoryReceiptVisible"] = False
        node["memoryAnimationName"] = "none"
        node["memoryAnimated"] = False
        node["animated"] = bool(node["workAnimated"])
    memory["liveEdgeCount"] = 0
    memory["animatedEdgeCount"] = 0

    assert runtime_layout.validate_control_tower_layout(measurements, label="reference-2048") == []


def test_layout_accepts_working_agent_with_memory_quiet_and_no_live_edge() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    memory["flowState"] = "idle"
    memory["mapBoxShadow"] = "none"
    edges = memory["edges"]
    assert isinstance(edges, list)
    edge = next(row for row in edges if row.get("agent") == "josh2")
    edge.update({"observedAt": "", "evidenceValid": False, "ageSeconds": None, "live": False, "animationName": "none", "animated": False})
    memory["liveEdgeCount"] = 0
    memory["animatedEdgeCount"] = 0
    nodes = memory["atlasAgentNodes"]
    assert isinstance(nodes, list)
    node = next(row for row in nodes if row.get("agent") == "josh2")
    node.update({"memoryState": "idle", "memoryClass": False, "memoryReceiptVisible": False, "memoryAnimationName": "none", "memoryAnimated": False, "animated": True})

    assert runtime_layout.validate_control_tower_layout(measurements, label="reference-2048") == []


def test_layout_rejects_live_atlas_without_static_activity_glow() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    memory["mapBoxShadow"] = "none"

    failures = runtime_layout.validate_control_tower_layout(measurements, label="reference-2048")

    assert any("map shell lacks its static activity glow" in failure for failure in failures)


def test_layout_accepts_idle_agent_with_exact_live_memory_path() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    board = memory["liveWorkAgents"]
    nodes = memory["atlasAgentNodes"]
    assert isinstance(board, list)
    assert isinstance(nodes, list)
    board_node = next(row for row in board if row.get("agent") == "josh2")
    board_node["working"] = False
    atlas_node = next(row for row in nodes if row.get("agent") == "josh2")
    atlas_node.update({
        "working": False,
        "workState": "quiet",
        "workClass": False,
        "auraAnimationName": "none",
        "presenceAnimationName": "none",
        "workAnimated": False,
        "animated": False,
    })
    memory["workingAgentCount"] = 0

    assert runtime_layout.validate_control_tower_layout(measurements, label="reference-2048") == []


def test_layout_rejects_paint_heavy_atlas_shell_and_edge_animations() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    memory.update({
        "mapAnimationName": "memory-map-live-breathe",
        "mapAnimated": True,
    })
    edges = memory["edges"]
    assert isinstance(edges, list)
    live_edge = edges[0]
    assert isinstance(live_edge, dict)
    live_edge["filter"] = "drop-shadow(rgb(101, 217, 255) 0px 0px 8px)"
    nodes = memory["atlasAgentNodes"]
    assert isinstance(nodes, list)
    live_node = next(row for row in nodes if row.get("agent") == "josh2")
    live_node.update({
        "memoryAnimationName": "memory-node-live-pulse",
        "memoryAnimated": True,
    })

    failures = runtime_layout.validate_control_tower_layout(measurements, label="reference-2048")

    assert any("map shell uses an expensive paint animation" in failure for failure in failures)
    assert any("live retrieval path uses an expensive SVG filter" in failure for failure in failures)
    assert any("josh2 node shell uses an expensive paint animation" in failure for failure in failures)


def test_layout_rejects_live_work_and_atlas_presence_mismatch() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    board = memory["liveWorkAgents"]
    assert isinstance(board, list)
    next(row for row in board if row.get("agent") == "jaimes")["working"] = True

    failures = runtime_layout.validate_control_tower_layout(measurements, label="reference-2048")

    assert any("do not match Live Work working agents" in failure for failure in failures)


def test_layout_rejects_unverified_controller_disguised_as_verified() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    board = memory["liveWorkAgents"]
    assert isinstance(board, list)
    card = next(row for row in board if row.get("agent") == "joshex")
    card.update({"modelFamily": "codex", "modelLabel": "GPT gpt-5.6", "modelChipVerified": True})

    failures = runtime_layout.validate_control_tower_layout(measurements, label="reference-2048")

    assert any("unverified route is styled as verified" in failure for failure in failures)


def test_layout_rejects_worker_without_accessible_model_label() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    board = memory["liveWorkAgents"]
    assert isinstance(board, list)
    card = next(row for row in board if row.get("agent") == "josh2")
    card["workerLabels"] = [""]

    failures = runtime_layout.validate_control_tower_layout(measurements, label="reference-2048")

    assert any("worker model icon lacks an accessible label" in failure for failure in failures)


def test_layout_accepts_exact_live_path_without_animation_in_reduced_motion() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    memory["reducedMotion"] = True
    edges = memory["edges"]
    assert isinstance(edges, list)
    live_edge = edges[0]
    assert isinstance(live_edge, dict)
    live_edge["animationName"] = "none"
    live_edge["animated"] = False
    memory["animatedEdgeCount"] = 0
    atlas_nodes = memory["atlasAgentNodes"]
    assert isinstance(atlas_nodes, list)
    for node in atlas_nodes:
        assert isinstance(node, dict)
        node["auraAnimationName"] = "none"
        node["presenceAnimationName"] = "none"
        node["memoryAnimationName"] = "none"
        node["workAnimated"] = False
        node["memoryAnimated"] = False
        node["animated"] = False

    assert runtime_layout.validate_control_tower_layout(
        measurements,
        label="kiosk-reduced-motion",
        expect_reduced_motion=True,
    ) == []


def test_layout_rejects_live_animation_after_exact_evidence_expires() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    edges = memory["edges"]
    assert isinstance(edges, list)
    live_edge = edges[0]
    assert isinstance(live_edge, dict)
    live_edge["ageSeconds"] = runtime_layout.MEMORY_ACTIVITY_MAX_AGE_SECONDS + 1

    failures = runtime_layout.validate_control_tower_layout(measurements, label="reference-2048")

    assert any("live retrieval path evidence is 101s old" in failure for failure in failures)


def test_layout_rejects_live_path_without_numeric_evidence_age() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    edges = memory["edges"]
    assert isinstance(edges, list)
    live_edge = edges[0]
    assert isinstance(live_edge, dict)
    live_edge["ageSeconds"] = None

    failures = runtime_layout.validate_control_tower_layout(measurements, label="reference-2048")

    assert any("live retrieval path lacks a numeric evidence age" in failure for failure in failures)


def test_layout_rejects_live_path_that_is_too_subtle_to_read() -> None:
    measurements = valid_kiosk_legibility_measurements()
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    edges = memory["edges"]
    assert isinstance(edges, list)
    live_edge = edges[0]
    assert isinstance(live_edge, dict)
    live_edge.update({"strokeWidth": 2.2, "strokeDasharray": "none", "strokeLinecap": "butt", "stroke": "none"})

    failures = runtime_layout.validate_control_tower_layout(measurements, label="reference-2048")

    assert any("not visually pronounced enough" in failure for failure in failures)
    assert any("lacks a rounded travel beacon" in failure for failure in failures)
    assert any("lacks a visible moving dash" in failure for failure in failures)
    assert any("lacks a visible evidence stroke" in failure for failure in failures)


def test_kiosk_legibility_reports_every_regression() -> None:
    measurements = valid_kiosk_legibility_measurements()
    measurements["pageOverflowX"] = 6
    measurements["pageOverflowY"] = 7
    layout = measurements["layout"]
    assert isinstance(layout, dict)
    atlas = layout["brainAtlas"]
    assert isinstance(atlas, dict)
    atlas["fullyInViewport"] = False
    layout["atlasFinopsTopDelta"] = 3
    layout["atlasFinopsHeightDelta"] = 4
    layout["jobsAboveFinopsGap"] = -2
    layout["liveAboveAtlasGap"] = -3
    memory = measurements["memory"]
    assert isinstance(memory, dict)
    memory["evidenceSource"] = "decorative-animation"
    memory["animatedInactiveCount"] = 1
    edges = memory["edges"]
    assert isinstance(edges, list)
    live_edge = edges[0]
    assert isinstance(live_edge, dict)
    live_edge["evidenceValid"] = False
    live_edge["ageSeconds"] = 101
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
    finops["walletWidth"] = 720
    finops["panelOverflowX"] = 2
    finops["walletActionCount"] = 3
    finops["visibleDetailFeeds"] = 1
    finops["metricBandCount"] = 2
    finops["metricCounts"] = [5, 4]
    finops["providerCount"] = 3
    provider_geometry = finops["providerGeometry"]
    assert isinstance(provider_geometry, list)
    provider_geometry[0]["width"] = 189
    provider_geometry[0]["height"] = 117
    provider_geometry[0]["overflowX"] = 2
    provider_geometry[0]["routeColor"] = "#FFFFFF"
    finops["providerNames"] = [{"fontSize": 11.5, "clipped": True}]
    finops["providerBodies"] = [{"fontSize": 7.5, "clipped": True}]
    finops["providerMetadata"] = [{"fontSize": 7.5, "clipped": True}]
    finops["ledgerOverflowX"] = 2
    finops["ledgerOverflowY"] = 2
    finops["ledgerRowCount"] = 10
    finops["ledgerRowMinHeight"] = 21
    finops["healthCount"] = 3
    finops["healthHeight"] = 59
    finops["healthOverflowY"] = 2
    today_jobs = measurements["todayJobs"]
    assert isinstance(today_jobs, dict)
    today_jobs["declaredRowCount"] = 138
    today_jobs["scrollOverflowY"] = 0
    today_jobs["nonGreenSummaryCount"] = 2
    today_jobs["reasonTriggerCount"] = 4
    today_jobs["missingReasonCount"] = 1
    today_jobs["objectReasonCount"] = 1
    today_jobs["pendingSummaryReason"] = "Pending jobs"
    today_jobs["nowMarkerPresent"] = False
    today_jobs["nowMarkerLabel"] = ""
    today_jobs["followNowState"] = "ready"
    today_jobs["nowCenterDelta"] = 50
    today_jobs["directChildrenValid"] = False

    failures = runtime_layout.validate_kiosk_legibility(measurements)

    expected_fragments = (
        "horizontal page overflow",
        "vertical page overflow",
        "Brain Atlas is not fully in the initial viewport",
        "Brain Atlas / FinOps top delta",
        "Brain Atlas / FinOps height delta",
        "Today's Jobs must remain above FinOps",
        "Live Work must remain above Brain Atlas",
        "renders 139 rows but declares 138",
        "Today's Jobs is not using its shorter scroll viewport",
        "memory flow is not registry-verified",
        "live retrieval path lacks an exact observed-at timestamp",
        "unevidenced Brain Atlas path is animated",
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
        "Today's Jobs non-green reason targets are incomplete",
        "Today's Jobs exposes 4 reason trigger",
        "Today's Jobs has missing non-green explanations",
        "Today's Jobs exposes an invalid object/undefined explanation",
        "Today's Jobs pending summary does not explain future versus failed",
        "Today's Jobs current-time marker is missing or unlabeled",
        "Today's Jobs auto-follow state is ready",
        "Today's Jobs rowgroup contains a non-row timeline child",
    )
    assert all(any(fragment in failure for failure in failures) for fragment in expected_fragments)
    assert len(failures) >= len(expected_fragments)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (("layout", "brainAtlas"), "Brain Atlas is not initially visible"),
        (("memory", "evidenceSource"), "Brain Atlas memory flow is not registry-verified"),
        (("liveWork", "objectives"), "Live Work objective measurements are missing"),
        (("finops", "providerMetadata"), "FinOps provider metadata measurements are missing"),
        (("finops", "ledgerPresent"), "FinOps model ledger is missing"),
        (("finops", "healthPresent"), "FinOps health rail is missing"),
        (("finops", "providerGeometry"), "FinOps provider identities are incomplete"),
        (("todayJobs", "pendingSummaryReason"), "Today's Jobs pending summary does not explain future versus failed"),
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
