from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "capability_watch.py"
SPEC = importlib.util.spec_from_file_location("capability_watch", MODULE_PATH)
assert SPEC and SPEC.loader
watch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watch)


def test_release_summary_excludes_release_body() -> None:
    result = watch.release_summary({"ok": True, "status": "ok", "data": {"tag_name": "v1", "name": "Release", "published_at": "2026-07-25T00:00:00Z", "html_url": "https://example.test/release", "body": "unbounded release notes"}})
    assert result["tag"] == "v1"
    assert "body" not in result
    assert "unbounded" not in str(result)


def test_openclaw_status_parses_current_nested_registry_and_availability() -> None:
    with mock.patch.object(watch, "run", side_effect=[
        {"ok": True, "detail": "OpenClaw 2026.7.1-2 (0790d9f)"},
        {
            "ok": True,
            "json": {
                "update": {"registry": {"latestVersion": "2026.7.2"}},
                "channel": {"value": "stable"},
                "availability": {"available": True, "latestVersion": "2026.7.2"},
            },
        },
    ]):
        status = watch.openclaw_status()
    assert status["currentVersion"] == "2026.7.1-2"
    assert status["latestVersion"] == "2026.7.2"
    assert status["updateAvailable"] is True
    assert status["channel"] == "stable"


def test_hermes_recommendation_compares_stable_tags_not_main_commit_count() -> None:
    sources = {
        "hermesUpdate": {
            "ok": True,
            "status": "watch",
            "version": "Hermes Agent v0.19.0 (2026.7.20) · 3204 commits behind",
            "detail": "Update available on origin/main",
        },
        "hermesLatestRelease": {"tag": "v2026.7.30"},
    }
    recommendations = watch.build_recommendations(sources)
    assert recommendations == [{
        "id": "hermes-stable-update-available",
        "status": "upgrade",
        "title": "Hermes stable update available",
        "detail": "v2026.7.20 -> v2026.7.30; prepare and verify the carried-patch candidate before promotion.",
        "owner": "JAIMES",
    }]


def test_beta_is_preview_not_action_required() -> None:
    sources = {"openclawNpm": {"distTags": {"latest": "2026.7.1-2", "beta": "2026.7.2-beta.5"}}}
    assert watch.build_recommendations(sources) == []
    assert watch.build_previews(sources)[0]["status"] == "preview"
