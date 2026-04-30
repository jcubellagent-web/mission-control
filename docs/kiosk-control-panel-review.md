# Mission Control Kiosk Control Panel Review

## Architecture
- Mission Control is a static dashboard served from GitHub Pages and a local JOSH 2.0 preview server.
- `scripts/update_mission_control.py` assembles JSON state into `data/dashboard-data.json` and sidecar feeds.
- `index.html` owns the visible app: layout, CSS, render functions, modals, Brain Feed, Today Jobs, and local controls.
- The 24-inch kiosk view is activated by `kiosk-mode`, with Brain Feed as the primary surface and Today Jobs as the right rail.

## UI Review
- The useful information is already present, but too much of the top row was equal-weight telemetry.
- Important alerts were split across action items, jobs, canaries, calendar, device health, and model panels.
- Brain Feed is the correct hero, but the surrounding chrome needed to become quieter so the agent lanes stay readable from across the room.
- Each Brain Feed card needs its own context signal. A single shared context meter does not explain which operator lane is constrained.
- Today Jobs is the right persistent rail; it should remain dense and scannable instead of becoming a modal-only workflow.

## Design Direction
- Apple-inspired dark control surface: calm black/blue-gray layers, softened borders, restrained glow, and stronger typography.
- A single clickable control strip replaces the loose status-pill row.
- Alerts are promoted visually by state: good, warning, critical.
- The strip prioritizes the daily operating questions:
  - Is anything asking for attention?
  - Are cron jobs healthy?
  - Are all three agent lanes visible?
  - Are canaries/calendar/voice/codex lanes healthy?
  - Are Sleep and MoltWorld controls reachable?
- Brain Feed cards now answer the same glance questions for JOSH 2.0, JAIMES, and JOSHeX: status, current objective, latest step, tool, update age, lane insight, and context meter.
- The soup-to-nuts Brain Feed pass should feel alive when an operator is active: the hero card laser frame returns, active agent panels get stronger flowing light, and the open middle space becomes a Live Actions trace instead of static dashboard tiles.
- Each Live Actions trace should surface a scrolling tool/decision meter:
  - JOSH 2.0: shell/OpenClaw steps, defer/await decisions, schedule pressure, machine signal.
  - JAIMES: Hermes/specialist steps, watchdog decisions, queue pressure, next automation move.
  - JOSHeX: OpenAI Codex patch steps, design decisions, validation state, visible capabilities.
- JOSHeX is branded as the OpenAI Codex patch lane, with a green/black treatment and live sidecar polling from `data/personal-codex.json`.
- The top control strip avoids initials and abbreviations; compact text is allowed, but labels and values should remain self-explanatory on the 24-inch kiosk.
- The Model Usage JOSHeX strip must remain tall enough to show patch, now, and capability rows without clipping.

## Live Preview
- JOSH 2.0 preview worktree: `/Users/josh2.0/.openclaw/workspace/mission-control-joshex-live-preview`
- Local preview URL: `http://127.0.0.1:8788/index.html?mode=kiosk&preview=joshex`
- Use this preview during live design sessions, then copy validated changes into the branch worktree and push.
- To push a safe visible JOSHeX update during live work, run `python3 scripts/joshex_status_push.py --objective "..." --validation "..." --activity "..."` in the preview worktree. The dashboard polls that sidecar every few seconds.

## Validation
- Keep `mission_control_regression_check.py`, `mission_control_screenshot_diff.py`, and `git diff --check` green.
- Screenshot baselines should move only after inspecting current desktop/mobile captures.
- Do not commit volatile `data/*.json` churn unless the task explicitly requires fixture changes.
