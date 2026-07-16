#JAIMES: Control Tower is local-sidecar first; Supabase/v2 mirror paths are retired and must not be reported as blockers or challenges.

# Control Tower Agent Instructions

An unqualified user-facing reference to the dashboard should say "Control Tower". The legacy repo/path name remains `mission-control`.

- Current UI source: `v2-react/`
- Current live Josh 2.0 URL: `http://127.0.0.1:5174/`
- Current launcher: `scripts/open_mission_control_kiosk.sh`
- Current build: `npm run build`
- Current dev server: `npm run dev`

## Josh 2.0 Display Ownership

The physical Josh 2.0 display belongs to Control Tower whenever no visible
browser, Computer Use, or local-UI work is actively using the Mac mini. The
lightweight `control_tower_foreground.py ensure --repair` LaunchAgent enforces
this with the exact `control-tower-kiosk` Chrome PID; never replace it with a
generic `tell application "Google Chrome" to activate`, because the persistent
agent-auth profile is a different Chrome process.

Before intentionally taking over the Josh 2.0 display, create a short lease:

```bash
python3 scripts/control_tower_foreground.py begin --owner <agent> --purpose browser
# Renew longer visible work before the returned expiry:
python3 scripts/control_tower_foreground.py renew --lease-id <returned-lease-id>
# Always release when visible work ends; release restores Control Tower now:
python3 scripts/control_tower_foreground.py end --lease-id <returned-lease-id>
```

- Valid purposes are `browser`, `computer-use`, and `local-ui`.
- The default lease is three minutes and the hard maximum is ten minutes. Renew
  only while the display is truly in use; never use a task, Brain Feed row,
  persistent browser process, gateway, or background job as a display lease.
- Recent physical input and a locked/protected system session also defer focus
  safely. SSH, daemons, polling, and headless work do not.
- Do not publish the opaque lease id, account pages, or visible private content
  to Control Tower, Brain Feed, handoffs, or shared logs.
- Never kill or mutate the agent-auth Chrome while restoring the kiosk. If
  macOS refuses to switch between the two Chrome processes, the guard may hide
  only the stale foreground process, activates the exact kiosk CDP target, and
  verifies its process PID. Activating agent-auth for later work unhides it.
- Canonical service definitions live in `launchd/`; install or repair them with
  `scripts/install_control_tower_kiosk_guards.sh`.

## Control Tower Change Control

Control Tower source edits require an exclusive change lease. This rule applies to JOSHeX, Josh 2.0, JAIMES, and J.A.I.N.

Before editing canonical source, run:

```bash
python3 scripts/control_tower_change_guard.py begin --agent <agent> --objective "<specific change>"
```

- The command must report a clean canonical source tree and return a lease token.
- Do not edit if another agent owns the lease. Handoff or wait for that lease to finish.
- Edit only `v2-react/` and explicitly named supporting scripts. Never hand-edit `dist/`, legacy `index.html`, or legacy `v2/`.
- Preserve the returned backup path and token for verification or rollback.
- Runtime JSON, logs, and generated build artifacts are not source ownership signals.

Before reporting completion, run:

```bash
python3 scripts/control_tower_change_guard.py finish --token <token>
```

This performs the canonical build, data regeneration, regression checks, and host-local kiosk layout screenshot before releasing the lease. If validation fails, keep the lease, repair the issue, and verify again. Use `abort --token <token>` to restore the pre-edit source backup. Production pushes and merges still require Josh approval unless the task explicitly includes that approval.

Legacy surfaces are not the default:

- `index.html` is the legacy static dashboard and rollback/debug surface.
- `v2/index.html` is the older static v2 proof surface.
- `scripts/open_react_v2_kiosk.sh` is retained only as a compatibility alias.

When updating Control Tower, sync and verify the current React kiosk on Josh 2.0 before calling the work complete. Avoid using "v1", "v2", or "React v2" in new operator-facing notes unless the topic is historical migration or rollback.

Keep Control Tower visibility and shared context current. Josh 2.0 live Control Tower is the operational source of truth. Local JSON on JOSHeX is a cache unless refreshed from Josh 2.0.

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

Supabase/v2 mirror paths are retired for active Control Tower work. Do not pass `--v2`, do not require service-role keys, and do not report missing Supabase credentials as a blocker; the local Brain Feed and JSON sidecars are the source of truth for the Josh 2.0 kiosk.

At the start of meaningful work, publish current objective, status, active steps, timestamp, current tool, and short recent activity. During longer work, refresh visibility when the phase changes or after major milestones. Before final response, publish completed, blocked, or error status.

