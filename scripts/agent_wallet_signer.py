#!/usr/bin/env python3
"""Route ecosystem-agent wallet signing requests to JAIMES's private broker."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PRIVATE_GATEWAY = Path(
    "/Users/jc_agent/.hermes/private/wallet-signer/wallet_signer_gateway.py"
)
ALLOWED_AGENTS = ("joshex", "josh2", "jaimes", "jain")
INPUT_ACTIONS = ("validate", "sign")
MAX_REQUEST_BYTES = 65_536


def load_request(path: str | None, action: str) -> bytes | None:
    if action not in INPUT_ACTIONS:
        if path:
            raise ValueError(f"{action} does not accept a request file")
        return None
    if not path:
        raise ValueError(f"{action} requires --request")
    request_path = Path(path)
    raw = request_path.read_bytes()
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("request is too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("request must be a JSON object")
    return json.dumps(value, separators=(",", ":")).encode()


def command(action: str, agent: str, timeout: float) -> list[str]:
    gateway_args = [
        str(PRIVATE_GATEWAY),
        action,
        "--agent",
        agent,
        "--timeout",
        str(timeout),
    ]
    if PRIVATE_GATEWAY.exists():
        return ["/usr/bin/python3", *gateway_args]
    return ["ssh", "jaimes", "/usr/bin/python3", *gateway_args]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate or request an approval-gated managed-wallet signature"
    )
    parser.add_argument("action", choices=("status", "canary", "validate", "sign"))
    parser.add_argument("--agent", required=True, choices=ALLOWED_AGENTS)
    parser.add_argument("--request")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    try:
        raw = load_request(args.request, args.action)
        proc = subprocess.run(
            command(args.action, args.agent, max(1.0, min(args.timeout, 60.0))),
            input=raw,
            capture_output=True,
            timeout=max(5.0, min(args.timeout + 5.0, 65.0)),
        )
        if proc.stdout:
            sys.stdout.buffer.write(proc.stdout)
        if proc.stderr:
            sys.stderr.buffer.write(proc.stderr)
        return proc.returncode
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "broadcast": False,
                },
                indent=2,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#JAIMES: this client exposes one shared request path while the wallet secret and signed payload stay on JAIMES.
