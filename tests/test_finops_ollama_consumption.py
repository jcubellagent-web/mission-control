from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "v2-react" / "src" / "main.tsx"


def test_ollama_finops_distinguishes_quota_from_receipt_activity() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "function providerConsumptionLabel" in source
    assert "Quota ${providerWindowValue(quotaWindow)}" in source
    assert "verified receipt calls" in source
    assert "receipt activity kept separate" in source
    assert "const activityScore = key === \"ollama\" ? pct : receiptActivityScore" in source
    assert 'aria-label={key === "ollama" ? `${pct}% quota consumption`' in source


def test_ollama_finops_does_not_render_governance_coverage_as_consumption() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "GLM ${ollamaGovernance.coveragePct}% eligible coverage" not in source
