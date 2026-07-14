import html
import importlib.util
import json
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "inbox_coordinator.py"


def load_module():
    spec = importlib.util.spec_from_file_location("inbox_coordinator", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class InboxCoordinatorTests(unittest.TestCase):
    def test_explicit_model_request_wins_when_healthy(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Use Grok for the public X/current-events read.",
            injected_health={"grok": True, "luna": True, "terra": True},
        )
        self.assertEqual(route["routeId"], "grok")
        self.assertIs(route["explicitRequest"], True)
        self.assertIs(route["requestedRouteHealthy"], True)
        self.assertEqual(route["provider"], "xai")

    def test_explicit_model_request_falls_back_when_unhealthy(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Use Gemini for this review.",
            injected_health={"gemini": False, "luna": True, "terra": True},
        )
        self.assertEqual(route["requestedRouteId"], "gemini")
        self.assertEqual(route["routeId"], "luna")
        self.assertIn("unhealthy", route["fallback"])

    def test_private_or_secret_terms_stay_on_josh_lane(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Summarize this OAuth token failure without exposing the token.",
            injected_health={"gemini": True, "luna": True},
        )
        self.assertEqual(route["routeId"], "luna")
        self.assertEqual(route["provider"], "codex")

    def test_final_summary_is_deterministic_pre_block(self):
        coordinator = load_module()

        class Args:
            model = "codex/gpt-5.6-luna"
            route = "Josh 2.0 Inbox coordinator"
            why = "fast coordination"
            complete = True
            done = ["Acknowledged", "Routed"]
            issue = []
            next = []
            approval = []

        text = coordinator.format_final(Args)
        self.assertTrue(text.startswith("<pre>"))
        self.assertTrue(text.endswith("</pre>"))
        decoded = html.unescape(text)
        self.assertIn("Model: codex/gpt-5.6-luna | Route: Josh 2.0 Inbox coordinator | Why: fast coordination", decoded)
        self.assertIn("Complete: Yes", decoded)
        self.assertIn("- n/a", decoded)

    def test_telemetry_excludes_prompt_and_output(self):
        coordinator = load_module()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            telemetry = Path(tmp) / "telemetry.jsonl"
            coordinator.TELEMETRY_PATH = telemetry
            coordinator.append_telemetry({
                "routeId": "luna",
                "worker": "josh2-codex-luna",
                "provider": "codex",
                "model": "gpt-5.6-luna",
                "host": "josh2",
                "routingReason": "fast Inbox coordination",
                "latencyMs": 1,
                "outcome": "routed",
                "prompt": "SECRET",
                "output": "SECRET",
            })
            row = json.loads(telemetry.read_text().splitlines()[0])
            self.assertNotIn("prompt", row)
            self.assertNotIn("output", row)
            self.assertEqual(row["model"], "gpt-5.6-luna")


if __name__ == "__main__":
    unittest.main()
