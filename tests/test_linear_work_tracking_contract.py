import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "linear-integration.json"
SKILL = ROOT / "agent-skills" / "linear-work-tracking" / "SKILL.md"
AGENTS = ROOT / "AGENTS.md"
GUARD = ROOT / "scripts" / "control_tower_change_guard.py"


def load_config() -> dict:
    return json.loads(CONFIG.read_text())


def test_linear_has_a_non_competing_source_of_truth_contract() -> None:
    config = load_config()
    assert config["sourceOfTruth"] == {
        "liveOperations": "control_tower",
        "durablePlanning": "linear",
        "decisionsAndMemory": "governed_sidecars",
    }
    skill = SKILL.read_text()
    agents = AGENTS.read_text()
    assert "Control Tower remains authoritative" in skill
    assert "Control Tower remains the source of truth for live execution" in agents
    assert "does not grant or bypass" in skill


def test_tracking_gate_is_durable_and_excludes_runtime_noise() -> None:
    tracking = load_config()["tracking"]
    assert {
        "approved_enhancement",
        "confirmed_bug_or_regression",
        "approved_proposal",
        "multi_session_work",
        "unresolved_follow_up",
    }.issubset(set(tracking["createFor"]))
    assert {
        "heartbeat",
        "routine_job_success",
        "telegram_reply",
        "live_card_update",
        "transient_telemetry",
        "self_healed_alert",
    }.issubset(set(tracking["exclude"]))
    assert tracking["oneIssueAcrossHandoffs"] is True
    assert tracking["searchKeyOrder"][:2] == ["workId", "proposalId"]


def test_status_and_agent_routing_are_explicit() -> None:
    config = load_config()
    assert config["statusMapping"] == {
        "accepted": "Todo",
        "planned": "Todo",
        "routed": "Todo",
        "active": "In Progress",
        "blocked": "In Progress",
        "verifying": "In Review",
        "done": "Done",
        "cancelled": "Canceled",
    }
    assert config["connector"]["directAgents"] == ["joshex", "josh2", "jaimes"]
    assert config["connector"]["delegatedAgents"] == {"jain": "jaimes"}
    assert config["labels"]["onePerGroup"] is True


def test_workspace_ids_and_privacy_boundary_are_safe_and_stable() -> None:
    config = load_config()
    workspace = config["workspace"]
    assert workspace["teamKey"] == "JCU"
    assert re.fullmatch(r"[0-9a-f-]{36}", workspace["teamId"])
    assert re.fullmatch(r"[0-9a-f-]{36}", workspace["projectId"])
    forbidden = set(config["privacy"]["forbidden"])
    assert {"raw_email", "oauth_payload", "token", "cookie", "credential"}.issubset(forbidden)
    serialized = CONFIG.read_text().lower()
    assert "@gmail.com" not in serialized
    assert "access_token" not in serialized


def test_change_guard_covers_the_new_shared_skill_and_runbook() -> None:
    guard = GUARD.read_text()
    assert '"agent-skills"' in guard
    assert '"docs/agent-runbooks.md"' in guard
