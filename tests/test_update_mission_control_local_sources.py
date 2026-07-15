from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "update_mission_control.py"
SPEC = importlib.util.spec_from_file_location("update_mission_control_local_sources_test", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_brain_feed_reads_canonical_local_sidecar_without_vite(monkeypatch, tmp_path) -> None:
    (tmp_path / "brain-feed.json").write_text(json.dumps({
        "status": "ready",
        "focus": "Local source",
        "energy": 83,
        "updatedAt": "2026-07-15T12:00:00Z",
    }))
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(module, "fetch_next", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Vite must not be called")))

    result = module.fetch_brain_feed()

    assert result == {
        "status": "ready",
        "context": "Local source",
        "runway": 83,
        "updatedAt": "2026-07-15T12:00:00Z",
    }


def test_fetch_next_handles_socket_timeout(monkeypatch) -> None:
    def timeout(*_args, **_kwargs):
        raise socket.timeout("timed out")

    monkeypatch.setattr(module.urllib.request, "urlopen", timeout)

    assert module.fetch_next("/data/example.json") is None


def test_telegram_qa_dashboard_boundary_drops_unknown_and_private_fields() -> None:
    payload = {
        "updatedAt": "2026-07-15T12:00:00Z",
        "status": "ok",
        "secret": "must-not-pass",
        "lanes": {
            "stress": {
                "consecutiveFailures": 0,
                "prompt": "private prompt",
                "lastSample": {
                    "checkedAt": "2026-07-15T12:00:00Z",
                    "mode": "stress",
                    "ok": True,
                    "messageIds": [123],
                    "stress": {"ok": True, "iterations": 100, "renderedCards": 900},
                },
            }
        },
        "history": [{
            "checkedAt": "2026-07-15T12:00:00Z",
            "mode": "stress",
            "ok": True,
            "rawPrompt": "private",
            "messageIds": [456],
            "stress": {"ok": True, "iterations": 100, "renderedCards": 900},
        }],
    }

    safe = module.sanitize_telegram_inbox_qa(payload)
    encoded = json.dumps(safe)

    assert "must-not-pass" not in encoded
    assert "private prompt" not in encoded
    assert '"rawPrompt":' not in encoded
    assert '"messageIds":' not in encoded
    assert safe["coverage"]["recurringProductionWrites"] is False
