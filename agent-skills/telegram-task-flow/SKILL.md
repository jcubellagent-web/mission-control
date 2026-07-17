---
name: telegram-task-flow
description: Use for Josh 2.0 or JAIMES Telegram task handling, immediate acknowledgements, objective cards, live work cards, final summaries, inline buttons, routing decisions, and iPhone-friendly agent interaction UX.
---

# Telegram Task Flow

Use this whenever a Telegram-facing task needs to feel clear, live, and low-noise.

## Message Contract

1. Acknowledge an owned Telegram task immediately with an eyes reaction. A runtime may use one short editable acknowledgement when reactions are unavailable, but it must not leave a duplicate acknowledgement bubble.
2. Resolve the objective from the current user request only. First identify the intent, concrete target, and desired outcome; then write a short operator objective in the agent's own words. Do not repeat the user's full prompt, lightly trim its courtesy words, or expose media/file identifiers. Treat quoted prior objectives, pasted cards, screenshots, compaction summaries, and example final templates as evidence—not as the live objective. When correcting an old card, use the correction being requested now as the objective. If the fast intake path cannot produce a genuine interpretation, keep only the acknowledgement reaction and wait for the main agent to determine the objective; do not publish a placeholder or prompt copy to Telegram or Control Tower.
3. After the objective and planned route are known, send one compact immutable task-header receipt inside a 38-column Telegram HTML `<pre>` block. Use a table layout with Objective, Owner, Agent, and Models. The objective must be a short operator summary in the agent's own words, never a pasted or near-copy version of the request. As a mechanical safeguard, do not reuse a six-word span from the current request. Show the selected named agent/sub-agent; when the worker has no formal persona name, label it as a `system`. List every model currently selected in the planned route without claiming an unverified switch.
4. Start one editable live work card immediately after the task header. Context compaction, session rollover, replayed history, and framework continuation markers are not new tasks: never send a new header, objective acknowledgement, or card. Resume by editing the existing origin-scoped live card, append work cumulatively, close it at 100%, then send only the final summary.
5. Keep the live work card concise and plain-English. The Inbox topic may use Telegram Rich Messages for native headings, milestone checklists, collapsible recent activity, and footers. Always keep the fixed-width HTML `<pre>` renderer as the automatic fallback and pre-wrap that fallback for mobile at 38 columns: emoji/check rows use a three-space hanging indent on every continuation line, while plain `- ` bullets use two spaces. Do not expose raw shell commands, file paths, JSON flags, or tool internals unless needed to explain a blocker.
6. Append useful progress lines as work happens instead of replacing the whole story. Preserve the same card across compaction and restart recovery. If the log gets too long, consolidate older finished checks into one short "Earlier:" line and keep the latest readable steps visible.
7. Do not show routing buttons by default. Auto-route unless the user truly needs to steer.
8. End with the agreed final summary template.
9. Show buttons only for concrete approval or mitigation steps from the final summary.

## Inbox Rich Live Cards

For Topic `1`, the visible sequence is: eyes reaction → immutable task header → editable live card → structured final summary. New live cards default to the native Rich Message renderer when Telegram accepts it. Existing cards keep their original renderer for their full lifecycle.

- Keep exactly one immutable task header, one editable live card, and one separate structured final summary.
- Persist the header message ID before sending the live card. If live-card delivery fails, retries must reuse the existing header instead of posting another one.
- Show milestone-derived progress: Accepted, Planned, Routed, Working, Verifying, Delivered. Do not advance progress because duplicate log messages accumulated.
- Show Josh 2.0 as the visible owner and list the verified active worker/model beneath him. Humanize internal worker identifiers before display.
- Show elapsed time and the last rendered update time. Long-running coordinator jobs must continue producing heartbeat edits so the card does not look frozen.
- Put older operational updates inside one collapsed `Recent activity` details block. Do not expose chain-of-thought, raw commands, secrets, private account content, or connector payloads.
- If `sendRichMessage` or a rich edit fails, replace or continue the same message through the fixed-width HTML fallback. Never create a second fallback card.
- `JOSH_TELEGRAM_RICH_CARDS=0` disables the native renderer. An explicit true value enables it outside Topic `1`; otherwise Topic `1` is the only default native-rich surface.
- `JOSH_TELEGRAM_TASK_HEADERS=0` disables the header. An explicit true value enables it outside Topic `1`; otherwise Topic `1` is the only default header surface.

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
- Topic `1` uses the header-first live-work tier defined above. Topic `17` retains its current one-live-card-plus-final tier until its renderer is separately upgraded.
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
