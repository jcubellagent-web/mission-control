# Josh 2.0 Telegram UX Audit

Updated: 2026-05-02
Owner: JOSHeX
Privacy: dashboard-safe

## Goal

Make Telegram the cleanest iPhone front door for Josh 2.0 while preserving autonomy, privacy, and Mission Control visibility.

## Current State

- Josh 2.0 is the right primary Telegram operator because it is always-on, reachable from iPhone, and already wired into OpenCLAW.
- Agent Control shows Josh 2.0 ready, approval posture clean, no legacy crontab entries, and no failed queues.
- Josh 2.0 OpenAI-Codex auth needs an interactive re-auth. Headless SSH cannot complete it.
- OpenCLAW doctor reports Telegram is in pairing/allowlist mode. That is safer, but new chats or groups need explicit approval.
- OpenCLAW doctor also reports the bundled Telegram channel cannot load `grammy`; that should be fixed before treating Telegram as fully optimized.
- Existing Mission Control scripts already support Telegram voice task routing and main Telegram session usage tracking.

## Best iPhone Experience

Josh 2.0 should feel like a command center, not a terminal over chat.

Every substantive Telegram reply should show:

- One plain model/auth line first.
- One blank line.
- Short bullet rows for Route, Objective, Status, Now, Done, Blocker, and Next.
- Only real blockers.
- Buttons for the next useful action.

Preferred compact format:

```text
Codex 5.5 (openai-codex subscription)

- Route: Josh 2.0 direct chat; foreground lane.
- Objective: Test Telegram readability.
- Status: Good; responsive.
- Now: Using compact bullets.
- Done: Brain Feed hook fired; direct chat path works.
- Blocker: None.
- Next: Tap a button or send the next task.
```

Default buttons should be:

- Run all safe steps
- Route to JOSHeX
- Route to JAIMES
- Check Mission Control
- Hold

Common commands should be short and memorable:

- `/new` reset the conversation.
- `/status` return Josh 2.0, JAIMES, J.AI.N, and Mission Control status.
- `/mc` push/check Mission Control.
- `/overview` send a compact ecosystem overview card.
- `/daily` send a compact daily digest card.
- `/route` explain which agent should own the request.
- `/joshex` hand work to Codex/JOSHeX.
- `/jaimes` hand work to JAIMES/Hermes.
- `/jain` hand work to J.AI.N workers.
- `/models` show active model, auth path, and fallback routing.
- `/help` show only the commands above.

## Routing Defaults

- Josh 2.0 owns device-local work, Telegram interaction, quick status, approvals, kiosk/Mission Control refresh, and light shell operations.
- JOSHeX owns architecture, repo changes, cross-agent coordination, sensitive connector work, and final decision records.
- JAIMES owns heavier services, Hermes workflows, background services, and headless service checks.
- J.AI.N owns worker/cron-style tasks and scheduled background jobs.
- Gemini should be used for dashboard-safe synthesis, drafts, classification, and second-pass thinking when no secrets or private account contents are needed.
- OpenRouter should be fallback/specialist only unless explicitly selected.

## UX Rules

- Never expose secrets, tokens, raw private account data, raw emails, or OAuth details in Telegram summaries or Mission Control.
- Do not ask for approval for safe, already-approved host-local operations.
- Do ask for approval for destructive actions, secrets, auth changes, public posting, payment/account changes, or broad permission changes.
- If blocked, state exactly what is blocked and provide one actionable fix.
- For long work, edit/update a work card instead of sending noisy progress messages.
- For voice notes, confirm the interpreted task before acting if the request is ambiguous, sensitive, or destructive.
- Keep normal responses short enough to read on an iPhone screen without scrolling through logs.
- Prefer compact bullet cards over paragraph-style labels on mobile.
- Keep `AGENTS.md` lean. Store detailed Telegram UX policy here and in `data/josh2-telegram-ux-config.json`; `AGENTS.md` should contain only the compact mandatory behavior pointer.

## Telegram Custom Options

Use these options in this order:

