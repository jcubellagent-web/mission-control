import ast
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "jaimes_telegram_fast_ack.py"


def load_fast_ack():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("jaimes_fast_ack_lease_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FastAckLeaseFreeTests(unittest.TestCase):
    def test_publish_jaimes_refuses_lease_bearing_events(self):
        module = load_fast_ack()
        with mock.patch.object(
            module.subprocess,
            "run",
            side_effect=AssertionError(
                "lease-bearing fast-ack publication reached subprocess"
            ),
        ):
            self.assertFalse(module.publish_jaimes("intake", "active", "ack"))
            self.assertFalse(
                module.publish_jaimes(
                    "intake", "accepted", "ack", work_event="start"
                )
            )
            self.assertFalse(
                module.publish_jaimes(
                    "intake", "accepted", "ack", work_event="heartbeat"
                )
            )

    def test_fast_ack_call_sites_never_request_active_work_ownership(self):
        source = SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(source)
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "publish_jaimes":
                continue
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                if node.args[1].value == "active":
                    violations.append((node.lineno, "active"))
            for keyword in node.keywords:
                if keyword.arg != "work_event" or not isinstance(
                    keyword.value, ast.Constant
                ):
                    continue
                if keyword.value.value in {"start", "heartbeat"}:
                    violations.append((node.lineno, keyword.value.value))
        self.assertEqual(violations, [])

    def test_telegram_card_heartbeat_remains_enabled(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'run_gateway_card_command(card, cmd, status="heartbeat")', source
        )
        self.assertIn('updates.append({"event": f"heartbeat:', source)
        self.assertIn('work_event="terminal"', source)


if __name__ == "__main__":
    unittest.main()
