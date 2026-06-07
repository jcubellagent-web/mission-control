# Mission Control Agent Instructions

An unqualified reference to "Mission Control" means the current Josh 2.0 React kiosk.

- Current UI source: `v2-react/`
- Current live Josh 2.0 URL: `http://127.0.0.1:5174/`
- Current launcher: `scripts/open_mission_control_kiosk.sh`
- Current build: `npm run build`
- Current dev server: `npm run dev`

Legacy surfaces are not the default:

- `index.html` is the legacy static dashboard and rollback/debug surface.
- `v2/index.html` is the older static v2 proof surface.
- `scripts/open_react_v2_kiosk.sh` is retained only as a compatibility alias.

When updating Mission Control, sync and verify the current React kiosk on Josh 2.0 before calling the work complete. Avoid using "v1", "v2", or "React v2" in new operator-facing notes unless the topic is historical migration or rollback.

Keep Mission Control visibility and shared context current. Josh 2.0 live Mission Control is the operational source of truth. Local JSON on JOSHeX is a cache unless refreshed from Josh 2.0.

Maintain local/shared sidecars:

- `data/brain-feed.json`
- `data/personal-codex.json`
- `data/jaimes-brain-feed.json`
- `data/jain-brain-feed.json`
- `data/agent-context-registry.json`
- `data/agent-heartbeats.json`

Maintain the Josh 2.0 local live Brain Feed lane with `scripts/agent_publish.py --brain-feed`:

- JOSHeX/Codex work: `--agent joshex`
- Josh 2.0 work: `--agent josh2`
- JAIMES/Hermes work: `--agent jaimes`
- J.AI.N worker/cron work: `--agent jain`

Supabase is only an optional mirror. Enable it with `MISSION_CONTROL_SUPABASE_BRAIN_FEED=1` when the project is available and healthy; it is not the source of truth for the Josh 2.0 kiosk.

At the start of meaningful work, publish current objective, status, active steps, timestamp, current tool, and short recent activity. During longer work, refresh visibility when the phase changes or after major milestones. Before final response, publish completed, blocked, or error status.

After shared state changes, regenerate dashboard data for sidecars, Today's Jobs, Shared Ledger, decisions, handoffs, task queues, `agent-context-registry.json`, and `agent-heartbeats.json`.

Before an agent picks up work from another agent, check:

- `data/agent-context-registry.json`
- `data/agent-task-queue.json`
- `data/handoff-queue.json`
- `data/shared-events.json`
- `data/agent-chat-sources.json`

Use `scripts/reconcile_agent_context.py` to close stale/superseded task and handoff noise without deleting history.

Use `agent-skills` as the durable source for shared agent skills/runbooks. When these skills change, sync them into each active host's `~/.codex/skills` directory and update `data/agent-skill-inventory.json`.

## JOSHeX Device Resource Guardrail

JOSHeX runs on Josh's personal MacBook. Treat this device as the trusted coordinator and personal-device specialist, not the default execution host for the agent ecosystem.

Prefer Josh 2.0 or JAIMES for real agent-ecosystem work whenever the task does not specifically require this MacBook's local desktop, private browser session, keychain, OAuth state, personal account permissions, or local files that only exist here.

Default ownership:

- JOSHeX: planning, coordination, architecture decisions, lightweight integration, private-Mac-only work, personal account/browser/keychain/OAuth work, and final review when the user is directly engaging from this laptop.
- Josh 2.0: Mission Control kiosk, OpenCLAW operations, device alerts on the Josh 2.0 Mac mini, dashboard refreshes, service checks, browser automation that should not disturb the MacBook, and ecosystem host maintenance.
- JAIMES/J.AI.N: heavier compute, long-running work, scheduled jobs, Gemini/Grok specialist passes, Sorare operations, intelligence scans, durable Hermes workflows, and background processing.

Dedicated-host autonomy:

