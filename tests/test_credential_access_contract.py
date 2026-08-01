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
    assert "unapproved-process-environment-inspection" in policy["forbidden"]
    assert "password-change" in policy["humanGates"]
    exception = policy["compatibilityExceptions"]["jaimesHermesSameUserEnvironmentReuse"]
    assert exception["replacementRequires"] == "dedicated-credential-broker-ipc-boundary"


def test_shared_skill_forbids_secret_memory_and_requires_host_routing() -> None:
    skill = (ROOT / "agent-skills" / "credential-access" / "SKILL.md").read_text(encoding="utf-8")
    assert "Route credentialed work to the Mac mini that owns the account or service" in skill
    assert "never retrieve a secret value into chat, model context" in skill
    assert "expected wait is under two seconds" in skill


def test_compatibility_launchers_remain_narrow_and_never_log_values() -> None:
    openclaw = (ROOT / "scripts" / "jaimes_openclaw_gateway_launcher.py").read_text(encoding="utf-8")
    telegram = (ROOT / "scripts" / "jaimes_telegram_fast_ack_launcher.py").read_text(encoding="utf-8")
    assert "PROVIDER_VARIABLES" in openclaw
    assert 'FORBIDDEN_VARIABLES = ("TELEGRAM_BOT_TOKEN",)' in openclaw
    assert "TOKEN_PATTERN" in telegram
    assert "capture_output=True" in telegram
    assert "print(token" not in telegram


def test_broker_uses_portable_zsh_array_parsing() -> None:
    runner = (ROOT / "scripts" / "op_agent_env.sh").read_text(encoding="utf-8")
    assert "IFS=',' read -rA selected_names" in runner
    assert "(@s:,:only_csv)" not in runner
