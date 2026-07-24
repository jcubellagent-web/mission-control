from __future__ import annotations

import json

from scripts import interaction_capability_probe as probe
from scripts import update_mission_control as dashboard


def test_sanitize_removes_content_bearing_keys_recursively() -> None:
    payload = probe.sanitize({
        "status": "ok",
        "url": "https://private.invalid",
        "nested": {
            "cookies": "secret",
            "selector": "#private",
            "accessibilityTree": "private tree",
            "latencyMs": 12,
        },
    })
    assert payload == {"status": "ok", "nested": {"latencyMs": 12}}


def test_semantic_first_surface_selection() -> None:
    config = {
        "hosts": {
            "josh2": {"browserSurface": "codex-browser", "desktopSurface": "codex-computer-use"},
            "jaimes": {"browserSurface": "cdp-playwright", "desktopSurface": "cua-driver"},
        }
    }
    assert probe.select_surface("semantic-operation", "josh2", config) == "connector-or-api"
    assert probe.select_surface("browser-dom", "josh2", config) == "codex-browser"
    assert probe.select_surface("browser-visual", "jaimes", config) == "cdp-playwright"
    assert probe.select_surface("desktop-ui", "jaimes", config) == "cua-driver"
    assert probe.select_surface("unknown", "josh2", config) == "unknown"


def test_collect_headless_is_metadata_only(monkeypatch, tmp_path) -> None:
    config = tmp_path / "routing.json"
    config.write_text(json.dumps({
        "semanticOrder": ["connector-or-api", "browser-dom", "accessibility", "vision", "coordinates"],
        "hosts": {
            "jaimes": {
                "role": "headless",
                "browserSurface": "cdp-playwright",
                "desktopSurface": "cua-driver",
                "headlessCdpRequired": True,
            }
        },
    }))
    monkeypatch.setattr(probe, "probe_browser", lambda: {
        "status": "ok",
        "browserPluginVersion": "1.2.3",
        "chromePluginVersion": "1.2.3",
        "agentBrowser": {"status": "down", "version": ""},
        "playwright": {"status": "ok", "version": "1.2.3"},
        "cdp": {"status": "ok", "latencyMs": 7, "port": 9222},
        "url": "forbidden",
    })
    monkeypatch.setattr(probe, "probe_cua_driver", lambda: {
        "status": "ok",
        "version": "0.12.3",
        "accessibility": True,
        "screenRecording": True,
        "screenCapturable": True,
        "cursorOverlay": True,
        "pictureInPicture": True,
        "width": 1920,
        "height": 1080,
        "screenshot": "forbidden",
    })
    monkeypatch.setattr(probe, "probe_codex_computer_use", lambda: {"status": "ok", "version": "1.0", "mcpEnabled": True})
    monkeypatch.setattr(probe, "display_online", lambda: {"status": "ok", "online": True, "width": 1920, "height": 1080})

    payload = probe.collect("jaimes", "headless", config)
    encoded = json.dumps(payload).lower()
    assert payload["status"] == "ok"
    assert payload["selectedSurfaces"]["browserDom"] == "cdp-playwright"
    assert payload["computerUse"]["cuaDriver"]["cursorOverlay"] is True
    for forbidden in ("forbidden", "cookie", "selector", "screenshot", "accessibilitytree", "url"):
        assert forbidden not in encoded


def test_control_tower_builds_latest_interaction_health_item() -> None:
    inventory = {
        "nodes": [
            {
                "node": "josh2",
                "interaction": {"host": "josh2", "role": "visible", "status": "ok", "checkedAt": "2026-07-24T20:00:00Z"},
            },
            {
                "node": "jaimes",
                "interaction": {"host": "jaimes", "role": "headless", "status": "ok", "checkedAt": "2026-07-24T20:00:00Z"},
            },
        ]
    }
    item = dashboard.build_interaction_control_capability(inventory)
    assert item is not None
    assert item["id"] == "interaction-control"
    assert item["status"] == "ok"
    assert item["summary"] == "Visible ready · headless ready"
    assert "private frames stay on-host" in item["detail"]
