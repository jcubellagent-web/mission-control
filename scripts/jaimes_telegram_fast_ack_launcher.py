#!/usr/bin/env python3
"""Start the JAIMES Telegram watcher with Hermes' live bot identity.

The Hermes gateway already owns the canonical JAIMES Telegram credential. This
launcher copies only that one value from the same-user gateway process into the
watcher environment, then immediately execs the watcher. It never prints,
persists, or forwards the credential and is deliberately independent of
unrelated provider-secret resolution.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


GATEWAY_LABEL = "ai.hermes.gateway"
WATCHER = Path(__file__).with_name("jaimes_telegram_fast_ack.py")
TOKEN_PATTERN = re.compile(r"(?:^|\s)TELEGRAM_BOT_TOKEN=([^\s]+)")


def run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=8, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed with exit {result.returncode}")
    return result.stdout


def gateway_pid() -> str:
    label = f"gui/{os.getuid()}/{GATEWAY_LABEL}"
    output = run(["launchctl", "print", label])
    match = re.search(r"(?m)^\s*pid = (\d+)\s*$", output)
    if not match:
        raise RuntimeError("Hermes gateway PID is unavailable")
    return match.group(1)


def gateway_telegram_token(pid: str) -> str:
    command = run(["ps", "eww", "-p", pid, "-o", "command="])
    match = TOKEN_PATTERN.search(command)
    if not match:
        raise RuntimeError("Hermes Telegram credential is unavailable")
    return match.group(1)


def main() -> int:
    try:
        os.environ["TELEGRAM_BOT_TOKEN"] = gateway_telegram_token(gateway_pid())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"JAIMES fast-ack launcher unavailable: {exc}", file=sys.stderr)
        return 69
    os.execv(sys.executable, [sys.executable, str(WATCHER), *sys.argv[1:]])
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
