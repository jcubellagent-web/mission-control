#!/usr/bin/env python3
"""Shared ownership registry for authorized Telegram intake lanes.

The registry controls routing only. Raw chat/message identifiers never enter
Control Tower projections; publishers submit a one-way origin-claim hash.
"""
# #JAIMES: all authorized topics enter the same stable work-identity contract;
# mentions override topic ownership without creating a second responder.
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = Path(
    os.environ.get(
        "TELEGRAM_INTAKE_LANES",
        str(ROOT / "config" / "telegram-intake-lanes.json"),
    )
).expanduser()
VALID_OWNERS = {"josh2", "jaimes", "jain", "joshex"}


@lru_cache(maxsize=1)
def load_registry() -> dict[str, Any]:
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _registered_topic(chat_id: Any, thread_id: Any) -> dict[str, Any] | None:
    """Return one explicitly registered topic, never a guessed default."""
    registry = load_registry()
    groups = registry.get("groups") if isinstance(registry.get("groups"), dict) else {}
    group = groups.get(str(chat_id)) if isinstance(groups, dict) else None
    if not isinstance(group, dict):
        return None
    topics = group.get("topics") if isinstance(group.get("topics"), dict) else {}
    topic = topics.get(str(thread_id)) if isinstance(topics, dict) else None
    return topic if isinstance(topic, dict) else None


def topic_metadata(chat_id: Any, thread_id: Any) -> dict[str, Any]:
    """Return safe topic routing metadata, or an empty mapping when unknown."""
    topic = _registered_topic(chat_id, thread_id)
    return dict(topic) if isinstance(topic, dict) else {}


def topic_owner(chat_id: Any, thread_id: Any, fallback: str = "") -> str:
    """Resolve an explicit topic owner; malformed or missing rows deny access.

    ``fallback`` remains in the signature for compatibility with older callers,
    but it is deliberately not an authorization source.  Ownership comes only
    from ``telegram-intake-lanes.json``.
    """
    del fallback
    topic = _registered_topic(chat_id, thread_id)
    owner = str(topic.get("owner") or "") if isinstance(topic, dict) else ""
    return owner if owner in VALID_OWNERS else ""


def direct_owner() -> str:
    """Return the configured direct-message owner, failing closed if invalid."""
    owner = str(load_registry().get("defaultAuthorizedOwner") or "")
    return owner if owner in VALID_OWNERS else ""


def _mentioned_owners(text: Any) -> set[str]:
    """Resolve exact configured handles without trusting partial-name matches."""
    value = str(text or "")
    overrides = load_registry().get("mentionOverrides")
    if not value or not isinstance(overrides, dict):
        return set()
    owners: set[str] = set()
    for raw_handle, raw_owner in overrides.items():
        handle = str(raw_handle or "").strip()
        owner = str(raw_owner or "").strip()
        if not handle.startswith("@") or owner not in VALID_OWNERS:
            continue
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(handle)}(?![A-Za-z0-9_])"
        if re.search(pattern, value, flags=re.IGNORECASE):
            owners.add(owner)
    return owners


def message_owner(
    chat_id: Any,
    thread_id: Any,
    *,
    text: Any = "",
    direct: bool = False,
) -> str:
    """Resolve one message owner, including an unambiguous configured mention."""
    base_owner = direct_owner() if direct else topic_owner(chat_id, thread_id)
    if not base_owner:
        return ""
    mentioned = _mentioned_owners(text)
    if len(mentioned) > 1:
        # Multiple agent mentions are ambiguous.  Every gateway must make the
        # same silent decision rather than creating a responder race.
        return ""
    return next(iter(mentioned)) if mentioned else base_owner


def owner_accepts(
    owner: str,
    chat_id: Any,
    thread_id: Any,
    *,
    direct: bool = False,
    text: Any = "",
) -> bool:
    if owner not in VALID_OWNERS:
        return False
    return message_owner(chat_id, thread_id, text=text, direct=direct) == owner


def _platform_name(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def telegram_source_is_bot(source: Any) -> bool:
    """Trust the adapter's normalized bot bit only for Telegram sources."""
    return bool(
        source is not None
        and _platform_name(getattr(source, "platform", "")) == "telegram"
        and getattr(source, "is_bot", False)
    )


def owner_accepts_source(owner: str, source: Any, *, text: Any = "") -> bool:
    """Apply ownership and bot-origin gates to a normalized gateway source."""
    if source is None or _platform_name(getattr(source, "platform", "")) != "telegram":
        return False
    if telegram_source_is_bot(source):
        return False
    direct = str(getattr(source, "chat_type", "") or "").strip().lower() == "dm"
    return owner_accepts(
        owner,
        getattr(source, "chat_id", ""),
        getattr(source, "thread_id", ""),
        direct=direct,
        text=text,
    )


def topic_matches(
    chat_id: Any,
    thread_id: Any,
    *,
    owner: str = "",
    label: str = "",
    lane: str = "",
) -> bool:
    """Match semantic lane metadata while keeping identifiers in the config."""
    topic = topic_metadata(chat_id, thread_id)
    if not topic:
        return False
    if owner and str(topic.get("owner") or "") != owner:
        return False
    if label and str(topic.get("label") or "") != label:
        return False
    if lane and str(topic.get("lane") or "") != lane:
        return False
    return True


def topics_for_owner(owner: str, chat_id: Any) -> set[str]:
    if owner not in VALID_OWNERS:
        return set()
    registry = load_registry()
    groups = registry.get("groups") if isinstance(registry.get("groups"), dict) else {}
    group = groups.get(str(chat_id)) if isinstance(groups, dict) else None
    topics = group.get("topics") if isinstance(group, dict) and isinstance(group.get("topics"), dict) else {}
    return {
        str(topic_id)
        for topic_id, row in topics.items()
        if isinstance(row, dict) and str(row.get("owner") or "") == owner
    }
