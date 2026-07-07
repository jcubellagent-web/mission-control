#!/usr/bin/env python3
"""Validate J.A.I.N Control Center topic metadata without calling Telegram."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REQUIRED_TOPICS = {
    "Inbox",
    "JAIMES Ops",
    "JOSH 2.0",
    "Sorare",
    "Crypto Alerts",
    "Approvals",
    "Mission Control",
    "News",
}


def load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text())


def main() -> int:
    topic_map_path = Path.home() / ".hermes/state/jain_control_center_topics.json"
    pin_manifest_path = Path.home() / ".hermes/state/jain_control_center_pinned_protocol.json"

    issues: list[str] = []

    try:
        topic_state = load_json(topic_map_path)
    except Exception as exc:  # dashboard-safe: path only, no secrets
        print(json.dumps({"ok": False, "issues": [f"topic map unreadable: {exc}"]}, indent=2))
        return 1

    topics = topic_state.get("topics") or {}
    missing = sorted(REQUIRED_TOPICS - set(topics))
    if missing:
        issues.append(f"missing topics: {', '.join(missing)}")

    chat_id = str(topic_state.get("chat_id") or "")
    if not chat_id.startswith("-100"):
        issues.append("chat_id does not look like a Telegram supergroup id")

    delivery_targets = topic_state.get("delivery_targets") or {}
    old_targets = [
        key
        for key, value in delivery_targets.items()
        if isinstance(value, str) and value.startswith("telegram:-") and not value.startswith(f"telegram:{chat_id}")
    ]
    if old_targets:
        issues.append(f"delivery targets may point outside Control Center: {', '.join(sorted(old_targets))}")

    pins = {}
    if pin_manifest_path.exists():
        try:
            pins = load_json(pin_manifest_path).get("pins") or {}
        except Exception as exc:
            issues.append(f"pin manifest unreadable: {exc}")
    else:
        issues.append("pin manifest missing")

    missing_pins = sorted(REQUIRED_TOPICS - set(pins))
    if missing_pins:
        issues.append(f"missing pinned protocol entries: {', '.join(missing_pins)}")

    result = {
        "ok": not issues,
        "chat": topic_state.get("title") or "J.A.I.N Control Center",
        "chat_id": chat_id,
        "topics": len(topics),
        "pins": len(pins),
        "issues": issues,
    }
    print(json.dumps(result, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
