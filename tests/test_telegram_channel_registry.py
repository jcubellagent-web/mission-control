"""Focused fail-closed tests for Telegram lane ownership."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "telegram_channel_registry.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("telegram_channel_registry_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TelegramChannelRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.registry_path = Path(self.temporary.name) / "telegram-intake-lanes.json"
        self.module.REGISTRY_PATH = self.registry_path
        self.write_registry(
            {
                "defaultAuthorizedOwner": "josh2",
                "groups": {
                    "group-alpha": {
                        "defaultOwner": "josh2",
                        "topics": {
                            "lane-josh": {"label": "Inbox", "owner": "josh2"},
                            "lane-jaimes": {"label": "JAIMES Ops", "owner": "jaimes"},
                            "lane-invalid": {"label": "Invalid", "owner": "unknown"},
                        },
                    }
                },
                "mentionOverrides": {
                    "@josh2": "josh2",
                    "@jaimes": "jaimes",
                    "@invalid": "unknown",
                },
            }
        )

    def write_registry(self, payload) -> None:
        self.registry_path.write_text(json.dumps(payload), encoding="utf-8")
        self.module.load_registry.cache_clear()

    @staticmethod
    def source(**changes):
        values = {
            "platform": SimpleNamespace(value="telegram"),
            "chat_id": "group-alpha",
            "thread_id": "lane-jaimes",
            "chat_type": "group",
            "is_bot": False,
        }
        values.update(changes)
        return SimpleNamespace(**values)

    def test_explicit_topic_rows_are_the_only_group_ownership_source(self):
        self.assertEqual(self.module.topic_owner("group-alpha", "lane-josh"), "josh2")
        self.assertEqual(self.module.topic_owner("group-alpha", "lane-jaimes"), "jaimes")
        self.assertEqual(self.module.topic_owner("group-alpha", "missing"), "")
        self.assertEqual(
            self.module.topic_owner("group-alpha", "missing", fallback="josh2"),
            "",
        )
        self.assertEqual(self.module.topic_owner("missing", "lane-josh"), "")
        self.assertEqual(self.module.topic_owner("group-alpha", "lane-invalid"), "")

    def test_missing_or_malformed_registry_denies_all_owners(self):
        self.registry_path.unlink()
        self.module.load_registry.cache_clear()
        self.assertFalse(self.module.owner_accepts("josh2", "group-alpha", "lane-josh"))
        self.assertEqual(self.module.topics_for_owner("josh2", "group-alpha"), set())

        self.registry_path.write_text("[]", encoding="utf-8")
        self.module.load_registry.cache_clear()
        self.assertFalse(self.module.owner_accepts("jaimes", "group-alpha", "lane-jaimes"))

    def test_direct_messages_use_only_the_configured_default_owner(self):
        self.assertTrue(
            self.module.owner_accepts("josh2", "direct-chat", "", direct=True)
        )
        self.assertFalse(
            self.module.owner_accepts("jaimes", "direct-chat", "", direct=True)
        )
        self.write_registry({"defaultAuthorizedOwner": "unknown", "groups": {}})
        self.assertFalse(
            self.module.owner_accepts("josh2", "direct-chat", "", direct=True)
        )

    def test_configured_single_mention_overrides_base_owner(self):
        self.assertTrue(
            self.module.owner_accepts(
                "jaimes",
                "group-alpha",
                "lane-josh",
                text="please ask @JAIMES about this",
            )
        )
        self.assertFalse(
            self.module.owner_accepts(
                "josh2",
                "group-alpha",
                "lane-josh",
                text="please ask @JAIMES about this",
            )
        )
        self.assertTrue(
            self.module.owner_accepts(
                "josh2",
                "group-alpha",
                "lane-josh",
                text="email@jaimes.example is not a handle",
            )
        )

    def test_multiple_distinct_agent_mentions_are_ambiguous_and_silent(self):
        text = "@jaimes and @josh2 should both answer"
        self.assertEqual(
            self.module.message_owner("group-alpha", "lane-josh", text=text),
            "",
        )
        self.assertFalse(
            self.module.owner_accepts("jaimes", "group-alpha", "lane-josh", text=text)
        )
        self.assertFalse(
            self.module.owner_accepts("josh2", "group-alpha", "lane-josh", text=text)
        )

    def test_bot_origin_is_never_accepted_even_on_owned_lane(self):
        bot = self.source(is_bot=True)
        self.assertTrue(self.module.telegram_source_is_bot(bot))
        self.assertFalse(self.module.owner_accepts_source("jaimes", bot, text="hello"))

        human = self.source(is_bot=False)
        self.assertFalse(self.module.telegram_source_is_bot(human))
        self.assertTrue(self.module.owner_accepts_source("jaimes", human, text="hello"))

    def test_non_telegram_and_non_owner_sources_are_rejected(self):
        self.assertFalse(
            self.module.owner_accepts_source(
                "jaimes",
                self.source(platform=SimpleNamespace(value="discord")),
            )
        )
        self.assertFalse(
            self.module.owner_accepts_source(
                "jaimes",
                self.source(thread_id="lane-josh"),
            )
        )

    def test_topic_metadata_matching_and_owner_enumeration_are_config_driven(self):
        self.assertTrue(
            self.module.topic_matches(
                "group-alpha",
                "lane-jaimes",
                owner="jaimes",
                label="JAIMES Ops",
            )
        )
        self.assertFalse(
            self.module.topic_matches(
                "group-alpha",
                "lane-jaimes",
                owner="josh2",
            )
        )
        self.assertEqual(
            self.module.topics_for_owner("jaimes", "group-alpha"),
            {"lane-jaimes"},
        )
        self.assertEqual(self.module.topics_for_owner("unknown", "group-alpha"), set())


if __name__ == "__main__":
    unittest.main()
