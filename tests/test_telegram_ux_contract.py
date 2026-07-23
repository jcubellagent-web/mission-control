from __future__ import annotations

import ast
import datetime as dt
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from telegram_gateway_lifecycle import (  # noqa: E402
    TERMINAL_VISIBILITY_MAX_AGE_SECONDS,
    terminal_visibility_age_seconds,
)
from telegram_ux_contract import (  # noqa: E402
    actionable_approval_step,
    approval_button_label,
    clean_approval_step,
    friendly_tool_name,
    parse_telegram_target_from_key,
)


class TelegramUxContractTests(unittest.TestCase):
    def test_target_parser_preserves_existing_group_and_direct_contract(self) -> None:
        group_key = "agent:main:telegram:group:-1003589561528:topic:17"
        self.assertEqual(
            parse_telegram_target_from_key(group_key),
            {
                "telegram_chat_id": "-1003589561528",
                "telegram_thread_id": "17",
                "telegram_session_key": group_key,
            },
        )
        direct_key = "agent:main:telegram:direct:6218150306"
        self.assertEqual(
            parse_telegram_target_from_key(direct_key),
            {"telegram_chat_id": "6218150306", "telegram_session_key": direct_key},
        )
        self.assertEqual(parse_telegram_target_from_key("unknown"), {"telegram_session_key": "unknown"})

    def test_tool_names_match_the_previous_shared_fallback_copy(self) -> None:
        self.assertEqual(friendly_tool_name("functions.exec_command"), "local check")
        self.assertEqual(friendly_tool_name("apply_patch"), "file edit")
        self.assertEqual(friendly_tool_name("tools.custom_worker"), "custom worker")
        self.assertEqual(friendly_tool_name(""), "task step")

    def test_approval_cleanup_and_actionability_are_stable(self) -> None:
        self.assertEqual(
            clean_approval_step("  - **Approve** [restart](https://example.invalid)  . "),
            "Approve restart.",
        )
        for value in ("n/a", "Context: 50%", "https://example.invalid", "If you want, restart it"):
            self.assertFalse(actionable_approval_step(value), value)
        self.assertTrue(actionable_approval_step("Restart the watcher after verification"))

    def test_approval_button_label_preserves_production_fallback(self) -> None:
        self.assertEqual(approval_button_label("Optional: approval to restart the watcher."), "Approve: restart the watcher")
        self.assertEqual(approval_button_label(""), "Approve: next action")
        long_label = approval_button_label("Approve " + "x" * 80)
        self.assertTrue(long_label.startswith("Approve: "))
        self.assertTrue(long_label.endswith("..."))
        self.assertLessEqual(len(long_label), 50)

    def test_terminal_visibility_age_uses_one_lifecycle_policy(self) -> None:
        self.assertEqual(TERMINAL_VISIBILITY_MAX_AGE_SECONDS, 90)
        self.assertEqual(
            terminal_visibility_age_seconds({"createdAt": "not-a-time"}),
            float(TERMINAL_VISIBILITY_MAX_AGE_SECONDS + 1),
        )
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)).isoformat()
        self.assertEqual(terminal_visibility_age_seconds({"createdAt": future}), 0.0)

    def test_watchers_import_one_contract_without_dead_optional_fallbacks(self) -> None:
        extracted = {
            "parse_telegram_target_from_key",
            "friendly_tool_name",
            "clean_approval_step",
            "actionable_approval_step",
            "approval_button_label",
            "terminal_visibility_age_seconds",
        }
        intentionally_local = {"gateway_public_fields", "load_json", "local_time_label", "save_approval_actions"}
        for filename in ("josh_telegram_fast_ack.py", "jaimes_telegram_fast_ack.py"):
            source = (SCRIPTS / filename).read_text()
            tree = ast.parse(source)
            declared = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            self.assertTrue(extracted.isdisjoint(declared), filename)
            self.assertTrue(intentionally_local.issubset(declared), filename)
            self.assertIn("from telegram_ux_contract import", source)
            self.assertNotIn("telegram_ux_helpers", source)
            self.assertNotIn("ux_final_action_steps", source)


if __name__ == "__main__":
    unittest.main()
