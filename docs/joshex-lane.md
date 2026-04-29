# JOSHeX Lane

JOSHeX is a local Codex contribution-visibility lane for Mission Control.
It is not a JAIMES/Hermes live-agent slot and must not be promoted into the Brain Feed hero.

## Source data

- File: `data/personal-codex.json` (legacy filename kept for compatibility)
- Dashboard payload key: `personalCodex`
- UI panel anchor: `#personal-codex`
- Quick panel: `buildJoshexQuickMarkup(...)`

## Supported fields

```json
{
  "status": "ready|active|idle|attention",
  "objective": "short current contribution summary",
  "validation": "latest validation state",
  "patchStatus": {
    "status": "clean|pending|unknown",
    "summary": "Clean at <sha> or N source files changed",
    "dirtyCount": 0,
    "files": [],
    "head": "short sha",
    "updatedAt": "ISO timestamp"
  },
  "actionRequired": [
    { "priority": "medium", "title": "review prepared patch" }
  ],
  "recentActivity": [
    { "event": "prepared local patch" }
  ],
  "capabilities": ["inspect", "edit", "validate", "prepare patches"],
  "agentSlot": false,
  "promoteToBrainFeed": false
}
```

## Routing rules

- Keep `agentSlot` false.
- Keep `promoteToBrainFeed` false.
- Do not add JOSHeX to `agentBrainFeeds`.
- High-signal items may be prefixed as `JOSHeX:` in:
  - Action Required
  - Recent Activity
  - Capability Stack
  - JOSHeX quick/full panels

## Patch status feed

`scripts/update_mission_control.py` populates `personalCodex.patchStatus` from local git state.
It ignores volatile `data/jaimes-brain-feed.json` so JAIMES telemetry does not make the lane look dirty.

## Validation

Run:

```bash
python3 -m py_compile scripts/update_mission_control.py
python3 scripts/update_mission_control.py
python3 scripts/mission_control_regression_check.py
python3 scripts/mission_control_visual_canaries.py
```

Expected checks:

- `dashboard-data.json.personalCodex` exists.
- `personalCodex.patchStatus` exists.
- `#personal-codex` panel is wired in `index.html`.
- `renderPersonalCodex(data)` is called from `renderDashboard(data)`.
- No `personalCodex` entry exists under `agentBrainFeeds`.
