from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "v2-react" / "src" / "main.tsx"


def test_ollama_finops_distinguishes_quota_from_receipt_activity() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "function providerConsumptionLabel" in source
    assert "direct Cloud calls today" in source
    assert "direct API tokens" in source
    assert "Account quota unavailable" in source
    assert "const activityScore = receiptActivityScore" in source
    assert 'aria-label={key === "ollama" ? `${directCallsToday} direct Cloud calls today`' in source
    assert "Math.round(windowPct * 10) / 10" in source


def test_ollama_finops_does_not_render_governance_coverage_as_consumption() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "GLM ${ollamaGovernance.coveragePct}% eligible coverage" not in source
    assert "quota used · receipt activity kept separate" not in source
