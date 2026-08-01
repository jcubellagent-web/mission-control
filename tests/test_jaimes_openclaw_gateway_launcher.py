from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "jaimes_openclaw_gateway_launcher.py"
SPEC = importlib.util.spec_from_file_location("jaimes_openclaw_gateway_launcher", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_process_variables_selects_only_allowed_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        MODULE,
        "run",
        lambda _args: (
            "node gateway OPENAI_API_KEY=openai-value GEMINI_API_KEY=gemini-value "
            "TELEGRAM_BOT_TOKEN=must-not-forward UNRELATED=value"
        ),
    )

    assert MODULE.process_variables("123") == {
        "OPENAI_API_KEY": "openai-value",
        "GEMINI_API_KEY": "gemini-value",
    }


def test_gateway_environment_never_forwards_telegram(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "inherited-bot-token")
    env = MODULE.gateway_environment({"OPENAI_API_KEY": "provider-value"})

    assert env["OPENAI_API_KEY"] == "provider-value"
    assert "TELEGRAM_BOT_TOKEN" not in env


def test_gateway_environment_requires_one_provider(monkeypatch) -> None:
    for name in (*MODULE.PROVIDER_VARIABLES, *MODULE.FORBIDDEN_VARIABLES):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="no provider credential"):
        MODULE.gateway_environment({"CONTROL_TOWER_SHARED_SECRET": "control-only"})


def test_command_after_separator() -> None:
    assert MODULE.command_after_separator(["--", "node", "gateway"]) == ["node", "gateway"]
    with pytest.raises(RuntimeError, match="command is missing"):
        MODULE.command_after_separator([])
