"""Focused v3 surface and terminal contracts for the JAIMES gateway adapter."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "jaimes_telegram_fast_ack.py"
if str(SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT.parent))
if "jaimes_completion_evidence" not in sys.modules:
    completion_evidence = types.ModuleType("jaimes_completion_evidence")
    completion_evidence.write_completion_evidence = lambda **_kwargs: None
    sys.modules["jaimes_completion_evidence"] = completion_evidence


def load_module():
    spec = importlib.util.spec_from_file_location("jaimes_gateway_adapter_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JaimesGatewayAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.event = {
            "ts": "2026-07-20T17:00:00Z",
            "platform_message_id": "9001",
            "db_message_id": "41",
            "run_id": "telegram-message-41",
            "session_id": "session-1",
            "prompt": "Please inspect and verify the current service.",
        }
        self.meta = {
            "telegram_chat_id": "-1000000000001",
            "telegram_thread_id": "17",
            "origin": {"message_id": "9001"},
        }

    @staticmethod
    def canonical_final() -> str:
        return (
            "Complete: Yes\n"
            "What was done:\n"
            "- Verified 12 lifecycle assertions against the current receipt.\n"
            "- Confirmed 3 surface boundaries with zero duplicate sends.\n"
            "- Recorded the tested route and retained the rollback controls.\n"
            "Issues:\n- n/a\n"
            "Appropriate next steps:\n- No action needed.\n"
            "Approval needed:\n- n/a"
        )

    def create_terminal_writer(self, state_path: Path) -> tuple[str, str, str]:
        key = "jaimes-fast-ack-test-9001"
        work_id, run_id, _ = self.module.telegram_work_identity(key, "telegram-message-41")
        context = self.module.begin_gateway_lifecycle(
            key=key,
            work_id=work_id,
            work_run_id=run_id,
            prompt="Please inspect, verify, and test the current service.",
        )
        self.assertTrue(context["writer"])
        self.module.advance_gateway_phase(context, "acknowledged")
        self.module.advance_gateway_phase(context, "working")
        self.module.save_json(state_path, {
            "active_cards": {
                "telegram-message-41": {
                    "status": "active",
                    "session_id": "session-1",
                    "inbound_message_id": "9001",
                    "key": key,
                    "work_id": work_id,
                    "ledger_run_id": run_id,
                    "origin_claim_hash": "a" * 64,
                    "objective": "Verify the current service",
                    "model": "planned/provider-model",
                    "route": "planned route claim",
                    "started_at": "2026-07-20T17:00:00Z",
                    "task_started_at": "2026-07-20T17:00:00Z",
                    "lifecycle_writer_enabled": True,
                    "lifecycle_version": 3,
                    "no_card_required": False,
                    "delivery_tier": 3,
                }
            }
        })
        return key, work_id, run_id

    def run_writer_tier(
        self,
        tier: int,
        *,
        objective: str = "Verify the current service",
        near_copy: bool = False,
        semantic: str = "",
    ):
        events: list[str] = []
        receipt = {
            "workId": "work-telegram-" + "a" * 24,
            "lifecycleVersion": 3,
            "deliveryTier": tier,
            "classifierReason": {1: "conversation", 2: "quick-answer", 3: "multi-step"}[tier],
            "sequence": 2,
            "fencingEpoch": 1,
            "phase": "classified",
        }
        context = {"receipt": receipt, "writer": True, "shadow": False, "lifecycle": Mock()}

        def claim(_context, kind):
            events.append(f"claim:{kind}")
            return {"allowed": True, "idempotencyKey": f"effect-{kind}"}

        def reaction(*_args, **_kwargs):
            events.append("api:reaction")
            return {"ok": True}

        def card(_cmd):
            events.append("api:card")
            return {
                "ok": True,
                "stdout": json.dumps({"message_id": "8001", "header_message_id": ""}),
            }

        def publish(*_args, **kwargs):
            events.append(f"publish:verified={kwargs.get('route_verified')}")
            return True

        patches = (
            patch.object(self.module, "begin_gateway_lifecycle", return_value=context),
            patch.object(self.module, "claim_gateway_effect", side_effect=claim),
            patch.object(self.module, "finish_gateway_effect"),
            patch.object(self.module, "set_eyes_reaction_result", side_effect=reaction),
            patch.object(self.module, "run_work_card_cmd", side_effect=card),
            patch.object(self.module, "work_card_surface_receipt", return_value={}),
            patch.object(self.module, "advance_gateway_phase", side_effect=lambda ctx, phase: events.append(f"phase:{phase}") or ctx["receipt"]),
            patch.object(self.module, "set_gateway_worker_route", side_effect=lambda *_args, **_kwargs: receipt),
            patch.object(self.module, "gateway_public_fields", return_value={
                "lifecycle_version": 3,
                "delivery_tier": tier,
                "lifecycle_writer_enabled": True,
            }),
            patch.object(self.module, "objective_from_prompt", return_value=objective),
            patch.object(
                self.module,
                "objective_is_near_copy",
                side_effect=lambda _prompt, candidate: bool(
                    near_copy
                    and candidate != "Respond to the current Telegram message"
                ),
            ),
            patch.object(self.module, "semantic_reinterpretation", return_value=semantic),
            patch.object(self.module, "runtime_route", return_value=("jaimes-local", "verified lane")),
            patch.object(self.module, "skill_for_prompt", return_value={}),
            patch.object(self.module, "publish_jaimes", side_effect=publish),
            patch.object(self.module, "send_initial_ack", side_effect=AssertionError("v3 must not create an ack bubble")),
            patch.object(self.module, "send_chat_action"),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10], patches[11], patches[12], patches[13], patches[14], patches[15], patches[16]:
            result = self.module.send_ack(
                dict(self.event),
                model="provider/model",
                state={},
                meta=dict(self.meta),
            )
        return result, events

    def test_writer_tier_1_has_no_reaction_card_or_ack_bubble(self):
        result, events = self.run_writer_tier(1)
        self.assertTrue(result["ok"])
        self.assertFalse(result["reaction_ok"])
        self.assertTrue(result["no_card_required"])
        self.assertNotIn("api:reaction", events)
        self.assertNotIn("api:card", events)
        self.assertIn("publish:verified=False", events)

    def test_writer_tier_2_claims_reaction_before_api_and_has_no_card(self):
        result, events = self.run_writer_tier(2)
        self.assertTrue(result["ok"])
        self.assertTrue(result["reaction_ok"])
        self.assertTrue(result["no_card_required"])
        self.assertLess(events.index("claim:reaction"), events.index("api:reaction"))
        self.assertNotIn("api:card", events)

    def test_writer_quick_answer_near_copy_keeps_terminal_lifecycle(self):
        result, events = self.run_writer_tier(
            2,
            objective=self.event["prompt"],
            near_copy=True,
            semantic="",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["objective"],
            "Respond to the current Telegram message",
        )
        self.assertTrue(result["no_card_required"])
        self.assertTrue(self.module.registerable_ack_result(result))
        self.assertNotEqual(result.get("status"), "awaiting-objective-interpretation")
        self.assertNotIn("api:card", events)

    def test_writer_tier_3_claims_each_surface_before_api(self):
        result, events = self.run_writer_tier(3)
        self.assertTrue(result["ok"])
        self.assertFalse(result["no_card_required"])
        self.assertLess(events.index("claim:reaction"), events.index("api:reaction"))
        self.assertLess(events.index("claim:card"), events.index("api:card"))

    def test_no_card_active_work_never_calls_work_card_updater(self):
        state = {
            "active_cards": {
                "telegram-message-41": {
                    "status": "active",
                    "session_id": "session-1",
                    "no_card_required": True,
                    "telegram_thread_id": "17",
                    "key": "tier-two",
                }
            }
        }
        with patch.dict(os.environ, {"JAIMES_TELEGRAM_LIVE_CARDS": "1"}), patch.object(
            self.module,
            "recent_progress_events",
            return_value=[{
                "event_id": "event-1",
                "run_id": "telegram-message-41",
                "type": "tool.result",
                "summary": "Verification passed",
            }],
        ), patch.object(
            self.module, "hermes_session_lineage", return_value={"session-1"}
        ), patch.object(
            self.module,
            "run_work_card_cmd",
            side_effect=AssertionError("Tier 1/2 must not touch work cards"),
        ):
            updates = self.module.update_active_cards(state, "session-1")
        self.assertEqual(updates, [])
        self.assertIn("event-1", state["processed_progress_events"])

    def test_terminal_outbox_and_effect_are_reserved_before_native_send(self):
        valid_final = (
            "Complete: Yes\n"
            "What was done:\n"
            "- Verified 12 lifecycle assertions against the current receipt.\n"
            "- Confirmed 3 surface boundaries with zero duplicate sends.\n"
            "- Recorded the tested route and retained the rollback controls.\n"
            "Issues:\n- n/a\n"
            "Appropriate next steps:\n- No action needed.\n"
            "Approval needed:\n- n/a"
        )
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rollout = base / "rollout.json"
            rollout.write_text(json.dumps({
                "masterState": "jaimes",
                "globalKillSwitch": False,
                "brainKillSwitch": True,
                "hosts": {"josh2": True, "jaimes": True},
                "writerLifecycleVersion": 3,
                "readerLifecycleVersions": [2, 3],
                "shadowMinimumPerOwner": 20,
                "brainFixtureMinimum": 20,
            }))
            state_path = base / "state.json"
            lifecycle_root = base / "lifecycle"
            visibility_root = base / "terminal-visibility-outbox"
            with patch.object(self.module, "LIFECYCLE_ROLLOUT_PATH", rollout), patch.object(
                self.module, "LIFECYCLE_PRIVATE_ROOT", lifecycle_root
            ), patch.object(self.module, "STATE_PATH", state_path), patch.object(
                self.module, "TERMINAL_VISIBILITY_OUTBOX_DIR", visibility_root
            ), patch.object(self.module, "active_hermes_sessions_metadata", return_value=[{
                "sessionId": "session-1",
                "provider": "provider",
                "runtime_model": "model",
                "model": "provider/model",
            }]):
                self.module._GATEWAY_LIFECYCLE = None
                key = "jaimes-fast-ack-test-9001"
                work_id, run_id, _ = self.module.telegram_work_identity(key, "telegram-message-41")
                context = self.module.begin_gateway_lifecycle(
                    key=key,
                    work_id=work_id,
                    work_run_id=run_id,
                    prompt="Please inspect, verify, and test the current service.",
                )
                self.assertTrue(context["writer"])
                self.module.advance_gateway_phase(context, "acknowledged")
                self.module.advance_gateway_phase(context, "working")
                state_path.write_text(json.dumps({
                    "active_cards": {
                        "telegram-message-41": {
                            "status": "active",
                            "session_id": "session-1",
                            "inbound_message_id": "9001",
                            "key": key,
                            "work_id": work_id,
                            "ledger_run_id": run_id,
                            "objective": "Verify the current service",
                            "model": "provider/model",
                            "route": "jaimes-local | Why: verified lane",
                            "started_at": "2026-07-20T17:00:00Z",
                            "task_started_at": "2026-07-20T17:00:00Z",
                            "lifecycle_writer_enabled": True,
                            "no_card_required": False,
                            "delivery_tier": 3,
                        },
                        "telegram-message-42": {
                            "status": "active",
                            "session_id": "session-1",
                            "inbound_message_id": "9001",
                            "key": "newer-competing-card",
                            "work_id": "newer-competing-work",
                            "ledger_run_id": "newer-competing-run",
                            "objective": "Different current service",
                            "model": "provider/model",
                            "route": "jaimes-local | Why: verified lane",
                            "started_at": "2026-07-20T17:00:30Z",
                            "task_started_at": "2026-07-20T17:00:30Z",
                            "lifecycle_writer_enabled": True,
                            "no_card_required": False,
                            "delivery_tier": 3,
                        },
                    }
                }))
                publish_observed: list[dict[str, str]] = []

                def publish(*_args, **kwargs):
                    with self.module.gateway_lifecycle().connect() as db:
                        publish_observed.append({
                            row["kind"]: row["state"]
                            for row in db.execute(
                                "SELECT kind,state FROM effects WHERE work_id=? AND kind IN ('card_edit','final')",
                                (work_id,),
                            )
                        })
                    self.assertEqual(kwargs["work_event"], "terminal")
                    return True

                with patch.object(self.module, "publish_jaimes", side_effect=publish):
                    prepared = self.module.prepare_terminal_response(
                        response_text=valid_final,
                        session_id="session-1",
                        model="provider/model",
                        inbound_message_id="9001",
                        card_run_id="telegram-message-41",
                        response_recorded_at="2026-07-20T17:01:00Z",
                    )
                self.assertTrue(prepared["managed"])
                self.assertEqual(publish_observed, [{}])
                receipt = self.module.gateway_lifecycle().read_work(work_id)
                self.assertEqual(receipt["phase"], "terminal")
                self.assertEqual(receipt["deliveryState"], "sending")
                saved = json.loads(state_path.read_text())
                card = saved["active_cards"]["telegram-message-41"]
                self.assertEqual(card["terminal_delivery_state"], "sending")
                self.assertTrue(card["terminal_final_effect_key"].startswith("effect-"))
                self.assertTrue(card["terminal_card_edit_effect_key"].startswith("effect-"))
                self.assertTrue(card["terminal_control_tower_published_at"])
                self.assertNotIn(
                    "terminal_delivery_state",
                    saved["active_cards"]["telegram-message-42"],
                )
                self.module.finish_card_terminal_delivery(card, state="delivered")
                self.module.finish_prepared_terminal_card_edit(card, state="delivered")
                receipt = self.module.gateway_lifecycle().read_work(work_id)
                self.assertEqual(receipt["deliveryState"], "delivered")

    def test_terminal_visibility_failure_recovers_across_restart_with_stable_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rollout = base / "rollout.json"
            rollout.write_text(json.dumps({
                "masterState": "jaimes",
                "globalKillSwitch": False,
                "brainKillSwitch": True,
                "hosts": {"josh2": True, "jaimes": True},
                "writerLifecycleVersion": 3,
                "readerLifecycleVersions": [2, 3],
                "shadowMinimumPerOwner": 20,
                "brainFixtureMinimum": 20,
            }))
            state_path = base / "state.json"
            lifecycle_root = base / "lifecycle"
            visibility_root = base / "terminal-visibility-outbox"
            runtime = [{
                "sessionId": "session-1",
                "provider": "provider",
                "runtime_model": "actual-model",
                "model": "provider/actual-model",
            }]
            with patch.object(self.module, "LIFECYCLE_ROLLOUT_PATH", rollout), patch.object(
                self.module, "LIFECYCLE_PRIVATE_ROOT", lifecycle_root
            ), patch.object(self.module, "STATE_PATH", state_path), patch.object(
                self.module, "TERMINAL_VISIBILITY_OUTBOX_DIR", visibility_root
            ), patch.object(self.module, "active_hermes_sessions_metadata", return_value=runtime):
                self.module._GATEWAY_LIFECYCLE = None
                _key, work_id, _ledger_run_id = self.create_terminal_writer(state_path)
                failed_publisher = Mock(return_value=False)
                with patch.object(self.module, "publish_jaimes", failed_publisher):
                    with self.assertRaisesRegex(
                        self.module.LifecycleError,
                        "terminal-visibility-publication-pending",
                    ):
                        self.module.prepare_terminal_response(
                            response_text=self.canonical_final(),
                            session_id="session-1",
                            model="provider/actual-model",
                            inbound_message_id="9001",
                            response_recorded_at="2026-07-20T17:01:00Z",
                        )
                current = self.module.gateway_lifecycle().read_work(work_id)
                self.assertEqual(current["phase"], "working")
                with self.module.gateway_lifecycle().connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 0)
                pending_path = next(visibility_root.glob("*.json"))
                pending = json.loads(pending_path.read_text())
                first_event_id = failed_publisher.call_args.kwargs["event_id"]
                self.assertEqual(pending["eventId"], first_event_id)
                self.assertFalse(pending["acceptedAt"])

                replay_publisher = Mock(return_value=True)
                with patch.object(self.module, "publish_jaimes", replay_publisher):
                    replay = self.module.recover_terminal_visibility_outbox()
                self.assertEqual(replay[0]["status"], "accepted")
                self.assertEqual(replay_publisher.call_args.kwargs["event_id"], first_event_id)

                no_republish = Mock(side_effect=AssertionError("accepted event must deduplicate locally"))
                with patch.object(self.module, "publish_jaimes", no_republish):
                    prepared = self.module.prepare_terminal_response(
                        response_text=self.canonical_final(),
                        session_id="session-1",
                        model="provider/actual-model",
                        inbound_message_id="9001",
                        response_recorded_at="2026-07-20T17:01:00Z",
                    )
                self.assertTrue(prepared["managed"])
                no_republish.assert_not_called()
                card = json.loads(state_path.read_text())["active_cards"]["telegram-message-41"]
                self.assertEqual(card["model"], "provider/actual-model")
                self.assertNotEqual(card["route"], "planned route claim")
                self.assertTrue(card["route_verified"])
                accepted = json.loads(pending_path.read_text())
                self.assertTrue(accepted["acceptedAt"])
                self.assertNotIn("response_text", accepted)

    def test_jaimes_terminal_rejects_unverified_or_mismatched_runtime_before_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rollout = base / "rollout.json"
            rollout.write_text(json.dumps({
                "masterState": "jaimes",
                "globalKillSwitch": False,
                "brainKillSwitch": True,
                "hosts": {"josh2": True, "jaimes": True},
                "writerLifecycleVersion": 3,
                "readerLifecycleVersions": [2, 3],
                "shadowMinimumPerOwner": 20,
                "brainFixtureMinimum": 20,
            }))
            state_path = base / "state.json"
            lifecycle_root = base / "lifecycle"
            visibility_root = base / "terminal-visibility-outbox"
            runtime = [{
                "sessionId": "session-1",
                "provider": "provider",
                "runtime_model": "actual-model",
                "model": "provider/actual-model",
            }]
            publisher = Mock()
            with patch.object(self.module, "LIFECYCLE_ROLLOUT_PATH", rollout), patch.object(
                self.module, "LIFECYCLE_PRIVATE_ROOT", lifecycle_root
            ), patch.object(self.module, "STATE_PATH", state_path), patch.object(
                self.module, "TERMINAL_VISIBILITY_OUTBOX_DIR", visibility_root
            ), patch.object(
                self.module, "active_hermes_sessions_metadata", return_value=runtime
            ), patch.object(self.module, "publish_jaimes", publisher):
                self.module._GATEWAY_LIFECYCLE = None
                _key, work_id, _ledger_run_id = self.create_terminal_writer(state_path)
                with self.assertRaisesRegex(
                    self.module.LifecycleError,
                    "terminal-runtime-route-unverified",
                ):
                    self.module.prepare_terminal_response(
                        response_text=self.canonical_final(),
                        session_id="session-1",
                        model="provider/different-model",
                        inbound_message_id="9001",
                    )
                publisher.assert_not_called()
                self.assertEqual(self.module.gateway_lifecycle().read_work(work_id)["phase"], "working")
                with self.module.gateway_lifecycle().connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 0)
                blocked = json.loads(next(visibility_root.glob("*.json")).read_text())
                self.assertFalse(blocked["routeVerified"])
                self.assertEqual(blocked["incident"]["code"], "terminal-runtime-route-unverified")
                self.assertNotIn("objective", blocked)

    def test_shadow_terminal_preserves_legacy_text_and_finishes_only_after_final_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rollout = base / "rollout.json"
            rollout.write_text(json.dumps({
                "masterState": "shadow",
                "globalKillSwitch": False,
                "brainKillSwitch": True,
                "hosts": {"josh2": True, "jaimes": True},
                "writerLifecycleVersion": 3,
                "readerLifecycleVersions": [2, 3],
                "shadowMinimumPerOwner": 20,
                "brainFixtureMinimum": 20,
            }))
            state_path = base / "state.json"
            lifecycle_root = base / "lifecycle"
            visibility_root = base / "terminal-visibility-outbox"
            runtime = [{
                "sessionId": "session-1",
                "provider": "provider",
                "runtime_model": "actual-model",
                "model": "provider/actual-model",
            }]
            with patch.object(self.module, "LIFECYCLE_ROLLOUT_PATH", rollout), patch.object(
                self.module, "LIFECYCLE_PRIVATE_ROOT", lifecycle_root
            ), patch.object(self.module, "STATE_PATH", state_path), patch.object(
                self.module, "TERMINAL_VISIBILITY_OUTBOX_DIR", visibility_root
            ), patch.object(self.module, "active_hermes_sessions_metadata", return_value=runtime):
                self.module._GATEWAY_LIFECYCLE = None
                key = "jaimes-shadow-terminal"
                work_id, ledger_run_id, _ = self.module.telegram_work_identity(
                    key,
                    "telegram-message-41",
                )
                context = self.module.begin_gateway_lifecycle(
                    key=key,
                    work_id=work_id,
                    work_run_id=ledger_run_id,
                    prompt="Compare the existing legacy final surface.",
                )
                self.assertTrue(context["shadow"])
                self.assertFalse(context["writer"])
                self.module.advance_gateway_phase(context, "acknowledged")
                self.module.advance_gateway_phase(context, "working")
                finish_shadow = Mock()
                context["lifecycle"].finish_shadow_sample = finish_shadow
                self.module.save_json(state_path, {"active_cards": {
                    "telegram-message-41": {
                        "status": "active",
                        "session_id": "session-1",
                        "inbound_message_id": "9001",
                        "key": key,
                        "work_id": work_id,
                        "ledger_run_id": ledger_run_id,
                        "origin_claim_hash": "c" * 64,
                        "objective": "Compare the legacy terminal surface",
                        "model": "planned-model",
                        "route": "planned-route",
                        "started_at": "2026-07-20T17:00:00Z",
                        "task_started_at": "2026-07-20T17:00:00Z",
                        "lifecycle_version": 3,
                        "lifecycle_writer_enabled": False,
                        "lifecycle_shadow": True,
                        "delivery_tier": 3,
                        "no_card_required": False,
                    }
                }})
                raw_final = self.canonical_final()
                with patch.object(self.module, "publish_jaimes", return_value=True):
                    prepared = self.module.prepare_terminal_response(
                        response_text=raw_final,
                        session_id="session-1",
                        model="provider/actual-model",
                        inbound_message_id="9001",
                        response_recorded_at="2026-07-20T17:01:00Z",
                    )
                self.assertTrue(prepared["managed"])
                self.assertTrue(prepared["shadow"])
                self.assertEqual(prepared["text"], raw_final)
                finish_shadow.assert_not_called()
                with context["lifecycle"].connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 0)

                state = self.module.load_json(state_path, {})
                final_record = {
                    "platform_message_id": "9901",
                    "content": raw_final,
                    "recorded_at": "2026-07-20T17:01:00Z",
                    "id": 99,
                }
                with patch.object(
                    self.module, "final_assistant_record_after", return_value=final_record
                ), patch.object(self.module, "edit_message", return_value={"ok": True}), patch.object(
                    self.module, "run_work_card_cmd", return_value={"ok": True}
                ), patch.object(self.module, "publish_jaimes", return_value=True):
                    completed = self.module.complete_cards_from_final_responses(
                        state,
                        "session-1",
                    )
                self.assertEqual(completed, 1)
                finish_shadow.assert_called_once_with(work_id, delivered=True)
                self.assertEqual(
                    state["active_cards"]["telegram-message-41"]["terminal_delivery_state"],
                    "shadow-delivered",
                )

    def test_shadow_indeterminate_terminal_is_finished_unclean(self):
        lifecycle = Mock()
        card = {"work_id": "work-shadow", "lifecycle_shadow": True}
        with patch.object(self.module, "gateway_context_for_card", return_value={
            "lifecycle": lifecycle,
            "receipt": {"workId": "work-shadow"},
            "shadow": True,
            "writer": False,
        }):
            self.module.finish_shadow_terminal_delivery(card, delivered=False)
        lifecycle.finish_shadow_sample.assert_called_once_with(
            "work-shadow",
            delivered=False,
        )
        self.assertEqual(card["terminal_delivery_state"], "shadow-unclean")

    def test_remote_publisher_passes_event_id_and_requires_accepted_work_ledger(self):
        accepted = Mock(
            returncode=0,
            stdout=json.dumps({"ok": True, "workLedger": {"accepted": True}}),
            stderr="",
        )
        with patch.object(self.module.subprocess, "run", return_value=accepted) as runner:
            result = self.module.publish_jaimes(
                "Safe title",
                "done",
                "Safe detail",
                work_id="work-safe",
                run_id="run-safe",
                event_id="event-stable",
                brain_feed=False,
            )
        self.assertTrue(result)
        remote_command = runner.call_args.args[0][-1]
        self.assertIn("--event-id event-stable", remote_command)

        rejected = Mock(
            returncode=0,
            stdout=json.dumps({"ok": True, "workLedger": {"accepted": False}}),
            stderr="",
        )
        with patch.object(self.module.subprocess, "run", return_value=rejected):
            self.assertFalse(self.module.publish_jaimes("Safe", "done", "Safe", brain_feed=False))

        legacy_success = Mock(returncode=0, stdout="", stderr="")
        with patch.object(self.module.subprocess, "run", return_value=legacy_success):
            self.assertTrue(
                self.module.publish_jaimes(
                    "Safe progress",
                    "active",
                    "Safe detail",
                    brain_feed=False,
                )
            )
            self.assertFalse(
                self.module.publish_jaimes(
                    "Safe terminal",
                    "done",
                    "Safe detail",
                    brain_feed=False,
                    require_accepted_ledger=True,
                )
            )

    def test_rollout_off_keeps_legacy_reaction_hook_while_writer_uses_v3_receipt(self):
        legacy_context = {"writer": False, "shadow": False}
        with patch.object(self.module, "begin_gateway_lifecycle", return_value=legacy_context), patch.object(
            self.module, "set_eyes_reaction", return_value=False
        ) as legacy_reaction, patch.object(
            self.module,
            "set_eyes_reaction_result",
            side_effect=AssertionError("rollout-off lane must retain the N-1 adapter"),
        ):
            result = self.module.send_ack(
                dict(self.event),
                model="provider/model",
                state={},
                meta=dict(self.meta),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "eyes_reaction_failed")
        legacy_reaction.assert_called_once()

        writer_receipt = {
            "workId": "work-telegram-" + "b" * 24,
            "lifecycleVersion": 3,
            "deliveryTier": 2,
            "classifierReason": "quick-answer",
            "sequence": 2,
            "fencingEpoch": 1,
            "phase": "classified",
        }
        writer_lifecycle = Mock()
        writer_lifecycle.read_work.return_value = writer_receipt
        writer_context = {
            "writer": True,
            "shadow": False,
            "receipt": writer_receipt,
            "lifecycle": writer_lifecycle,
        }
        with patch.object(self.module, "begin_gateway_lifecycle", return_value=writer_context), patch.object(
            self.module, "claim_gateway_effect", return_value={"allowed": True, "idempotencyKey": "effect-reaction"}
        ), patch.object(self.module, "finish_gateway_effect"), patch.object(
            self.module,
            "set_eyes_reaction_result",
            return_value={"ok": False, "delivery_indeterminate": True},
        ) as v3_reaction, patch.object(
            self.module,
            "set_eyes_reaction",
            side_effect=AssertionError("v3 writer must consume the receipt-bearing adapter"),
        ):
            result = self.module.send_ack(
                dict(self.event),
                model="provider/model",
                state={},
                meta=dict(self.meta),
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["surface_indeterminate"])
        self.assertEqual(result["error"], "eyes_reaction_indeterminate")
        v3_reaction.assert_called_once()

    def test_handoff_accepts_tier_1_and_tier_2_without_card_ids(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            self.module, "HANDOFF_DIR", Path(tmp)
        ):
            for tier, reaction in ((1, False), (2, True)):
                with self.subTest(tier=tier):
                    chat, thread, message = "-1000000000001", "1", str(9100 + tier)
                    record_path, _ = self.module.handoff_paths(chat, thread, message)
                    expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)
                    self.module.write_handoff_record(record_path, {
                        "schema_version": 1,
                        "status": "accepted",
                        "agent": "jaimes",
                        "chat_id": chat,
                        "thread_id": thread,
                        "inbound_message_id": message,
                        "reaction_ok": reaction,
                        "header_message_id": "",
                        "live_message_id": "",
                        "no_card_required": True,
                        "delivery_tier": tier,
                        "expires_at": expires.isoformat().replace("+00:00", "Z"),
                    })
                    code, receipt = self.module.await_handoff(chat, thread, message, 0.5)
                    self.assertEqual(code, 0)
                    self.assertTrue(receipt["ok"])

    def test_progress_and_terminal_card_edits_are_reserved_before_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rollout = base / "rollout.json"
            rollout.write_text(json.dumps({
                "masterState": "jaimes",
                "globalKillSwitch": False,
                "brainKillSwitch": True,
                "hosts": {"josh2": True, "jaimes": True},
                "writerLifecycleVersion": 3,
                "readerLifecycleVersions": [2, 3],
                "shadowMinimumPerOwner": 20,
                "brainFixtureMinimum": 20,
            }))
            with patch.object(self.module, "LIFECYCLE_ROLLOUT_PATH", rollout), patch.object(
                self.module, "LIFECYCLE_PRIVATE_ROOT", base / "lifecycle"
            ):
                self.module._GATEWAY_LIFECYCLE = None
                key = "jaimes-progress-card"
                work_id, run_id, _ = self.module.telegram_work_identity(key, "telegram-message-41")
                context = self.module.begin_gateway_lifecycle(
                    key=key,
                    work_id=work_id,
                    work_run_id=run_id,
                    prompt="Inspect, verify, and test the multi-step service.",
                )
                self.assertEqual(context["receipt"]["deliveryTier"], 3)
                self.module.advance_gateway_phase(context, "acknowledged")
                self.module.advance_gateway_phase(context, "working")
                card = {
                    "key": key,
                    "work_id": work_id,
                    "ledger_run_id": run_id,
                    "lifecycle_version": 3,
                    "lifecycle_writer_enabled": True,
                    "delivery_tier": 3,
                    "no_card_required": False,
                }
                observed = []

                def helper(_command):
                    with self.module.gateway_lifecycle().connect() as db:
                        row = db.execute(
                            "SELECT state FROM effects WHERE work_id=? AND kind='card_edit' ORDER BY sequence DESC LIMIT 1",
                            (work_id,),
                        ).fetchone()
                    observed.append(row["state"] if row else "")
                    return {"ok": True}

                with patch.object(self.module, "run_work_card_cmd", side_effect=helper):
                    progress = self.module.run_gateway_card_command(
                        card,
                        ["python3", "jaimes_work_card.py", "update"],
                        status="progress",
                    )
                self.assertTrue(progress["ok"])
                self.assertEqual(observed, ["sending"])
                current = self.module.gateway_lifecycle().read_work(work_id)
                current = self.module.gateway_lifecycle().transition(
                    work_id,
                    "verifying",
                    expected_sequence=current["sequence"],
                    fencing_epoch=current["fencingEpoch"],
                )
                self.module.gateway_lifecycle().commit_terminal(
                    work_id,
                    "succeeded",
                    expected_sequence=current["sequence"],
                    fencing_epoch=current["fencingEpoch"],
                    private_payload={"final": "safe final"},
                )
                with patch.object(self.module, "run_work_card_cmd", side_effect=helper):
                    closed = self.module.run_gateway_card_command(
                        card,
                        ["python3", "jaimes_work_card.py", "done"],
                        status="delivery",
                    )
                self.assertTrue(closed["ok"])
                self.assertEqual(observed, ["sending", "sending"])

    def test_killed_pinned_writer_never_falls_back_to_unreceipted_card_edit(self):
        receipt = {
            "workId": "work-telegram-" + "b" * 24,
            "lifecycleVersion": 3,
            "deliveryTier": 3,
            "sequence": 5,
            "fencingEpoch": 1,
            "phase": "working",
            "writerAuthorityAtStart": True,
            "writerEnabled": False,
        }
        card = {
            "work_id": receipt["workId"],
            "lifecycle_version": 3,
            "no_card_required": False,
        }
        context = {"receipt": receipt, "writer": False, "shadow": False, "lifecycle": Mock()}
        with patch.object(self.module, "gateway_context_for_card", return_value=context), patch.object(
            self.module, "run_work_card_cmd"
        ) as helper:
            result = self.module.run_gateway_card_command(card, ["update"])
        helper.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertTrue(result["killed"])


if __name__ == "__main__":
    unittest.main()
