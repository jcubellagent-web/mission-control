from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import project_context_registry


def test_project_context_registry_is_dashboard_safe_and_complete(tmp_path: Path) -> None:
    config = tmp_path / "contexts.json"
    config.write_text(json.dumps({
        "canonicalMemory": "governed",
        "sharedSources": ["AGENTS.md"],
        "projects": [
            {"key": "one", "label": "One", "scope": "First", "contractVersion": 1},
            {"key": "two", "label": "Two", "scope": "Second", "contractVersion": 1},
        ],
    }), encoding="utf-8")

    registry, chat_sources = project_context_registry.build(config)

    assert registry["summary"] == {
        "projects": 2,
        "covered": 2,
        "missing": 0,
        "coveragePct": 100.0,
        "status": "ready",
    }
    assert all("path" not in row for row in registry["projects"])
    assert {row["id"] for row in chat_sources["sources"]} == {
        "governed-memory", "project-context", "control-tower"
    }
