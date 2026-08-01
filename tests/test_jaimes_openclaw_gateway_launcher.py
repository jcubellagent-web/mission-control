from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "jaimes_openclaw_gateway_launcher.py"
SPEC = importlib.util.spec_from_file_location("jaimes_openclaw_gateway_launcher", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_command_after_separator() -> None:
    assert MODULE.command_after_separator(["--", "node", "gateway"]) == ["node", "gateway"]
    with pytest.raises(RuntimeError, match="command is missing"):
        MODULE.command_after_separator([])


def test_broker_command_selects_providers_without_telegram(tmp_path, monkeypatch) -> None:
    runner = tmp_path / "op_agent_env.sh"
    template = tmp_path / "hermes.op.env"
    runner.write_text("#!/bin/zsh\n", encoding="utf-8")
    template.write_text("OPENAI_API_KEY=op://example/reference\n", encoding="utf-8")
    monkeypatch.setattr(MODULE, "OP_ENV_RUNNER", runner)
    monkeypatch.setattr(MODULE, "OP_ENV_TEMPLATE", template)

    command = MODULE.broker_command(["node", "gateway"])

    assert command[:4] == [str(runner), str(template), "--only", ",".join(MODULE.PROVIDER_VARIABLES)]
    assert command[4:] == ["--", "node", "gateway"]
    assert "TELEGRAM_BOT_TOKEN" not in command[3]


def test_launcher_never_inspects_process_environments() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "ps" + " eww" not in source
    assert "process_variables" not in source
    assert "service_pid" not in source
