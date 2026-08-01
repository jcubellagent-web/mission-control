from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_credential_policy_is_use_without_reveal() -> None:
    policy = json.loads((ROOT / "config" / "agent-credential-access.json").read_text(encoding="utf-8"))
    assert policy["primarySecretStore"] == "1password"
    assert policy["principle"] == "use-without-reveal"
    assert policy["authorization"]["ordinaryExpected1PasswordCliAccess"] == (
        "verify-and-authorize-within-two-seconds"
    )
    assert "secret-in-model-context" in policy["forbidden"]
    assert "process-environment-inspection" in policy["forbidden"]
    assert "password-change" in policy["humanGates"]


def test_shared_skill_forbids_secret_memory_and_requires_host_routing() -> None:
    skill = (ROOT / "agent-skills" / "credential-access" / "SKILL.md").read_text(encoding="utf-8")
    assert "Route credentialed work to the Mac mini that owns the account or service" in skill
    assert "never retrieve a secret value into chat, model context" in skill
    assert "expected wait is under two seconds" in skill


def test_launchers_do_not_use_legacy_secret_capture_paths() -> None:
    launchers = [
        ROOT / "scripts" / "jaimes_openclaw_gateway_launcher.py",
        ROOT / "scripts" / "jaimes_telegram_fast_ack_launcher.py",
    ]
    for launcher in launchers:
        source = launcher.read_text(encoding="utf-8")
        assert "ps" + " eww" not in source
        assert "/usr/bin/" + "printenv" not in source
        assert "secrets.json" not in source
