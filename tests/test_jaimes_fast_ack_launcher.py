from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import jaimes_telegram_fast_ack_launcher as launcher


def test_owner_routes_to_the_correct_watcher_and_profile(monkeypatch) -> None:
    assert launcher.watcher_path().name == "jaimes_telegram_fast_ack.py"
    assert launcher.credential_template_path().name == "agent-ecosystem-hermes.op.env"
    monkeypatch.setenv("TELEGRAM_FAST_ACK_OWNER", "josh2")
    assert launcher.watcher_path().name == "josh_telegram_fast_ack.py"
    assert launcher.credential_template_path().name == "agent-ecosystem.op.env"


def test_broker_command_resolves_only_telegram_into_the_watcher(tmp_path, monkeypatch) -> None:
    runner = tmp_path / "op_agent_env.sh"
    template = tmp_path / "hermes.op.env"
    watcher = tmp_path / "jaimes_telegram_fast_ack.py"
    runner.write_text("#!/bin/zsh\n", encoding="utf-8")
    template.write_text("TELEGRAM_BOT_TOKEN=op://example/reference\n", encoding="utf-8")
    watcher.write_text("# watcher\n", encoding="utf-8")
    monkeypatch.setattr(launcher, "OP_ENV_RUNNER", runner)
    monkeypatch.setattr(launcher, "OP_ENV_TEMPLATE", template)
    monkeypatch.setattr(launcher, "WATCHER", watcher)

    command = launcher.broker_command(["--fixture"])

    assert command == [
        str(runner),
        str(template),
        "--only",
        "TELEGRAM_BOT_TOKEN",
        "--",
        sys.executable,
        str(watcher),
        "--fixture",
    ]


def test_launcher_never_reveals_or_captures_the_secret() -> None:
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert "ps" + " eww" not in source
    assert "/usr/bin/" + "printenv" not in source
    assert "TELEGRAM_BOT_TOKEN=" not in source
    assert "secrets.json" not in source
