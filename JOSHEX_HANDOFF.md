# JOSHeX Handoff — Mission Control Agent Board Polish

## Context
Josh approved continuing the Mission Control work with JOSHeX.

Main deployed fix:
- Commit: `50cbb7870 Fix Mission Control agent board cutoff`
- Current worktree starts from latest `origin/main`, which includes that fix plus publisher refresh commits.
- Live marker: `mc-agent-board-cutoff-fix-20260430`

## User ask
Josh disliked a weird cutoff and wants to clearly see what these agents are doing in real time:
- JOSH 2.0
- JAIMES
- JOSHeX

The immediate cutoff fix is already deployed. Continue by polishing the real-time agent board UX.

## Repo/worktree
- Primary repo: `/Users/jc_agent/.openclaw/workspace/mission-control`
- Your worktree: `/Users/jc_agent/.openclaw/workspace/mission-control-joshex-agent-board-polish`
- Branch: `joshex/agent-board-polish`

## Current behavior
Brain Feed hero now renders three side-by-side live cards using `.bf-hero-grid.tri-live`:
- `JOSH 2.0`
- `JAIMES`
- `JOSHeX`

The code path is in `index.html` around:
- `synthesizeJoshexBrainFeed()`
- `pickBrainFeedObjectiveFeeds()`
- `renderBrainFeedObjectiveCard()`
- `renderObjectiveBrainFeed()`
- CSS marker: `REALTIME AGENT BOARD CUTOFF FIX 2026-04-30`

## JOSHeX polish completed
This pass kept the JAIMES cutoff fix intact and made the three real-time lanes easier to read at a glance:
- Added clear `LIVE` / `IDLE` / `STALE` / `DONE` state pills.
- Rebalanced each card so the agent name and current objective dominate, while model/provider moves to a small support chip.
- Added a compact `Now` row for the current/latest step and kept `Tool`, `Flow`, and `Model` as bottom chips.
- Brightened objective and detail text so the board reads better on the physical display.
- Derived JOSHeX card activity from Personal Codex patch state, including changed-file summaries when present.
- Updated desktop/mobile screenshot baselines for the intentional card hierarchy change.

## Desired improvements
Do a conservative polish pass, not a full redesign:
1. Make each agent card clearer at a glance:
   - current objective
   - live/stale/idle state
   - current tool
   - latest update age
   - next/recent step if available
2. Reduce visual ambiguity:
   - avoid single-letter clipping/initial artifacts
   - ensure names and model labels do not dominate objectives
   - keep objective text readable on the physical display
3. Keep Brain Feed as hero.
4. Keep Today's Jobs as right rail.
5. Preserve lower insight dock visibility.
6. Do not commit volatile data files.

## Validation commands
Use this PATH first:
```bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
```

Then run:
```bash
python3 scripts/mission_control_regression_check.py
python3 scripts/mission_control_screenshot_diff.py --max-diff-ratio 0.08
git diff --check
```

If visual changes are intentional and screenshot diff fails, inspect screenshots/current, then update baselines only after confirming layout:
```bash
python3 scripts/mission_control_screenshot_diff.py --update-baseline
python3 scripts/mission_control_screenshot_diff.py --max-diff-ratio 0.08
```

## Guardrails
- Josh authorized this continuation task to push/merge if validation passes and changed files are expected.
- Do not commit `data/jaimes-brain-feed.json` or other live feed/cache churn.
- Prefer one focused commit on branch `joshex/agent-board-polish`.
- Keep UI dense and glanceable; no big empty cards.

## Validation status from this pass
- `mission_control_regression_check.py`: passed.
- `mission_control_screenshot_diff.py --max-diff-ratio 0.08`: passed after intentional baseline update.
- `git diff --check`: passed.
- Live-data browser probe: rendered 3 Brain Feed cards with `cutoff=False`.

## Known issue
Physical screenshot over SSH failed with:
```text
could not create image from display
```
Treat that as a macOS session/screencapture issue, not proof the UI failed. Browser DOM and GitHub Pages markers verified live.
