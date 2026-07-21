"""Focused contracts for JAIMES Telegram runtime ownership."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "hermes-plugins" / "jaimes-topic17-runtime-owner"
PLUGIN_PATH = PLUGIN_DIR / "__init__.py"


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "jaimes_topic17_runtime_owner_test",
        PLUGIN_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Topic17RuntimeOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = _load_plugin()
        self.session = {
            "HERMES_SESSION_PLATFORM": "telegram",
            "HERMES_SESSION_CHAT_ID": "group-alpha",
            "HERMES_SESSION_THREAD_ID": "lane-jaimes-ops",
        }
        self.registry = SimpleNamespace(
            telegram_source_is_bot=lambda source: bool(getattr(source, "is_bot", False)),
            owner_accepts_source=lambda owner, source, text="": bool(
                owner == "jaimes" and getattr(source, "accepted", False)
            ),
            topic_matches=lambda chat_id, thread_id, **criteria: bool(
                chat_id == "group-alpha"
                and thread_id == "lane-jaimes-ops"
                and criteria == {"owner": "jaimes", "label": "JAIMES Ops"}
            ),
            topic_owner=lambda chat_id, thread_id: (
                "jaimes" if (chat_id, thread_id) == ("group-alpha", "lane-jaimes-ops") else "josh2"
            ),
        )

    def session_patch(self):
        return patch.object(
            self.plugin,
            "_session_value",
            side_effect=lambda name: self.session.get(name, ""),
        )

    @staticmethod
    def event(**changes):
        source_values = {
            "platform": SimpleNamespace(value="telegram"),
            "chat_id": "group-alpha",
            "thread_id": "lane-jaimes-ops",
            "chat_type": "group",
            "is_bot": False,
            "accepted": True,
        }
        source_values.update(changes.pop("source", {}))
        values = {"text": "hello", "source": SimpleNamespace(**source_values)}
        values.update(changes)
        return SimpleNamespace(**values)

    def assert_surface_block(self, result):
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "block")
        self.assertIn("JAIMES Ops", result["message"])
        self.assertIn("managed by the gateway", result["message"])
        self.assertNotIn("group-alpha", result["message"])

    def test_bot_origin_telegram_update_is_silently_skipped(self):
        with patch.object(self.plugin, "_load_registry_module", return_value=self.registry):
            result = self.plugin._on_pre_gateway_dispatch(
                self.event(source={"is_bot": True})
            )
        self.assertEqual(
            result,
            {"action": "skip", "reason": "telegram-bot-origin"},
        )

    def test_non_owner_telegram_update_is_silently_skipped(self):
        with patch.object(self.plugin, "_load_registry_module", return_value=self.registry):
            result = self.plugin._on_pre_gateway_dispatch(
                self.event(source={"accepted": False})
            )
        self.assertEqual(
            result,
            {"action": "skip", "reason": "telegram-non-owner"},
        )

    def test_owner_human_update_continues_without_plugin_reply(self):
        with patch.object(self.plugin, "_load_registry_module", return_value=self.registry):
            self.assertIsNone(self.plugin._on_pre_gateway_dispatch(self.event()))

    def test_non_telegram_update_is_out_of_scope(self):
        with patch.object(
            self.plugin,
            "_load_registry_module",
            side_effect=AssertionError("non-Telegram must not load ownership"),
        ):
            self.assertIsNone(
                self.plugin._on_pre_gateway_dispatch(
                    self.event(source={"platform": SimpleNamespace(value="discord")})
                )
            )

    def test_registry_failure_denies_telegram_silently(self):
        with patch.object(self.plugin, "_load_registry_module", return_value=None):
            result = self.plugin._on_pre_gateway_dispatch(self.event())
        self.assertEqual(
            result,
            {"action": "skip", "reason": "telegram-ownership-unavailable"},
        )

    def test_message_text_is_forwarded_for_configured_mention_override(self):
        accepts = Mock(return_value=True)
        registry = SimpleNamespace(
            telegram_source_is_bot=Mock(return_value=False),
            owner_accepts_source=accepts,
        )
        with patch.object(self.plugin, "_load_registry_module", return_value=registry):
            self.assertIsNone(
                self.plugin._on_pre_gateway_dispatch(self.event(text="please ask @jaimes"))
            )
        accepts.assert_called_once()
        self.assertEqual(accepts.call_args.args[0], "jaimes")
        self.assertEqual(accepts.call_args.kwargs["text"], "please ask @jaimes")

    def test_existing_managed_lane_blocks_direct_model_surfaces(self):
        cases = [
            ("send_message", {}),
            (
                "send_message",
                {"action": "send", "target": "telegram", "message": "working"},
            ),
            ("terminal", {"command": "hermes send --to telegram working"}),
            (
                "execute_code",
                {
                    "code": (
                        "from telegram import Bot\n"
                        "bot.send_message(chat_id=target, text='working')"
                    )
                },
            ),
            (
                "terminal",
                {"command": "python3 scripts/jaimes_work_card.py update --key task"},
            ),
            (
                "terminal",
                {"command": "python3 scripts/agent_publish.py --agent jaimes"},
            ),
        ]
        with self.session_patch(), patch.object(
            self.plugin,
            "_load_registry_module",
            return_value=self.registry,
        ):
            for tool_name, args in cases:
                with self.subTest(tool_name=tool_name, args=args):
                    self.assert_surface_block(
                        self.plugin._on_pre_tool_call(tool_name, args)
                    )

    def test_read_only_and_non_telegram_tools_remain_available(self):
        cases = [
            ("send_message", {"action": "list"}),
            ("send_message", {"target": "slack:#ops", "message": "hello"}),
            ("terminal", {"command": "curl https://api.telegram.org/botX/getMe"}),
            ("read_file", {"path": "/workspace/notes.md"}),
        ]
        with self.session_patch(), patch.object(
            self.plugin,
            "_load_registry_module",
            return_value=self.registry,
        ):
            for tool_name, args in cases:
                with self.subTest(tool_name=tool_name, args=args):
                    self.assertIsNone(self.plugin._on_pre_tool_call(tool_name, args))

    def test_tool_guard_does_not_expand_to_other_known_lanes(self):
        self.session["HERMES_SESSION_THREAD_ID"] = "lane-research"
        registry = SimpleNamespace(
            topic_matches=Mock(return_value=False),
            topic_owner=Mock(return_value="jaimes"),
        )
        with self.session_patch(), patch.object(
            self.plugin,
            "_load_registry_module",
            return_value=registry,
        ):
            self.assertIsNone(
                self.plugin._on_pre_tool_call(
                    "send_message",
                    {"target": "telegram", "message": "hello"},
                )
            )

    def test_tool_guard_preserves_exact_lane_scope_when_registry_is_unknown(self):
        with self.session_patch(), patch.object(
            self.plugin,
            "_load_registry_module",
            return_value=None,
        ):
            self.assertIsNone(
                self.plugin._on_pre_tool_call(
                    "send_message",
                    {"target": "telegram", "message": "hello"},
                )
            )

        registry = SimpleNamespace(
            topic_matches=Mock(return_value=False),
            topic_owner=Mock(return_value=""),
        )
        with self.session_patch(), patch.object(
            self.plugin,
            "_load_registry_module",
            return_value=registry,
        ):
            self.assertIsNone(
                self.plugin._on_pre_tool_call(
                    "send_message",
                    {"target": "telegram", "message": "hello"},
                )
            )

    def test_register_wires_ingress_and_tool_guards(self):
        calls = []

        class Context:
            def register_hook(self, name, callback):
                calls.append((name, callback))

        self.plugin.register(Context())
        self.assertEqual(
            calls,
            [
                ("pre_gateway_dispatch", self.plugin._on_pre_gateway_dispatch),
                ("pre_tool_call", self.plugin._on_pre_tool_call),
                ("transform_llm_output", self.plugin._on_transform_llm_output),
            ],
        )

        manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
        self.assertIn("- pre_gateway_dispatch", manifest)
        self.assertIn("- pre_tool_call", manifest)
        self.assertIn("- transform_llm_output", manifest)

    def test_terminal_transform_returns_only_prepared_gateway_final(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout='{"managed": true, "ok": true, "text": "<pre>canonical</pre>"}',
            stderr="",
        )
        with self.session_patch(), patch.object(
            self.plugin, "_load_registry_module", return_value=self.registry
        ), patch.object(
            self.plugin, "_writer_rollout_required", return_value=True
        ), patch.object(
            self.plugin.subprocess, "run", return_value=completed
        ) as run:
            result = self.plugin._on_transform_llm_output(
                response_text="model text",
                session_id="session-1",
                model="provider/model",
                platform="telegram",
            )
        self.assertEqual(result, "<pre>canonical</pre>")
        payload = run.call_args.kwargs["input"]
        self.assertIn('"response_text": "model text"', payload)
        self.assertNotIn("model text", " ".join(run.call_args.args[0]))

    def test_terminal_transform_fails_closed_when_writer_receipt_is_required(self):
        failed = SimpleNamespace(returncode=2, stdout="", stderr="private error")
        with self.session_patch(), patch.object(
            self.plugin, "_load_registry_module", return_value=self.registry
        ), patch.object(
            self.plugin, "_writer_rollout_required", return_value=True
        ), patch.object(self.plugin.subprocess, "run", return_value=failed):
            with self.assertRaises(self.plugin.GatewayLifecycleAbort):
                self.plugin._on_transform_llm_output(
                    response_text="must not pass through",
                    session_id="session-1",
                    model="provider/model",
                    platform="telegram",
                )

    def test_terminal_transform_recovers_owned_session_after_context_is_cleared(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout='{"managed": true, "ok": true, "text": "<pre>canonical</pre>"}',
            stderr="",
        )
        recovered = {
            "session_id": "session-1",
            "inbound_message_id": "private-origin-receipt",
        }
        with patch.object(self.plugin, "_session_value", return_value=""), patch.object(
            self.plugin, "_active_managed_card", return_value=recovered
        ), patch.object(
            self.plugin, "_writer_rollout_required", return_value=True
        ), patch.object(
            self.plugin.subprocess, "run", return_value=completed
        ) as run:
            result = self.plugin._on_transform_llm_output(
                response_text="model text",
                session_id="session-1",
                model="provider/model",
                platform="telegram",
            )
        self.assertEqual(result, "<pre>canonical</pre>")
        payload = run.call_args.kwargs["input"]
        self.assertIn('"inbound_message_id": "private-origin-receipt"', payload)

    def test_terminal_transform_does_not_claim_contextless_unmanaged_session(self):
        with patch.object(self.plugin, "_session_value", return_value=""), patch.object(
            self.plugin, "_active_managed_card", return_value=None
        ), patch.object(
            self.plugin.subprocess,
            "run",
            side_effect=AssertionError("unmanaged session must not prepare a terminal"),
        ):
            self.assertIsNone(
                self.plugin._on_transform_llm_output(
                    response_text="model text",
                    session_id="session-other",
                    model="provider/model",
                    platform="telegram",
                )
            )

    def test_terminal_transform_leaves_native_and_unmanaged_turns_unchanged(self):
        with patch.object(
            self.plugin.subprocess,
            "run",
            side_effect=AssertionError("unmanaged surfaces must not invoke gateway"),
        ):
            self.assertIsNone(
                self.plugin._on_transform_llm_output(
                    response_text="desktop final",
                    session_id="session-1",
                    model="provider/model",
                    platform="cli",
                )
            )

    def test_plugin_contains_no_embedded_chat_or_topic_identifier(self):
        source = PLUGIN_PATH.read_text(encoding="utf-8")
        self.assertNotIn("_MANAGED_CHAT_ID", source)
        self.assertNotIn("_MANAGED_THREAD_ID", source)
        self.assertNotRegex(source, r"[\"']-100\d{6,}[\"']")


if __name__ == "__main__":
    unittest.main()
