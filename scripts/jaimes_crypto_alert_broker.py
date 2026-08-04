#!/usr/bin/env python3
"""Fixed local Telegram capability broker for JAIMES Crypto Radar alerts.

The broker owns the live JAIMES Telegram credential in memory. Clients may
request only three fixed operations and can never provide a destination,
message, token, file path, or command.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import stat
import sys
import time
from types import ModuleType
from typing import Any


PRIVATE_DIR = Path("/Users/jc_agent/.openclaw/private")
SOCKET_PATH = PRIVATE_DIR / "crypto-radar-telegram-broker.sock"
AUDIT_PATH = PRIVATE_DIR / "crypto-radar-telegram-broker-audit.jsonl"
CONSUMER_PATH = Path("/Users/jc_agent/crypto-radar-runtime/broker/crypto_radar_watch_delivery.py")
EXPECTED_CHAT_ID = "-1003589561528"
EXPECTED_TOPIC_ID = 20
CANARY_MESSAGE = (
    "JAIMES CRYPTO ALERTS CANARY\n"
    "Fixed local delivery broker is healthy. No trade or wallet action taken."
)
MAX_REQUEST_BYTES = 256
ALLOWED_OPERATIONS = frozenset({"health", "deliver-next", "canary"})


def stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def audit(*, operation: str | None, result: str) -> None:
    """Persist metadata only; never request content, command output, or secrets."""
    record = {"time": stamp(), "operation": operation, "result": result}
    AUDIT_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    os.chmod(AUDIT_PATH, 0o600)


def load_consumer() -> ModuleType:
    info = CONSUMER_PATH.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise RuntimeError("consumer-integrity")
    spec = importlib.util.spec_from_file_location("jaimes_crypto_radar_watch_delivery", CONSUMER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("consumer-unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if str(getattr(module, "CHAT_ID", "")) != EXPECTED_CHAT_ID:
        raise RuntimeError("destination-mismatch")
    if int(getattr(module, "TOPIC_ID", -1)) != EXPECTED_TOPIC_ID:
        raise RuntimeError("destination-mismatch")
    if not callable(getattr(module, "run", None)) or not callable(getattr(module, "send", None)):
        raise RuntimeError("consumer-interface")
    return module


def execute(operation: str, consumer: ModuleType) -> dict[str, Any]:
    if operation not in ALLOWED_OPERATIONS:
        audit(operation=operation, result="denied")
        return {"ok": False, "error": "ERR_OPERATION_DENIED"}
    if operation == "health":
        audit(operation=operation, result="ok")
        return {"ok": True, "operation": operation, "status": "ready"}
    if operation == "deliver-next":
        exit_code = int(consumer.run(dry_run=False))
        result = "ok" if exit_code == 0 else "consumer-failed"
        audit(operation=operation, result=result)
        return {
            "ok": exit_code == 0,
            "operation": operation,
            "exit_code": exit_code,
            "error": None if exit_code == 0 else "ERR_DELIVERY_CONSUMER",
        }
    ok, category, message_id = consumer.send({}, CANARY_MESSAGE)
    result = "ok" if ok else "canary-failed"
    audit(operation=operation, result=result)
    return {
        "ok": bool(ok),
        "operation": operation,
        "message_id": message_id if isinstance(message_id, int) else None,
        "error": None if ok else str(category or "ERR_CANARY"),
    }


def handle_request(data: bytes, consumer: ModuleType) -> dict[str, Any]:
    if not data or len(data) > MAX_REQUEST_BYTES:
        return {"ok": False, "error": "ERR_INVALID_REQUEST"}
    try:
        request = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "ERR_INVALID_REQUEST"}
    if not isinstance(request, dict) or set(request) != {"operation"}:
        return {"ok": False, "error": "ERR_INVALID_REQUEST"}
    operation = request.get("operation")
    if not isinstance(operation, str):
        return {"ok": False, "error": "ERR_INVALID_REQUEST"}
    return execute(operation, consumer)


def serve() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or any(character.isspace() for character in token):
        print("JAIMES Crypto Alerts broker unavailable: Telegram capability is absent", file=sys.stderr)
        return 69
    consumer = load_consumer()
    PRIVATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(PRIVATE_DIR, 0o700)
    if SOCKET_PATH.exists() or SOCKET_PATH.is_socket():
        SOCKET_PATH.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o600)
    server.listen(8)

    def stop(_signum: int, _frame: object) -> None:
        server.close()
        SOCKET_PATH.unlink(missing_ok=True)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    audit(operation=None, result="started")
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            return 0
        with conn:
            conn.settimeout(30)
            try:
                data = conn.recv(MAX_REQUEST_BYTES + 1)
                response = handle_request(data, consumer)
            except (OSError, TimeoutError):
                response = {"ok": False, "error": "ERR_REQUEST_TIMEOUT"}
            try:
                conn.sendall(json.dumps(response, sort_keys=True).encode("utf-8"))
            except (BrokenPipeError, OSError):
                pass


if __name__ == "__main__":
    raise SystemExit(serve())
