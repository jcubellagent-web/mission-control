from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_finops_wallet_respects_publisher_status_and_five_minute_sla() -> None:
    source = (ROOT / "v2-react" / "src" / "main.tsx").read_text(encoding="utf-8")
    start = source.index("function cryptoFreshness")
    end = source.index("function cryptoStatusClass", start)
    freshness = source[start:end]

    assert 'wallet.status || wallet.summary?.freshnessStatus' in freshness
    assert '["attention", "provisional", "stale"].includes(reportedStatus)' in freshness
    assert "age > 15 * 60 * 1000" in freshness
    assert "age > 60 * 60 * 1000" not in freshness
