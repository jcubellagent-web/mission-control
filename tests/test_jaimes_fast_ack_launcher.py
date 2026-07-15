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
