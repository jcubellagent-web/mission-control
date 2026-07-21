from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brain_topic_manager as topic_manager


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.manage_topics = True
        self.create_result: dict[str, object] = {
            "ok": True, "state": "delivered", "result": {"message_thread_id": 77},
        }

    def __call__(self, method: str, payload: dict[str, object], _timeout: int) -> dict[str, object]:
        self.calls.append((method, dict(payload)))
        if method == "getMe":
            return {"ok": True, "result": {"id": 12345, "username": "private-bot"}}
        if method == "getChatMember":
            return {
                "ok": True,
                "result": {"status": "administrator", "can_manage_topics": self.manage_topics},
            }
        return self.create_result


class BrainTopicManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="brain-topic-manager-test-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.control = self.folder / "private" / "control.json"
        self.topic = self.folder / "private" / "topic.json"
        self.inventory = self.folder / "private" / "inventory.json"
        self.control.parent.mkdir(parents=True, mode=0o700)
        self.write_private(self.control, {
            "state": "confirmed", "chatId": "-100123", "botId": "12345",
            "canManageTopics": True,
        })
        self.write_private(self.inventory, {"chatId": "-100123", "topics": []})
        self.telegram = FakeTelegram()

    @staticmethod
    def write_private(path: Path, value: dict[str, object]) -> None:
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def manager(self) -> topic_manager.BrainTopicManager:
        return topic_manager.BrainTopicManager(
            control_receipt_path=self.control,
            topic_receipt_path=self.topic,
            inventory_path=self.inventory,
            transport=self.telegram,
        )

    def test_existing_confirmed_receipt_is_reused_without_any_api_call(self) -> None:
        # Backward-compatible with the already-created private receipt, which
        # predates the manager schemaVersion field.
        self.write_private(self.topic, {
            "state": "confirmed", "topicName": "Brain", "chatId": "-100123",
            "botId": "12345", "topicId": "77", "attemptCount": 1,
        })
        result = self.manager().ensure()
        self.assertTrue(result["ok"])
        self.assertTrue(result["reused"])
        self.assertEqual(result["attemptCount"], 1)
        self.assertEqual(self.telegram.calls, [])
        self.assertNotIn("77", json.dumps(result))
        self.assertNotIn("-100123", json.dumps(result))

    def test_unique_trusted_inventory_is_reused_and_create_is_never_called(self) -> None:
        self.write_private(self.inventory, {
            "chatId": "-100123", "topics": [{"name": "Brain", "topicId": "77"}],
        })
        result = self.manager().ensure()
        self.assertTrue(result["ok"])
        self.assertTrue(result["reused"])
        self.assertEqual([method for method, _ in self.telegram.calls], ["getMe", "getChatMember"])
        self.assertEqual(json.loads(self.topic.read_text())["state"], "confirmed")

    def test_confirmed_topic_reuse_requires_exact_control_bot_identity(self) -> None:
        self.write_private(self.topic, {
            "state": "confirmed", "topicName": "Brain", "chatId": "-100123",
            "botId": "99999", "topicId": "77", "attemptCount": 1,
        })
        with self.assertRaisesRegex(topic_manager.BrainTopicManagerError, "topic-receipt-binding-mismatch"):
            self.manager().ensure()
        self.assertEqual(self.telegram.calls, [])

    def test_verify_control_live_binds_confirmed_topic_and_permission(self) -> None:
        self.write_private(self.topic, {
            "state": "confirmed", "topicName": "Brain", "chatId": "-100123",
            "botId": "12345", "topicId": "77", "attemptCount": 1,
        })
        result = self.manager().verify_control()
        self.assertTrue(result["ok"])
        self.assertEqual(
            [method for method, _ in self.telegram.calls],
            ["getMe", "getChatMember"],
        )
        control = json.loads(self.control.read_text())
        self.assertEqual(control["state"], "confirmed")
        self.assertTrue(control["canManageTopics"])
        self.assertEqual(control["botId"], "12345")
        self.assertEqual(os.stat(self.control).st_mode & 0o777, 0o600)
        self.assertNotIn("12345", json.dumps(result))
        self.assertNotIn("-100123", json.dumps(result))

    def test_verify_control_mismatch_invalidates_prior_control_without_create(self) -> None:
        self.write_private(self.topic, {
            "state": "confirmed", "topicName": "Brain", "chatId": "-100123",
            "botId": "99999", "topicId": "77", "attemptCount": 1,
        })
        result = self.manager().verify_control()
        self.assertFalse(result["ok"])
        self.assertEqual([method for method, _ in self.telegram.calls], ["getMe"])
        control = json.loads(self.control.read_text())
        self.assertEqual(control["state"], "unverified")
        self.assertFalse(control["canManageTopics"])
        self.assertNotIn("createForumTopic", [method for method, _ in self.telegram.calls])

    def test_duplicate_trusted_inventory_fails_before_create(self) -> None:
        self.write_private(self.inventory, {
            "chatId": "-100123",
            "topics": [
                {"name": "Brain", "topicId": "77"},
                {"name": "Brain", "topicId": "78"},
            ],
        })
        with self.assertRaisesRegex(topic_manager.BrainTopicManagerError, "duplicate-brain-topics"):
            self.manager().ensure()
        self.assertNotIn("createForumTopic", [method for method, _ in self.telegram.calls])

    def test_missing_trusted_inventory_fails_before_create(self) -> None:
        self.inventory.unlink()
        with self.assertRaisesRegex(topic_manager.BrainTopicManagerError, "topic-inventory-required"):
            self.manager().ensure()
        self.assertNotIn("createForumTopic", [method for method, _ in self.telegram.calls])

    def test_permission_denial_is_awaiting_input_with_exact_requirement(self) -> None:
        self.telegram.manage_topics = False
        result = self.manager().ensure()
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "awaiting_input")
        self.assertEqual(result["permission"], "can_manage_topics-required")
        self.assertNotIn("createForumTopic", [method for method, _ in self.telegram.calls])
        private = json.loads(self.topic.read_text())
        self.assertEqual(private["requiredPermission"], "can_manage_topics")
        self.assertEqual(private["attemptCount"], 0)

    def test_create_persists_attempt_and_calls_create_exactly_once(self) -> None:
        first = self.manager().ensure()
        self.assertTrue(first["ok"])
        self.assertFalse(first["reused"])
        self.assertEqual([method for method, _ in self.telegram.calls].count("createForumTopic"), 1)
        private = json.loads(self.topic.read_text())
        self.assertEqual(private["state"], "confirmed")
        self.assertEqual(private["attemptCount"], 1)
        self.telegram.calls.clear()
        second = self.manager().ensure()
        self.assertTrue(second["reused"])
        self.assertEqual(self.telegram.calls, [])

    def test_ambiguous_create_is_indeterminate_and_never_blindly_retried(self) -> None:
        self.telegram.create_result = {
            "ok": False, "state": "indeterminate", "errorClass": "telegram-result-unknown",
        }
        first = self.manager().ensure()
        self.assertEqual(first["state"], "indeterminate")
        self.assertEqual([method for method, _ in self.telegram.calls].count("createForumTopic"), 1)
        self.telegram.calls.clear()
        second = self.manager().ensure()
        self.assertEqual(second["state"], "indeterminate")
        self.assertEqual(self.telegram.calls, [])

    def test_restart_from_attempting_is_fenced_indeterminate_without_api(self) -> None:
        self.write_private(self.topic, {
            "schemaVersion": 1, "state": "attempting", "topicName": "Brain",
            "chatId": "-100123", "topicId": "", "attemptCount": 1,
        })
        result = self.manager().ensure()
        self.assertEqual(result["state"], "indeterminate")
        self.assertEqual(self.telegram.calls, [])
        self.assertEqual(json.loads(self.topic.read_text())["state"], "indeterminate")

    def test_private_receipts_require_exact_owner_only_mode(self) -> None:
        self.control.chmod(0o644)
        with self.assertRaisesRegex(topic_manager.BrainTopicManagerError, "permissions-invalid"):
            self.manager().ensure()

    def test_control_receipt_must_explicitly_prove_manage_topics(self) -> None:
        self.write_private(self.control, {
            "state": "confirmed", "chatId": "-100123", "botId": "12345",
            "canManageTopics": False,
        })
        with self.assertRaisesRegex(topic_manager.BrainTopicManagerError, "topic-control-receipt-invalid"):
            self.manager().ensure()
        self.assertEqual(self.telegram.calls, [])

    def test_create_transport_treats_http_5xx_as_indeterminate_without_retry(self) -> None:
        error = urllib.error.HTTPError(
            "https://private.invalid", 502, "bad gateway", None, io.BytesIO(b"{}"),
        )
        with mock.patch.object(topic_manager.urllib.request, "urlopen", side_effect=error) as send:
            with mock.patch.dict(sys.modules, {
                "send_josh_reply": type("Reply", (), {"API_BASE": "https://private.invalid"}),
            }):
                result = topic_manager.default_transport(
                    "createForumTopic", {"chat_id": "private", "name": "Brain"}, 1,
                )
        self.assertEqual(result["state"], "indeterminate")
        self.assertEqual(send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
