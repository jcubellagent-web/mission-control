from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TelegramRuleAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.agents = (ROOT / "AGENTS.md").read_text()
        cls.protocol = (ROOT / "docs/control-center-topic-protocol.md").read_text()
        cls.skill = (ROOT / "agent-skills/telegram-task-flow/SKILL.md").read_text()
        cls.registry = json.loads((ROOT / "config/telegram-intake-lanes.json").read_text())

    def test_authority_chain_is_explicit_and_nonduplicative(self) -> None:
        for path in (
            "config/telegram-intake-lanes.json",
            "scripts/telegram_channel_registry.py",
            "agent-skills/telegram-task-flow/SKILL.md",
        ):
            self.assertIn(path, self.agents)
            self.assertIn(path, self.protocol)
        self.assertIn("Do not maintain a second detailed Telegram UX specification here", self.agents)
        self.assertNotIn("Before publishing an objective to Telegram or Control Tower", self.agents)
        self.assertIn("Before asking Josh for context", self.skill)

    def test_protocol_does_not_reintroduce_responder_races_or_topic_moves(self) -> None:
        forbidden = (
            "first appropriate owner should acknowledge",
            "Specialist work should be moved to the matching topic",
            "agents should copy/summarize the work into the correct topic",
            "the other topics can stay mention-gated",
        )
        for phrase in forbidden:
            self.assertNotIn(phrase, self.protocol)
        self.assertIn("multiple agent mentions", self.protocol)
        self.assertIn("stay in the origin topic", self.protocol)

    def test_protocol_topic_table_matches_the_runtime_registry(self) -> None:
        groups = self.registry["groups"]
        self.assertEqual(len(groups), 1)
        topics = next(iter(groups.values()))["topics"]
        rows = {
            label: owner
            for label, owner in re.findall(r"^\| ([^|]+?) \| ([^|]+?) \|", self.protocol, flags=re.MULTILINE)
            if label != "Topic"
        }
        owner_labels = {"josh2": "JOSH 2.0", "jaimes": "JAIMES"}
        expected = {
            str(row["label"]): owner_labels[str(row["owner"])]
            for row in topics.values()
        }
        self.assertEqual(rows, expected)

    def test_only_supported_inbox_hint_is_advertised(self) -> None:
        hashtags = set(re.findall(r"#[A-Za-z0-9_]+", self.protocol))
        self.assertEqual(hashtags, {"#jaimes"})
        self.assertIn("Only configured `@mentions` change response ownership", self.protocol)

    def test_agents_keeps_hard_delivery_invariants(self) -> None:
        normalized = " ".join(self.agents.split())
        for phrase in (
            "Exactly one registered owner",
            "one editable live card",
            "one structured final",
            "remain in the origin topic",
            "receiving agent's Brain Feed lane",
            "never create a parallel Telegram surface",
        ):
            self.assertIn(phrase, normalized)


if __name__ == "__main__":
    unittest.main()
