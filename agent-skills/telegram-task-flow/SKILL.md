---
name: telegram-task-flow
description: Use for Josh 2.0 or JAIMES Telegram task handling, immediate acknowledgements, objective cards, live work cards, final summaries, inline buttons, routing decisions, and iPhone-friendly agent interaction UX.
---

# Telegram Task Flow

Use this whenever a Telegram-facing task needs to feel clear, live, and low-noise.

## Message Contract

1. Acknowledge an owned Telegram task immediately with an eyes reaction. A runtime may use one short editable acknowledgement when reactions are unavailable, but it must not leave a duplicate acknowledgement bubble.
2. Resolve the objective to a short task summary. Do not repeat the user's full prompt or expose media/file identifiers as the objective.
3. Start one editable live work card only after the objective is known. Context compaction, session rollover, replayed history, and framework continuation markers are not new tasks: never send a new objective/acknowledgement or card. Resume by editing the existing origin-scoped card, append work cumulatively, close it at 100%, then send only the final summary.
4. Keep the live work card concise and plain-English. Render it inside a Telegram HTML `<pre>` block and pre-wrap for mobile at 38 fixed-width columns: emoji/check rows use a three-space hanging indent on every continuation line, while plain `- ` bullets use two spaces. These code-block values are intentional; proportional-text spacing is not visually equivalent in Telegram. Do not expose raw shell commands, file paths, JSON flags, or tool internals unless needed to explain a blocker.
5. Append useful progress lines as work happens instead of replacing the whole story. Preserve the same card across compaction and restart recovery. If the log gets too long, consolidate older finished checks into one short "Earlier:" line and keep the latest readable steps visible.
6. Do not show routing buttons by default. Auto-route unless the user truly needs to steer.
7. End with the agreed final summary template.
8. Show buttons only for concrete approval or mitigation steps from the final summary.

## Final Summary Template

Render the entire final summary inside a Telegram HTML `<pre>` block using the same 38-column fixed-width geometry as live cards. Use plain-text labels inside the block: no Markdown bold, headings, or other emphasis. Pre-wrap every dynamic line server-side; plain `- ` bullets use two-space continuation indents and other wrapped rows use three spaces.

- `Complete:` Yes or No plus whether the objective was completed.
- `What was done:` 3-5 tight bullets.
- `Issues:` bullets, or `n/a`.
- `Appropriate next steps:` useful next action, or `No action needed.`
- `Approval needed:` one approval bullet per issue when needed, or `n/a`.

The first line of a final summary must be:

`Model: <verified provider/model> | Route: <actual lane> | Why: <short verified reason>`

Never claim a model switch from policy or intent. Report only the runtime route that actually handled the work.

## Primary Group Topics

The primary Telegram group is `J.A.I.N Control Center` (`-1003589561528`).

- Topic `1`, Inbox: Josh 2.0 owns untagged front-door requests.
- Topic `17`, JAIMES Ops: JAIMES owns untagged backend, cron, SSH, repair, and heavy-execution requests.
- A direct mention overrides normal ownership.
- The non-owner observes silently unless mentioned or explicitly delegated work.
- Replies and editable cards stay in the originating topic.
- Never let both bots answer the same untagged request.
- Both primary topics use the same delivery tiers: instant answer, short answer, or one editable live card plus one final summary.
- Runtime-specific mechanics may differ, but visible labels, model disclosure, summary order, approval buttons, and low-noise behavior must remain consistent.

## Model Disclosure

Always show the model route plainly: Gemini when sufficient and safe, Codex/OpenCLAW for execution or private/device work, Grok/xAI for X-native/current-events work.

## Brain Feed

Publish under the Telegram recipient, not the helper. Josh 2.0 chat uses `--agent josh2`; JAIMES chat uses `--agent jaimes`.

Publishing is mandatory:

- Publish when the objective is determined.
- Refresh Brain Feed on meaningful phase changes or longer-task heartbeats.
- Publish done, blocked, or error before the final summary.
- Do not suppress Brain Feed in live Telegram work. `--no-brain-feed` is only for dry-runs, render tests, or explicit maintenance overrides.
- `/new` must reload this Brain Feed contract. The runtime wrapper should publish a dashboard-safe session-ready state, clear stale live-card tracking from the previous session, and continue publishing objective/progress/completion under the receiving agent.

## Shared Memory

Before asking Josh for context another agent may already know, repeating ecosystem work, or accepting a handoff, retrieve from the governed shared memory registry using the host-specific wrapper in the `shared-memory-retrieval` skill.

- Keep the returned `retrievalId` when memory materially influences the task.
- After a meaningful result, record `helpful`, `ignored`, `harmful`, or `corrected` outcome feedback.
- A correction creates a governed candidate; it never silently overwrites durable memory or policy.
- Do not expose raw private prompt content in feedback reasons or Control Tower status.
