import json

from scripts import self_update_monitor as monitor


def test_latest_manifest_is_dashboard_safe(tmp_path):
    sandbox = tmp_path / "candidate"
    sandbox.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "candidate-test.json").write_text(json.dumps({
        "target": "1.2.3", "sandbox": str(sandbox),
        "promotion": {"status": "manual-review-required"},
    }))
    result = monitor.latest_manifest(evidence)
    assert result["healthy"] is True
    assert result["target"] == "1.2.3"
    assert "sandbox" not in result


def test_build_fails_closed_when_a_probe_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "CAPABILITY_WATCH", tmp_path / "watch.json")
    (tmp_path / "watch.json").write_text("{}")
    monkeypatch.setattr(monitor, "ROOT", tmp_path)
    monkeypatch.setattr(monitor, "sync_remote_capability_watch", lambda *args: {"ok": True, "updated": False})
    calls = iter([True, True, False, True, True, True])
    monkeypatch.setattr(monitor, "run", lambda *args, **kwargs: {"ok": next(calls)})
    monkeypatch.setattr(monitor, "latest_remote_manifest", lambda *args: {"present": False, "healthy": True})
    result = monitor.build({"capabilityWatchMaxAgeSeconds": 100})
    assert result["status"] == "attention"
    assert "openclawGateway" in result["failures"]
    assert result["automaticPromotion"] is False


def test_sync_remote_capability_watch_preserves_remote_timestamp(monkeypatch, tmp_path):
    local = tmp_path / "capability-watch.json"
    local.write_text(json.dumps({
        "updatedAt": "2026-08-01T00:00:00Z",
        "privacy": "dashboard-safe metadata only",
    }))
    remote = {
        "updatedAt": "2026-08-03T12:00:00Z",
        "status": "watch",
        "summary": "one recommendation",
        "sources": {},
        "recommendations": [],
        "previews": [],
        "fastLane": {"status": "watch", "summary": "one fast-track release"},
        "privacy": "dashboard-safe metadata only",
    }
    monkeypatch.setattr(monitor, "remote_capability_watch", lambda *args: {"ok": True, "payload": remote})
    result = monitor.sync_remote_capability_watch("jaimes", "/safe/watch.json", local)
    assert result["ok"] is True
    assert result["updated"] is True
    assert json.loads(local.read_text())["updatedAt"] == "2026-08-03T12:00:00Z"
    assert json.loads(local.read_text())["fastLane"]["summary"] == "one fast-track release"


def test_latest_remote_manifest_parses_dashboard_safe_probe(monkeypatch):
    monkeypatch.setattr(monitor, "run_capture", lambda *args, **kwargs: {
        "ok": True,
        "code": 0,
        "output": json.dumps({
            "present": True,
            "healthy": True,
            "target": "abc123",
            "ageSeconds": 12,
            "promotion": "manual-review-required",
            "observationRecorded": False,
        }),
    })
    result = monitor.latest_remote_manifest("jaimes", "/safe/evidence")
    assert result["healthy"] is True
    assert result["target"] == "abc123"
    assert "sandbox" not in result