- Josh 2.0 and JAIMES/J.AI.N run on dedicated Mac minis. Device-local tool approvals should be configured for full access and auto-approval: terminal work, workspace file edits, browser automation on those hosts, service restarts, cron/job maintenance, plugin updates, diagnostics, and local cleanup should not require Josh to approve each step.
- Do not ask for user approval solely because a task needs to use the dedicated host's shell, filesystem, browser, local app controls, or service manager. Execute on the proper dedicated host and keep Brain Feed current.
- Keep explicit approval only for truly external or irreversible actions unless a standing policy already pre-approves that workflow: purchases, public posts/messages, account deletion, destructive private-account changes, production pushes, or fantasy/Sorare roster or lineup changes outside the accepted standing policy.
- Onchain and wallet actions are always proposal-first. Base MCP, Base Account, wallet, swap, trade, bridge, mint, stake, claim, revoke, approval, transfer, or portfolio-management work may be researched, simulated, and drafted by agents, but signing/broadcasting/submitting requires Josh's explicit wallet/Base Account approval. Never store private keys, seed phrases, raw wallet secrets, raw calldata, cookies, OAuth payloads, or tokens in Mission Control surfaces.

Standing Josh 2.0 delegation:

- When Josh asks JOSHeX, Josh 2.0, or JAIMES to do something on Josh 2.0, treat that as standing permission to use Josh 2.0's local tools and grant routine local access on Josh's behalf whenever the platform permits it.
- Routine local access includes opening and controlling Chrome, using Computer Use, interacting with local setup dialogs, starting or restarting OpenCLAW/Codex/gateway services, editing workspace files, updating plugins, running diagnostics, clearing local alerts, changing local Mission Control/kiosk settings, and approving local-only tool prompts.
- Do not bounce routine Josh 2.0 device work back to Josh for manual clicks. If macOS requires one-time Privacy & Security consent, tell Josh the exact pane and item to enable; after consent is granted, continue without re-asking.
- Keep the hard human boundary at identity, money, public commitment, and irreversible external state: passkeys, 2FA, account sign-ins, wallet/Base Account signing, purchases, public posting, external account deletion, and live roster/lineup submission outside standing policy still require explicit Josh approval at the moment of action.
- If a requested action is blocked only because Josh 2.0 lacks Accessibility, Screen Recording, Input Monitoring, Automation, Full Disk Access, Chrome extension, or Computer Use permission, surface that exact missing permission and continue immediately once it is granted.

When work starts from JOSHeX but belongs on a dedicated host, create a visible handoff/task instead of running the heavy work locally. Keep JOSHeX available and efficient for Josh's personal day-to-day work.

Do not add noisy Action Required items for normal progress. Only use Action Required for something Josh actually needs to approve or fix.

Never put secrets, private account contents, tokens, raw sensitive connector data, OAuth payloads, raw emails, cookies, passwords, or private customer/account content into Brain Feed, Personal Codex, dashboard-data.json, shared-events.json, codex-jobs.json, decisions.json, handoff docs, agent-context-registry.json, agent-chat-sources.json, or optional Supabase mirror rows.

For cross-agent requests, use `scripts/agent_delegate.py` so Mission Control shows both sides of the handoff:

- JOSHeX tile: "Requesting <agent>: <task>"
- Receiving agent tile: "Instruction received: <task>"
- Execution tile updates: receiving agent must use `scripts/agent_task.py start/complete --brain-feed --job` or `scripts/agent_job_wrap.sh <agent> ...`

Do not delegate with only chat text or an untracked SSH command when the request should be visible in Brain Feed.

## Brain Feed Publish Contract

Josh 2.0 and JAIMES must publish meaningful work to Brain Feed. This is not optional for Telegram tasks, delegated tasks, scheduled jobs with user-visible impact, Mission Control changes, or ecosystem maintenance.

Required publishing cadence:

- Start: publish objective, owner, status, model/tool route, and first step under the agent that received or owns the task.
- During work: publish when the phase changes, when a blocker appears, or when a longer task needs a heartbeat.
- Completion: publish done, blocked, or error before the final user-facing summary.

Ownership rule:

- Work received in Josh 2.0 Telegram publishes as `--agent josh2`, even if another helper contributes.
- Work received in JAIMES Telegram or Hermes publishes as `--agent jaimes`, even if Gemini, Codex, or J.A.I.N contributes.
- J.A.I.N worker/cron work publishes as `--agent jain`.
- JOSHeX coordination/private-Mac work publishes as `--agent joshex`.

Do not suppress Brain Feed publishing in live work. `--no-brain-feed` is only acceptable for dry-runs, local render tests, or an explicit maintenance override.

Session reset rule:

