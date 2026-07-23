import html
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "inbox_coordinator.py"
TELEGRAM_HEALTH_RESULT = """Complete: Yes — current Telegram health was assessed read-only.

What was done:
- The local OpenClaw gateway is running and listening on port 18790, but this environment could not complete its loopback connectivity probe.
- The local launchd domain has no registered Josh 2.0 or JAIMES Telegram fast-ack service entries.
- The available Josh Telegram logs are empty and last modified May 5, so they provide no current delivery evidence.

Issues:
- Live Telegram API and end-to-end message delivery could not be verified from this restricted environment.
- Fast-ack service registration and fresh operational logging are unverified.

Appropriate next steps:
- Run the existing read-only Telegram health probe on the service-owning host to confirm gateway connectivity, watcher status, and a fresh Telegram identity check.

Approval needed:
- n/a"""


def load_module():
    spec = importlib.util.spec_from_file_location("inbox_coordinator", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class InboxCoordinatorTests(unittest.TestCase):
    def test_work_card_uses_telegram_capable_workspace_helper(self):
        coordinator = load_module()
        self.assertEqual(coordinator.WORK_CARD_SCRIPT, coordinator.WORKSPACE / "scripts" / "josh_work_card.py")
        self.assertEqual(coordinator.SEND_REPLY_SCRIPT, coordinator.WORK_CARD_SCRIPT.with_name("send_josh_reply.py"))

    def test_progress_uses_fixed_gateway_code_and_no_worker_text(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordinator.TELEGRAM_GATEWAY_SCRIPT = root / "josh_telegram_fast_ack.py"
            coordinator.TELEGRAM_GATEWAY_SCRIPT.write_text("", encoding="utf-8")
            calls = []

            def fake_run(command, **kwargs):
                calls.append((list(command), str(kwargs.get("input") or "")))
                return type("Result", (), {
                    "returncode": 0,
                    "stdout": json.dumps({"ok": True, "status": "progress-recorded"}),
                    "stderr": "",
                })()

            snapshot = {
                "origin": {
                    "runId": "run-safe-1",
                    "cardKey": "card-safe-1",
                    "chatId": "private-chat-target",
                    "threadId": "1",
                }
            }
            with patch.object(coordinator.subprocess, "run", side_effect=fake_run):
                self.assertTrue(coordinator.update_card_progress(snapshot, "worker_started"))
                self.assertFalse(coordinator.update_card_progress(snapshot, "MODEL SAID secret text"))
            self.assertEqual(len(calls), 1)
            self.assertIn("--progress-event-json-stdin", calls[0][0])
            self.assertEqual(json.loads(calls[0][1]), {
                "runId": "run-safe-1",
                "progressCode": "worker_started",
            })
            self.assertNotIn("card-safe-1", calls[0][1])
            self.assertNotIn("private-chat-target", calls[0][1])

    def test_progress_retries_only_until_the_run_card_binding_is_ready(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordinator.TELEGRAM_GATEWAY_SCRIPT = root / "josh_telegram_fast_ack.py"
            coordinator.TELEGRAM_GATEWAY_SCRIPT.write_text("", encoding="utf-8")
            not_ready = type("Result", (), {
                "returncode": 4,
                "stdout": json.dumps({"ok": False, "status": "run-card-not-ready"}),
                "stderr": "",
            })()
            accepted = type("Result", (), {
                "returncode": 0,
                "stdout": json.dumps({"ok": True, "status": "progress-recorded"}),
                "stderr": "",
            })()
            snapshot = {"origin": {"runId": "run-race-1"}}
            with patch.object(coordinator.subprocess, "run", side_effect=[not_ready, accepted]) as run, \
                 patch.object(coordinator.time, "sleep") as sleep:
                self.assertTrue(coordinator.update_card_progress(snapshot, "worker_started"))
            self.assertEqual(run.call_count, 2)
            sleep.assert_called_once_with(0.1)

    def test_fallback_progress_is_fixed_and_disclosed_before_execution(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordinator.TELEGRAM_GATEWAY_SCRIPT = root / "josh_telegram_fast_ack.py"
            coordinator.TELEGRAM_GATEWAY_SCRIPT.write_text("", encoding="utf-8")
            accepted = type("Result", (), {
                "returncode": 0,
                "stdout": json.dumps({"ok": True, "status": "progress-recorded"}),
                "stderr": "",
            })()
            with patch.object(coordinator.subprocess, "run", return_value=accepted) as run:
                self.assertTrue(coordinator.update_card_progress(
                    {"origin": {"runId": "run-fallback-1"}},
                    "fallback_selected",
                ))
            payload = json.loads(str(run.call_args.kwargs["input"]))
            self.assertEqual(payload, {
                "runId": "run-fallback-1",
                "progressCode": "fallback_selected",
            })

    def test_progress_rejects_a_zero_exit_without_an_accepted_receipt(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordinator.TELEGRAM_GATEWAY_SCRIPT = root / "josh_telegram_fast_ack.py"
            coordinator.TELEGRAM_GATEWAY_SCRIPT.write_text("", encoding="utf-8")
            ambiguous = type("Result", (), {
                "returncode": 0,
                "stdout": json.dumps({"ok": False, "status": "progress-card-update-failed"}),
                "stderr": "",
            })()
            with patch.object(coordinator.subprocess, "run", return_value=ambiguous) as run, \
                 patch.object(coordinator.time, "sleep") as sleep:
                self.assertFalse(coordinator.update_card_progress(
                    {"origin": {"runId": "run-ambiguous-1"}},
                    "verifying",
                ))
            self.assertEqual(run.call_count, 1)
            sleep.assert_not_called()

    def test_execution_checkpoint_is_lease_bound_and_allowlisted(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            self.configure_private_state(coordinator, Path(tmp))
            coordinator.PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
            coordinator.save_json(coordinator.STATE_PATH, {"jobs": {"job-1": {
                "jobId": "job-1",
                "status": "running",
                "leaseToken": "lease-1",
            }}})
            execution = {
                "actualHost": "josh2",
                "actualWorker": "worker",
                "actualProvider": "codex",
                "actualModel": "verified-model",
                "actualAuth": "Verified runtime checkpoint",
                "authVerified": True,
                "modelVerified": True,
                "executionVerified": True,
                "output": "must not persist in progress checkpoint",
                "token": "must not persist either",
            }
            self.assertFalse(coordinator.checkpoint_worker_execution("job-1", "wrong", execution))
            self.assertTrue(coordinator.checkpoint_worker_execution("job-1", "lease-1", execution))
            persisted = json.loads(coordinator.STATE_PATH.read_text())["jobs"]["job-1"]
            self.assertNotIn("output", persisted["actual"])
            self.assertNotIn("token", persisted["actual"])
            self.assertTrue(persisted["actual"]["executionVerified"])
            self.assertEqual(persisted["actual"]["actualAuth"], "Verified runtime checkpoint")
            self.assertIs(persisted["actual"]["authVerified"], True)

    def test_fallback_route_checkpoint_is_lease_bound_and_allowlisted(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            self.configure_private_state(coordinator, Path(tmp))
            coordinator.PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
            coordinator.save_json(coordinator.STATE_PATH, {"jobs": {"job-1": {
                "jobId": "job-1",
                "status": "running",
                "leaseToken": "lease-1",
            }}})
            route = {
                **coordinator.ROUTES["gemini-pro"],
                "routeId": "gemini-pro",
                "routingReason": "dashboard-safe model-routing audit",
                "fallback": "glm execution failed; selected gemini-pro",
                "privacy": "dashboard-safe",
                "secret": "must not persist",
            }
            self.assertFalse(coordinator.checkpoint_worker_route("job-1", "wrong", route))
            self.assertTrue(coordinator.checkpoint_worker_route("job-1", "lease-1", route))
            persisted = json.loads(coordinator.STATE_PATH.read_text())["jobs"]["job-1"]
            self.assertEqual(persisted["route"]["routeId"], "gemini-pro")
            self.assertEqual(persisted["route"]["auth"], "Antigravity session")
            self.assertNotIn("secret", persisted["route"])

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

    def test_explicit_model_request_fails_closed_when_unhealthy(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Use Gemini for this review.",
            injected_health={"gemini": False, "glm": False, "terra": True},
        )
        self.assertEqual(route["requestedRouteId"], "gemini")
        self.assertEqual(route["routeId"], "gemini")
        self.assertIs(route["ok"], False)
        self.assertIs(route["preflightVerified"], False)
        self.assertEqual(route["preflightError"], "route-unhealthy")
        self.assertEqual(route["outcome"], "blocked")
        self.assertEqual(route["fallback"], "")

    def test_submit_rejects_a_blocked_explicit_route_without_creating_a_job(self):
        coordinator = load_module()
        blocked = coordinator.route_prompt(
            "Use Gemini for this review.",
            injected_health={"gemini": False, "terra": True},
        )
        with tempfile.TemporaryDirectory() as tmp:
            self.configure_private_state(coordinator, Path(tmp))

            class Args:
                prompt = "Use Gemini for this review."
                prompt_file = ""
                privacy = "dashboard-safe"
                route_plan_json = json.dumps(blocked)
                origin_run_id = "run-explicit-blocked"
                message_id = "100"
                card_key = "card-explicit-blocked"
                chat_id = "chat"
                thread_id = "1"
                work_id = ""
                work_run_id = ""
                origin_claim_hash = ""
                delivery_tier = 3
                timeout = 30
                dry_run = False

            with patch.object(coordinator, "spawn_worker") as spawn:
                result = coordinator.submit_job(Args())
            spawn.assert_not_called()
            self.assertIs(result["ok"], False)
            self.assertEqual(result["status"], "explicit-route-preflight-failed")
            self.assertFalse(coordinator.STATE_PATH.exists())

    def test_negated_model_name_is_not_an_explicit_route(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Do not use Gemini; fix the broken production script.",
            injected_health={"gemini": True, "terra": True, "luna": True},
        )
        self.assertEqual(route["routeId"], "terra")
        self.assertIs(route["explicitRequest"], False)

    def test_execution_intent_wins_over_review_word(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Review and fix the broken integration.",
            injected_health={"gemini": True, "terra": True, "luna": True},
        )
        self.assertEqual(route["routeId"], "terra")

    def test_read_only_health_request_does_not_route_as_a_change(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Assess current Telegram health and give me three concrete findings. Make no changes.",
            injected_health={"luna": True, "terra": True},
        )
        self.assertEqual(route["routeId"], "luna")
        self.assertEqual(route["routingReason"], "read-only health/status check")

    def test_positive_repair_intent_wins_over_an_unrelated_no_changes_clause(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Check Telegram health, make no changes to the gateway, and fix the Fast Ack watcher.",
            injected_health={"luna": True, "terra": True},
        )
        self.assertEqual(route["routeId"], "terra")

    def test_explicit_glm_request_selects_glm_when_healthy(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Use GLM for this task.",
            injected_health={"glm": True, "luna": True},
        )
        self.assertEqual(route["requestedRouteId"], "glm")
        self.assertEqual(route["routeId"], "glm")
        self.assertIs(route["explicitRequest"], True)

    def test_glm_is_selected_for_dashboard_safe_technical_reasoning(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Give me a structured code review and make no edits.",
            injected_health={"glm": True, "terra": True},
        )
        self.assertEqual(route["routeId"], "glm")
        self.assertEqual(route["model"], "glm-5.2:cloud")
        self.assertEqual(route["routingReason"], "dashboard-safe large-context technical reasoning")

    def test_model_routing_audit_uses_verified_specialist_before_read_only_health(self):
        coordinator = load_module()
        prompt = (
            "Assess whether our model routing is resilient and whether private work and "
            "execution are routed appropriately. Make no changes.\n"
            "Return three findings, the verified model and authentication route actually "
            "used, any fallback that occurred, and a final conclusion of functioning or "
            "needs attention."
        )
        route = coordinator.route_prompt(
            prompt,
            injected_health={"glm": True, "luna": True, "terra": True},
        )
        self.assertEqual(route["routeId"], "glm")
        self.assertEqual(route["model"], "glm-5.2:cloud")
        self.assertEqual(route["auth"], "Ollama Cloud")
        self.assertEqual(route["routingReason"], "dashboard-safe model-routing audit")

    def test_output_contract_route_fields_cannot_hijack_unrelated_requests(self):
        coordinator = load_module()
        output_contract = (
            "Return three findings, the verified model and authentication route actually "
            "used, any fallback, and a conclusion."
        )
        health = coordinator.route_prompt(
            f"Check Telegram health. {output_contract}",
            injected_health={"luna": True, "glm": True, "gemini": True},
        )
        review = coordinator.route_prompt(
            f"Review this document. {output_contract}",
            injected_health={"gemini": True, "glm": True, "luna": True},
        )
        self.assertEqual(health["routeId"], "luna")
        self.assertEqual(health["routingReason"], "fast Inbox coordination")
        self.assertEqual(review["routeId"], "gemini")
        self.assertEqual(review["routingReason"], "dashboard-safe review/summarization")

        comparison = coordinator.route_prompt(
            "Review this document. Include a comparison with Gemini.",
            injected_health={"gemini": False, "glm": True, "terra": True},
        )
        self.assertIs(comparison["explicitRequest"], False)
        self.assertEqual(comparison["routeId"], "glm")
        self.assertIn("gemini unhealthy; selected glm", comparison["fallback"])

    def test_model_routing_audit_uses_policy_fallback_not_shallow_luna(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Audit the model routing, provider authentication, and fallback policy read-only.",
            injected_health={"glm": False, "gemini-pro": True, "terra": True, "luna": True},
        )
        self.assertEqual(route["routeId"], "gemini-pro")
        self.assertIn("glm unhealthy; selected gemini-pro", route["fallback"])

    def test_telegram_response_behavior_audit_uses_read_only_terra(self):
        coordinator = load_module()
        prompt = "Please run an audit to make sure your Telegram response behavior is as expected."
        route = coordinator.route_prompt(
            prompt,
            injected_health={"terra": True, "luna": True, "gemini": True},
        )
        self.assertEqual(route["routeId"], "terra")
        self.assertEqual(route["routingReason"], "trusted Telegram response-contract audit")
        self.assertTrue(coordinator.read_only_execution_requested(prompt))

    def test_approved_inbox_e2e_canary_uses_terra_and_bypasses_health_override(self):
        coordinator = load_module()
        prompt = (
            "Run a full end-to-end reliability verification for this exact Inbox topic. "
            "I explicitly approve one temporary production canary and deletion of all "
            "canary messages afterward. Verify Telegram delivery and cleanup."
        )
        route = coordinator.route_prompt(
            prompt,
            injected_health={"terra": True, "luna": True},
        )
        self.assertEqual(route["routeId"], "terra")
        self.assertEqual(
            route["routingReason"],
            "trusted Telegram end-to-end reliability verification",
        )
        self.assertFalse(coordinator.read_only_execution_requested(prompt))
        with patch.object(coordinator.subprocess, "run") as run:
            self.assertIsNone(coordinator.telegram_health_host_context(prompt))
        run.assert_not_called()

    def test_e2e_canary_requires_explicit_approval_and_confirmed_cleanup(self):
        coordinator = load_module()
        base = "Run an end-to-end reliability verification for this Inbox topic."
        missing = coordinator.telegram_e2e_canary_context(
            base,
            {"chatId": "-1003589561528", "threadId": "1"},
        )
        self.assertEqual(missing["status"], "missing-approval")
        self.assertFalse(missing["executed"])

        prompt = (
            base + " I explicitly approve one temporary production canary and deletion "
            "of all canary messages afterward."
        )
        receipt = {
            "ok": True,
            "problemCount": 0,
            "stress": {"ok": True, "iterations": 100, "renderedCards": 100, "problemCount": 0},
            "transport": {
                "ok": True,
                "status": "passed",
                "failureCount": 0,
                "cleanup": {
                    "status": "pending", "attempted": 3, "deleted": 2,
                    "failedCount": 1, "indeterminateCount": 0,
                },
                "final": {"attempts": 1, "successes": 1, "count": 1},
            },
        }
        completed = type("Result", (), {"returncode": 0, "stdout": json.dumps(receipt)})()
        with tempfile.TemporaryDirectory() as tmp:
            coordinator.PRIVATE_DIR = Path(tmp)
            coordinator.TELEGRAM_RESPONSE_CANARY_SCRIPT = Path(tmp) / "canary.py"
            coordinator.TELEGRAM_RESPONSE_CANARY_SCRIPT.write_text("", encoding="utf-8")
            with patch.object(coordinator.subprocess, "run", return_value=completed):
                context = coordinator.telegram_e2e_canary_context(
                    prompt,
                    {"chatId": "-1003589561528", "threadId": "1"},
                )
        self.assertTrue(context["executed"])
        self.assertFalse(context["ok"])
        self.assertEqual(context["status"], "failed")
        self.assertNotIn("messageIds", json.dumps(context))
        gated = coordinator.enforce_e2e_canary_evidence_gate(
            "Complete: Yes\nWhat was done:\n- Everything passed.",
            context,
        )
        self.assertIn("Complete: Yes", gated)
        self.assertIn("ran but its transport", gated)
        blocked = dict(context, executed=False, status="missing-approval")
        self.assertIn(
            "Complete: No",
            coordinator.enforce_e2e_canary_evidence_gate("Complete: Yes", blocked),
        )

    def test_telegram_response_audit_guidance_separates_worker_and_delivery_contracts(self):
        coordinator = load_module()
        guidance = coordinator.telegram_response_audit_guidance(
            "Audit the Telegram reply contract and response formatting.",
            {
                "available": True,
                "josh2": {"ok": True},
                "jaimes": {"ok": True, "telegramState": "connected"},
            },
        )
        self.assertIn("worker returns only Complete", guidance)
        self.assertIn("delivery formatter must prepend", guidance)
        self.assertIn("Model: <verified provider/model> | Route: <actual lane> | Why:", guidance)
        self.assertIn("missing optional test runner", guidance)
        self.assertIn("completed audit with negative findings uses Complete: Yes", guidance)
        self.assertIn('"telegramState":"connected"', guidance)
        self.assertEqual(coordinator.telegram_response_audit_guidance("Summarize this note."), "")

    def test_telegram_response_audit_host_context_uses_fresh_allowlisted_host_facts(self):
        coordinator = load_module()
        checked_at = coordinator.utc_now()
        runtime = {
            "checkedAt": checked_at,
            "ok": True,
            "checks": {
                key: {"ok": True, "detail": "private detail is not forwarded"}
                for key in (
                    "controlTower", "brainFeed", "gateway", "telegramFastAck",
                    "telegramWorkCardHelper", "telegramInboxClaimHelper", "sourceFreshness",
                )
            },
        }
        jaimes = {
            "checkedAt": checked_at,
            "ok": True,
            "status": "ok",
            "probe": {
                "gatewayState": "running",
                "telegramState": "connected",
                "fastAckState": "running",
                "fastAckIdentity": {"ok": True, "private": "not forwarded"},
                "fastAckDelivery": {
                    "lastSurfaceOk": True,
                    "surfaceIndeterminate": False,
                    "terminalIssueCount": 0,
                    "private": "not forwarded",
                },
                "telegramSessionPresent": True,
            },
        }
        responses = [
            type("Result", (), {"returncode": 0, "stdout": json.dumps(runtime)})(),
            type("Result", (), {"returncode": 0, "stdout": json.dumps(jaimes)})(),
        ]
        with patch.object(coordinator.subprocess, "run", side_effect=responses):
            context = coordinator.telegram_response_audit_host_context(
                "Audit the Telegram response behavior and lifecycle."
            )
        self.assertTrue(context["available"])
        self.assertTrue(context["josh2"]["checks"]["gateway"]["ok"])
        self.assertEqual(context["jaimes"]["telegramState"], "connected")
        self.assertNotIn("detail", context["josh2"]["checks"]["gateway"])
        self.assertNotIn("private", context["jaimes"])

    def test_glm_cloud_cannot_receive_private_context(self):
        coordinator = load_module()
        route = coordinator.route_prompt(
            "Use GLM 5.2 to review this OAuth token incident.",
            privacy="sensitive-account",
            injected_health={"glm": True, "terra": True},
        )
        self.assertEqual(route["requestedRouteId"], "glm")
        self.assertEqual(route["routeId"], "glm")
        self.assertIs(route["ok"], False)
        self.assertIs(route["preflightVerified"], False)
        self.assertEqual(route["preflightError"], "privacy-policy")
        self.assertEqual(route["fallback"], "")

    def test_glm_health_requires_authenticated_jaimes_cloud_probe(self):
        coordinator = load_module()
        with patch.object(coordinator, "remote_check", return_value=False) as remote:
            self.assertIs(coordinator.health("glm"), False)
            self.assertIn("glm-5.2:cloud", remote.call_args.args[0])
        with patch.object(coordinator, "remote_check", return_value=True):
            self.assertIs(coordinator.health("glm"), True)

    def test_runtime_execution_failure_launches_fresh_disclosed_fallback(self):
        coordinator = load_module()
        initial = coordinator.route_prompt(
            "Audit model routing and authentication fallback behavior read-only.",
            injected_health={"glm": True},
        )
        attempts = []
        events = []

        def fake_execute(_prompt, route, _timeout):
            events.append(f"execute:{route['routeId']}")
            attempts.append(route["routeId"])
            if route["routeId"] == "glm":
                raise RuntimeError("primary failed")
            return {
                "output": "Complete: Yes",
                "actualHost": "jaimes",
                "actualWorker": route["worker"],
                "actualProvider": route["provider"],
                "actualModel": route["model"],
                "modelVerified": True,
                "executionVerified": True,
            }

        with patch.object(coordinator, "execute_route", side_effect=fake_execute):
            effective, execution = coordinator.execute_route_with_fallback(
                "safe prompt",
                initial,
                60,
                injected_health={"gemini-pro": True, "terra": True},
                on_fallback=lambda route: events.append(f"disclose:{route['routeId']}") or True,
            )
        self.assertEqual(attempts, ["glm", "gemini-pro"])
        self.assertEqual(events, ["execute:glm", "disclose:gemini-pro", "execute:gemini-pro"])
        self.assertEqual(effective["routeId"], "gemini-pro")
        self.assertEqual(effective["auth"], "Antigravity session")
        self.assertIn("glm (ollama/glm-5.2:cloud) execution failed", effective["fallback"])
        self.assertEqual(effective["attemptedRoutes"], ["glm", "gemini-pro"])
        self.assertEqual(execution["actualModel"], "gemini-3.1-pro-high")

    def test_runtime_fallback_fails_closed_when_every_eligible_route_fails(self):
        coordinator = load_module()
        initial = coordinator.route_prompt(
            "Audit model routing and authentication fallback behavior read-only.",
            injected_health={"glm": True},
        )
        with patch.object(coordinator, "execute_route", side_effect=RuntimeError("failed")):
            with self.assertRaisesRegex(
                coordinator.RouteExecutionError,
                "No verified execution route completed",
            ) as caught:
                coordinator.execute_route_with_fallback(
                    "safe prompt",
                    initial,
                    60,
                    injected_health={"gemini-pro": False, "terra": False},
                )
        self.assertEqual(caught.exception.attempts, ["glm"])
        self.assertEqual(caught.exception.route["attemptedRoutes"], ["glm"])
        self.assertIn("no eligible fallback remained", caught.exception.route["fallback"])

    def test_runtime_fallback_never_expands_beyond_the_original_policy_ladder(self):
        coordinator = load_module()
        cases = (
            (
                "Audit model routing and authentication fallback behavior read-only.",
                {"glm": True},
                ["glm", "gemini-pro", "terra"],
            ),
            (
                "Review this dashboard-safe document.",
                {"gemini": True},
                ["gemini", "glm", "terra"],
            ),
            (
                "Review this dashboard-safe document after the primary preflight fails.",
                {"gemini": False, "glm": True, "terra": True},
                ["glm", "terra"],
            ),
        )
        for prompt, initial_health, expected in cases:
            with self.subTest(prompt=prompt):
                initial = coordinator.route_prompt(prompt, injected_health=initial_health)
                self.assertEqual(
                    initial["policyRouteId"],
                    "glm" if prompt.startswith("Audit model") else "gemini",
                )
                attempts = []
                disclosures = []

                def fail(_prompt, route, _timeout):
                    attempts.append(route["routeId"])
                    raise RuntimeError("failed")

                with patch.object(coordinator, "execute_route", side_effect=fail):
                    with self.assertRaises(coordinator.RouteExecutionError) as caught:
                        coordinator.execute_route_with_fallback(
                            "safe prompt",
                            initial,
                            60,
                            injected_health={
                                "gemini": True,
                                "gemini-pro": True,
                                "glm": True,
                                "terra": True,
                                "sol": True,
                                "luna": True,
                            },
                            on_fallback=lambda route: disclosures.append(route["routeId"]) or True,
                        )
                self.assertEqual(attempts, expected)
                self.assertEqual(caught.exception.attempts, expected)
                self.assertEqual(disclosures, expected[1:])

    def test_runtime_fallback_does_not_execute_before_disclosure_is_accepted(self):
        coordinator = load_module()
        initial = coordinator.route_prompt(
            "Audit model routing and authentication fallback behavior read-only.",
            injected_health={"glm": True},
        )
        attempts = []

        def fail_primary(_prompt, route, _timeout):
            attempts.append(route["routeId"])
            raise RuntimeError("failed")

        with patch.object(coordinator, "execute_route", side_effect=fail_primary):
            with self.assertRaisesRegex(RuntimeError, "could not be disclosed"):
                coordinator.execute_route_with_fallback(
                    "safe prompt",
                    initial,
                    60,
                    injected_health={"gemini-pro": True},
                    on_fallback=lambda _route: False,
                )
        self.assertEqual(attempts, ["glm"])

    def test_runtime_explicit_failure_never_executes_or_discloses_a_fallback(self):
        coordinator = load_module()
        initial = coordinator.route_prompt(
            "Use GLM for this dashboard-safe audit.",
            injected_health={"glm": True},
        )
        attempts = []
        disclosures = []

        def fail_explicit(_prompt, route, _timeout):
            attempts.append(route["routeId"])
            raise RuntimeError("explicit route failed")

        with patch.object(coordinator, "execute_route", side_effect=fail_explicit):
            with self.assertRaisesRegex(coordinator.RouteExecutionError, "automatic fallback is disabled"):
                coordinator.execute_route_with_fallback(
                    "safe prompt",
                    initial,
                    60,
                    injected_health={"gemini-pro": True, "terra": True},
                    on_fallback=lambda route: disclosures.append(route["routeId"]) or True,
                )
        self.assertEqual(attempts, ["glm"])
        self.assertEqual(disclosures, [])

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
            injected_health={"grok": True, "terra": True},
        )
        self.assertEqual(route["requestedRouteId"], "grok")
        self.assertEqual(route["routeId"], "grok")
        self.assertIs(route["ok"], False)
        self.assertIs(route["policyAllowed"], False)
        self.assertIs(route["preflightVerified"], False)
        self.assertEqual(route["preflightError"], "privacy-policy")
        self.assertEqual(route["fallback"], "")

    def test_final_summary_is_deterministic_proportional_html(self):
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
        self.assertTrue(text.startswith("<b>JOSH 2.0 · COMPLETE</b>"))
        self.assertFalse(text.startswith("<pre>"))
        decoded = html.unescape(text)
        plain = re.sub(r"<[^>]+>", "", decoded)
        self.assertIn("Model: codex/gpt-5.6-luna | Route:", decoded)
        self.assertIn("Josh 2.0 Inbox coordinator | Why:", decoded)
        self.assertIn("fast coordination", decoded)
        self.assertIn("Complete: Yes", plain)
        self.assertIn("• None", decoded)
        self.assertIn("<blockquote>", decoded)

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

    def test_message_id_is_the_canonical_dedupe_identity(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            self.configure_private_state(coordinator, Path(tmp))
            route = coordinator.route_prompt("Routine check", injected_health={"luna": True})
            first, _ = coordinator.make_job("Routine check", route, {"messageId": "42", "runId": "run-a", "cardKey": "card-a", "chatId": "chat", "threadId": "1"}, 30)
            second, deduped = coordinator.make_job("Routine check", route, {"messageId": "42", "runId": "run-b", "cardKey": "card-b", "chatId": "chat", "threadId": "1"}, 30)
            self.assertIs(deduped, True)
            self.assertEqual(first["jobId"], second["jobId"])

    def test_retry_preserves_prompt_then_result_is_consumed_once(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.configure_private_state(coordinator, root)
            published = []
            coordinator.publish_control_tower = lambda *args, **kwargs: published.append((args, kwargs)) or True
            coordinator.spawn_worker = lambda job_id: None
            coordinator.deliver_result = lambda *args, **kwargs: True
            route = coordinator.route_prompt("Retry me", injected_health={"luna": True})
            job, _ = coordinator.make_job("Retry me", route, {"runId": "run-2"}, 30)
            prompt_path = Path(job["promptPath"])

            coordinator.execute_route = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("transient"))
            first = coordinator.run_worker(job["jobId"])
            self.assertEqual(first["outcome"], "retry")
            self.assertTrue(prompt_path.exists())
            self.assertEqual(published[-1][0][1], "active")

            coordinator.execute_route = lambda *args, **kwargs: {
                "output": "Complete: Yes\nWhat was done:\n- The retry test confirmed that the worker resumed from the saved private prompt.\n- The coordinator created one result artifact for deterministic Telegram delivery.\n- The successful handoff removed the private prompt before the result was consumed.\nIssues:\n- n/a\nAppropriate next steps:\n- No action needed.\nApproval needed:\n- n/a",
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
            self.assertIn("Complete: Yes", taken["output"])
            self.assertFalse(coordinator.take_result(job["jobId"])["ok"])

    def test_run_worker_marks_explicit_read_only_codex_execution(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            self.configure_private_state(coordinator, Path(tmp))
            coordinator.spawn_worker = lambda job_id: None
            coordinator.deliver_result = lambda *args, **kwargs: True
            coordinator.telegram_health_host_context = lambda prompt: None
            prompt = "Assess current Telegram health and give me three findings. Make no changes."
            route = coordinator.route_prompt(prompt, injected_health={"luna": True, "terra": True})
            job, _ = coordinator.make_job(prompt, route, {"runId": "run-read-only"}, 30)
            captured = {}

            def execute(_prompt, execution_route, _timeout):
                captured.update(execution_route)
                return {
                    "output": "Complete: Yes\nWhat was done:\n- Confirmed the gateway service passed its current health check.\n- Verified successfully that the Fast Ack service passed its host check.\n- Ran three service checks and all three checks passed.\nIssues:\n- n/a\nAppropriate next steps:\n- No action needed.\nApproval needed:\n- n/a",
                    "actualHost": "josh2",
                    "actualWorker": "test-worker",
                    "actualProvider": "codex",
                    "actualModel": "gpt-5.6-luna",
                    "modelVerified": True,
                    "executionVerified": True,
                }

            coordinator.execute_route = execute
            result = coordinator.run_worker(job["jobId"])
            self.assertEqual(result["outcome"], "done")
            self.assertIs(captured["readOnly"], True)

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

    def test_postprocessor_enforces_structured_final_and_redacts_secret_values(self):
        coordinator = load_module()
        route = {
            "routeId": "grok",
            "provider": "xai",
            "worker": "jaimes-grok-public",
            "host": "jaimes",
            "auth": "Grok CLI authentication",
            "routingReason": "explicit model request",
            "fallback": "gemini execution failed; selected grok",
        }
        execution = {
            "actualProvider": "xai",
            "actualModel": "grok-test",
            "actualWorker": "jaimes-grok-public",
            "actualHost": "jaimes",
            "actualAuth": "Grok CLI authentication",
            "authVerified": True,
            "modelVerified": True,
            "executionVerified": True,
        }
        text = coordinator.render_final_html(
            route,
            execution,
            "Complete: Yes\nWhat was done:\n- The Inbox route confirmed a successful response from the selected public model.\n- The delivery layer removed the worker identity before formatting the Telegram card.\n- The redaction check confirmed token: abcdefghijklmnop was removed before the final message.\nIssues:\n- n/a\nAppropriate next steps:\n- No action needed.\nApproval needed:\n- n/a",
        )
        decoded = html.unescape(text)
        body = re.sub(r"<[^>]+>", "", decoded)
        flat = " ".join(body.split())
        self.assertTrue(text.startswith("<b>JOSH 2.0 · COMPLETE</b>"))
        self.assertFalse(text.startswith("<pre>"))
        self.assertIn("Model: xai/grok-test | Route:", body)
        self.assertIn("auth=Grok CLI authentication", body)
        self.assertIn("fallback=gemini execution failed; selected grok", body)
        self.assertIn("Complete: Yes", body)
        self.assertIn("What was done:", body)
        self.assertIn("Issues:", body)
        self.assertIn("Appropriate next steps:", body)
        self.assertIn("Approval needed:", body)
        self.assertIn("The Inbox route confirmed a successful response", flat)
        self.assertIn("No action needed.", flat)
        self.assertNotIn("jaimes-grok-public", decoded)
        self.assertNotIn("**", decoded)
        self.assertNotIn("abcdefghijklmnop", decoded)
        self.assertIn("[redacted]", decoded)
        self.assertIn("• ", text)

    def test_final_auth_is_unverified_without_an_executor_checkpoint(self):
        coordinator = load_module()
        route = {
            "routeId": "gemini",
            "auth": "Antigravity session",
            "routingReason": "dashboard-safe review/summarization",
            "fallback": "",
        }
        base_execution = {
            "actualProvider": "gemini",
            "actualModel": "gemini-test",
            "modelVerified": True,
            "executionVerified": True,
        }
        output = (
            "Complete: Yes\nWhat was done:\n"
            "- The document identifies three supported operating constraints.\n"
            "- The review confirms the second section supersedes the first.\n"
            "- The conclusion recommends retaining the current policy.\n"
            "Issues:\n- n/a\nAppropriate next steps:\n- Keep the current policy.\n"
            "Approval needed:\n- n/a"
        )
        no_checkpoint = html.unescape(coordinator.render_final_html(route, base_execution, output))
        unverified_checkpoint = html.unescape(coordinator.render_final_html(
            route,
            {**base_execution, "actualAuth": "Antigravity session", "authVerified": False},
            output,
        ))
        self.assertIn("auth=unverified", no_checkpoint)
        self.assertIn("auth=unverified", unverified_checkpoint)
        self.assertNotIn("auth=Antigravity session", no_checkpoint)

    def test_weak_assessment_is_downgraded_without_invented_results(self):
        coordinator = load_module()
        sections = coordinator.parse_model_sections(
            "Complete: Yes\n"
            "What was done:\n"
            "- Assessment complete.\n"
            "- Reviewed the requested assessment.\n"
            "- Prepared the result for deterministic Telegram delivery.\n"
            "Issues:\n- n/a\n"
            "Appropriate next steps:\n- No action needed.\n"
            "Approval needed:\n- n/a"
        )
        self.assertIs(sections["complete"], False)
        self.assertIs(sections["summarySufficient"], False)
        self.assertIn(coordinator.MISSING_FINDINGS_ISSUE, sections["issues"])
        self.assertEqual(sections["next"], [coordinator.RETRY_FINDINGS_NEXT_STEP])
        self.assertEqual(len(sections["done"]), 3)
        self.assertIn("No unreported findings were inferred or presented as facts.", sections["done"])
        self.assertNotIn("Assessment complete.", sections["done"])
        self.assertNotIn("Prepared the result for deterministic Telegram delivery.", sections["done"])
        serialized = json.dumps({key: sections[key] for key in ("done", "issues", "next", "approval")})
        self.assertNotIn("completion claim requires", serialized.lower())
        self.assertNotIn("supplied summary contained", serialized.lower())
        self.assertNotIn("did not include enough concrete", serialized.lower())

    def test_quick_answer_accepts_one_concise_result_without_weakening_tier_three(self):
        coordinator = load_module()
        output = (
            "Complete: Yes — greeting received.\n"
            "What was done:\n"
            "- Acknowledged your message.\n"
            "Issues:\n- n/a\n"
            "Appropriate next steps:\n- n/a\n"
            "Approval needed:\n- n/a"
        )
        quick = coordinator.parse_model_sections(output, delivery_tier=1)
        substantial = coordinator.parse_model_sections(output, delivery_tier=3)
        self.assertIs(quick["complete"], True)
        self.assertIs(quick["summarySufficient"], True)
        self.assertEqual(quick["done"], ["greeting received.", "Acknowledged your message."])
        self.assertIs(substantial["complete"], False)
        self.assertIs(substantial["summarySufficient"], False)

    def test_quick_readiness_answer_preserves_direct_result_and_drops_formatter_metadata(self):
        coordinator = load_module()
        output = (
            "Complete: Yes — the response system is functioning for this test.\n"
            "What was done:\n"
            "- Followed the requested section order and plain-text format.\n"
            "- Omitted the prohibited Model line.\n"
            "- Kept the response concise and structured.\n"
            "- Included findings, issues, next step, and approval status.\n"
            "Issues: n/a\n"
            "Appropriate next steps: Send a specific task to test tool use.\n"
            "Approval needed: n/a"
        )
        quick = coordinator.parse_model_sections(output, delivery_tier=2)
        substantial = coordinator.parse_model_sections(output, delivery_tier=3)
        self.assertIs(quick["complete"], True)
        self.assertIs(quick["summarySufficient"], True)
        self.assertEqual(
            quick["done"],
            ["the response system is functioning for this test."],
        )
        self.assertIs(substantial["complete"], False)

    def test_quick_answer_with_only_formatter_metadata_still_fails_closed(self):
        coordinator = load_module()
        sections = coordinator.parse_model_sections(
            "Complete: Yes\n"
            "What was done:\n"
            "- Followed the requested format.\n"
            "- Omitted the prohibited Model line.\n"
            "Issues: n/a\n"
            "Appropriate next steps: n/a\n"
            "Approval needed: n/a",
            delivery_tier=2,
        )
        self.assertIs(sections["complete"], False)
        self.assertIs(sections["summarySufficient"], False)

    def test_agent_rh_findings_pass_semantic_gate(self):
        coordinator = load_module()
        sections = coordinator.parse_model_sections(
            "Complete: Yes\n"
            "What was done:\n"
            "- The website, documentation, and X account describe Agent RH as a read-only Robinhood Chain signal source.\n"
            "- Agent RH only monitors Robinhood Chain and cannot control or trade through a Robinhood brokerage account.\n"
            "- Connecting brokerage credentials or wallets creates unnecessary account and trade-control risk.\n"
            "Issues:\n"
            "- Brokerage credential or wallet access would create unnecessary account-control risk.\n"
            "Appropriate next steps:\n"
            "- Keep Agent RH read-only and avoid connecting brokerage credentials or wallets.\n"
            "Approval needed:\n- n/a"
        )
        self.assertIs(sections["complete"], True)
        self.assertIs(sections["summarySufficient"], True)
        self.assertEqual(len(sections["done"]), 3)
        self.assertIn("cannot control or trade", sections["done"][1])
        self.assertIn("risk", sections["issues"][0])

    def test_route_assessment_findings_are_not_downgraded_by_verb_wording(self):
        coordinator = load_module()
        sections = coordinator.parse_model_sections(
            "Complete: Yes\n"
            "What was done:\n"
            "- Dashboard-safe architecture reviews route to the verified specialist lane when allowance remains.\n"
            "- Private execution remains reserved for the Josh 2.0 coordinator and never crosses the public specialist boundary.\n"
            "- The actual fallback occurred only when the requested provider authentication route was unavailable.\n"
            "Issues:\n- No routing contradiction was observed in this assessment.\n"
            "Appropriate next steps:\n- Keep the current policy and rerun the parity canary after routing changes.\n"
            "Approval needed:\n- n/a"
        )
        self.assertIs(sections["complete"], True)
        self.assertIs(sections["summarySufficient"], True)
        self.assertEqual(len(sections["done"]), 3)

    def test_complete_yes_cannot_hide_that_no_specialist_executed(self):
        coordinator = load_module()
        sections = coordinator.parse_model_sections(
            "Complete: Yes — review completed; ecosystem needs attention.\n"
            "What was done:\n"
            "- Verified automatic summary and planning policies from configuration.\n"
            "- Confirmed each documented fallback ladder has a named provider.\n"
            "- Attempted the required canary, but no specialist model completed execution.\n"
            "Issues:\n"
            "- Specialist-host connectivity prevented successful verification.\n"
            "- Codex fallback also failed to initialize in the restricted environment.\n"
            "Appropriate next steps:\n"
            "- Restore the specialist runtime and rerun the canary end-to-end.\n"
            "Approval needed:\n- n/a",
            require_successful_execution=True,
        )
        self.assertIs(sections["complete"], False)
        self.assertIs(sections["summarySufficient"], False)
        self.assertIn(
            "The completion claim contradicted an unresolved model or route execution failure.",
            sections["summaryQualityIssues"],
        )

    def test_diagnostic_primary_failure_with_verified_fallback_can_complete(self):
        coordinator = load_module()
        sections = coordinator.parse_model_sections(
            "Complete: Yes — the diagnostic objective completed.\n"
            "What was done:\n"
            "- The GLM provider failed to initialize during the injected diagnostic canary.\n"
            "- The coordinator disclosed the Gemini Pro fallback before retrying.\n"
            "- Gemini Pro completed with a verified execution checkpoint.\n"
            "Issues:\n"
            "- GLM initialization remains unavailable for this diagnostic path.\n"
            "Appropriate next steps:\n"
            "- Repair GLM initialization and rerun the canary.\n"
            "Approval needed:\n- n/a"
        )
        self.assertIs(sections["complete"], True)
        self.assertIs(sections["summarySufficient"], True)

    def test_no_specialist_execution_is_valid_for_a_completed_failure_diagnosis(self):
        coordinator = load_module()
        sections = coordinator.parse_model_sections(
            "Complete: Yes — the failure diagnosis completed.\n"
            "What was done:\n"
            "- Confirmed no specialist model completed execution because DNS resolution failed.\n"
            "- The diagnostic isolated the failed hostname lookup before any provider request.\n"
            "- The remediation is to restore the resolver entry and rerun the canary.\n"
            "Issues:\n"
            "- DNS resolution still blocks the specialist runtime.\n"
            "Appropriate next steps:\n"
            "- Restore the resolver entry and rerun the canary.\n"
            "Approval needed:\n- n/a"
        )
        self.assertIs(sections["complete"], True)
        self.assertIs(sections["summarySufficient"], True)

    def test_negative_telegram_health_findings_are_concrete_and_complete(self):
        coordinator = load_module()
        sections = coordinator.parse_model_sections(TELEGRAM_HEALTH_RESULT)
        self.assertIs(sections["complete"], True)
        self.assertIs(sections["summarySufficient"], True)
        self.assertEqual(len(sections["done"]), 3)
        self.assertTrue(all(coordinator.is_concrete_result_item(item) for item in sections["done"]))
        self.assertFalse(coordinator.is_concrete_result_item(
            "The assigned worker is named Josh 2.0 for this request."
        ))
        for generic in (
            "The review remains active while the requested work is being discussed.",
            "The report has no evidence-backed conclusion for the user at this time.",
            "There are no findings in the prepared assessment summary for the user.",
            "The report mentions port 18790 without reporting an operational observation.",
            "The gateway health assessment remains active while the team discusses the request.",
            "The service status review is running while the requested work remains pending.",
            "The runtime report was last modified May 5 while the request remained pending.",
        ):
            self.assertFalse(coordinator.is_concrete_result_item(generic), generic)

    def test_negative_operational_findings_require_issues(self):
        coordinator = load_module()
        no_issues = TELEGRAM_HEALTH_RESULT.replace(
            "- Live Telegram API and end-to-end message delivery could not be verified from this restricted environment.\n"
            "- Fast-ack service registration and fresh operational logging are unverified.",
            "- n/a",
        ).replace(
            "Run the existing read-only Telegram health probe on the service-owning host to confirm gateway connectivity, watcher status, and a fresh Telegram identity check.",
            "No action needed.",
        )
        sections = coordinator.parse_model_sections(no_issues)
        self.assertIs(sections["complete"], False)
        self.assertIn("A reported risk or limitation was not reflected in Issues.", sections["summaryQualityIssues"])
        self.assertFalse(coordinator.has_operational_risk(
            "The runtime has no missing helpers in the canonical Telegram delivery path."
        ))
        self.assertFalse(coordinator.has_operational_risk(
            "The gateway service has no remaining issues after the health check."
        ))
        self.assertFalse(coordinator.has_operational_risk(
            "There are no service failures in the current host snapshot."
        ))
        self.assertTrue(coordinator.has_operational_risk(
            "The Telegram Fast Ack service is not running on the owning host."
        ))

    def test_incomplete_summary_keeps_three_source_findings_without_grader_boilerplate(self):
        coordinator = load_module()
        source = [
            "The available service inventory contains an entry for the gateway runtime.",
            "The watcher inventory contains a separate entry for the acknowledgement runtime.",
            "The log inventory contains a dated file for the Telegram delivery path.",
        ]
        self.assertEqual(coordinator.incomplete_summary_done_items(source, 0), source)

    def test_telegram_health_host_context_is_read_only_and_allowlisted(self):
        coordinator = load_module()
        payload = {
            "checkedAt": coordinator.utc_now(),
            "ok": True,
            "checks": {
                "gateway": {"ok": True, "detail": "listening", "latencyMs": 0.1, "secret": "omit"},
                "telegramFastAck": {"ok": True, "detail": "launchd running"},
                "telegramWorkCardHelper": {"ok": True, "detail": "runtime helper matches canonical source"},
                "telegramInboxClaimHelper": {"ok": True, "detail": "canonical helper selected"},
                "sourceFreshness": {"ok": True, "detail": "canonical source is current"},
                "secretCheck": {"ok": True, "detail": "must not cross the boundary"},
            },
            "privatePath": "/private/omit-me",
        }
        completed = type("Result", (), {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""})()
        with patch.object(coordinator.subprocess, "run", return_value=completed) as run:
            context = coordinator.telegram_health_host_context(
                "Assess current Telegram health and make no changes."
            )
        command = run.call_args.args[0]
        self.assertEqual(command[-1], "--no-write")
        self.assertNotIn("--recover", command)
        self.assertIs(context["available"], True)
        self.assertEqual(set(context["checks"]), {
            "gateway", "telegramFastAck", "telegramWorkCardHelper", "telegramInboxClaimHelper",
            "sourceFreshness",
        })
        serialized = json.dumps(context)
        self.assertNotIn("secretCheck", serialized)
        self.assertNotIn("privatePath", serialized)
        self.assertNotIn("omit-me", serialized)

    def test_telegram_health_host_context_fails_closed_on_partial_or_mistyped_snapshot(self):
        coordinator = load_module()
        base = {
            "checkedAt": coordinator.utc_now(),
            "ok": True,
            "checks": {
                "gateway": {"ok": True},
                "telegramFastAck": {"ok": True},
                "telegramWorkCardHelper": {"ok": True},
                "telegramInboxClaimHelper": {"ok": True},
                "sourceFreshness": {"ok": True},
            },
        }
        for mutate in (
            lambda payload: payload["checks"].pop("sourceFreshness"),
            lambda payload: payload["checks"]["gateway"].update(ok="false"),
            lambda payload: payload.update(ok="false"),
            lambda payload: payload.update(checkedAt="not-a-time"),
        ):
            payload = json.loads(json.dumps(base))
            mutate(payload)
            completed = type("Result", (), {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""})()
            with patch.object(coordinator.subprocess, "run", return_value=completed):
                context = coordinator.telegram_health_host_context("Check Telegram health read-only.")
            self.assertEqual(context["available"], False)

    def test_probe_wide_status_is_not_used_for_allowlisted_telegram_health(self):
        coordinator = load_module()
        payload = {
            "checkedAt": coordinator.utc_now(),
            "ok": False,
            "checks": {
                key: {"ok": True, "detail": "healthy"}
                for key in (
                    "gateway", "telegramFastAck", "telegramWorkCardHelper",
                    "telegramInboxClaimHelper", "sourceFreshness",
                )
            },
        }
        completed = type("Result", (), {"returncode": 1, "stdout": json.dumps(payload), "stderr": ""})()
        with patch.object(coordinator.subprocess, "run", return_value=completed):
            context = coordinator.telegram_health_host_context("Check Telegram health read-only.")
        self.assertIs(context["available"], True)
        self.assertIs(context["ok"], True)

    def test_mutating_telegram_health_request_does_not_receive_read_only_probe_context(self):
        coordinator = load_module()
        with patch.object(coordinator.subprocess, "run") as run:
            context = coordinator.telegram_health_host_context(
                "Check Telegram health, make no changes to the gateway, and restart the watcher."
            )
        self.assertIsNone(context)
        run.assert_not_called()

    def test_healthy_host_context_replaces_contradictory_sandbox_output(self):
        coordinator = load_module()
        context = {
            "available": True,
            "checkedAt": coordinator.utc_now(),
            "ok": True,
            "checks": {
                "gateway": {"ok": True, "detail": "listening on port 18790"},
                "telegramFastAck": {"ok": True, "detail": "launchd running"},
                "telegramWorkCardHelper": {"ok": True, "detail": "runtime helper matches source"},
                "telegramInboxClaimHelper": {"ok": True, "detail": "canonical helper selected"},
                "sourceFreshness": {"ok": True, "detail": "source is current"},
            },
        }
        gated = coordinator.enforce_host_evidence_gate(TELEGRAM_HEALTH_RESULT, context)
        self.assertNotIn("could not complete its loopback", gated)
        self.assertNotIn("has no registered", gated)
        self.assertNotIn("logs are empty", gated)
        self.assertIn("gateway check passed", gated)
        self.assertIn("Fast Ack service check passed", gated)
        sections = coordinator.parse_model_sections(gated)
        self.assertIs(sections["complete"], True)
        self.assertIs(sections["summarySufficient"], True)

    def test_read_only_codex_executor_uses_os_enforced_read_only_sandbox(self):
        coordinator = load_module()
        self.assertIn('"--sandbox", "read-only"', coordinator.LLM_EXECUTOR_CODE)
        self.assertIn('cfg.get("readOnly")', coordinator.LLM_EXECUTOR_CODE)
        for prompt in (
            "Check Telegram health. Make no changes.",
            "Check Telegram health and do not make changes.",
            "Check Telegram health; don't make any changes.",
            "Check Telegram health without making changes.",
        ):
            self.assertTrue(coordinator.read_only_execution_requested(prompt), prompt)
        self.assertFalse(coordinator.read_only_execution_requested(
            "Check Telegram health, make no changes to the gateway, and restart the watcher."
        ))

    def test_telegram_health_host_context_fails_closed_when_probe_is_unavailable(self):
        coordinator = load_module()
        completed = type("Result", (), {"returncode": 1, "stdout": "not-json", "stderr": "private error"})()
        with patch.object(coordinator.subprocess, "run", return_value=completed):
            context = coordinator.telegram_health_host_context("Check Telegram health read-only.")
        self.assertEqual(context["available"], False)
        self.assertIn("do not infer", context["instruction"])

        gated = coordinator.enforce_host_evidence_gate(
            "Complete: Yes\nWhat was done:\n- A sandbox guessed that services were healthy.",
            context,
        )
        self.assertIn("Complete: No", gated)
        self.assertIn("No sandbox-local failure was treated as evidence", gated)

    def test_non_health_output_is_not_changed_by_host_evidence_gate(self):
        coordinator = load_module()
        output = "Complete: Yes\nWhat was done:\n- The requested file was updated."
        self.assertEqual(coordinator.enforce_host_evidence_gate(output, None), output)

    def test_risk_and_no_action_without_an_issue_are_rejected(self):
        coordinator = load_module()
        sections = coordinator.parse_model_sections(
            "Complete: Yes\n"
            "What was done:\n"
            "- The product documentation describes the integration as read-only by default.\n"
            "- The integration cannot place brokerage trades or control an account.\n"
            "- Connecting a wallet creates an unnecessary credential-exposure risk.\n"
            "Issues:\n- n/a\n"
            "Appropriate next steps:\n- No action needed.\n"
            "Approval needed:\n- n/a"
        )
        self.assertIs(sections["complete"], False)
        self.assertIn("A reported risk or limitation was not reflected in Issues.", sections["summaryQualityIssues"])
        self.assertIn("The No action needed conclusion was not supported by the reported findings.", sections["summaryQualityIssues"])

    def test_duplicate_result_bullets_do_not_satisfy_minimum(self):
        coordinator = load_module()
        sections = coordinator.parse_model_sections(
            "Complete: Yes\n"
            "What was done:\n"
            "- The health check confirmed that the Telegram gateway is responding normally.\n"
            "- The health check confirmed that the Telegram gateway is responding normally.\n"
            "- The delivery probe confirmed that one test response reached the expected topic.\n"
            "Issues:\n- n/a\n"
            "Appropriate next steps:\n- No action needed.\n"
            "Approval needed:\n- n/a"
        )
        self.assertIs(sections["complete"], False)
        self.assertIn("A completion claim requires three to five unique result bullets.", sections["summaryQualityIssues"])

    def test_delivery_uses_fail_card_when_summary_is_insufficient(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordinator.PRIVATE_DIR = root
            coordinator.TELEGRAM_GATEWAY_SCRIPT = root / "josh_telegram_fast_ack.py"
            coordinator.TELEGRAM_GATEWAY_SCRIPT.write_text("", encoding="utf-8")
            commands = []
            rendered = []

            def fake_run(cmd, **kwargs):
                commands.append(cmd)
                rendered.append(str(kwargs.get("input") or ""))
                return type("Result", (), {
                    "returncode": 0,
                    "stdout": json.dumps({"ok": True, "status": "closed-and-final-delivered"}),
                })()

            with patch.object(coordinator.subprocess, "run", side_effect=fake_run):
                delivered = coordinator.deliver_result(
                    "job-weak",
                    {"origin": {"cardKey": "card-weak", "runId": "run-weak", "chatId": "chat", "threadId": "1"}},
                    {"routeId": "luna", "routingReason": "test"},
                    {
                        "actualProvider": "codex",
                        "actualModel": "gpt-test",
                        "actualWorker": "worker",
                        "actualHost": "josh2",
                        "modelVerified": True,
                        "executionVerified": True,
                    },
                    "Complete: Yes\nWhat was done:\n- Assessment complete.\nIssues:\n- n/a\nAppropriate next steps:\n- No action needed.\nApproval needed:\n- n/a",
                )
            self.assertIs(delivered, True)
            self.assertIn("--close-before-final", commands[0])
            self.assertEqual(commands[0][commands[0].index("--terminal-status") + 1], "failed")
            plain = re.sub(r"<[^>]+>", "", html.unescape(rendered[0]))
            self.assertIn("Complete: No", plain)
            self.assertIn(coordinator.MISSING_FINDINGS_ISSUE, html.unescape(rendered[0]).replace("\n  ", " "))

    def test_delivery_uses_done_card_for_negative_telegram_health_findings(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordinator.PRIVATE_DIR = root
            coordinator.TELEGRAM_GATEWAY_SCRIPT = root / "josh_telegram_fast_ack.py"
            coordinator.TELEGRAM_GATEWAY_SCRIPT.write_text("", encoding="utf-8")
            commands = []
            rendered = []

            def fake_run(cmd, **kwargs):
                commands.append(cmd)
                rendered.append(str(kwargs.get("input") or ""))
                return type("Result", (), {
                    "returncode": 0,
                    "stdout": json.dumps({"ok": True, "status": "closed-and-final-delivered"}),
                })()

            with patch.object(coordinator.subprocess, "run", side_effect=fake_run):
                delivered = coordinator.deliver_result(
                    "job-health",
                    {"origin": {"cardKey": "card-health", "runId": "run-health", "chatId": "chat", "threadId": "1"}},
                    {"routeId": "luna", "routingReason": "read-only health/status check"},
                    {
                        "actualProvider": "codex",
                        "actualModel": "gpt-5.6-luna",
                        "actualWorker": "worker",
                        "actualHost": "josh2",
                        "modelVerified": True,
                        "executionVerified": True,
                    },
                    TELEGRAM_HEALTH_RESULT,
                )
            self.assertIs(delivered, True)
            self.assertIn("--close-before-final", commands[0])
            self.assertEqual(commands[0][commands[0].index("--terminal-status") + 1], "done")
            plain = re.sub(r"<[^>]+>", "", html.unescape(rendered[0]))
            self.assertIn("Complete: Yes", plain)
            self.assertNotIn("supplied summary contained", plain.lower())
            self.assertNotIn("completion requires", plain.lower())

    def test_unstructured_or_unverified_output_never_claims_completion(self):
        coordinator = load_module()
        route = {"routeId": "luna", "routingReason": "test route"}
        execution = {
            "actualProvider": "codex",
            "actualModel": "gpt-5.6-luna",
            "modelVerified": False,
            "executionVerified": False,
        }
        body = re.sub(
            r"<[^>]+>",
            "",
            html.unescape(coordinator.render_final_html(route, execution, "Worker stopped before producing a result.")),
        )
        self.assertIn("Model: unverified", body)
        self.assertIn("Complete: No", body)
        self.assertIn("Worker execution was not verified.", body)

    def test_worker_contract_requests_the_structured_sections(self):
        coordinator = load_module()
        self.assertIn("exactly these plain-text sections", coordinator.WORKER_OUTPUT_CONTRACT)
        self.assertIn("What was done:", coordinator.WORKER_OUTPUT_CONTRACT)
        self.assertIn("Approval needed:", coordinator.WORKER_OUTPUT_CONTRACT)
        self.assertIn("concrete findings, outcomes, or changes", coordinator.WORKER_OUTPUT_CONTRACT)
        self.assertIn("Generic process statements", coordinator.WORKER_OUTPUT_CONTRACT)
        self.assertIn("every reported risk or limitation in Issues", coordinator.WORKER_OUTPUT_CONTRACT)
        self.assertIn("Do not include a Model line", coordinator.WORKER_OUTPUT_CONTRACT)
        self.assertIn("private worker-to-delivery handoff", coordinator.WORKER_OUTPUT_CONTRACT)
        self.assertIn("delivery layer is required to prepend", coordinator.WORKER_OUTPUT_CONTRACT)
        self.assertIn("do not treat that required delivery header as a formatter defect", coordinator.WORKER_OUTPUT_CONTRACT)

    def test_local_codex_execution_reports_verified_runtime_auth(self):
        coordinator = load_module()
        self.assertIn(
            '"actualAuth": "OpenAI Codex OAuth/subscription", "authVerified": True',
            coordinator.LLM_EXECUTOR_CODE,
        )

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

    def test_recovery_never_exceeds_retry_budget(self):
        coordinator = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            self.configure_private_state(coordinator, Path(tmp))
            route = coordinator.route_prompt("Recover me", injected_health={"luna": True})
            job, _ = coordinator.make_job("Recover me", route, {"runId": "run-exhausted"}, 30)
            state = coordinator.read_json(coordinator.STATE_PATH, {"jobs": {}})
            state["jobs"][job["jobId"]].update({
                "status": "running",
                "attempt": int(job["maxRetries"]) + 1,
                "workerPid": 99999999,
                "leaseToken": "old",
            })
            coordinator.save_json(coordinator.STATE_PATH, state)
            spawned = []
            coordinator.spawn_worker = spawned.append
            result = coordinator.recover()
            self.assertEqual(result["recovered"], 0)
            self.assertEqual(spawned, [])
            status = coordinator.job_status(job["jobId"])["job"]["status"]
            self.assertEqual(status, "failed")


if __name__ == "__main__":
    unittest.main()