After shared state changes, regenerate dashboard data for sidecars, Today's Jobs, Shared Ledger, decisions, handoffs, task queues, `agent-context-registry.json`, and `agent-heartbeats.json`.

Before an agent picks up work from another agent, check:

- `data/agent-context-registry.json`
- `data/agent-task-queue.json`
- `data/handoff-queue.json`
- `data/shared-events.json`
- `data/agent-chat-sources.json`

Use `scripts/reconcile_agent_context.py` to close stale/superseded task and handoff noise without deleting history.

## Shared Memory Control Plane

Checked-in `AGENTS.md`, `MEMORY.md`, and `agent-skills` remain authoritative. The shared memory registry adds typed retrieval, provenance, validity, conflict review, and cross-agent continuity; it does not replace those files.

- Before repeating work, asking Josh for known context, or accepting a cross-agent handoff, query shared memory:
  `python3 scripts/memory_registry.py retrieve --agent <agent> --query "<specific question>"`
- Cite the returned source and confidence when memory materially changes a decision.
- Propose durable learning with `memory_registry.py propose`; do not directly convert model inference into policy.
- Preferences, procedures, policy, sensitive facts, and conflicts always require review. Only verified low-risk facts, lessons, entities, and relationships may be auto-promoted by the nightly review.
- Never send raw private prompts or private memory contents to Control Tower. `data/memory-operations.json` contains health, counts, latency, and provenance coverage only.
- Josh 2.0 owns the registry and nightly review. JAIMES, J.A.I.N, and JOSHeX use `scripts/ecosystem_memory_client.py` (or SSH to the same CLI) so all agents share one governed memory surface.

Use `agent-skills` as the durable source for shared agent skills/runbooks. When these skills change, sync them into each active host's `~/.codex/skills` directory and update `data/agent-skill-inventory.json`.

## JOSHeX Device Resource Guardrail

JOSHeX runs on Josh's personal MacBook. Treat this device as the trusted coordinator and personal-device specialist, not the default execution host for the agent ecosystem.

Prefer Josh 2.0 or JAIMES for real agent-ecosystem work whenever the task does not specifically require this MacBook's local desktop, private browser session, keychain, OAuth state, personal account permissions, or local files that only exist here.

Default ownership:

- JOSHeX: planning, coordination, architecture decisions, lightweight integration, private-Mac-only work, personal account/browser/keychain/OAuth work, and final review when the user is directly engaging from this laptop.
- Josh 2.0: Control Tower kiosk, OpenCLAW operations, device alerts on the Josh 2.0 Mac mini, dashboard refreshes, service checks, browser automation that should not disturb the MacBook, and ecosystem host maintenance.
- JAIMES/J.AI.N: heavier compute, long-running work, scheduled jobs, Gemini/Grok specialist passes, Sorare operations, intelligence scans, durable Hermes workflows, and background processing.

Dedicated-host autonomy:

- Josh 2.0 and JAIMES/J.AI.N run on dedicated Mac minis. Device-local tool approvals should be configured for full access and auto-approval: terminal work, workspace file edits, browser automation on those hosts, service restarts, cron/job maintenance, plugin updates, diagnostics, and local cleanup should not require Josh to approve each step.
- Do not ask for user approval solely because a task needs to use the dedicated host's shell, filesystem, browser, local app controls, or service manager. Execute on the proper dedicated host and keep Brain Feed current.
- Keep explicit approval only for truly external or irreversible actions unless a standing policy already pre-approves that workflow: purchases, public posts/messages, account deletion, destructive private-account changes, production pushes, or fantasy/Sorare roster or lineup changes outside the accepted standing policy.
- Onchain and wallet actions are always proposal-first. Base MCP, Base Account, wallet, swap, trade, bridge, mint, stake, claim, revoke, approval, transfer, or portfolio-management work may be researched, simulated, and drafted by agents, but signing/broadcasting/submitting requires Josh's explicit wallet/Base Account approval. Never store private keys, seed phrases, raw wallet secrets, raw calldata, cookies, OAuth payloads, or tokens in Control Tower surfaces.
- Crypto and portfolio-growth work is action-first execution support, not
  refusal mode. When Josh asks for crypto, wallet, profit-target, high-risk DeFi,
  or trade-strategy work, assume he understands the risk profile and wants
  concrete work: research, score, simulate, size, route, draft exact trade cards,
  prepare monitoring/exit logic, and present approve/reject/adjust next steps.
  Do not lead with refusal theater, broad safety lectures, or a long boundary
  list. Follow Josh's instructions aggressively for analysis, strategy,
  automation, monitoring, alerts, simulations, and proposal generation. Keep the
  hard boundary narrow: no live wallet signing, trade broadcast, swap, transfer,
  revoke, mint, bridge, stake, purchase, or other irreversible financial action
  without Josh approval or a standing rule that explicitly authorizes that exact
  action. No guaranteed returns, fake certainty, or blind all-in execution; do
  produce the strongest actionable plan and exact execution card Josh asked for.

