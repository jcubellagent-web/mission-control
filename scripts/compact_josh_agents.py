#!/usr/bin/env python3
"""Compact Josh 2.0 AGENTS.md while preserving hard operating rules."""

from pathlib import Path

SECTIONS = {
    "## Session Startup": """## Session Startup

- Use injected Project Context first; do not reread bootstrap files unless needed.
- Re-anchor on mandatory Reply Format and Codex/OpenAI subscription-first routing.
- Resume from `memory/tasks.md` plus today's relevant daily-note tail.
- Read MEMORY.md and older notes only by narrow snippet when the task requires it.
- Skip heavy media/wrapper content unless relevant.
- Batch independent reads. Don't ask permission for routine safe work.
""",
    "## Reply Format": """## Reply Format

- Josh-facing replies must use the structured MEMORY.md format.
- First visible line must show exact Active Model/Auth route.
- Prefer compact bullet-card style on Telegram: one model/auth line, blank line, then short bullets.
- Include route reason when model/agent choice is not obvious; announce route switches.
- Use inline buttons when useful. Fast path: `python3 scripts/send_josh_reply.py --message "..." --buttons '[...]'`.
- If sending externally, return `NO_REPLY`; never double-send.
""",
    "## Model Routing": """## Model Routing

- Classify objective at start. Full tier table lives in MEMORY.md and `config/codex-first-operating-policy-2026-04-13.md`.
- Default: Codex/OpenAI subscription-first for compatible Josh chat and execution; `openai/gpt-5.4` remains API fallback/judge.
- Gemini = dashboard-safe long-context research/review/digest. Grok = live/freshness. Anthropic = manual-only when Josh asks.
- OpenRouter utility lanes are optional overflow/specialist lanes, not source-of-truth defaults.
- Re-audit routing only after OpenClaw/auth/model/provider/billing behavior changes.
""",
}


def replace_section(text: str, heading: str, replacement: str) -> str:
    if heading not in text:
        return text.rstrip() + "\n\n" + replacement.strip() + "\n"
    before, rest = text.split(heading, 1)
    lines = rest.splitlines()
    end_index = len(lines)
    for idx, line in enumerate(lines[1:], start=1):
        if line.startswith("## ") or line.startswith("<!--"):
            end_index = idx
            break
    after = "\n".join(lines[end_index:]).lstrip("\n")
    new_text = before.rstrip() + "\n\n" + replacement.strip() + "\n"
    if after:
        new_text += "\n" + after.rstrip() + "\n"
    return new_text


def main() -> int:
    path = Path.home() / ".openclaw" / "workspace" / "AGENTS.md"
    text = path.read_text(encoding="utf-8")
    for heading, replacement in SECTIONS.items():
        text = replace_section(text, heading, replacement)
    path.write_text(text, encoding="utf-8")
    print(len(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
