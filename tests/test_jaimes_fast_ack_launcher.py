from __future__ import annotations

import os
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


def test_josh_owner_reuses_live_openclaw_gateway_without_changing_jaimes_default() -> None:
    assert launcher.gateway_service_label() == "ai.hermes.gateway"
    assert launcher.watcher_path().name == "jaimes_telegram_fast_ack.py"
    with patch.dict(os.environ, {"TELEGRAM_FAST_ACK_OWNER": "josh2"}):
        assert launcher.gateway_service_label() == "ai.openclaw.gateway"
        assert launcher.watcher_path().name == "josh_telegram_fast_ack.py"
        assert launcher.credential_template_path().name == "agent-ecosystem.op.env"


def test_josh_resolver_uses_local_gateway_config_before_1password_fallback() -> None:
    with patch.dict(os.environ, {"TELEGRAM_FAST_ACK_OWNER": "josh2"}), patch.object(
        launcher, "gateway_pid", side_effect=RuntimeError("gateway environment scrubbed")
    ), patch.object(
        launcher, "local_openclaw_telegram_token", return_value="telegram-value"
    ) as local, patch.object(
        launcher,
        "secure_telegram_token",
        side_effect=AssertionError("local provisioned credential should win"),
    ):
        assert launcher.resolve_telegram_token() == "telegram-value"
    local.assert_called_once_with()


def test_josh_local_secretref_reads_only_the_protected_telegram_store_path(tmp_path) -> None:
    config = tmp_path / "openclaw.json"
    store = tmp_path / "secrets.json"
    config.write_text(
        '{"channels":{"telegram":{"botToken":{"source":"file","provider":"default","id":"opaque"}}}}',
        encoding="utf-8",
    )
    store.write_text(
        '{"openclaw":{"channels":{"telegram":{"botToken":"123456789:telegram-value_abcdefghijklmnopqrstuvwxyz"}}}}',
        encoding="utf-8",
    )
    with patch.dict(os.environ, {"TELEGRAM_FAST_ACK_OWNER": "josh2"}), patch.object(
        launcher, "OPENCLAW_CONFIG", config
    ), patch.object(launcher, "OPENCLAW_SECRET_STORE", store):
        assert launcher.local_openclaw_telegram_token().startswith("123456789:")
