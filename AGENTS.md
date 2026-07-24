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

Standing validated ecosystem push authorization (Josh, 2026-07-24): JOSHeX, JAIMES, and Josh 2.0 may automatically push a validated source commit to the canonical `mission-control` repository's `origin/main` after the applicable change guard and test gates pass and the source commit is clean. `config/control-tower-push-policy.json` records this narrow authorization so each agent's new lease can capture it before edits begin. This does not authorize merges, public releases or posts, purchases, account changes, wallet actions, destructive external actions, or pushes to another repository, remote, or branch.

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

## Linear Durable Work Ledger

Control Tower remains the source of truth for live execution, heartbeats, queues, current jobs, and agent availability. Linear is the durable planning layer for work that must survive the current execution: approved enhancements, confirmed bugs or regressions, approved proposals, cross-session initiatives, and unresolved follow-ups that need an owner.

- Apply `agent-skills/linear-work-tracking/SKILL.md` and `config/linear-integration.json` before creating or updating ecosystem issues.
- Search by Control Tower `workId` or proposal ID before creating an issue. Reuse one issue across handoffs and lifecycle changes.
- Create qualifying shared tasks with `agent_task.py` or `agent_delegate.py --durable --area <Area> --acceptance-criterion <criterion>`. Claim the returned Josh 2.0-canonical sanitized intent, process it with the connected Linear tool, and acknowledge its verified `JCU-*` issue ID; tasks without `--durable` never enter Linear.
- Update Linear only at meaningful boundaries: accepted, started, blocked, verifying, completed, or cancelled. Never mirror heartbeats, routine jobs, Telegram replies, live-card edits, transient telemetry, or self-healed alerts.
- Linear ownership does not grant or bypass a shared-source edit lease. Agents may prepare, research, test unrelated surfaces, and coordinate in parallel while the canonical lease continues to protect source edits.
- JOSHeX, Josh 2.0, and JAIMES use their connected Linear Codex tools. J.A.I.N delegates durable issue writes to one stable JAIMES connector task per source `workId` until a verified headless connector is intentionally added. Noncanonical lanes run `linear_work_intent.py flush-local` before consuming intents so temporary canonical transport failures are replayed.
- Store only dashboard-safe objectives, acceptance criteria, owner lane, priority, approval state, stable work references, and safe artifact links. Never store raw prompts, raw emails, private account content, OAuth payloads, tokens, cookies, or credentials.

#JAIMES: Linear tracks durable work boundaries; Control Tower remains authoritative for live state and shared edit leases remain mandatory.

## Continuous Ecosystem Maintenance

Use `docs/continuous-maintenance.md` and `config/continuous-maintenance.json` for ongoing ecosystem hygiene. The hourly maintenance portfolio projects append-only proposal history, adaptive refactor discoveries, risk and aging, active WIP, and seven-run reliability readiness into Control Tower.

- Keep automatic source mutation disabled. Automation may discover, classify, prepare a sandbox, run tests, and package rollback evidence; source promotion remains reviewed and lease-gated.
- Freeze elective maintenance whenever a required reliability gate fails. Security fixes, reliability repairs, and rollbacks may continue.
- Keep at most three maintenance items in leased, implementing, or verifying state.
- Preserve proposal history and use one stable proposal ID across lifecycle transitions. Do not create duplicate current work rows for status changes.
- Major dependencies, medium/high-risk refactors, architecture or contract changes, and protected paths require the review level defined by the checked-in risk policy.

#JAIMES: continuous maintenance earns promotion authority through exact evidence; a clean-looking dashboard never substitutes for seven consecutive six-gate runs.

## JOSHeX Device Resource Guardrail

JOSHeX runs on Josh's personal MacBook. Treat this device as the trusted coordinator and personal-device specialist, not the default execution host for the agent ecosystem.

Prefer Josh 2.0 or JAIMES for real agent-ecosystem work whenever the task does not specifically require this MacBook's local desktop, private browser session, keychain, OAuth state, personal account permissions, or local files that only exist here.

Personal-Mac TCC fallback:

- Treat the first read-only macOS TCC `Operation not permitted` result for a required local path as the single probe. Stop local retries; sandbox escalation and alternate commands do not override TCC.
- For dashboard-safe ecosystem work available on a canonical dedicated host, continue there without copying, reaching into, or claiming access to denied personal-Mac data. Preserve JOSHeX ownership and use the owning host's current checkout.
- If the task requires MacBook-only private state, do not route around the denial through helper apps, alternate runtimes, AppleScript, SSH, browser uploads, or an already-authorized app. Ask once for the minimum Files & Folders or file-picker permission for the exact resource, or report the blocker.
- Never request Full Disk Access, Accessibility, Screen Recording, Input Monitoring, or Automation solely for repository inspection. Use exact-target reads and scope searches away from secret-bearing configuration, private data, and generated telemetry.

