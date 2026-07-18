#!/usr/bin/env python3
"""Shared ownership registry for authorized Telegram intake lanes.

The registry controls routing only. Raw chat/message identifiers never enter
Control Tower projections; publishers submit a one-way origin-claim hash.
"""
# #JAIMES: all authorized topics enter the same stable work-identity contract;
# mentions override topic ownership without creating a second responder.
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "telegram-intake-lanes.json"
VALID_OWNERS = {"josh2", "jaimes", "jain", "joshex"}


@lru_cache(maxsize=1)
def load_registry() -> dict[str, Any]:
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def topic_owner(chat_id: Any, thread_id: Any, fallback: str = "josh2") -> str:
    registry = load_registry()
    groups = registry.get("groups") if isinstance(registry.get("groups"), dict) else {}
    group = groups.get(str(chat_id)) if isinstance(groups, dict) else None
    if not isinstance(group, dict):
        return fallback if fallback in VALID_OWNERS else "josh2"
    topics = group.get("topics") if isinstance(group.get("topics"), dict) else {}
    topic = topics.get(str(thread_id)) if isinstance(topics, dict) else None
    owner = topic.get("owner") if isinstance(topic, dict) else group.get("defaultOwner")
    owner = str(owner or registry.get("defaultAuthorizedOwner") or fallback)
    return owner if owner in VALID_OWNERS else "josh2"


def owner_accepts(owner: str, chat_id: Any, thread_id: Any, *, direct: bool = False) -> bool:
    if direct:
        return owner in {"josh2", "jaimes"}
    return topic_owner(chat_id, thread_id) == owner


def topics_for_owner(owner: str, chat_id: Any) -> set[str]:
    registry = load_registry()
    groups = registry.get("groups") if isinstance(registry.get("groups"), dict) else {}
    group = groups.get(str(chat_id)) if isinstance(groups, dict) else None
    topics = group.get("topics") if isinstance(group, dict) and isinstance(group.get("topics"), dict) else {}
    return {
        str(topic_id)
        for topic_id, row in topics.items()
        if isinstance(row, dict) and str(row.get("owner") or "") == owner
    }