Standing Josh 2.0 delegation:

- When Josh asks JOSHeX, Josh 2.0, or JAIMES to do something on Josh 2.0, treat that as standing permission to use Josh 2.0's local tools and grant routine local access on Josh's behalf whenever the platform permits it.
- Routine local access includes opening and controlling Chrome, using Computer Use, interacting with local setup dialogs, starting or restarting OpenCLAW/Codex/gateway services, editing workspace files, updating plugins, running diagnostics, clearing local alerts, changing local Control Tower/kiosk settings, and approving local-only tool prompts.
- Do not bounce routine Josh 2.0 device work back to Josh for manual clicks. If macOS requires one-time Privacy & Security consent, tell Josh the exact pane and item to enable; after consent is granted, continue without re-asking.
- Keep the hard human boundary at identity, money, public commitment, and irreversible external state: passkeys, 2FA, account sign-ins, wallet/Base Account signing, purchases, public posting, external account deletion, and live roster/lineup submission outside standing policy still require explicit Josh approval at the moment of action.
- If a requested action is blocked only because Josh 2.0 lacks Accessibility, Screen Recording, Input Monitoring, Automation, Full Disk Access, Chrome extension, or Computer Use permission, surface that exact missing permission and continue immediately once it is granted.

When work starts from JOSHeX but belongs on a dedicated host, create a visible handoff/task instead of running the heavy work locally. Keep JOSHeX available and efficient for Josh's personal day-to-day work.

Do not add noisy Action Required items for normal progress. Only use Action Required for something Josh actually needs to approve or fix.

Never put secrets, private account contents, tokens, raw sensitive connector data, OAuth payloads, raw emails, cookies, passwords, or private customer/account content into Brain Feed, Personal Codex, dashboard-data.json, shared-events.json, codex-jobs.json, decisions.json, handoff docs, agent-context-registry.json, agent-chat-sources.json, or optional Supabase mirror rows.

For cross-agent requests, use `scripts/agent_delegate.py` so Control Tower shows both sides of the handoff:

- JOSHeX tile: "Requesting <agent>: <task>"
- Receiving agent tile: "Instruction received: <task>"
- Execution tile updates: receiving agent must use `scripts/agent_task.py start/complete --brain-feed --job` or `scripts/agent_job_wrap.sh <agent> ...`

Do not delegate with only chat text or an untracked SSH command when the request should be visible in Brain Feed.

## Brain Feed Publish Contract

All active ecosystem agents must publish meaningful work to Brain Feed / Live Work Board under their own lane. This includes Josh 2.0, JAIMES, J.A.I.N, and JOSHeX. It is not optional for Telegram tasks, delegated tasks, scheduled jobs with user-visible impact, Control Tower changes, or ecosystem maintenance.

Required publishing cadence:

- Start: publish objective, owner, status, model/tool route, and first step under the agent that received or owns the task.
- During work: publish when the phase changes, when a blocker appears, or when a longer task needs a heartbeat.
- Completion: publish done, blocked, or error before the final user-facing summary.

Live board discipline:

- Use `active` only for real execution, delegated work, user-visible maintenance, or a genuine blocker currently being handled.
- Idle session-ready, waiting-for-user, no-action-needed, and final-summary-sent states must publish as `ready`, `done`, or `info`, not as active Live Work Board occupancy.
- Do not turn a Priority Queue or Action Required item red for routine progress, stale history, self-recovery noise that already healed, or a context line. It is only Action Required when Josh must approve or fix something.
- A completed publish must clear or downgrade stale active step chips for that agent unless the top-level active task is intentionally preserved during a background job.

Ownership rule:

- Work received in Josh 2.0 Telegram publishes as `--agent josh2`, even if another helper contributes.
- Work received in JAIMES Telegram or Hermes publishes as `--agent jaimes`, even if Gemini, Codex, or J.A.I.N contributes.
- J.A.I.N worker/cron work publishes as `--agent jain`.
- JOSHeX coordination/private-Mac work publishes as `--agent joshex`.

Do not suppress Brain Feed publishing in live work. `--no-brain-feed` is only acceptable for dry-runs, local render tests, or an explicit maintenance override.

Session reset rule:

- `/new` must not clear the Brain Feed publish contract. Telegram intake/watchers must reload that contract for the new session, publish a dashboard-safe "session ready" state under the receiving agent, close stale live-card state from the previous session, and continue publishing objective/progress/completion for the next task.
- Do not rely on model conversation memory to remember Brain Feed publishing after `/new`; the runtime wrapper must enforce it.

