from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATE = ROOT / "scripts" / "update_mission_control.py"
ROUTER = ROOT / "scripts" / "agent_route.py"


def test_ollama_fallback_reports_direct_metrics_not_a_projected_quota() -> None:
    source = UPDATE.read_text(encoding="utf-8")

    assert '"codexbarSource": "ollama-runtime-direct"' in source
    assert '"dataConfidence": "direct-request-metrics; account-quota-unavailable"' in source
    assert '"quotaTelemetryStatus": "unavailable"' in source
    assert 'return projected_ollama_limits(projection, runtime_verified=verified)' not in source


def test_router_does_not_consult_the_projected_ollama_quota_file() -> None:
    source = ROUTER.read_text(encoding="utf-8")

    assert "def canonical_ollama_allowance_limits" not in source
    assert "CONTROL_TOWER_OLLAMA_QUOTA_PATH" not in source
    assert "Return a soft GLM routing signal from direct provider telemetry only." in source
    assert '"technical-review",' in source
