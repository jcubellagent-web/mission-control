#!/usr/bin/env python3
"""Append Josh 2.0 Telegram command-center instructions if missing."""

from pathlib import Path

BLOCK = """
## Telegram Command Center UX

- Keep AGENTS lean. Full policy lives in `mission-control/docs/josh2-telegram-ux-audit.md` and `mission-control/data/josh2-telegram-ux-config.json`.
- Telegram replies must use compact bullet-card style: one model/auth line, blank line, then short bullets for Route, Objective, Status, Now, Done, Blocker, and Next.
- Prefer inline buttons for safe next steps, routing, Mission Control checks, and Hold.
- Use `scripts/josh_work_card.py` by default for most requested tasks with more than one step, especially anything over about 60 seconds or anything that changes Mission Control/agent state.
""".strip()


def main() -> int:
    path = Path.home() / ".openclaw" / "workspace" / "AGENTS.md"
    text = path.read_text(encoding="utf-8")
    if "## Telegram Command Center UX" in text:
        print("already_present")
        return 0
    path.write_text(text.rstrip() + "\n\n" + BLOCK + "\n", encoding="utf-8")
    print("updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