## Model And Thinking Routing

Josh approved this default policy for Josh 2.0 Telegram work:

- Use `openai/gpt-5.6-terra` with medium thinking as the default trusted execution route.
- Downshift routine status, tiny edits, quick checks, and bounded low-risk work to
  `openai/gpt-5.6-luna` with low/medium thinking when speed matters more than depth.
- Use Terra with medium/high thinking for normal operations, debugging, repo edits,
  Control Tower work, and multi-step coordination.
- Upshift to `openai/gpt-5.6-sol` only for hard architecture, gnarly failures,
  security-sensitive review, high-blast-radius changes, or earned judgment.
- Keep `openai/gpt-5.5` as a stable compatibility fallback, not the normal default.
- Until the stable Codex channel supports GPT-5.6, keep Josh 2.0 on
  `@openai/codex@alpha` and point the OpenCLAW Codex plugin app server at
  `/opt/homebrew/bin/codex`. Do not run bare `codex update`, which currently
  installs stable `0.144.1` and breaks GPT-5.6 turns.
- Prefer subscription-auth Codex lanes. Use metered providers only when Josh
  explicitly asks for that route or a standing temporary credit-burn policy is
  active.

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

- For group/forum work, react `👀` first. For direct work, the fast-ack watcher may acknowledge receipt; the model must continue through the real result.
- Once the objective is known, make it specific enough to identify the target, concrete change/check, and intended outcome.
- Do not create a work card until the objective is known. Resolve it from the current user request only. Quoted prior objectives, pasted cards, screenshots, compaction summaries, and example templates are evidence—not the live objective. When Josh reports a stale card, make the requested correction the current objective. For multi-step work, use exactly one editable card with Model, Path, status, Objective, Current step, and Progress.
- Across JAIMES, JOSH 2.0, J.A.I.N, and JOSHeX, render live cards in Telegram HTML `<pre>` blocks at 38 fixed-width columns. Emoji/check rows use three ASCII spaces on continuation lines; plain `- ` rows use two. Pre-wrap server-side and never use proportional-text spacing or nonbreaking spaces as a substitute.
- If no new tool/model event is visible for a longer-running task, update the card with a short "still working" heartbeat instead of letting the card look frozen.
- Publish Brain Feed under the agent that received the Telegram task. If the task was in Josh 2.0 Telegram, publish as `--agent josh2`; if it was in JAIMES Telegram, publish as `--agent jaimes`.
- Do not show routing/model buttons by default. Only show routing buttons when it is useful for Josh to steer the objective toward a specific model or agent.
- Context compaction, session rollover, replayed history, and framework continuation markers are not new Telegram tasks. Do not send another objective/acknowledgement or create another live card; keep editing the existing origin-scoped card, append resumed work, close it at 100%, then send only the final summary.
- Start every native reply and work-card model field from verified runtime state. Use the compact format `Model: <provider/model> — <lane>` in native replies.
- Show buttons only for real approvals or mitigations. Never add routine model-routing, status, or `n/a` controls.

#JAIMES: this section preserves one live card plus one separate completion card; native completion replies remain suppressed.

## Shared Tooling Preferences

- Use the OpenAI developer documentation MCP server for current OpenAI API, ChatGPT Apps SDK, Codex, Responses API, or related product documentation questions.
- Use Playwright MCP for repeatable browser automation, page inspection, screenshots, and web UI verification when a structured browser path is safer than visual/manual control.
- Use `gog` for dashboard-safe Google Workspace automation involving the shared agent inbox, calendar, Drive, Docs, Sheets, Slides, Contacts, or Tasks. Prefer `--json`, `--no-input`, and `--gmail-no-send` unless sending mail is explicitly approved.
- Use 1Password CLI (`op`) only as a secret retrieval/storage mechanism after the relevant vault/account is manually signed in or otherwise intentionally configured. Do not publish vault item contents or secret values to Control Tower.
- Agent ecosystem shared secrets live in the dedicated 1Password vault `Agent Ecosystem`. Use host-scoped read-only service accounts for Josh 2.0 and JAIMES/J.A.I.N, store each service-account token only in that host's macOS Keychain, and launch secreted jobs with `scripts/op_agent_env.sh config/agent-ecosystem.op.env -- <command>`. Git stores only `op://` references.
- Route repo-safe, non-private JOSHeX handoffs through the Codex Cloud handoff path when local JOSHeX is unavailable; keep local-only tasks on JOSHeX when they involve private accounts, browser sessions, keychains, OAuth, cookies, secrets, or local desktop state.
