# Control Tower Architecture + UI Audit — JOSHeX Execution Brief

**Author:** JAIMES (GLM-5.2)
**Date:** 2026-06-25
**Status:** For JOSHeX review and execution

## Context

Josh requested a deep architecture and front-end/UI audit of Control Tower. JAIMES performed the audit but was reminded that JOSHeX owns `v2-react/` UI source. This document is the handoff — JOSHeX owns implementation.

## Current State

- Build: passes (`npm run build`)
- Regression: passes (`python3 scripts/mission_control_regression_check.py`)
- Path guard: passes (`npm run doctor:paths`)
- Live kiosk: JOSH 2.0 at `http://127.0.0.1:5174/` — HTTP 200
- Visual design quality: 9/10 (excellent dark-mode layout, contrast, hierarchy)

## Findings by Severity

### P0 — Architecture

| Issue | Detail | Impact |
|---|---|---|
| Monolith `main.tsx` | 6,013 lines, 48 components in one file | Unmaintainable, slow IDE, merge conflicts |
| Monolith `styles.css` | 17,726 lines, 1,964 `!important` declarations | Cascade broken, override hell |
| Duplicated CSS selectors | 97 selectors defined multiple times (e.g. `.calendar-empty-block` 12x, `.control-tower-grid` 9x) | Conflicting rules, unpredictable rendering |

### P1 — Code Quality

| Issue | Detail | Impact |
|---|---|---|
| Hardcoded colors | 1,167 unique colors despite 100 CSS custom properties | Token system bypassed, no theming |
| Unused CSS tokens | 32 of 100 tokens never referenced | Dead design tokens |
| Zero `React.memo` | No component memoization | Every parent re-render re-renders all 48 children |
| Zero error boundaries | No crash protection | Single component error kills entire dashboard |
| Only 2 `useMemo` | Heavy computed values recalculated each render | Performance waste on always-on display |
| 22 `@keyframes` animations | Continuous GPU work | Burn on 24" always-on display |

### P1 — Data Layer

| Issue | Detail |
|---|---|
| Schema mismatch | `MissionControlState` types expect `statuses[]`, `events[]`, `jobs[]` — all empty on live dashboard |
| Real data location | `brainFeed` (dict), `liveObjectives` (dict), `activeAgents` (list), `agentBrainFeeds` (3 lanes) |
| Type coverage | `types.ts` has 396 lines but doesn't model 30+ top-level dashboard keys |
| Missing types | `capabilityStack`, `capabilityWatch`, `capabilityInventory`, `reliabilityUpgrades`, `calendarHealth`, `mcpInventory`, `semanticMemory`, `sorareMlCockpit`, `voiceRouter`, `telegramAiBotFeatures` |

### P2 — UI Polish

| Issue | Detail | Fix |
|---|---|---|
| Text truncation | Multiple strings cut off with ellipses on 24" display | Increase truncation length or use tooltips |
| "CODEX SPA..." labels | Two separate Codex Spark windows (`5-hour` + `Weekly`) both truncated to same prefix | Longer labels or abbreviated display |
| Ollama Pro blank gap | Large blank space when no active sessions | Add "No active sessions" empty state |
| "NEEDS JOSH" not disruptive | Shares same styling as routine metrics when idle | Add red/orange pulse when action required |
| "SYSTEM: All clear" conflict | Shows "All clear" while JOSHeX is `OFFLINE • 5d` | Reconcile state logic — `missionFocusCount()` returns 0 because `state.statuses` is empty (data schema mismatch) |
| Ollama/Grok columns sparse | Asymmetric density vs Codex/Gemini | Balance or make responsive |

### P3 — Accessibility

| Issue | Severity |
|---|---|
| 78 aria attributes but only 2 role attributes | P2 — interactive elements need roles |
| No skip-to-content link | P3 |
| No focus-visible styles | P3 |

## Component Inventory (48 components)

### By module group:

