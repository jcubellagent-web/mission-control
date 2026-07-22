#!/usr/bin/env python3
"""Validate the primary Telegram topic, response, and memory contracts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib


GROUP_ID = "-1003589561528"
#JAIMES: validate every visible Control Center topic and every supported sub-agent model family.
TOPIC_OWNERS = {
    "1": "josh2",
    "17": "jaimes",
    "18": "josh2",
    "19": "jaimes",
    "20": "jaimes",
    "21": "josh2",
    "22": "josh2",
    "56": "jaimes",
}
REQUIRED_MODEL_FAMILIES = {"gpt", "gemini", "ollama", "grok"}
REQUIRED_FINAL_LABELS = (
    "Model:",
    "Complete:",
    "What was done:",
    "Issues:",
    "Appropriate next steps:",
    "Approval needed:",
)


def check(condition: bool, name: str, problems: list[str]) -> None:
    if not condition:
        problems.append(name)


def load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise SystemExit("PyYAML is required for the JAIMES contract check") from exc
    return yaml.safe_load(path.read_text()) or {}


def subagent_model_families(defaults: dict) -> set[str]:
    route = defaults.get("subagents", {}).get("model", {})
    models = [route.get("primary"), *(route.get("fallbacks") or [])]
    families: set[str] = set()
    for value in models:
        model = str(value or "").lower()
        if model.startswith("openai/gpt-"):
            families.add("gpt")
        if model.startswith(("google-gemini-cli/", "google/gemini", "gemini/")):
            families.add("gemini")
        if model.startswith("ollama/"):
            families.add("ollama")
        if model.startswith("xai/grok"):
            families.add("grok")
    return families


def check_josh2(home: Path) -> list[str]:
    problems: list[str] = []
    config = json.loads((home / ".openclaw/openclaw.json").read_text())
    telegram = config.get("channels", {}).get("telegram", {})
    group = telegram.get("groups", {}).get(GROUP_ID, {})
    topics = group.get("topics", {})
    agents = (home / ".openclaw/workspace/AGENTS.md").read_text()
    skill = (home / ".openclaw/workspace/mission-control/agent-skills/telegram-task-flow/SKILL.md").read_text()
    edit_skill = home / ".openclaw/workspace/mission-control/agent-skills/shared-edit-coordination/SKILL.md"
    preflight = home / ".openclaw/workspace/mission-control/scripts/ecosystem_edit_preflight.py"

    check(bool(group), "Josh 2.0 group config missing", problems)
    # The gateway/fast-ack lifecycle is the only progress/final writer. Pin
    # upstream OpenClaw surfaces off so a future default change cannot create
    # an extra preview, rich final, or implicit quote reply beside that card.
    streaming = telegram.get("streaming", {})
    check(streaming.get("mode") == "off", "OpenClaw Telegram streaming must stay off", problems)
    check(
        streaming.get("preview", {}).get("toolProgress") is False,
        "OpenClaw Telegram tool progress must stay off",
        problems,
    )
    check(telegram.get("richMessages") is False, "OpenClaw native rich messages must stay off", problems)
    check(telegram.get("replyToMode") == "off", "OpenClaw implicit reply quoting must stay off", problems)
    check(
        config.get("messages", {}).get("ackReactionScope") == "off",
        "OpenClaw built-in acknowledgement reactions must stay off",
        problems,
    )
    for topic_id, owner in TOPIC_OWNERS.items():
        topic = topics.get(topic_id, {})
        check(topic.get("enabled") is True, f"Topic {topic_id} must be enabled for Josh 2.0", problems)
        check(topic.get("ingest") is True, f"Topic {topic_id} must be ingested by Josh 2.0", problems)
        check(
            topic.get("requireMention") is (owner != "josh2"),
            f"Topic {topic_id} Josh 2.0 mention gate does not match ownership",
            problems,
        )
    missing_families = REQUIRED_MODEL_FAMILIES - subagent_model_families(config.get("agents", {}).get("defaults", {}))
    check(not missing_families, f"Josh 2.0 sub-agent routes missing: {', '.join(sorted(missing_families))}", problems)
    check("Topic 1 Inbox: JOSH 2.0" in agents, "Inbox ownership missing from AGENTS.md", problems)
    check("feedback" in agents and "retrievalId" in agents, "Josh 2.0 memory outcome feedback missing", problems)
    check("shared-edit-coordination" in agents, "Josh 2.0 shared-edit bootstrap missing", problems)
    check(edit_skill.exists(), "shared-edit coordination skill missing", problems)
    check(preflight.exists(), "ecosystem edit preflight missing", problems)
    for label in REQUIRED_FINAL_LABELS:
        check(label in skill, f"shared Telegram skill missing {label}", problems)
    return problems


def check_jaimes(home: Path) -> list[str]:
    problems: list[str] = []
    config = load_yaml(home / ".hermes/config.yaml")
    openclaw = json.loads((home / ".openclaw/openclaw.json").read_text())
    telegram = config.get("telegram", {})
    prompt = telegram.get("channel_prompts", {}).get(GROUP_ID, "")
    free_topics = {str(item) for item in telegram.get("free_response_topics", [])}
    memory = (home / ".hermes/memories/MEMORY.md").read_text()
    edit_skill = home / ".openclaw/workspace/mission-control/agent-skills/shared-edit-coordination/SKILL.md"
    preflight = home / ".openclaw/workspace/mission-control/scripts/ecosystem_edit_preflight.py"
    gateway_plist = home / "Library/LaunchAgents/ai.hermes.gateway.plist"

    allowed_groups = telegram.get("group_allowed_chats", telegram.get("allowed_group_ids", []))
    check(GROUP_ID in {str(item) for item in allowed_groups}, "JAIMES group allowlist missing", problems)
    expected_free = {topic for topic, owner in TOPIC_OWNERS.items() if owner == "jaimes"}
    check(free_topics == expected_free, "JAIMES free-response topics do not exactly match ownership", problems)
    # Hermes 0.19 adds native draft/rich transports. They remain deliberately
    # disabled in managed topics: the custom gateway owns one editable card and
    # one final while Hermes keeps important-only notifications and reactions.
    streaming = config.get("gateway", {}).get("streaming", {})
    extra = telegram.get("extra", {})
    notifications = config.get("display", {}).get("platforms", {}).get("telegram", {})
    check(streaming.get("enabled") is False, "Hermes Telegram streaming must stay disabled", problems)
    check(streaming.get("transport") == "auto", "Hermes Telegram transport fallback drifted", problems)
    check(extra.get("rich_messages") is False, "Hermes native rich messages must stay off", problems)
    check(extra.get("rich_drafts") is False, "Hermes native rich drafts must stay off", problems)
    check(telegram.get("reactions") is True, "Hermes processing reactions must stay enabled", problems)
    check(telegram.get("exclusive_bot_mentions") is True, "Hermes exclusive bot mentions must stay enabled", problems)
    check(telegram.get("require_mention") is True, "Hermes group mention gate must stay enabled", problems)
    check(
        telegram.get("observe_unmentioned_group_messages") is True,
        "Hermes unmentioned context observation must stay enabled",
        problems,
    )
    check(notifications.get("notifications") == "important", "Hermes Telegram notifications must stay important-only", problems)
    try:
        gateway_args = plistlib.loads(gateway_plist.read_bytes()).get("ProgramArguments", [])
    except (OSError, plistlib.InvalidFileException):
        gateway_args = []
    check(
        gateway_args[:3]
        == [
            str(home / ".openclaw/workspace/scripts/op_agent_env.sh"),
            str(home / ".openclaw/workspace/config/agent-ecosystem-hermes.op.env"),
            "--",
        ],
        "Hermes gateway must resolve managed credentials before launch",
        problems,
    )
    missing_families = REQUIRED_MODEL_FAMILIES - subagent_model_families(openclaw.get("agents", {}).get("defaults", {}))
    check(not missing_families, f"JAIMES sub-agent routes missing: {', '.join(sorted(missing_families))}", problems)
    for topic_id, owner in TOPIC_OWNERS.items():
        owner_label = "JAIMES" if owner == "jaimes" else "Josh 2.0"
        check(f"Topic {topic_id}" in prompt and owner_label in prompt, f"JAIMES prompt missing Topic {topic_id} ownership", problems)
    check("Topic 17" in prompt and "JAIMES" in prompt, "JAIMES Ops ownership missing from channel prompt", problems)
    check("Topic 1" in prompt and "Josh 2.0" in prompt, "Inbox ownership missing from channel prompt", problems)
    check("retrievalId" in prompt and "feedback" in prompt, "JAIMES channel prompt lacks memory outcome feedback", problems)
    check("feedback" in memory and "retrievalId" in memory, "JAIMES durable memory feedback rule missing", problems)
    check("shared-edit-coordination" in prompt and "ecosystem_edit_preflight" in prompt, "JAIMES shared-edit preflight missing", problems)
    check(edit_skill.exists(), "shared-edit coordination skill missing", problems)
    check(preflight.exists(), "ecosystem edit preflight missing", problems)
    for label in REQUIRED_FINAL_LABELS:
        check(label in prompt, f"JAIMES channel prompt missing {label}", problems)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("josh2", "jaimes"), required=True)
    parser.add_argument("--home", default=os.environ.get("HOME", "~"))
    args = parser.parse_args()
    home = Path(args.home).expanduser()
    problems = check_josh2(home) if args.role == "josh2" else check_jaimes(home)
    result = {"role": args.role, "ok": not problems, "problems": problems}
    print(json.dumps(result, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
