import html
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "inbox_coordinator.py"


def load_module():
    spec = importlib.util.spec_from_file_location("inbox_coordinator", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class InboxCoordinatorTests(unittest.TestCase):
    def configure_private_state(self, coordinator, root: Path):
        coordinator.PRIVATE_DIR = root / "private"
        coordinator.STATE_PATH = coordinator.PRIVATE_DIR / "jobs.json"
        coordinator.LOCK_PATH = coordinator.PRIVATE_DIR / "jobs.lock"
        coordinator.TELEMETRY_PATH = root / "telemetry.jsonl"
        coordinator.publish_control_tower = lambda *args, **kwargs: None

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

    def test_explicit_glm_request_selects_glm_when_healthy(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Use GLM for this task.",
            injected_health={"glm": True, "luna": True},
        )
        self.assertEqual(route["requestedRouteId"], "glm")
        self.assertEqual(route["routeId"], "glm")
        self.assertIs(route["explicitRequest"], True)

    def test_private_or_secret_terms_stay_on_josh_lane(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Summarize this OAuth token failure without exposing the token.",
            injected_health={"gemini": True, "luna": True},
        )
        self.assertEqual(route["routeId"], "luna")
        self.assertEqual(route["provider"], "codex")

    def test_explicit_remote_model_cannot_override_privacy_policy(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Use Grok on this OAuth token incident.",
            privacy="sensitive-account",
            injected_health={"grok": True, "luna": True},
        )
        self.assertEqual(route["requestedRouteId"], "grok")
        self.assertEqual(route["routeId"], "luna")
        self.assertIs(route["policyAllowed"], False)
        self.assertIn("privacy policy blocked", route["fallback"])

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
        self.assertIn("Model: codex/gpt-5.6-luna | Route:", decoded)
        self.assertIn("Josh 2.0 Inbox coordinator | Why:", decoded)
        self.assertIn("fast coordination", decoded)
        self.assertIn("Complete: Yes", decoded)
        self.assertIn("- n/a", decoded)
        self.assertLessEqual(max(len(line) for line in decoded.removeprefix("<pre>").removesuffix("</pre>").splitlines()), 38)

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
            self.assertNotIn("promptSignature", row)
            self.assertEqual(row["model"], "gpt-5.6-luna")

    def test_deduplication_and_private_state_permissions(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.configure_private_state(coordinator, root)
            route = coordinator.route_prompt("Routine check", injected_health={"luna": True})
            origin = {"runId": "run-1", "cardKey": "card-1", "chatId": "chat", "threadId": "1"}
            first, first_deduped = coordinator.make_job("Routine check", route, origin, 30)
            second, second_deduped = coordinator.make_job("Routine check", route, origin, 30)
            self.assertIs(first_deduped, False)
            self.assertIs(second_deduped, True)
            self.assertEqual(first["jobId"], second["jobId"])
            self.assertEqual(stat.S_IMODE(coordinator.STATE_PATH.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(Path(first["promptPath"]).stat().st_mode), 0o600)

    def test_retry_preserves_prompt_then_result_is_consumed_once(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.configure_private_state(coordinator, root)
            coordinator.spawn_worker = lambda job_id: None
            coordinator.deliver_result = lambda *args, **kwargs: True
            route = coordinator.route_prompt("Retry me", injected_health={"luna": True})
            job, _ = coordinator.make_job("Retry me", route, {"runId": "run-2"}, 30)
            prompt_path = Path(job["promptPath"])

            coordinator.execute_route = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("transient"))
            first = coordinator.run_worker(job["jobId"])
            self.assertEqual(first["outcome"], "retry")
            self.assertTrue(prompt_path.exists())

            coordinator.execute_route = lambda *args, **kwargs: {
                "output": "finished",
                "actualHost": "josh2",
                "actualWorker": "test-worker",
                "actualProvider": "codex",
                "actualModel": "gpt-5.6-luna",
                "modelVerified": True,
                "executionVerified": True,
            }
            second = coordinator.run_worker(job["jobId"])
            self.assertEqual(second["outcome"], "done")
            self.assertFalse(prompt_path.exists())
            taken = coordinator.take_result(job["jobId"])
            self.assertEqual(taken["output"], "finished")
            self.assertFalse(coordinator.take_result(job["jobId"])["ok"])

    def test_sensitive_prompt_is_never_persisted_or_hashed(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.configure_private_state(coordinator, root)
            route = coordinator.route_prompt("Handle this API key safely", injected_health={"luna": True})
            job, _ = coordinator.make_job(
                "Handle this API key safely",
                route,
                {"messageId": "100", "chatId": "chat", "threadId": "1"},
                30,
            )
            self.assertIs(job["promptEphemeral"], True)
            self.assertEqual(job["promptPath"], "")
            self.assertEqual(job["promptSignature"], "")
            persisted = coordinator.STATE_PATH.read_text()
            self.assertNotIn("API key", persisted)

    def test_postprocessor_is_natural_and_redacts_secret_values(self):
        coordinator = load_module()
        route = {
            "routeId": "grok",
            "provider": "xai",
            "worker": "jaimes-grok-public",
            "host": "jaimes",
            "routingReason": "explicit model request",
            "fallback": "",
        }
        execution = {
            "actualProvider": "xai",
            "actualModel": "grok-test",
            "actualWorker": "jaimes-grok-public",
            "actualHost": "jaimes",
            "executionVerified": True,
        }
        text = coordinator.render_final_html(
            route,
            execution,
            "**The Inbox route is working naturally.**\n\n- Checked the route\n- token: abcdefghijklmnop\n\nNext, send it a normal request.",
        )
        decoded = html.unescape(text)
        self.assertIn("The Inbox route is working naturally.", decoded)
        self.assertIn("Next, send it a normal request.", decoded)
        self.assertNotIn("Model:", decoded)
        self.assertNotIn("jaimes-grok-public", decoded)
        self.assertNotIn("**", decoded)
        self.assertNotIn("abcdefghijklmnop", decoded)
        self.assertIn("[redacted]", decoded)
        self.assertFalse(text.startswith("<pre>"))
        self.assertLessEqual(len(decoded), 3950)

    def test_worker_contract_requests_a_direct_natural_answer(self):
        coordinator = load_module()
        self.assertIn("Answer the user directly", coordinator.WORKER_OUTPUT_CONTRACT)
        self.assertIn("do not force the reply into status-report sections", coordinator.WORKER_OUTPUT_CONTRACT)
        self.assertNotIn("using exactly these plain-text sections", coordinator.WORKER_OUTPUT_CONTRACT)

    def test_recovery_requeues_only_dead_workers(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.configure_private_state(coordinator, root)
            route = coordinator.route_prompt("Recover me", injected_health={"luna": True})
            job, _ = coordinator.make_job("Recover me", route, {"runId": "run-3"}, 30)
            state = coordinator.read_json(coordinator.STATE_PATH, {"jobs": {}})
            state["jobs"][job["jobId"]].update({"status": "running", "workerPid": 99999999, "leaseToken": "old"})
            coordinator.save_json(coordinator.STATE_PATH, state)
            spawned = []
            coordinator.spawn_worker = spawned.append
            result = coordinator.recover()
            self.assertEqual(result["recovered"], 1)
            self.assertEqual(spawned, [job["jobId"]])


if __name__ == "__main__":
    unittest.main()
