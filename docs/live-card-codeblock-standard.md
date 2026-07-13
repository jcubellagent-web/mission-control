# Ecosystem Live-Card Code-Block Standard

All Josh-facing live work cards use one fixed-width Telegram rendering contract.

## Required geometry

- Container: Telegram HTML `<pre>`
- Server-side wrap width: 38 columns
- Emoji/check continuation indent: 3 ASCII spaces
- Plain `- ` continuation indent: 2 ASCII spaces
- Numbered continuation indent: length of the numeric prefix
- Final summaries remain normal Telegram bubbles

Do not use proportional-text spacing, `&nbsp;`, or Unicode nonbreaking spaces to simulate alignment. Telegram code blocks use fixed-width cells, so values that look correct in proportional text do not render one-to-one.

## Implementations

- JAIMES/Hermes: `scripts/jaimes_work_card.py`
- JOSH 2.0/OpenClaw: `scripts/josh_work_card.py`
- J.A.I.N: consumes the shared contract in `AGENTS.md` and the canonical skill
- JOSHeX: consumes the checked-in `AGENTS.md` and `agent-skills/telegram-task-flow`

Host-local mirrors must be copied from the canonical scripts after each verified change; they must not independently drift.

## Verification

- `tests/test_jaimes_work_card_single_message.py`
- `tests/test_josh_work_card_codeblock.py`
- Compile both renderers
- Render a representative emoji row and plain bullet
- Confirm no visible row exceeds 38 code points after HTML unescaping
- Confirm the live Bot API edit succeeds in the originating chat/topic
