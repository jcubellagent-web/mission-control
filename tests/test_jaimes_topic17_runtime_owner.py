"""Focused contract tests for the JAIMES Ops runtime-ownership plugin."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


TEST_DIR = Path(__file__).resolve().parent
REPO_PLUGIN_PATH = (
    TEST_DIR.parent
    / "hermes-plugins"
    / "jaimes-topic17-runtime-owner"
    / "__init__.py"
)
STAGED_PLUGIN_PATH = (
    TEST_DIR.parent / "jaimes-topic17-runtime-owner" / "__init__.py"
)
PLUGIN_PATH = REPO_PLUGIN_PATH if REPO_PLUGIN_PATH.exists() else STAGED_PLUGIN_PATH


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "jaimes_topic17_runtime_owner_test", PLUGIN_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Topic17RuntimeOwnerTests(unittest.TestCase):
    def setUp(self):
        self.plugin = _load_plugin()
        self.session = {
            "HERMES_SESSION_PLATFORM": "telegram",
            "HERMES_SESSION_CHAT_ID": "-1003589561528",
            "HERMES_SESSION_THREAD_ID": "17",
        }

    def session_patch(self):
        return patch.object(
            self.plugin,
            "_session_value",
            side_effect=lambda name: self.session.get(name, ""),
        )

    def assert_human_block(self, result):
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "block")
        self.assertIn("JAIMES Ops", result["message"])
        self.assertIn("managed by the gateway", result["message"])
        self.assertIn("Continue the substantive work normally", result["message"])
        self.assertNotIn("-1003589561528", result["message"])

    def test_compression_child_recovers_parent_managed_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state_path = home / ".openclaw" / "telegram" / "jaimes_fast_ack_state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                "active_cards": {
                    "telegram-message-41": {
                        "status": "active",
                        "session_id": "parent-session",
                        "telegram_chat_id": "-1003589561528",
                        "telegram_thread_id": "17",
                        "started_at": "2026-07-23T05:00:00Z",
                        "lifecycle_writer_enabled": True,
                    }
                }
            }))
            state_db = home / ".hermes" / "state.db"
            state_db.parent.mkdir(parents=True)
            with sqlite3.connect(state_db) as db:
                db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT)")
                db.execute("INSERT INTO sessions VALUES (?, ?)", ("parent-session", None))
                db.execute("INSERT INTO sessions VALUES (?, ?)", ("child-session", "parent-session"))

            registry = SimpleNamespace(owner_accepts=lambda *_args, **_kwargs: True)
            with patch.object(self.plugin.Path, "home", return_value=home), patch.object(
                self.plugin, "_load_registry_module", return_value=registry
            ):
                card = self.plugin._active_managed_card("child-session")

            self.assertIsNotNone(card)
            self.assertEqual(card["session_id"], "parent-session")
            self.assertEqual(card["_runtime_run_id"], "telegram-message-41")

    def test_blocks_model_telegram_send_message_surfaces(self):
        cases = [
            {},
            {"action": "send", "target": "telegram", "message": "hello"},
            {
                "action": "send",
                "target": "telegram:-1003589561528:17",
                "message": "working",
            },
            {"action": "react", "target": "telegram:-1003589561528:17"},
            {"action": "unreact", "target": "telegram:-1003589561528:17"},
        ]
        with self.session_patch():
            for args in cases:
                with self.subTest(args=args):
                    self.assert_human_block(
                        self.plugin._on_pre_tool_call("send_message", args)
                    )

    def test_send_message_list_and_non_telegram_target_remain_available(self):
        with self.session_patch():
            self.assertIsNone(
                self.plugin._on_pre_tool_call("send_message", {"action": "list"})
            )
            self.assertIsNone(
                self.plugin._on_pre_tool_call(
                    "send_message",
                    {
                        "action": "send",
                        "target": "slack:#ops",
                        "message": "hello",
                    },
                )
            )

    def test_guard_is_scoped_to_exact_telegram_chat_and_thread(self):
        cases = [
            ("HERMES_SESSION_PLATFORM", "discord"),
            ("HERMES_SESSION_CHAT_ID", "-1000000000000"),
            ("HERMES_SESSION_THREAD_ID", "18"),
        ]
        for changed_name, changed_value in cases:
            with self.subTest(changed_name=changed_name):
                original = self.session[changed_name]
                self.session[changed_name] = changed_value
                try:
                    with self.session_patch():
                        self.assertIsNone(
                            self.plugin._on_pre_tool_call(
                                "send_message",
                                {"target": "telegram", "message": "hello"},
                            )
                        )
                finally:
                    self.session[changed_name] = original

    def test_blocks_raw_outbound_telegram_surfaces(self):
        cases = [
            (
                "terminal",
                {
                    "command": (
                        "python3 -c 'import os, urllib.request; "
                        "token=os.environ[\"gateway_telegram_token\"]; "
                        "urllib.request.urlopen(\"https://api.telegram.org/"
                        "botTOKEN/sendMessage\")'"
                    )
                },
            ),
            (
                "execute_code",
                {
                    "code": (
                        "import os, requests\n"
                        "requests.post(f\"https://api.telegram.org/bot"
                        "{os.environ['TELEGRAM_BOT_TOKEN']}/editMessageText\")"
                    )
                },
            ),
            (
                "execute_code",
                {
                    "code": (
                        "from telegram import Bot\n"
                        "bot = Bot(token=token)\n"
                        "bot.send_message(chat_id=chat_id, text='working')"
                    )
                },
            ),
            ("terminal", {"command": "hermes send --to telegram 'working'"}),
        ]
        with self.session_patch():
            for tool_name, args in cases:
                with self.subTest(tool_name=tool_name, args=args):
                    self.assert_human_block(
                        self.plugin._on_pre_tool_call(tool_name, args)
                    )

    def test_blocks_direct_live_card_helper_invocation(self):
        cases = [
            (
                "terminal",
                {
                    "command": (
                        "python3 /workspace/scripts/jaimes_work_card.py start "
                        "--key health-check --title 'Health check'"
                    )
                },
            ),
            (
                "execute_code",
                {
                    "code": (
                        "import subprocess\n"
                        "subprocess.run(['python3', 'jaimes_live_card.py', "
                        "'update'], check=True)"
                    )
                },
            ),
            (
                "terminal",
                {"command": "./mission-control/scripts/jaimes_work_card.py start --key check"},
            ),
            (
                "terminal",
                {"command": "/Users/jc_agent/bin/jaimes_work_card.py update --key check"},
            ),
            (
                "terminal",
                {"command": "bash ~/scripts/jaimes_bf_push.sh 'Working' active exec"},
            ),
            (
                "terminal",
                {"command": "python3 mission-control/scripts/agent_publish.py --agent jaimes"},
            ),
        ]
        with self.session_patch():
            for tool_name, args in cases:
                with self.subTest(tool_name=tool_name, args=args):
                    self.assert_human_block(
                        self.plugin._on_pre_tool_call(tool_name, args)
                    )

    def test_blocks_imported_runtime_surface_helpers(self):
        cases = [
            (
                "execute_code",
                {
                    "code": (
                        "import jaimes_work_card as card\n"
                        "card.api_call('sendMessage', payload)"
                    )
                },
            ),
            (
                "execute_code",
                {
                    "code": (
                        "from jaimes_work_card import send_card\n"
                        "send_card(payload, timeout=15)"
                    )
                },
            ),
            (
                "terminal",
                {
                    "command": (
                        "python3 -c \"import jaimes_telegram_fast_ack as ack; "
                        "ack.edit_message(17, 'working')\""
                    )
                },
            ),
            (
                "execute_code",
                {
                    "code": (
                        "from jaimes_telegram_fast_ack import send_ack\n"
                        "send_ack(event)"
                    )
                },
            ),
        ]
        with self.session_patch():
            for tool_name, args in cases:
                with self.subTest(tool_name=tool_name, args=args):
                    self.assert_human_block(
                        self.plugin._on_pre_tool_call(tool_name, args)
                    )

    def test_allows_read_only_telegram_diagnostics_and_normal_work(self):
        cases = [
            (
                "terminal",
                {
                    "command": (
                        "curl -fsS https://api.telegram.org/botREDACTED/getMe"
                    )
                },
            ),
            (
                "terminal",
                {
                    "command": (
                        "rg -n 'jaimes_work_card.py|sendMessage' scripts tests"
                    )
                },
            ),
            (
                "terminal",
                {"command": "rg -n 'agent_publish.py|jaimes_bf_push.sh' scripts tests"},
            ),
            ("execute_code", {"code": "print(sum(range(100)))"}),
            (
                "execute_code",
                {
                    "code": (
                        "import jaimes_work_card\n"
                        "print(jaimes_work_card.__doc__)"
                    )
                },
            ),
            ("read_file", {"path": "/workspace/notes.md"}),
        ]
        with self.session_patch():
            for tool_name, args in cases:
                with self.subTest(tool_name=tool_name, args=args):
                    self.assertIsNone(
                        self.plugin._on_pre_tool_call(tool_name, args)
                    )

    def test_register_wires_gateway_ownership_surface_and_terminal_hooks(self):
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


if __name__ == "__main__":
    unittest.main()
