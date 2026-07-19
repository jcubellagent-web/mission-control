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
import tempfile
from pathlib import Path


GATEWAY_LABEL = "ai.hermes.gateway"
WATCHER = Path(__file__).with_name("jaimes_telegram_fast_ack.py")
TOKEN_PATTERN = re.compile(r"(?:^|\s)TELEGRAM_BOT_TOKEN=([^\s]+)")
TOKEN_REFERENCE_PATTERN = re.compile(r"^\s*TELEGRAM_BOT_TOKEN\s*=\s*(op://.+?)\s*$")
OP_ENV_TEMPLATE = Path.home() / ".openclaw/workspace/config/agent-ecosystem-hermes.op.env"
OP_ENV_RUNNER = Path.home() / ".openclaw/workspace/scripts/op_agent_env.sh"
PRIVATE_DIR = Path.home() / ".openclaw/private"


def run(args: list[str], *, timeout: int = 8) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
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


def telegram_reference() -> str:
    try:
        lines = OP_ENV_TEMPLATE.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError("Hermes Telegram credential reference is unavailable") from exc
    for line in lines:
        match = TOKEN_REFERENCE_PATTERN.fullmatch(line)
        if match:
            return match.group(1)
    raise RuntimeError("Hermes Telegram credential reference is unavailable")


def secure_telegram_token() -> str:
    """Resolve only the Telegram credential through the managed 1Password runner."""
    if not OP_ENV_RUNNER.is_file():
        raise RuntimeError("Hermes credential runner is unavailable")
    PRIVATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix=".jaimes-telegram-", suffix=".op.env", dir=PRIVATE_DIR)
    path = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"TELEGRAM_BOT_TOKEN={telegram_reference()}\n")
        token = run(
            [str(OP_ENV_RUNNER), str(path), "--", "/usr/bin/printenv", "TELEGRAM_BOT_TOKEN"],
            timeout=40,
        ).strip()
    finally:
        path.unlink(missing_ok=True)
    if not token or re.search(r"\s", token):
        raise RuntimeError("Hermes Telegram credential is unavailable")
    return token


def resolve_telegram_token() -> str:
    """Prefer the live gateway identity, with a secure restart-safe fallback."""
    try:
        return gateway_telegram_token(gateway_pid())
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return secure_telegram_token()


def main() -> int:
    try:
        os.environ["TELEGRAM_BOT_TOKEN"] = resolve_telegram_token()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        del exc
        print("JAIMES fast-ack launcher unavailable: secure Telegram credential could not be resolved", file=sys.stderr)
        return 69
    os.execv(sys.executable, [sys.executable, str(WATCHER), *sys.argv[1:]])
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