- Inline keyboards: primary control surface for approvals, routing, Mission Control checks, and safe next steps.
- Callback queries: every inline tap must be acknowledged immediately so Telegram does not show a stuck spinner.
- Bot command menu: persistent low-friction entrypoint for `/status`, `/mc`, `/models`, `/route`, `/joshex`, `/jaimes`, `/jain`, `/new`, and `/help`.
- Chat menu button: set to Telegram's commands menu for now; use a Web App later only if a true mobile control panel is needed.
- Reply keyboards: keep available but not default; they are more intrusive than inline buttons.
- Copy-text buttons: use later for handoff snippets, one-time commands, and URLs that Josh may need to paste.
- ForceReply: use later for narrow forms where Josh must type one specific value.

The active config lives in `data/josh2-telegram-ux-config.json`. Reapply Telegram bot settings with `scripts/josh_telegram_setup.py` from the Mission Control checkout on Josh 2.0.

## Implemented Helpers

- `scripts/josh_work_card.py`: compact editable Telegram work cards. Use by default for most tasks requested through Josh 2.0 when the task has more than one step, might take more than about 60 seconds, or changes Mission Control/agent state.
- `scripts/josh_telegram_digest.py overview`: sends a compact ecosystem overview card.
- `scripts/josh_telegram_digest.py daily`: sends a compact daily digest card.
- `scripts/josh_agent_quick_card.py <agent>`: sends a compact quick card for Josh 2.0, JOSHeX, JAIMES, or J.AI.N.
- `scripts/josh_telegram_callback_action.py <callback>`: maps common button payloads to concrete actions/cards without polling Telegram directly.
- `telegram-control-panel.html`: dashboard-safe Telegram Web App artifact. Public target: `https://jcubellagent-web.github.io/mission-control/telegram-control-panel.html` after the durable files are published to GitHub Pages.

Work-card default:

```bash
python3 scripts/josh_work_card.py start --key <task-key> --title "<objective>" --now "<current step>"
python3 scripts/josh_work_card.py update --key <task-key> --now "<current step>" --done "<done A>|<done B>"
python3 scripts/josh_work_card.py done --key <task-key> --done "<verified A>|<verified B>"
```

Do not create work cards for single-turn pleasantries, trivial answers, or tasks where a concise bullet reply is enough.

## AGENTS Lean Rule

Josh 2.0 `AGENTS.md` should stay below the bootstrap warning zone. Keep only hard operating rules there. Detailed routing, Telegram UX, startup nuance, and implementation notes belong in Mission Control docs/config or MEMORY.md.

Current desired AGENTS shape:

- Mission Control / Brain Feed must-run rules.
- Session startup hard rules.
- Reply format hard rules.
- Routing defaults.
- Red lines and external-action approval boundaries.
- Short pointers to detailed docs.

Do not paste long option lists, implementation backlogs, or explanatory policy into AGENTS. Add or update a doc, then leave a pointer.

## Implementation Backlog

P0:

- Complete interactive Josh 2.0 OpenAI-Codex re-auth.
- Keep the Telegram runtime dependency fix verified.
- Keep `/models`, `/status`, `/mc`, `/route`, `/overview`, and `/daily` in the bot command menu.
- Ensure every Telegram final response includes active model/auth and route.
- Use editable work cards for most multi-step Josh 2.0 tasks.

P1:

- Add a single `Check Mission Control` button that both pushes status and verifies the visible row.
- Add a `Route to JOSHeX` button for architecture/repo/coordinator tasks.
- Add a `Route to JAIMES` button for headless service work.
- Add concise failure templates for auth expired, gateway down, missing dependency, stale Mission Control, and approval required.
- Verify the GitHub Pages Web App URL after publish, then keep the `Open control panel` inline button pointed at it.

P2:

- Add a lightweight preference layer for response length, default model routing, and notification quiet hours.
- Add per-agent quick cards for Josh 2.0, JAIMES, J.AI.N, and JOSHeX.

## Acceptance Criteria

- Josh 2.0 can answer `/status`, `/models`, and `/mc` from Telegram.
- Josh 2.0 command menu includes `/overview` and `/daily`.
- A normal Telegram user can tell which model is being used and why.
- Long-running work has a stable work card and Mission Control row.
- Agent quick cards are available for Josh 2.0, JOSHeX, JAIMES, and J.AI.N.
- Safe local work proceeds without repetitive approvals.
- Sensitive or destructive work still requires explicit user approval.
- Mission Control agrees with Telegram status after a refresh.
