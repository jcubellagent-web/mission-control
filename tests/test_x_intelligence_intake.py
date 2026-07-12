import argparse
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "x_intelligence_intake.py"
spec = importlib.util.spec_from_file_location("x_intelligence_intake", MODULE_PATH)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def args(tmp_path, **overrides):
    values = dict(
        url="https://x.com/RobinhoodApp/status/1234567890123456789",
        claim="Robinhood announced a Robinhood Chain developer update",
        timestamp="2026-07-12T12:00:00Z",
        corroboration=["https://robinhood.com/us/en/newsroom/"],
        source_tier="primary",
        conflicting=False,
        implementation=False,
        state=str(tmp_path / "recent.json"),
        no_write=False,
        format="json",
        silent_routine=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def test_parse_public_status_url_only():
    row = mod.parse_x_url("https://x.com/OpenAI/status/1234567890")
    assert row["handle"] == "OpenAI"
    try:
        mod.parse_x_url("https://x.com/OpenAI")
    except ValueError:
        pass
    else:
        raise AssertionError("profile URL must be rejected")


def test_primary_corroboration_is_high_confidence(tmp_path):
    row = mod.intake(args(tmp_path))
    assert row["confidence"] == "high"
    assert row["primary_source_count"] == 1
    assert row["policy"] == {"x_scraping": False, "xai_used": False, "account_mutation": False, "incremental_api_spend_usd": 0}


def test_x_only_is_low_and_incomplete(tmp_path):
    row = mod.intake(args(tmp_path, corroboration=[], source_tier="unknown"))
    assert row["confidence"] == "low"
    assert "not independently verified" in row["coverage_limitation"]


def test_topic_routes():
    assert mod.topic_for("Solana bridge exploit")[0] == 20
    assert mod.topic_for("Hermes agent release")[0] == 17
    assert mod.topic_for("MLB pitcher scratch")[0] == 19
    assert mod.topic_for("breaking sanctions announcement")[0] == 56
    assert mod.topic_for("miscellaneous chatter")[0] == 1


def test_dedup_replay(tmp_path):
    first = mod.intake(args(tmp_path))
    second = mod.intake(args(tmp_path))
    assert first["status"] == "new"
    assert second["status"] == "duplicate"
    assert second["recommended_action"].startswith("No new action")


def test_model_routes_are_policy_metadata():
    assert mod.model_route("short", [], False, False)[0] == "Gemini Flash"
    assert mod.model_route("short", [], True, False)[0] == "Gemini Pro"
    assert mod.model_route("short", [], False, True)[0] == "Codex/OpenAI"


def test_no_network_or_browser_imports():
    source = MODULE_PATH.read_text()
    forbidden = ["requests", "selenium", "playwright", "browser_", "x_search", "api.x.ai", "urllib.request"]
    assert not any(term in source for term in forbidden)
