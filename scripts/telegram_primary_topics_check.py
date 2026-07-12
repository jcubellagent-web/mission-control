#!/usr/bin/env python3
"""Validate the primary Telegram topic, response, and memory contracts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


GROUP_ID = "-1003589561528"
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
    check(topics.get("1", {}).get("requireMention") is False, "Inbox must accept untagged requests", problems)
    check(topics.get("17", {}).get("requireMention") is True, "JAIMES Ops must remain mention-gated for Josh 2.0", problems)
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
    telegram = config.get("telegram", {})
    prompt = telegram.get("channel_prompts", {}).get(GROUP_ID, "")
    free_topics = {str(item) for item in telegram.get("free_response_topics", [])}
    memory = (home / ".hermes/memories/MEMORY.md").read_text()
    edit_skill = home / ".openclaw/workspace/mission-control/agent-skills/shared-edit-coordination/SKILL.md"
    preflight = home / ".openclaw/workspace/mission-control/scripts/ecosystem_edit_preflight.py"

    allowed_groups = telegram.get("group_allowed_chats", telegram.get("allowed_group_ids", []))
    check(GROUP_ID in {str(item) for item in allowed_groups}, "JAIMES group allowlist missing", problems)
    check("17" in free_topics, "JAIMES Ops must accept untagged requests", problems)
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
