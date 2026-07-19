from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import jaimes_telegram_fast_ack_launcher as launcher


def test_gateway_pid_is_read_from_launchd_without_credentials() -> None:
    with patch.object(launcher, "run", return_value="state = running\n\tpid = 42764\n"):
        assert launcher.gateway_pid() == "42764"


def test_only_telegram_token_is_selected_from_gateway_environment() -> None:
    process = "python gateway OTHER_SECRET=do-not-copy TELEGRAM_BOT_TOKEN=telegram-value PATH=/usr/bin"
    with patch.object(launcher, "run", return_value=process):
        assert launcher.gateway_telegram_token("42764") == "telegram-value"


def test_missing_gateway_token_fails_closed() -> None:
    with patch.object(launcher, "run", return_value="python gateway PATH=/usr/bin"):
        try:
            launcher.gateway_telegram_token("42764")
        except RuntimeError as exc:
            assert "credential is unavailable" in str(exc)
        else:
            raise AssertionError("missing token must fail closed")


def test_secure_fallback_resolves_only_telegram_reference_and_removes_temp_file(tmp_path) -> None:
    source = tmp_path / "hermes.op.env"
    source.write_text(
        "OPENAI_API_KEY=op://Agent Ecosystem/OpenAI/credential\n"
        "TELEGRAM_BOT_TOKEN=op://Agent Ecosystem/JAIMES Telegram/credential\n",
        encoding="utf-8",
    )
    runner = tmp_path / "op_agent_env.sh"
    runner.write_text("#!/bin/sh\n", encoding="utf-8")
    private_dir = tmp_path / "private"
    observed = {}

    def fake_run(args, *, timeout=8):
        template = Path(args[1])
        observed["path"] = template
        observed["contents"] = template.read_text(encoding="utf-8")
        observed["timeout"] = timeout
        return "telegram-value\n"

    with (
        patch.object(launcher, "OP_ENV_TEMPLATE", source),
        patch.object(launcher, "OP_ENV_RUNNER", runner),
        patch.object(launcher, "PRIVATE_DIR", private_dir),
        patch.object(launcher, "run", side_effect=fake_run),
    ):
        assert launcher.secure_telegram_token() == "telegram-value"

    assert observed["contents"] == "TELEGRAM_BOT_TOKEN=op://Agent Ecosystem/JAIMES Telegram/credential\n"
    assert observed["timeout"] == 40
    assert not observed["path"].exists()


def test_resolver_falls_back_after_gateway_scrubs_its_environment() -> None:
    with (
        patch.object(launcher, "gateway_pid", return_value="42764"),
        patch.object(launcher, "gateway_telegram_token", side_effect=RuntimeError("credential is unavailable")),
        patch.object(launcher, "secure_telegram_token", return_value="telegram-value") as fallback,
    ):
        assert launcher.resolve_telegram_token() == "telegram-value"
    fallback.assert_called_once_with()