#JAIMES: one failed personal-Mac TCC probe now routes ordinary ecosystem work to canonical dedicated hosts instead of expanding MacBook permissions.

Default ownership:

- JOSHeX: planning, coordination, architecture decisions, lightweight integration, private-Mac-only work, personal account/browser/keychain/OAuth work, and final review when the user is directly engaging from this laptop.
- Josh 2.0: Control Tower kiosk, OpenCLAW operations, device alerts on the Josh 2.0 Mac mini, dashboard refreshes, service checks, browser automation that should not disturb the MacBook, and ecosystem host maintenance.
- JAIMES/J.AI.N: heavier compute, long-running work, scheduled jobs, Gemini/Grok specialist passes, Sorare operations, intelligence scans, durable Hermes workflows, and background processing.

Dedicated-host autonomy:

- Josh 2.0 and JAIMES/J.AI.N run on dedicated Mac minis. Device-local tool approvals should be configured for full access and auto-approval: terminal work, workspace file edits, browser automation on those hosts, service restarts, cron/job maintenance, plugin updates, diagnostics, and local cleanup should not require Josh to approve each step.
- Do not ask for user approval solely because a task needs to use the dedicated host's shell, filesystem, browser, local app controls, or service manager. Execute on the proper dedicated host and keep Brain Feed current.
- Keep explicit approval only for truly external or irreversible actions unless a standing policy already pre-approves that workflow: purchases, public posts/messages, account deletion, destructive private-account changes, production pushes, or fantasy/Sorare roster or lineup changes outside the accepted standing policy.
- Onchain and wallet actions are always proposal-first. Base MCP, Base Account, wallet, swap, trade, bridge, mint, stake, claim, revoke, approval, transfer, or portfolio-management work may be researched, simulated, and drafted by agents, but signing/broadcasting/submitting requires Josh's explicit wallet/Base Account approval. Never store private keys, seed phrases, raw wallet secrets, raw calldata, cookies, OAuth payloads, or tokens in Control Tower surfaces.
- All four agent lanes use `scripts/agent_wallet_signer.py` and
  `docs/shared-wallet-signer.md` for the managed EVM wallet. The shared client
  routes requests to JAIMES's Keychain-isolated one-shot signer; agents never
  copy or receive the key or raw signed transaction. Status, validation, and the
  non-broadcast canary are available to JOSHeX, Josh 2.0, JAIMES, and J.A.I.N.
  A real signature requires a matching, short-lived Josh approval artifact, and
  broadcasting remains a separate explicitly approved action.
#JAIMES: agents share the signer broker rather than wallet custody, preserving one-time approval and non-broadcast defaults.
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

Josh approved this default policy for Codex-app, Josh 2.0, and JAIMES work:

- For every substantial dashboard-safe task, apply the checked-in
  `agent-skills/multi-model-routing/SKILL.md` policy before committing the bulk
  reasoning to GPT. The current Codex conversation remains coordinator and final
  integrator; a specialist claim is valid only after an actual verified provider
  result returns.
- Derive Codex conservation from the exact weekly allowance and full-reset-credit
  count. While the weekly balance is positive and at least one reset credit is
  available, keep normal routing even below 20% or when pace predicts early
  exhaustion. At zero, mark Codex exhausted until Josh applies a reset. Once no
  reset credits remain, route eligible non-execution work to Gemini, GLM, or Grok
  first at 20% or less or when pace predicts early exhaustion. Never spend a
  specialist call merely to create artificial balance; match it to the task.

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
- Use `ollama/glm-5.2:cloud` as a deliberate sub-agent on both Josh 2.0 and
  JAIMES for dashboard-safe large-context technical analysis, architecture
  analysis, multi-file planning, structured code review, and parallel technical
  second opinions. GLM may reason, plan, and review; the owning Codex lane keeps
  repo edits, terminal execution, permissions, approvals, and final verification.
- GLM 5.2 is Ollama Cloud, not an offline/private model. Never send it secrets,
  OAuth payloads, cookies, raw emails, raw connector or account contents, wallet
  data, credentials, or other private context. Use local Qwen/Llama for offline
  private drafts, Gemini for summaries/digests/general synthesis, Grok for
  X-native/current-events context, and the GPT/Codex ladder for trusted execution.
