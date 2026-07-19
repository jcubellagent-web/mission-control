# Ecosystem Live-Card Rendering Standard

Josh-facing Telegram surfaces share one semantic contract with two renderers:
native rich blocks are primary for the Inbox, and fixed-width code blocks are
the compatibility fallback and the current renderer for legacy surfaces.

## Native Inbox contract

- Visible sequence: eyes reaction, one editable native-rich live card, one proportional rich final
- Live card: native heading, objective, verified model/owner, compact progress block, current-phase callout, six-stage checklist, active work, collapsed recent activity, and timing footer
- Final: bold outcome, verified model/route line, completion callout, bold section labels, and concise bullets
- Do not wrap the entire native live card or normal final in one `<pre>` block

## Fixed-width fallback geometry

- Container: Telegram HTML `<pre>` for fallback live cards and fallback final summaries
- Server-side wrap width: 38 columns
- Emoji/check continuation indent: 3 ASCII spaces
- Plain `- ` continuation indent: 2 ASCII spaces
- Numbered continuation indent: length of the numeric prefix
- Fallback final summaries use the same fixed-width `<pre>` geometry
- Compaction, replayed history, and framework continuation markers reuse the existing origin-scoped card; they never create an objective or another card

Do not use proportional-text spacing, `&nbsp;`, or Unicode nonbreaking spaces to
simulate alignment inside a fallback. Telegram code blocks use fixed-width
cells, so values that look correct in proportional text do not render one-to-one.

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
- Render the native Inbox card and assert it begins with `<h3>`, contains six checklist inputs, a details block, and a footer
- Render a representative fallback emoji row and plain bullet
- Confirm no fallback row exceeds 38 code points after HTML unescaping
- Confirm the normal final uses proportional rich HTML and the fallback remains valid
- Confirm the live Bot API edit succeeds in the originating chat/topic
