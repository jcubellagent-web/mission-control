#!/usr/bin/env python3
"""Replace verbose Josh Telegram AGENTS block with the compact pointer."""

from pathlib import Path

START = "## Telegram Command Center UX"
LEAN_BLOCK = """
## Telegram Command Center UX

- Keep AGENTS lean. Full policy lives in `mission-control/docs/josh2-telegram-ux-audit.md` and `mission-control/data/josh2-telegram-ux-config.json`.
- Telegram replies must use compact bullet-card style: one model/auth line, blank line, then short bullets for Route, Objective, Status, Now, Done, Blocker, and Next.
- Prefer inline buttons for safe next steps, routing, Mission Control checks, and Hold.
- Use `scripts/josh_work_card.py` by default for most requested tasks with more than one step, especially anything over about 60 seconds or anything that changes Mission Control/agent state.
""".strip()


def main() -> int:
    path = Path.home() / ".openclaw" / "workspace" / "AGENTS.md"
    text = path.read_text(encoding="utf-8")
    if START not in text:
        path.write_text(text.rstrip() + "\n\n" + LEAN_BLOCK + "\n", encoding="utf-8")
        print("appended_lean_block")
        return 0

    before, rest = text.split(START, 1)
    lines = rest.splitlines()
    end_index = len(lines)
    for idx, line in enumerate(lines[1:], start=1):
        if line.startswith("## "):
            end_index = idx
            break
    after = "\n".join(lines[end_index:]).lstrip("\n")
    new_text = before.rstrip() + "\n\n" + LEAN_BLOCK + "\n"
    if after:
        new_text += "\n" + after.rstrip() + "\n"
    path.write_text(new_text, encoding="utf-8")
    print("replaced_with_lean_block")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
