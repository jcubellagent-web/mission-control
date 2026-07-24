from __future__ import annotations

import datetime as dt
import importlib.util
import json
import stat
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def now() -> dt.datetime:
    return dt.datetime(2026, 7, 24, 4, 0, tzinfo=dt.timezone.utc)


def raw_codexbar() -> list[dict]:
    return [{
        "provider": "ollama",
        "source": "web",
        "usage": {
            "accountEmail": "private@example.com",
            "identity": {"providerID": "private-id", "cookie": "NEVER_EXPORT"},
            "updatedAt": "2026-07-24T03:59:00Z",
            "primary": {"usedPercent": 0, "resetsAt": "2026-07-24T08:00:00Z", "windowMinutes": 300},
            "secondary": {"usedPercent": 0.7, "resetsAt": "2026-07-27T00:00:00Z", "windowMinutes": 10080},
        },
    }]


def test_projection_is_strictly_quota_only() -> None:
    bridge = load_module("quota_bridge_projection", ROOT / "scripts" / "codexbar_quota_bridge.py")
    projected = bridge.build_projection(raw_codexbar(), now=now())
    serialized = json.dumps(projected)

    assert set(projected) == bridge.TOP_LEVEL_FIELDS
    assert projected["windows"][0]["remainingPercent"] == 100
    assert projected["windows"][1]["remainingPercent"] == 99.3
    assert "private@example.com" not in serialized
    assert "private-id" not in serialized
    assert "NEVER_EXPORT" not in serialized
    assert "identity" not in serialized
    assert "cookie" not in serialized


def test_projection_rejects_unknown_wire_fields() -> None:
    bridge = load_module("quota_bridge_schema", ROOT / "scripts" / "codexbar_quota_bridge.py")
    projected = bridge.build_projection(raw_codexbar(), now=now())
    projected["accountEmail"] = "must-fail"
    with pytest.raises(bridge.BridgeError, match="top-level-schema"):
        bridge.validate_projection(projected, now=now())

    projected = bridge.build_projection(raw_codexbar(), now=now())
    projected["windows"][0]["token"] = "must-fail"
    with pytest.raises(bridge.BridgeError, match="window-schema"):
        bridge.validate_projection(projected, now=now())


def test_ingest_is_private_atomic_monotonic_and_idempotent(tmp_path: Path) -> None:
    bridge = load_module("quota_bridge_ingest", ROOT / "scripts" / "codexbar_quota_bridge.py")
    path = tmp_path / "ollama.json"
    projected = bridge.build_projection(raw_codexbar(), now=now())

    assert bridge.ingest_projection(projected, path, now=now()) == "accepted"
    stored = json.loads(path.read_text())
    assert stored["receivedAt"] == "2026-07-24T04:00:00Z"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))
    assert bridge.ingest_projection(projected, path, now=now()) == "duplicate"

    older = dict(projected)
    older["observedAt"] = "2026-07-24T03:58:00Z"
    older["exportedAt"] = "2026-07-24T04:00:00Z"
    with pytest.raises(bridge.BridgeError, match="replayed-observation"):
        bridge.ingest_projection(older, path, now=now())

    conflict = json.loads(json.dumps(projected))
    conflict["windows"][0]["usedPercent"] = 1
    conflict["windows"][0]["remainingPercent"] = 99
    with pytest.raises(bridge.BridgeError, match="conflicting-duplicate"):
        bridge.ingest_projection(conflict, path, now=now())


def test_stale_and_future_observations_fail_closed() -> None:
    bridge = load_module("quota_bridge_freshness", ROOT / "scripts" / "codexbar_quota_bridge.py")
    stale = raw_codexbar()
    stale[0]["usage"]["updatedAt"] = "2026-07-24T03:00:00Z"
    with pytest.raises(bridge.BridgeError, match="observation-stale"):
        bridge.build_projection(stale, now=now())

    future = raw_codexbar()
    future[0]["usage"]["updatedAt"] = "2026-07-24T04:03:00Z"
    with pytest.raises(bridge.BridgeError, match="observation-in-future"):
        bridge.build_projection(future, now=now())


def test_control_tower_projection_is_freshness_bounded_and_health_separate(tmp_path: Path, monkeypatch) -> None:
    bridge = load_module("quota_bridge_for_projection", ROOT / "scripts" / "codexbar_quota_bridge.py")
    updater = load_module("quota_projection_consumer", ROOT / "scripts" / "update_mission_control.py")
    sidecar = tmp_path / "ollama.json"
    payload = bridge.build_projection(raw_codexbar(), now=now())
    payload["receivedAt"] = "2026-07-24T04:00:00Z"
    sidecar.write_text(json.dumps(payload))
    sidecar.chmod(0o600)
    monkeypatch.setattr(updater, "CODEXBAR_QUOTA_OLLAMA_PATH", sidecar)

    fresh = updater.read_projected_codexbar_quota("ollama", now=now())
    assert fresh["quotaTelemetryStatus"] == "fresh"
    assert fresh["usageWindows"][1]["remainingPercent"] == 99.3
    healthy = updater.projected_ollama_limits(fresh, runtime_verified=True)
    unhealthy = updater.projected_ollama_limits(fresh, runtime_verified=False)
    assert healthy["available"] is True
    assert healthy["status"] == "ready"
    assert unhealthy["available"] is False
    assert unhealthy["status"] == "unavailable"
    assert unhealthy["usageWindows"] == healthy["usageWindows"]

    stale = updater.read_projected_codexbar_quota(
        "ollama",
        now=dt.datetime(2026, 7, 24, 4, 10, 1, tzinfo=dt.timezone.utc),
    )
    assert stale["quotaTelemetryStatus"] == "stale"
    assert stale["usageWindows"] == []