- Honor an explicit privacy-safe request for GLM 5.2 in a fresh sub-agent lane and
  disclose `ollama/glm-5.2:cloud` before substantive work. If the model is
  unavailable or the context is unsafe for cloud processing, explain the block
  and fall back to the appropriate Codex or local Ollama lane.
- Every separately executing agentic lane—including a model lane, sub-agent,
  delegated worker, parallel worker, or future launcher—must publish with the
  exact controlling `workId` and `runId` as `executionRole=worker`. The launcher
  keeps its verified model route and heartbeat current, records disclosed route
  changes, and terminal-cleans it. Never infer lane parentage from owner, title,
  objective text, provider name, or model name. `model_lane.py` enforces this for
  fresh model lanes; other launchers must use the same canonical `agent_publish`
  worker contract. Diagnostic probes must opt out explicitly and must not conceal
  substantive work.

## Cookie And Keychain Disambiguation

When the user mentions a visible alert for `cookie.codex`, "Codex cookies",
"Keychain Not Found", or "A keychain cannot be found to store cookie.codex",
treat it as a macOS/Codex keychain alert on the device. Inspect visible
`SecurityAgent` windows, stale `openclaw models auth login` processes, Codex
auth health, and default keychain state. Do not route this to Sorare cookie
freshness or Sorare auth refresh unless the user explicitly says Sorare.

When the user says "Sorare cookie", treat that as Sorare auth/cookie freshness.
Keep the two paths separate in Brain Feed, work cards, and final reports.

## Telegram Completion Authority

Do not maintain a second detailed Telegram UX specification here. The authority
chain is:

1. `config/telegram-intake-lanes.json` defines registered topics, one owner per
   topic, and configured mention overrides.
2. `scripts/telegram_channel_registry.py` enforces that authority and fails
   closed when ownership is missing or ambiguous.
3. `scripts/telegram_gateway_lifecycle.py` owns durable work identity, receipt,
   fencing, recovery, and delivery state.
4. `agent-skills/telegram-task-flow/SKILL.md` owns the visible reaction,
   objective, live-card, final-summary, routing-disclosure, button, and Brain
   Feed contract.

Keep these hard invariants at every runtime boundary:

- Exactly one registered owner may answer an untagged topic message. One
  unambiguous configured `@mention` may override that owner; multiple agent
  mentions are silent. Replies, cards, and finals remain in the origin topic.
- The gateway and fast-ack runtime exclusively own Telegram reactions, live
  cards, edits, and final delivery. Models return substantive work normally and
  never create a parallel Telegram surface through tools or Bot API calls.
- One owned task produces at most one acknowledgement reaction, one editable
  live card, and one structured final. The card receives heartbeats while work
  is active, and model/route labels come only from verified execution state.
- Telegram work publishes lifecycle state under the receiving agent's Brain
  Feed lane. Compaction, replay, and session rollover reuse the existing
  origin-scoped work; they never create another task surface.
- Buttons are reserved for concrete approvals or mitigations. Normal routing,
  status, and `n/a` states do not create controls.

#JAIMES: runtime mechanics may differ, but registry ownership, gateway
single-writer delivery, the shared visible contract, and origin-topic closure
are common to Josh 2.0 and JAIMES.

## Shared Tooling Preferences

- Use the OpenAI developer documentation MCP server for current OpenAI API, ChatGPT Apps SDK, Codex, Responses API, or related product documentation questions.
- Use Playwright MCP for repeatable browser automation, page inspection, screenshots, and web UI verification when a structured browser path is safer than visual/manual control.
- Use `gog` for dashboard-safe Google Workspace automation involving the shared agent inbox, calendar, Drive, Docs, Sheets, Slides, Contacts, or Tasks. Prefer `--json`, `--no-input`, and `--gmail-no-send` unless sending mail is explicitly approved.
- Use 1Password CLI (`op`) only as a secret retrieval/storage mechanism after the relevant vault/account is manually signed in or otherwise intentionally configured. Do not publish vault item contents or secret values to Control Tower.
- Agent ecosystem shared secrets live in the dedicated 1Password vault `Agent Ecosystem`. Use host-scoped read-only service accounts for Josh 2.0 and JAIMES/J.A.I.N, store each service-account token only in that host's macOS Keychain, and launch secreted jobs with `scripts/op_agent_env.sh config/agent-ecosystem.op.env -- <command>`. Git stores only `op://` references.
- Route repo-safe, non-private JOSHeX handoffs through the Codex Cloud handoff path when local JOSHeX is unavailable; keep local-only tasks on JOSHeX when they involve private accounts, browser sessions, keychains, OAuth, cookies, secrets, or local desktop state.
