#!/usr/bin/env python3
"""Launch the fixed Crypto Alerts broker with JAIMES' live Telegram identity."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

from jaimes_telegram_fast_ack_launcher import resolve_telegram_token


BROKER = Path(__file__).with_name("jaimes_crypto_alert_broker.py")


def main() -> int:
    try:
        token = resolve_telegram_token()
        if not token or re.search(r"\s", token):
            raise RuntimeError("credential-unavailable")
        os.environ["TELEGRAM_BOT_TOKEN"] = token
        del token
        os.execv(sys.executable, [sys.executable, str(BROKER)])
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        print("JAIMES Crypto Alerts broker unavailable: secure Telegram capability could not be resolved", file=sys.stderr)
        return 69
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
