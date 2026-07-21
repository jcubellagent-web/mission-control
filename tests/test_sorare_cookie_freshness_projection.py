from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "update_mission_control.py"
CHECKER_LOG = "/Users/josh2.0/.openclaw/workspace/logs/sorare_cookie_freshness.log"
COOKIE_ARTIFACT = "/Users/josh2.0/.openclaw/workspace/.sorare_cookies_fresh.json"


def test_cookie_freshness_uses_execution_log_for_both_host_probes() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    legacy = f"'Sorare Cookie Freshness': '/{COOKIE_ARTIFACT.lstrip('/')}'"

    assert source.count(CHECKER_LOG) == 2
    assert legacy not in source
    assert "cookie artifact's" in source
    assert "execution receipt" in source
