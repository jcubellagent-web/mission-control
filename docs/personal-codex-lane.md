# Personal Codex Mission Control Lane

This lane gives Josh's local Codex a distinct, non-live-agent home in Mission Control.
It is intentionally separate from JOSH 2.0, JAIMES, and J.A.I.N Brain Feed slots.

## Sidecar Contract

Personal Codex writes or updates:

`data/personal-codex.json`

Supported keys:

- `status`: one of `ready`, `working`, `blocked`, `needs_josh`, `offline`
- `objective`: current scoped Mission Control objective
- `updatedAt`: ISO timestamp
- `summary`: one concise operator-facing sentence
- `mode`: usually `local`, `patch`, or `approval-needed`
- `repo`: `{ "path": "...", "branch": "...", "dirty": true|false }`
- `validation`: `{ "pyCompile": "pass|fail|unknown", "regression": "pass|fail|unknown", "visualCanaries": "pass|warn|fail|unknown" }`
- `actionRequired`: list of `{ "priority": "high|medium|low", "title": "...", "url": "..." }`
- `recentActivity`: list of `{ "time": "...", "event": "..." }`
- `metrics`: `{ "openQuestions": 0, "localChanges": 0, "lastValidationMinutesAgo": 0 }`
- `links`: list of `{ "label": "...", "url": "..." }`

`scripts/update_mission_control.py` loads this file defensively and emits it as
`dashboard-data.json.personalCodex`.

## How It Appears

The dashboard renders a dedicated Personal Codex panel below System Health and
above Capability Stack. It uses an amber accent so it is easy to distinguish
from:

- red critical Mission Control signals
- green healthy live-agent status
- JOSH 2.0 / JAIMES / J.A.I.N Brain Feed hero cards

High-signal Personal Codex items are also promoted into:

- `actionRequired`, prefixed with `Personal Codex:`
- `recentActivity`, prefixed with `Personal Codex:`
- `capabilityStack`, as a `Personal Codex` capability tile when not offline

## Guidance For JOSH 2.0

- Treat Personal Codex as a local contribution lane, not a live runtime agent.
- It may inspect, edit, validate, and prepare patches in this cloned repo.
- It should not touch secrets, auth files, cron, public posting, Telegram tokens,
  SSH keys, or Google tokens without Josh's explicit approval.
- If Personal Codex needs Josh, write one concise item to `actionRequired`.
- Keep `recentActivity` sparse. Record completed work, blockers, or validations,
  not generic "working" messages.

## Guidance For JAIMES

- Do not promote Personal Codex into JAIMES Brain Feed.
- Do not treat `personalCodex.status = working` as Hermes task ownership.
- Use the sidecar only as operator visibility into local Codex work.
- If a JAIMES workflow depends on Personal Codex output, point Josh to the local
  repo diff or patch rather than assuming push access exists.
- Personal Codex may contribute dashboard UI/data improvements, but JAIMES keeps
  ownership of Hermes/Sorare/fantasy workflows unless Josh explicitly delegates.

## Validation

Before claiming the lane is healthy, run:

```bash
PYTHONPYCACHEPREFIX=/tmp/mission-control-pycache python3 -m py_compile scripts/update_mission_control.py
PYTHONPYCACHEPREFIX=/tmp/mission-control-pycache python3 scripts/update_mission_control.py
PYTHONPYCACHEPREFIX=/tmp/mission-control-pycache python3 scripts/mission_control_regression_check.py
PYTHONPYCACHEPREFIX=/tmp/mission-control-pycache python3 scripts/mission_control_visual_canaries.py
git diff --check
```

In cloned/local Codex workspaces, visual canaries may warn about missing live
runtime tools such as `gog`. That is an environment warning, not a Personal Codex
lane failure.