- `/new` must not clear the Brain Feed publish contract. Telegram intake/watchers must reload that contract for the new session, publish a dashboard-safe "session ready" state under the receiving agent, close stale live-card state from the previous session, and continue publishing objective/progress/completion for the next task.
- Do not rely on model conversation memory to remember Brain Feed publishing after `/new`; the runtime wrapper must enforce it.

## Cookie And Keychain Disambiguation

When the user mentions a visible alert for `cookie.codex`, "Codex cookies",
"Keychain Not Found", or "A keychain cannot be found to store cookie.codex",
treat it as a macOS/Codex keychain alert on the device. Inspect visible
`SecurityAgent` windows, stale `openclaw models auth login` processes, Codex
auth health, and default keychain state. Do not route this to Sorare cookie
freshness or Sorare auth refresh unless the user explicitly says Sorare.

When the user says "Sorare cookie", treat that as Sorare auth/cookie freshness.
Keep the two paths separate in Brain Feed, work cards, and final reports.

## Telegram Completion UX

For Josh 2.0 and JAIMES Telegram tasks:

- Immediate acknowledgement must be exactly short: `recieved, determining objective`.
- The fast-ack watcher owns that acknowledgement. The model/agent must not output only that acknowledgement or stop after it; it must continue to execute the objective and provide a real result.
- As soon as the objective is known, edit that acknowledgement to `Objective: <objective>`.
- Objectives must be specific enough for Josh to understand the exact work at a glance: include the target system, the concrete change/check, and the intended outcome. Do not use vague placeholders such as "Sync agent ecosystem state" when the user asked for a specific bug fix, audit, cleanup, or UX change.
- Do not create a work card until the objective is known. Once known, immediately start the editable work card with the real objective. Keep it simple: show Objective, Current step, Done so far, Issues, Next steps, Status, Route, Using, and Updated. Do not duplicate Current step or Next steps inside the done log.
- If no new tool/model event is visible for a longer-running task, update the card with a short "still working" heartbeat instead of letting the card look frozen.
- Publish Brain Feed under the agent that received the Telegram task. If the task was in Josh 2.0 Telegram, publish as `--agent josh2`; if it was in JAIMES Telegram, publish as `--agent jaimes`.
- Do not show routing/model buttons by default. Only show routing buttons when it is useful for Josh to steer the objective toward a specific model or agent.
- Do not send the final Telegram template until all local/tool work is complete, or until there is a blocker that needs Josh's attention or approval. After sending the final template, do not keep running follow-up cleanup that can generate more Telegram cards; finish cleanup first, then send the final.
- The final Telegram message must be a separate catch-up summary after the card, using this exact structure with bold headers:
  - `Complete:` then `Yes` or `No` plus the specific objective in plain language.
  - `What was done:` with 3-5 tight user-facing bullets that explain the outcome and verification, not internal implementation trivia.
  - `Issues:` with issue bullets, or `n/a`.
  - `Appropriate next steps:` with the next useful action, or `No action needed.`
  - `Approval needed:` with one approval bullet per issue when approval is needed, or `n/a`.
- After the final message, show buttons only if they map directly to real mitigation/approval steps in `Approval needed:`. Never create approval buttons for `n/a`, `Context`, status metadata, or routine no-action summaries.
- Do not end a non-trivial Telegram task with a freeform paragraph when a work card was used.

## Shared Tooling Preferences

- Use the OpenAI developer documentation MCP server for current OpenAI API, ChatGPT Apps SDK, Codex, Responses API, or related product documentation questions.
- Use Playwright MCP for repeatable browser automation, page inspection, screenshots, and web UI verification when a structured browser path is safer than visual/manual control.
- Use `gog` for dashboard-safe Google Workspace automation involving the shared agent inbox, calendar, Drive, Docs, Sheets, Slides, Contacts, or Tasks. Prefer `--json`, `--no-input`, and `--gmail-no-send` unless sending mail is explicitly approved.
- Use 1Password CLI (`op`) only as a secret retrieval/storage mechanism after the relevant vault/account is manually signed in or otherwise intentionally configured. Do not publish vault item contents or secret values to Mission Control.
- Route repo-safe, non-private JOSHeX handoffs through the Codex Cloud handoff path when local JOSHeX is unavailable; keep local-only tasks on JOSHeX when they involve private accounts, browser sessions, keychains, OAuth, cookies, secrets, or local desktop state.