- **brain-feed** (5): BrainFeed (844 lines!), BrainCostCard (665 lines), BrainHero, BrainAttentionStrip, BrainOperationsSummary
- **finops** (5): ResourceStack, ModelUsageCard, AgenticCryptoPanel, MetricMini, Metric
- **flight-deck** (8): AgentFlightDeck, PriorityQueuePanel, AgentHeroCard, AgentHandoffBeams, AgentRail, AgentOpsMiniDashboard, AgentEcosystemMap, ApprovalInbox
- **signal-feed** (2): SignalFeed, SignalFeedRows
- **jobs** (12): DailyJobsCalendar, CalendarJobBlockCard, CalendarNextBrief, CalendarPhaseStrip, SorareDailyJobsPanel, SorareJobLine, JobCategoryView, JobFocusRow, JobFocusView, JobTableHeader, JobsRail, UpcomingJobRow
- **shared** (14): SectionCue (499 lines!), MissionHealthPanel, MissionTimeline, ModelRoutingLadderVisual, ReliabilityUpgradesPanel, RuntimeCapabilityPanel, SchedulerInventoryDisclosure, PipelineCard, EmptyRow, WorkItemRow, TowerAgentRow, ActivityLedger, ActivityLedgerRow, AgentWorkBoard
- **control-tower** (2): ControlTower, TowerCommandStrip
- **root** (1): App

## Recommended Refactor Plan

### Phase 1: Split the monolith (P0)
```
v2-react/src/
  components/
    brain-feed/BrainFeed.tsx
    finops/FinOpsDashboard.tsx
    flight-deck/AgentFlightDeck.tsx
    signal-feed/SignalFeed.tsx
    jobs/DailyJobsCalendar.tsx
    shared/SectionCue.tsx
  hooks/
  utils/format.ts
  types/internal.ts
  constants.ts
  App.tsx
```

### Phase 2: Fix CSS architecture (P0)
- Remove 1,964 `!important` by fixing specificity order
- Consolidate 1,167 hardcoded colors → use 100 tokens exclusively
- Delete 32 unused tokens
- Merge 97 duplicated selectors

### Phase 3: Data layer alignment (P1)
- Update `MissionControlState` to model all 40+ top-level keys
- Fix `missionFocusCount()` to read from `brainFeed`/`activeAgents` when `statuses` is empty
- Add TypeScript strict types for all dashboard keys

### Phase 4: Performance (P1)
- Wrap heavy components in `React.memo` (BrainFeed, BrainCostCard, ModelRoutingLadder)
- Extract computed values into `useMemo`
- Add error boundary around root
- Audit 22 animations for GPU cost

### Phase 5: UI polish (P2)
- Fix truncation thresholds for 24" display
- Add "No active sessions" empty states
- Make "NEEDS JOSH" visually disruptive (pulse animation) when active
- Reconcile "SYSTEM: All clear" with stale agent warnings
- Abbreviate Codex Spark window labels

## What JAIMES Owns (not touching v2-react/)

- Trade ledger/event publication scripts
- Wallet refresh helpers
- Sorare/Hermes execution
- Scheduled jobs
- Data-helper scripts
- Brain Feed publishing

## Data Flow Notes

The `data.ts` `loadFallback()` function reads from `brain-feed.json`, `personal-codex.json`, and `dashboard-data.json`. It pulls agent statuses from `jaimesBrainFeed`, `jainBrainFeed`, and the brain feed sidecar. The live data on JOSH 2.0 shows these are populated:

- `jaimesBrainFeed`: active=True, objective="Control Tower Phase 2+5..."
- `jainBrainFeed`: active=False, status="ready"
- `joshBrainFeed`: active=False, status="done"

But `state.statuses` ends up empty because the `loadFromSupabase()` path may be failing silently and the fallback normalization may not be mapping fields correctly. This is worth investigating as part of Phase 3.

## Bundle Stats

- `main.tsx`: 266KB source
- `styles.css`: 388KB source
- Build output: 94KB JS (gzip) + 49KB CSS (gzip) = 143KB total
- 1,582 modules transformed
- Build time: ~700ms