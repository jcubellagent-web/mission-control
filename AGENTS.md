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

Keep Brain Feed visibility current with `scripts/agent_publish.py --agent joshex --brain-feed` for Codex/JOSHeX work, and regenerate dashboard data after changing sidecars/jobs/shared events.

For cross-agent requests, use `scripts/agent_delegate.py` so Mission Control shows both sides of the handoff:

- JOSHeX tile: "Requesting <agent>: <task>"
- Receiving agent tile: "Instruction received: <task>"
- Execution tile updates: receiving agent must use `scripts/agent_task.py start/complete --brain-feed --job` or `scripts/agent_job_wrap.sh <agent> ...`

Do not delegate with only chat text or an untracked SSH command when the request should be visible in Brain Feed.
