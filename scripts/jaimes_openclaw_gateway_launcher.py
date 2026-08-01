#!/usr/bin/env python3
"""Start JAIMES-host OpenCLAW from the healthy Hermes credential snapshot.

The legacy full-bundle 1Password wrapper made the whole J.A.I.N gateway fail
closed whenever any optional provider item or a locked keychain was
unavailable.  Hermes already holds the same host-scoped provider credentials.
This launcher copies only provider/control variables from that same-user
process, explicitly excludes Telegram, and immediately execs OpenCLAW.  It
never prints or persists credential values.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Iterable


HERMES_GATEWAY_LABEL = "ai.hermes.gateway"
PROVIDER_VARIABLES = (
    "OPENROUTER_API_KEY",
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "CONTROL_TOWER_SHARED_SECRET",
)
FORBIDDEN_VARIABLES = ("TELEGRAM_BOT_TOKEN",)
VARIABLE_PATTERN = re.compile(r"(?:^|\s)([A-Z][A-Z0-9_]*)=([^\s]+)")


def run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=8, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed with exit {result.returncode}")
    return result.stdout


def service_pid(label: str = HERMES_GATEWAY_LABEL) -> str:
    output = run(["launchctl", "print", f"gui/{os.getuid()}/{label}"])
    match = re.search(r"(?m)^\s*pid = (\d+)\s*$", output)
    if not match:
        raise RuntimeError("Hermes gateway PID is unavailable")
    return match.group(1)


def process_variables(pid: str, allowed: Iterable[str] = PROVIDER_VARIABLES) -> dict[str, str]:
    command = run(["ps", "eww", "-p", pid, "-o", "command="])
    wanted = set(allowed)
    return {
        name: value
        for name, value in VARIABLE_PATTERN.findall(command)
        if name in wanted and value
    }


def gateway_environment(source: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    for name in PROVIDER_VARIABLES:
        value = source.get(name)
        if value:
            env[name] = value
    for name in FORBIDDEN_VARIABLES:
        env.pop(name, None)
    if not any(env.get(name) for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY")):
        raise RuntimeError("no provider credential is available from the Hermes gateway")
    return env


def command_after_separator(argv: list[str]) -> list[str]:
    args = list(argv)
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        raise RuntimeError("OpenCLAW gateway command is missing")
    return args


def main() -> int:
    try:
        command = command_after_separator(sys.argv[1:])
        env = gateway_environment(process_variables(service_pid()))
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"JAIMES OpenCLAW launcher unavailable: {exc}", file=sys.stderr)
        return 69
    os.execvpe(command[0], command, env)
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
