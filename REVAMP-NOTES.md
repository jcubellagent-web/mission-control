# Mission Control UI/UX Revamp — `ui-revamp-2026-04-19`

Historical note: this revamp applied to the legacy static `index.html` surface. Current Mission Control work means the Josh 2.0 React kiosk in `v2-react/` at `http://127.0.0.1:5174/`.

One single-file HTML dashboard, 11.8k lines. All revamp changes land as a
**CSS-only overlay layer** appended at the end of the main `<style>` block
(search for `REVAMP LAYER — ui-revamp-2026-04-19`). The layer rides on top
of every existing rule with higher specificity and `!important` where
inline styles forced our hand, so no JS logic, DOM id, or render function
needed to change.

## Hard constraints honored

- Zero changes to JavaScript logic, data flow, SSE/WebSocket wiring.
- Zero removed or renamed `id` attributes.
- No new API calls, no new data sources, no JS-driven insight generation.
- Everything stays in `index.html`.
- Branch `ui-revamp-2026-04-19` only — not merged, not pushed.

## Commits (in order)

| SHA | Phase |
|-----|-------|
| `80a41dcd7` | design-tokens and card-system *(prior subagent)* |
| `cac1ceea1` | brain-feed and system-health polish *(prior subagent)* |
| `4f2625bcf` | jobs-panel |
| `fb2ff2859` | model-usage |
| `17fe0297a` | intel-highlights |
| `c55edb47a` | memory-roadmap |
| `a71db049b` | agent-chat / Activity & Agents |
| `317cdf576` | final-polish (stats row, freshness, night-mode, hover, mobile) |

## Shared tokens added

```css
--rv-surface-0: #0b0b0b;
--rv-surface-1: #101010;
--rv-surface-2: #141414;
--rv-line: rgba(255,255,255,0.07);
--rv-line-soft: rgba(255,255,255,0.045);
--rv-line-strong: rgba(255,255,255,0.12);
--rv-text / --rv-text-dim / --rv-text-muted / --rv-text-label
--rv-accent: #e53e3e       /* only red used for critical/active */
--rv-ok: #4ade80           /* only green, only for healthy status */
```

## Per-module changes & insight surfaced

### Jobs panel (`.card-jobs-full`)
- Agent chips (JOSH 2.0 / JAIMES / J.A.I.N) flattened to one surface, one
  border, one radius. Dots all collapse to a single green when active —
  no more purple / blue color coding. Agent identity is conveyed by the
  text label.
- Category headers forced to neutral off-white (was bright blue / green /
  purple inline `color` attributes overridden via `!important`).
- Row rhythm tightened, run badges flattened (`.tjob-run-*`), `#jobs-updated`
  timestamp now tabular-nums uppercase micro-label.
- **Insight already surfaced in DOM:** the jobs-agents-row now reads as a
  clean status strip at the top — JOSH 2.0 / JAIMES / J.A.I.N with
  freshness text, which is the glance-first “who is up” readout.

### Model Usage (`.card-model`)
- Every gradient on `.mu-budget-fill` and `.mu-lane-card` replaced with
  flat RGBA fills. Budget bar height reduced to 6px.
- `.mu-codexbar-pill` neutralized (was purple) to a plain off-white chip.
- `.mu-active-hero` lost its color-mix gradient and now sits on the
  unified dark `--rv-surface-2`.
- System Health rows (JOSH 2.0 / J.A.I.N / JAIMES) share one surface,
  all active dots are green (healthy), text metrics use tabular-nums.
- **Insight surfaced:** daily/weekly cost numbers + budget score remain
  the hero numbers on the left, provider breakdown below. No new
  highlight added — the existing `.mu-budget-main` already reads as the
  insight once the gradient noise is stripped.

### Breaking Highlights / Intel (`.card-intel-highlights`)
- All category chips (`.intel-hi-cat`) overridden to a single neutral
  chip — **except** chips whose inline style contains `239,68,68` or
  `229,62,62` (the SIGNAL / BREAKING red), which keep the accent.
  This means AI / MACRO / HOT / POLICY / CRYPTO / 𝕏 no longer fight the
  eye; only genuine signal keeps its highlight.
- Skeleton dots (`.ns-cat-blue/green/orange/purple`) unified to neutral
  white-alpha; only `.ns-cat-red` keeps the accent.
- `.live-badge` tightened to the shared accent red so Brain Feed LIVE
  and Breaking Highlights LIVE look identical.
- **Insight surfaced:** the top SIGNAL items still render in red accent
  chips; everything else recedes. One-glance scan of what's breaking.

### Memory Roadmap (`.agent-chat-section` inside `.card-brain`)
- Active-week roadmap cards moved off cyan (`#7dd3fc` / `rgba(14,165,233)`)
  to the red accent, matching the rest of the system.
