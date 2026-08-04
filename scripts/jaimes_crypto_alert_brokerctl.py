#!/usr/bin/env python3
"""Minimal client for the fixed JAIMES Crypto Alerts broker."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import sys


SOCKET_PATH = Path("/Users/jc_agent/.openclaw/private/crypto-radar-telegram-broker.sock")
ALLOWED_OPERATIONS = frozenset({"health", "deliver-next", "canary"})


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ALLOWED_OPERATIONS:
        print("usage: jaimes_crypto_alert_brokerctl.py health|deliver-next|canary", file=sys.stderr)
        return 2
    request = json.dumps({"operation": sys.argv[1]}, separators=(",", ":")).encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(30)
            conn.connect(str(SOCKET_PATH))
            conn.sendall(request)
            response = json.loads(conn.recv(4096).decode("utf-8"))
    except (OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        print(json.dumps({"ok": False, "error": "ERR_BROKER_UNAVAILABLE"}, sort_keys=True))
        return 1
    print(json.dumps(response, sort_keys=True))
    return 0 if isinstance(response, dict) and response.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
