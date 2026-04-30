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

## Live Preview
- JOSH 2.0 preview worktree: `/Users/josh2.0/.openclaw/workspace/mission-control-joshex-live-preview`
- Local preview URL: `http://127.0.0.1:8788/index.html?mode=kiosk&preview=joshex`
- Use this preview during live design sessions, then copy validated changes into the branch worktree and push.

## Validation
- Keep `mission_control_regression_check.py`, `mission_control_screenshot_diff.py`, and `git diff --check` green.
- Screenshot baselines should move only after inspecting current desktop/mobile captures.
- Do not commit volatile `data/*.json` churn unless the task explicitly requires fixture changes.
