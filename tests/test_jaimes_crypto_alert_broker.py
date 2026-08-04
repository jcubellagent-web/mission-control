#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "jaimes_crypto_alert_broker.py"
if not MODULE_PATH.exists():
    MODULE_PATH = Path(__file__).with_name("jaimes_crypto_alert_broker.py")
SPEC = importlib.util.spec_from_file_location("jaimes_crypto_alert_broker", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
broker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(broker)


class FakeConsumer(types.SimpleNamespace):
    def __init__(self) -> None:
        super().__init__()
        self.run_calls = 0
        self.sent: list[tuple[object, str]] = []

    def run(self, *, dry_run: bool) -> int:
        self.run_calls += 1
        return 0 if dry_run is False else 1

    def send(self, record: object, message: str) -> tuple[bool, None, int]:
        self.sent.append((record, message))
        return True, None, 123


class BrokerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.audit_patch = mock.patch.object(broker, "AUDIT_PATH", Path(self.temporary.name) / "audit.jsonl")
        self.audit_patch.start()
        self.consumer = FakeConsumer()

    def tearDown(self) -> None:
        self.audit_patch.stop()
        self.temporary.cleanup()

    def test_rejects_caller_payload(self) -> None:
        response = broker.handle_request(
            json.dumps({"operation": "canary", "message": "caller text"}).encode(), self.consumer
        )
        self.assertEqual(response, {"ok": False, "error": "ERR_INVALID_REQUEST"})
        self.assertEqual(self.consumer.sent, [])

    def test_deliver_next_invokes_only_fixed_consumer(self) -> None:
        response = broker.handle_request(b'{"operation":"deliver-next"}', self.consumer)
        self.assertTrue(response["ok"])
        self.assertEqual(self.consumer.run_calls, 1)

    def test_canary_uses_fixed_text(self) -> None:
        response = broker.handle_request(b'{"operation":"canary"}', self.consumer)
        self.assertTrue(response["ok"])
        self.assertEqual(self.consumer.sent, [({}, broker.CANARY_MESSAGE)])

    def test_unknown_operation_is_denied(self) -> None:
        response = broker.handle_request(b'{"operation":"send-anything"}', self.consumer)
        self.assertEqual(response, {"ok": False, "error": "ERR_OPERATION_DENIED"})

    def test_health_returns_no_sensitive_fields(self) -> None:
        response = broker.handle_request(b'{"operation":"health"}', self.consumer)
        self.assertEqual(response, {"ok": True, "operation": "health", "status": "ready"})


if __name__ == "__main__":
    unittest.main()
