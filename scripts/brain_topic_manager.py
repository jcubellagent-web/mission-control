#!/usr/bin/env python3
"""Idempotent, private Brain forum-topic manager for the Josh gateway.

The manager never discovers or creates from tracked IDs.  It verifies the bot
and group through owner-only private receipts, reuses one trusted existing
Brain topic, and writes a durable create intent before the sole API call.  An
unknown create result is permanently fenced as indeterminate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hmac
import json
import os
import socket
import stat
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping


TOPIC_MANAGER_SCHEMA_VERSION = 1
TOPIC_NAME = "Brain"


class BrainTopicManagerError(RuntimeError):
    pass


def clean_text(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").split())[:limit]


def safe_error_class(exc: BaseException) -> str:
    if isinstance(exc, BrainTopicManagerError):
        text = clean_text(exc, 80)
        return text if text.replace("-", "").isalnum() else "brain-topic-manager-error"
    return "brain-topic-manager-error"


def _private_json(path: Path) -> dict[str, Any]:
    raw = path.expanduser()
    if raw.is_symlink():
        raise BrainTopicManagerError("private-receipt-symlink")
    try:
        info = raw.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise BrainTopicManagerError("private-receipt-unreadable") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise BrainTopicManagerError("private-receipt-permissions-invalid")
    try:
        value = json.loads(raw.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BrainTopicManagerError("private-receipt-invalid") from exc
    if not isinstance(value, dict):
        raise BrainTopicManagerError("private-receipt-invalid")
    return value


def _atomic_private_json(path: Path, value: Mapping[str, Any]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(value), handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        fd = -1
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if fd >= 0:
            os.close(fd)
        Path(temporary).unlink(missing_ok=True)


def default_transport(method: str, payload: Mapping[str, Any], timeout: int) -> dict[str, Any]:
    workspace_scripts = Path(__file__).resolve().parents[2] / "scripts"
    if str(workspace_scripts) not in sys.path:
        sys.path.insert(0, str(workspace_scripts))
    try:
        from send_josh_reply import API_BASE  # type: ignore
    except Exception:
        API_BASE = ""
    if not API_BASE:
        return {"ok": False, "state": "dead_letter", "errorClass": "telegram-helper-unavailable"}
    request = urllib.request.Request(
        f"{API_BASE}/{method}",
        data=json.dumps(dict(payload), separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            try:
                parsed = json.loads(response.read())
            except json.JSONDecodeError:
                return {"ok": False, "state": "indeterminate", "errorClass": "telegram-response-invalid"}
    except urllib.error.HTTPError as exc:
        if 500 <= exc.code <= 599:
            # createForumTopic may have committed before the upstream failure;
            # its result is unknowable and must never be retried blindly.
            return {"ok": False, "state": "indeterminate", "errorClass": "telegram-result-unknown"}
        return {"ok": False, "state": "dead_letter", "errorClass": f"telegram-http-{exc.code}"}
    except (TimeoutError, socket.timeout, urllib.error.URLError, OSError):
        return {"ok": False, "state": "indeterminate", "errorClass": "telegram-result-unknown"}
    if parsed.get("ok") is True:
        return {"ok": True, "state": "delivered", "result": parsed.get("result") or {}}
    try:
        error_code = int(parsed.get("error_code") or 0)
    except (TypeError, ValueError):
        error_code = 0
    if 500 <= error_code <= 599:
        return {"ok": False, "state": "indeterminate", "errorClass": "telegram-result-unknown"}
    return {"ok": False, "state": "dead_letter", "errorClass": "telegram-api-rejected"}


class BrainTopicManager:
    def __init__(
        self,
        *,
        control_receipt_path: Path | str,
        topic_receipt_path: Path | str,
        inventory_path: Path | str,
        transport: Callable[[str, Mapping[str, Any], int], dict[str, Any]] | None = None,
    ) -> None:
        self.control_receipt_path = Path(control_receipt_path).expanduser().resolve()
        self.topic_receipt_path = Path(topic_receipt_path).expanduser().resolve()
        self.inventory_path = Path(inventory_path).expanduser().resolve()
        self.transport = transport or default_transport

    def _control(self) -> tuple[str, str]:
        receipt = _private_json(self.control_receipt_path)
        chat_id = clean_text(receipt.get("chatId"), 80)
        bot_id = clean_text(receipt.get("botId"), 80)
        if (
            receipt.get("state") != "confirmed"
            or receipt.get("canManageTopics") is not True
            or not chat_id
            or not bot_id
        ):
            raise BrainTopicManagerError("topic-control-receipt-invalid")
        return chat_id, bot_id

    def _existing_receipt(self) -> dict[str, Any] | None:
        try:
            return _private_json(self.topic_receipt_path)
        except FileNotFoundError:
            return None

    @staticmethod
    def _confirmed(value: Mapping[str, Any], chat_id: str, bot_id: str) -> bool:
        return bool(
            value.get("schemaVersion") in {None, TOPIC_MANAGER_SCHEMA_VERSION}
            and value.get("state") == "confirmed"
            and value.get("topicName") == TOPIC_NAME
            and clean_text(value.get("chatId"), 80) == chat_id
            and clean_text(value.get("botId"), 80) == bot_id
            and clean_text(value.get("topicId"), 80).isdigit()
            and int(value.get("attemptCount") or 0) == 1
        )

    def verify_control(self) -> dict[str, Any]:
        """Mint the control receipt from a confirmed topic and live bot proof."""
        error_class = "topic-control-verification-failed"
        try:
            topic = _private_json(self.topic_receipt_path)
            chat_id = clean_text(topic.get("chatId"), 80)
            bot_id = clean_text(topic.get("botId"), 80)
            if not self._confirmed(topic, chat_id, bot_id) or not bot_id.isdigit():
                raise BrainTopicManagerError("topic-creation-receipt-invalid")
            identity = self.transport("getMe", {}, 8)
            identity_result = identity.get("result") if identity.get("ok") else None
            actual_bot_id = clean_text(
                identity_result.get("id") if isinstance(identity_result, dict) else "", 80,
            )
            if not actual_bot_id or not hmac.compare_digest(actual_bot_id, bot_id):
                raise BrainTopicManagerError("telegram-bot-identity-mismatch")
            membership = self.transport(
                "getChatMember", {"chat_id": chat_id, "user_id": int(bot_id)}, 8,
            )
            member = membership.get("result") if membership.get("ok") else None
            allowed = bool(
                isinstance(member, dict)
                and (
                    member.get("status") == "creator"
                    or (
                        member.get("status") == "administrator"
                        and member.get("can_manage_topics") is True
                    )
                )
            )
            if not allowed:
                raise BrainTopicManagerError("telegram-topic-permission-unverified")
        except Exception as exc:
            if isinstance(exc, BrainTopicManagerError):
                error_class = safe_error_class(exc)
            _atomic_private_json(self.control_receipt_path, {
                "schemaVersion": TOPIC_MANAGER_SCHEMA_VERSION,
                "state": "unverified", "canManageTopics": False,
                "errorClass": error_class,
            })
            return {
                "ok": False, "state": "unverified", "permission": "unverified",
                "errorClass": error_class,
                "privacy": {"identifiersIncluded": False},
            }
        _atomic_private_json(self.control_receipt_path, {
            "schemaVersion": TOPIC_MANAGER_SCHEMA_VERSION,
            "state": "confirmed", "chatId": chat_id, "botId": bot_id,
            "canManageTopics": True,
            "verifiedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        })
        return {
            "ok": True, "state": "confirmed", "permission": "verified",
            "privacy": {"identifiersIncluded": False},
        }

    def _inventory_topic(self, chat_id: str) -> str:
        try:
            inventory = _private_json(self.inventory_path)
        except FileNotFoundError as exc:
            # Telegram does not expose a reliable forum-topic listing API.
            # Creation therefore requires a fresh trusted inventory proving
            # that no Brain topic already exists.
            raise BrainTopicManagerError("topic-inventory-required") from exc
        if clean_text(inventory.get("chatId"), 80) != chat_id:
            raise BrainTopicManagerError("topic-inventory-group-mismatch")
        topics = inventory.get("topics")
        if not isinstance(topics, list):
            raise BrainTopicManagerError("topic-inventory-invalid")
        matches = [
            clean_text(row.get("topicId"), 80)
            for row in topics
            if isinstance(row, dict) and row.get("name") == TOPIC_NAME
        ]
        if len(matches) > 1:
            raise BrainTopicManagerError("duplicate-brain-topics-detected")
        if matches and not matches[0].isdigit():
            raise BrainTopicManagerError("topic-inventory-invalid")
        return matches[0] if matches else ""

    def _verify_permission(self, chat_id: str, expected_bot_id: str) -> str:
        identity = self.transport("getMe", {}, 8)
        actual_bot_id = clean_text((identity.get("result") or {}).get("id"), 80) if identity.get("ok") else ""
        if not actual_bot_id or actual_bot_id != expected_bot_id:
            raise BrainTopicManagerError("telegram-bot-identity-mismatch")
        membership = self.transport(
            "getChatMember", {"chat_id": chat_id, "user_id": int(expected_bot_id)}, 8,
        )
        result = membership.get("result") if membership.get("ok") else None
        if not isinstance(result, dict):
            raise BrainTopicManagerError("telegram-topic-permission-unverified")
        if result.get("status") == "creator" or (
            result.get("status") == "administrator" and result.get("can_manage_topics") is True
        ):
            return "allowed"
        return "denied"

    def ensure(self) -> dict[str, Any]:
        chat_id, bot_id = self._control()
        existing = self._existing_receipt()
        if existing and self._confirmed(existing, chat_id, bot_id):
            return {
                "ok": True, "state": "confirmed", "reused": True,
                "attemptCount": 1, "permission": "verified",
                "privacy": {"identifiersIncluded": False},
            }
        if existing and existing.get("state") == "confirmed":
            raise BrainTopicManagerError("topic-receipt-binding-mismatch")
        if existing and existing.get("state") in {"attempting", "indeterminate"}:
            if existing.get("state") == "attempting":
                existing = {**existing, "state": "indeterminate", "errorClass": "telegram-result-unknown"}
                _atomic_private_json(self.topic_receipt_path, existing)
            return {
                "ok": False, "state": "indeterminate", "reused": False,
                "attemptCount": int(existing.get("attemptCount") or 1),
                "permission": "verified", "mitigation": "confirm-existing-topic",
                "privacy": {"identifiersIncluded": False},
            }
        if existing and int(existing.get("attemptCount") or 0) >= 1:
            return {
                "ok": False, "state": clean_text(existing.get("state"), 40) or "dead_letter",
                "reused": False, "attemptCount": int(existing.get("attemptCount") or 1),
                "permission": "verified", "privacy": {"identifiersIncluded": False},
            }

        permission = self._verify_permission(chat_id, bot_id)
        if permission != "allowed":
            receipt = {
                "schemaVersion": TOPIC_MANAGER_SCHEMA_VERSION,
                "state": "awaiting_input", "topicName": TOPIC_NAME,
                "chatId": chat_id, "botId": bot_id, "topicId": "", "attemptCount": 0,
                "requiredPermission": "can_manage_topics", "errorClass": "topic-permission-required",
            }
            _atomic_private_json(self.topic_receipt_path, receipt)
            return {
                "ok": False, "state": "awaiting_input", "reused": False,
                "attemptCount": 0, "permission": "can_manage_topics-required",
                "privacy": {"identifiersIncluded": False},
            }

        trusted_topic_id = self._inventory_topic(chat_id)
        if trusted_topic_id:
            _atomic_private_json(self.topic_receipt_path, {
                "schemaVersion": TOPIC_MANAGER_SCHEMA_VERSION,
                "state": "confirmed", "topicName": TOPIC_NAME,
                "chatId": chat_id, "botId": bot_id, "topicId": trusted_topic_id,
                # Reuse is still represented as one resolved creation history;
                # no createForumTopic call is performed.
                "attemptCount": 1, "creationMethod": "trusted-reuse",
            })
            return {
                "ok": True, "state": "confirmed", "reused": True,
                "attemptCount": 1, "permission": "verified",
                "privacy": {"identifiersIncluded": False},
            }

        prepared = {
            "schemaVersion": TOPIC_MANAGER_SCHEMA_VERSION,
            "state": "prepared", "topicName": TOPIC_NAME,
            "chatId": chat_id, "botId": bot_id, "topicId": "", "attemptCount": 0,
            "requiredPermission": "can_manage_topics", "errorClass": "",
        }
        _atomic_private_json(self.topic_receipt_path, prepared)
        attempting = {**prepared, "state": "attempting", "attemptCount": 1}
        _atomic_private_json(self.topic_receipt_path, attempting)
        result = self.transport("createForumTopic", {"chat_id": chat_id, "name": TOPIC_NAME}, 12)
        topic_id = clean_text((result.get("result") or {}).get("message_thread_id"), 80) if result.get("ok") else ""
        if result.get("ok") and topic_id.isdigit():
            _atomic_private_json(self.topic_receipt_path, {
                **attempting, "state": "confirmed", "topicId": topic_id,
                "creationMethod": "createForumTopic", "errorClass": "",
            })
            return {
                "ok": True, "state": "confirmed", "reused": False,
                "attemptCount": 1, "permission": "verified",
                "privacy": {"identifiersIncluded": False},
            }
        state = "indeterminate" if result.get("state") == "indeterminate" else "dead_letter"
        _atomic_private_json(self.topic_receipt_path, {
            **attempting, "state": state,
            "errorClass": clean_text(result.get("errorClass"), 80) or "topic-create-failed",
        })
        return {
            "ok": False, "state": state, "reused": False,
            "attemptCount": 1, "permission": "verified",
            "mitigation": "confirm-existing-topic" if state == "indeterminate" else "operator-review",
            "privacy": {"identifiersIncluded": False},
        }


def parser() -> argparse.ArgumentParser:
    private = Path.home() / ".openclaw/private/telegram-topic-control"
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--control-receipt", default=os.environ.get(
        "BRAIN_TOPIC_CONTROL_RECEIPT", str(private / "control-center-bot.json"),
    ))
    result.add_argument("--topic-receipt", default=os.environ.get(
        "BRAIN_TOPIC_RECEIPT", str(private / "brain-topic-creation.json"),
    ))
    result.add_argument("--inventory", default=os.environ.get(
        "BRAIN_TOPIC_INVENTORY", str(private / "forum-topic-inventory.json"),
    ))
    result.add_argument("command", choices=("ensure", "verify-control"))
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        manager = BrainTopicManager(
            control_receipt_path=args.control_receipt,
            topic_receipt_path=args.topic_receipt,
            inventory_path=args.inventory,
        )
        result = manager.verify_control() if args.command == "verify-control" else manager.ensure()
    except Exception as exc:
        result = {
            "ok": False, "state": "error", "errorClass": safe_error_class(exc),
            "privacy": {"identifiersIncluded": False},
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
