#!/usr/bin/env python3
"""Start a Telegram watcher through the owning host's 1Password broker."""
from __future__ import annotations

import os
import sys
from pathlib import Path


WATCHER = Path(__file__).with_name("jaimes_telegram_fast_ack.py")
OP_ENV_RUNNER = Path.home() / ".openclaw/workspace/scripts/op_agent_env.sh"
OP_ENV_TEMPLATE = Path.home() / ".openclaw/workspace/config/agent-ecosystem-hermes.op.env"


def runtime_owner() -> str:
    return str(os.environ.get("TELEGRAM_FAST_ACK_OWNER") or "jaimes").strip().lower()


def watcher_path() -> Path:
    if runtime_owner() == "josh2":
        return Path(__file__).with_name("josh_telegram_fast_ack.py")
    return WATCHER


def credential_template_path() -> Path:
    if runtime_owner() == "josh2":
        return Path.home() / ".openclaw/workspace/config/agent-ecosystem.op.env"
    return OP_ENV_TEMPLATE


def broker_command(argv: list[str]) -> list[str]:
    template = credential_template_path()
    watcher = watcher_path()
    if not OP_ENV_RUNNER.is_file() or not template.is_file() or not watcher.is_file():
        raise RuntimeError("Telegram credential broker prerequisites are missing")
    return [
        str(OP_ENV_RUNNER),
        str(template),
        "--only",
        "TELEGRAM_BOT_TOKEN",
        "--",
        sys.executable,
        str(watcher),
        *argv,
    ]


def main() -> int:
    try:
        command = broker_command(sys.argv[1:])
    except (OSError, RuntimeError):
        print(
            "Telegram fast-ack launcher unavailable: secure credential broker could not start",
            file=sys.stderr,
        )
        return 69
    os.execv(command[0], command)
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
