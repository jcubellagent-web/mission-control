#!/usr/bin/env python3
"""Start JAIMES OpenCLAW through the host-local 1Password environment broker."""
from __future__ import annotations

import os
import sys
from pathlib import Path


OP_ENV_RUNNER = Path.home() / ".openclaw/workspace/scripts/op_agent_env.sh"
OP_ENV_TEMPLATE = Path.home() / ".openclaw/workspace/config/agent-ecosystem-hermes.op.env"
PROVIDER_VARIABLES = (
    "OPENROUTER_API_KEY",
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "CONTROL_TOWER_SHARED_SECRET",
)
FORBIDDEN_VARIABLES = ("TELEGRAM_BOT_TOKEN",)


def command_after_separator(argv: list[str]) -> list[str]:
    args = list(argv)
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        raise RuntimeError("OpenCLAW gateway command is missing")
    return args


def broker_command(command: list[str]) -> list[str]:
    if not OP_ENV_RUNNER.is_file() or not OP_ENV_TEMPLATE.is_file():
        raise RuntimeError("JAIMES credential broker prerequisites are missing")
    return [
        str(OP_ENV_RUNNER),
        str(OP_ENV_TEMPLATE),
        "--only",
        ",".join(PROVIDER_VARIABLES),
        "--",
        *command,
    ]


def main() -> int:
    try:
        command = broker_command(command_after_separator(sys.argv[1:]))
    except (OSError, RuntimeError) as exc:
        print(f"JAIMES OpenCLAW launcher unavailable: {exc}", file=sys.stderr)
        return 69
    env = dict(os.environ)
    for name in FORBIDDEN_VARIABLES:
        env.pop(name, None)
    os.execve(command[0], command, env)
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