- All roadmap cards share one dark surface; radii unified to 8px.
- Summary pills (LIVE / ON DECK / DEFERRED) flattened to 3px radius,
  LIVE uses green (healthy), warn uses amber, rest neutral.
- **Insight surfaced:** the current-week pill is the active red card —
  your eye lands on "where are we now" instantly.

### Agent Chat / Activity & Agents (`.ua-*`, `.agent-comms-*`)
- The 4 agent tag variants (`.ua-agent-tag.josh/jain/jaimes/agent`) all
  collapsed to **one** neutral dark chip. Agent identity is text-only.
- Only the `.ua-dot.active` status keeps the accent red (pulse removed,
  replaced with a calm ring).
- `.ua-model-pill` lost its blue — now a neutral chip. `.ua-stale-pill`
  keeps amber (warn is distinct from accent).
- Activity drawer toggle is a clean outlined button.
- **Insight surfaced:** who is currently active reads as a single red
  dot next to the most recent agent; stale rows fade.

### System Health — already polished by prior subagent
- Left as committed in `cac1ceea1`; revamp layer only tightens the row
  surface to match the unified dark palette.

### Final polish (everything else)
- `.stats-row .stat-pill` flattened: 4px radius, uppercase micro-labels.
- `.connection-status` / `.last-updated` tightened with tabular-nums.
- `.alert-banner` radius 8px, calmer accent border.
- `.module-action-btn` (↗ expand buttons) unified to 4px radius, neutral.
- `.bf-freshness-row` 8px radius, smaller label, tabular timestamp.
- `.brain-stream`, `.jobs-shell` unified on `--rv-surface-2`.
- Card hover → a single 1px border lift, no shadow.
- Night-mode overlay stays functional, clock cleaned up.
- Mobile (375px): card radius drop to 10px, chips shrink proportionally,
  agents row re-spaced.

### Buttons work (`buttons_work`)
- Added a CSS-only button layer covering header controls, phone remote buttons,
  card expand buttons, drill cards, modal close buttons, X, and Eight Sleep controls.
- Removed leftover gradient / purple-blue button treatments where the revamp layer can safely override them.
- Stabilized button dimensions, active/hover states, and mobile sizes without changing DOM IDs or JavaScript handlers.

## IDs & classes preserved (nothing removed, nothing renamed)

All DOM `id` attributes the JS reads from are untouched — verified by
diff review before each commit. Notable critical IDs preserved:

- `#brain-feed-card`, `#jain-brain-feed-card`, `#agent-chat-feed`
- `#unified-activity-log`, `#agent-comms-toggle`, `#agent-comms-toggle-meta`
- `#intel-highlights-card`, `#intelligence-feed-card`, `#intel-ticker-wrap`
- `#model-usage-card`, `#system-health-card`
- `#today-jobs-card`, `#jobs-agents-row`, `#jobs-updated`
- `#jag-josh-dot`, `#jag-jaimes-dot`, `#jag-jain-dot`
- `#sh-josh-*`, `#sh-jain-*`, `#sh-jaimes-*`
- `#ctx-header-inline`, `#bf-last-updated`, `#bf-active-model-badge`
- `#god-mode-badge`, `#connection-status`, `#last-updated-text`
- `#night-mode-overlay`, `#night-clock`, `#night-hint`
- All hidden JS-compat spans (`sorare-missions-val`, `fantasy-*`, etc.)

No new classes were added to the DOM at all (we didn't touch the HTML
body); the revamp is pure CSS overrides on existing class names plus
the shared `--rv-*` CSS custom properties.

## Intentionally left alone

- The lobster background pattern (sacred per brief).
- The `god-mode-badge` style — already matches the accent red system.
- The `bf-*` brain feed core typography hierarchy — prior subagent got
  that right, we just tightened the freshness strip around it.
- The `cron-*` legacy class family — kept for backward compatibility,
  not rendered in the main view any more.
- All modal CSS (`.module-modal-*`) — not visible in the default view;
  a follow-up pass could unify those too, but out of scope here.
- JS-side color maps for signal categories (`CAT_COLORS` object in
  `renderIntelHighlights`) — we override via CSS instead of touching JS.

## Testing protocol run

Screenshots at each phase: `screenshots/revamp-<phase>.png`.
Baselines: `screenshots/revamp-before-desktop.png` (1440x900),
`screenshots/revamp-before-mobile.png` (375x812).
Final afters: `screenshots/revamp-after-desktop.png`,
`screenshots/revamp-after-mobile.png`.

No visual breakage or overflow detected at 375 / 768 / 1440.

## How to revert a single phase

Every commit is a self-contained CSS block append at the end of the
main `<style>`; `git revert <sha>` of any single commit restores that
module's previous look without touching the rest.
