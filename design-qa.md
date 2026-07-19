# Control Tower reference-shaped FinOps QA

## Scope and evidence

- Source visual: `/var/folders/1w/ndj9slyx5rj80g5pn88wkp9c0000gn/T/codex-clipboard-e8afc5a8-90c3-4459-9903-b0e337563f7d.png`
- Final live panel: `/private/tmp/control-tower-finops-simple/control-tower-finops-simple-final2-panel.png`
- Same-input comparison: `/private/tmp/control-tower-finops-simple/control-tower-finops-simple-final-comparison.png`
- Browser and viewport: the existing Josh 2.0 Google Chrome kiosk at 1920 x 1080, DPR 1.
- State reviewed: current local sidecars, verified route activity, current wallet freshness, twelve tracked models with nine displayed ledger rows, and the complete Control Tower first viewport.

## Reference match

1. Replaced the dense wallet trade and activity feeds with one quiet wallet summary and four proposal-first action rows.
2. Consolidated three competing telemetry areas into exactly two continuous metric bands: five primary values and four secondary values.
3. Reduced each provider to identity, current model, utilization, one purpose line, explicit state, and one segmented meter.
4. Replaced nested model cards with one nine-row table and a truthful shown/total count.
5. Added one continuous four-cell health rail using live or explicitly unavailable values; no reference-only numbers were fabricated.
6. Reviewed the source and live implementation together in the same comparison image, then corrected provider purpose and current-model overflow.

## Final measurements

- Page: 1920 x 1080 with zero horizontal or vertical overflow.
- FinOps panel: 1199 x 506 px with zero panel overflow.
- Wallet rail: 224 x 352 px with four action rows and no visible transaction/activity feeds.
- Metric bands: two 946 x 45 px bands with cell counts `[5, 4]`.
- Provider grid: four route-mapped cards, each approximately 283 x 124 px; provider name 16 px, body 11 px, metadata 10-11.5 px; no measured clipping or overflow.
- Model ledger: 365 x 253 px with nine approximately 23.7 px rows; no horizontal or vertical overflow.
- Health rail: 1177 x 78 px with four equal cells and no overflow.
- Canonical route colors: Codex `#65D1D5`, Antigravity `#72D69A`, Ollama `#A8ABB3`, Grok `#1677FF`.

## Interaction and runtime checks

- Read-only `Refresh wallet` issued exactly one request, entered disabled/`aria-busy=true`/`Refreshing…` immediately, recovered to enabled within nine seconds, and moved no major region by even one pixel.
- The upstream wallet refresh returned HTTP 500. The last-known balance remained visible and the header continued to say `Wallet stale`; no false fresh state or layout shift was introduced.
- Strict browser and visual guard passed at 1440 x 1000, 390 x 844, and 1920 x 1080 with zero console errors, page errors, failed asset requests, or internal-text leaks.
- Permanent layout guard now fails closed on the old dense hierarchy, missing regions, wrong metric/provider counts, route-color drift, text/card overflow, ledger overflow, missing health rail, undersized rows, and wallet-width drift.
- Build passed, regression check passed, route-contract check passed, runtime-layout self-test passed, strict runtime/visual check passed, and the repository suite passed 397 tests.
- The in-app Browser plugin path was unavailable because its required Node REPL bridge was not exposed; verification used the user-selected, already-open Josh 2.0 Chrome kiosk through its existing CDP endpoint.

## Severity review

- P0: none.
- P1: none.
- P2: none.

final result: passed

---

# Control Tower Brain Atlas proof-recovery and text-fit QA

## Scope and evidence

- User-reported source capture: `/var/folders/1w/ndj9slyx5rj80g5pn88wkp9c0000gn/T/codex-clipboard-d6caf64a-988f-4b49-8490-84adfe5240a3.png`
- Final physical-kiosk Atlas crop: `/private/tmp/brain-atlas-polish-active-atlas-1920.png`
- Final full physical-kiosk capture: `/private/tmp/brain-atlas-polish-active-kiosk-1920.png`
- Same-input before/after comparison: `/private/tmp/brain-atlas-polish-comparison.png`
- Measured physical-kiosk report: `/private/tmp/brain-atlas-polish-active-report.json`
- Browser and viewport: the existing Josh 2.0 Google Chrome kiosk at 1920 x 1080, DPR 1. The user-selected kiosk was inspected through its existing CDP 9224 endpoint; agent-auth Chrome was not touched.

## Proof recovery

1. Confirmed the lower lane was not empty and the receipt source was not offline. The canonical graph was ready, but the dashboard presentation boundary rejected one work node that combined receipts from two owners.
2. Scoped work-node identity to owner, stable work ID, and execution generation. Historical handoffs now remain separate exact audit paths without deleting or rewriting receipts.
3. Added the same single-owner invariant to the producer validator and added a cross-owner, cross-generation generator-to-sanitizer regression.
4. Added a release check that fails when a ready canonical Brain Atlas graph is downgraded to an unavailable dashboard projection.

## Text and spacing polish

1. Added hard SVG clip regions to agent, memory-stage, named-work, and verified-model copy so future values cannot cross a node border.
2. Replaced variable-length active subtitles with the distance-readable `ACTIVE · WORKING` state; the Live Work Board remains the task-detail source.
3. Separated agent title and status baselines, removed one redundant context pill, raised the header line box, and corrected the metric-number line height.
4. Added permanent HTML and SVG text-fit measurements, including node-border containment and title/detail overlap checks.

## Final physical measurements

- Page: 1920 x 1080 with zero horizontal or vertical overflow.
- Brain Atlas: 1208.45 x 510.73 px, fully in the initial viewport with a visible 2 px bottom inset.
- Unified map: 1166.45 x 295.73 px with 95% substantive horizontal fill.
- Text containment: 0 HTML overflows, 0 SVG node-text overflows, 0 title/detail overlaps, and unclipped purpose copy.
- Exact proof: ready; 100 bounded nodes, 141 exact edges, 59 displayed receipts, and 3 recent verified paths. All nine visible proof edges remain static.
- Active-state parity: JOSHeX appeared as working in both Live Work and Brain Atlas. Aura, presence dot, and header beacon changed across the motion sample; quiet agents remained still.

## Runtime and regression checks

- Production React build passed.
- Focused Brain Atlas, dashboard projection, and runtime-layout suite passed: 104 tests. The full repository release suite passed: 693 tests.
- Physical kiosk probe recorded zero exceptions, failed requests, page overflow, text overflow, or Live Work parity mismatches.
- The in-app Browser plugin path was unavailable because its required Node REPL bridge was not exposed; QA used the user's already-open physical Josh 2.0 kiosk.

## Severity review

- P0: none.
- P1: none.
- P2: none.

final result: passed

# Control Tower alert truth and matte-blue FinOps QA

## Scope and evidence

- User-reported source capture: `/tmp/control-tower-alert-finops-reference.png`
- Final live kiosk capture: `/tmp/control-tower-alert-finops-audited-kiosk-1920.png`
- Same-input before/after comparison: `/tmp/control-tower-alert-finops-comparison.png`
- Browser and viewport: the existing Josh 2.0 Google Chrome kiosk at 1920 x 1080, DPR 1, plus strict checks at 1440 x 1000 and 390 x 844.
- State reviewed: fresh local sidecars after Wi-Fi recovery, successful QA status and Deep release QA runs, one real nonblocking inbox-system item, active Codex route telemetry, current wallet data, and the full first viewport.

## Alert truth corrections

1. Traced the original red `Decision` to two Wi-Fi/DNS-era JAIMES job failures plus recursively aggregated QA state. Both real jobs were rerun successfully before any alert was cleared.
2. Marked aggregate QA jobs with stable IDs and separated QA evidence from operational failures, preserving the audit trail without allowing the QA rollup to fail itself recursively.
3. Limited `Decision` to explicit approval metadata. Operational items now render through a separate `System` lane with watch/risk severity and never silently disappear.
4. Preserved Personal Codex `kind` and `requiresApproval` metadata end to end so validation and system issues cannot be promoted into false approvals.
5. Made the health sweep authoritative in the release benchmark when the live projection is missing or between refreshes.

## FinOps palette review

1. Replaced decorative amber panel borders, dividers, controls, labels, and neutral icons with the existing matte-blue/cyan/slate Control Tower palette.
2. Preserved semantic green for healthy/wallet state, red for true risk, purple for watch state, and the canonical provider colors: Codex `#65D1D5`, Antigravity `#72D69A`, Ollama `#A8ABB3`, and Grok `#1677FF`.
3. The final comparison shows no remaining amber cast in the FinOps dashboard and no loss of hierarchy, provider identity, or health semantics.

## Runtime and release checks

- QA status and Deep release QA both completed `ok` with failure streaks cleared.
- Live payload uses production serialization and measured below the 250 KB hard limit.
- Strict browser/visual verification passed at desktop, mobile, and 1920 kiosk widths with zero page overflow, console errors, page errors, failed requests, clipped Live Work text, FinOps overflow, or internal-text leaks.
- FinOps measured four provider cards, two metric bands with `[5, 4]` cells, a nine-row model ledger, four health cells, and zero panel overflow.
- Today’s Jobs retained a centered current-time marker and a reason target for every non-green summary and row.
- React build passed, regression checker passed, runtime-layout self-test passed, full Deep release QA passed, and the repository suite passed 432 tests.

## Severity review

- P0: none.
- P1: none.
- P2: none.

final result: passed

---

# Control Tower Brain Atlas distance-readability QA

## Scope and evidence

- User reference capture: `/var/folders/1w/ndj9slyx5rj80g5pn88wkp9c0000gn/T/codex-clipboard-3c2a46e6-6fee-4d64-8068-8ec2d2d940d3.png`
- Final 1920 x 1080 implementation crop: `/private/tmp/brain-atlas-distance-final-atlas-1920.png`
- Final same-input before/after comparison: `/private/tmp/brain-atlas-distance-final-comparison.png`
- Full kiosk capture: `/private/tmp/brain-atlas-distance-final-kiosk-1920.png`
- Browser and viewport: isolated Google Chrome verification at 1920 x 1080, DPR 1, plus strict checks at 1440 x 1000, 390 x 844, 2048 x 1228, and 1920 x 1080 reduced motion.
- State reviewed: current live Control Tower sidecars, one or more truthfully active Live Work agents, governed-memory telemetry, three recent exact execution paths, and the complete first viewport.

## Visual and interaction corrections

1. Replaced the letterboxed 1000 x 376 graph coordinate space with a 1464 x 376 layout and proportionally remapped every node, edge, work card, receipt, and verified-model path. Text and circles retain their aspect ratio; no nonuniform SVG stretching is used.
2. Increased substantive graph use from roughly 66% to 97% of the available map width at the physical 1920 x 1080 kiosk size.
3. Strengthened the distance hierarchy with a larger Brain Atlas title, a dedicated working-count beacon, brighter persistent active-node shells, larger live-agent labels, a visible presence dot, and faster evidence-bound motion.
4. Replaced contradictory `working · idle` language with `working · memory quiet` when an agent is active but no governed-memory receipt is currently moving.
5. Active agent nodes now show the current dashboard-safe work title. Named exact-proof rows remain readable and static; opaque work IDs are not exposed.
6. Shortened and normalized the section-purpose copy so it stays fully visible without competing with the right-side state chips.
7. The verified-work selector still changes the selected exact-proof row and retains its accessible label/description relationship.

## Final measurements

- Brain Atlas panel: 1208.45 x 510.73 px; FinOps matched its top and height exactly.
- Unified map: 1166.45 x 300.77 px with zero panel or page overflow.
- Substantive horizontal fill: 97% at 1920 x 1080 and 96% or better at the 2048 reference and reduced-motion probes.
- Active agent title: 18 px; active detail: 13.5 px; lane labels: 12.5 px; section heading: 14 px; purpose copy: 10.5 px.
- Current Live Work agents and animated Brain Atlas agents matched exactly in every tested state.

## Motion, truth, and runtime checks

- Across a 650 ms live sample, the active-agent aura, presence dot, and header beacon changed opacity and scale while the persistent node shell remained visible.
- All nine exact-proof edges reported `animation-name: none`; quiet agents had no aura or presence animation.
- Reduced-motion mode disabled the aura, presence-dot, header-beacon, and memory-edge animations while preserving the active-state styling.
- Governed-memory edge animation remains conditional on a recent exact registry timestamp. No current timestamp means no moving memory path.
- The horizontal-fill guard measures substantive nodes rather than the decorative full-width layer divider, preventing a false pass if the graph becomes cramped again.
- Strict browser and visual verification passed with zero console errors, page errors, failed requests, bad HTTP responses, internal-text leaks, or desktop/kiosk page overflow.
- Focused Brain Atlas/runtime tests passed: 85. Runtime-layout self-test and the production React build also passed.

## Severity review

- P0: none.
- P1: none.
- P2: none.

final result: passed

---

# Control Tower Today’s Jobs outcome-clarity QA

## Scope and evidence

- User-reported source capture: `/Users/joshcubell/.codex/attachments/7ac6a090-1b93-492a-9a0b-301aed03e1ec/image-1.png`
- Final live kiosk capture: `/private/tmp/control-tower-todays-jobs-clarity/todays-jobs-final-accurate-kiosk-1920.png`
- Same-input before/after comparison: `/private/tmp/control-tower-todays-jobs-clarity/todays-jobs-before-after.png`
- Hover-state evidence: `/private/tmp/control-tower-todays-jobs-clarity/todays-jobs-tooltip-hover.png`
- Browser and viewport: the existing Josh 2.0 Google Chrome kiosk at 1920 x 1080, DPR 1, plus strict checks at 1440 x 1000 and 390 x 844.
- State reviewed: current native schedule inventory and current scheduler evidence at 11:03 AM ET on 2026-07-18.

## Outcome and timeline corrections

1. Replaced the ambiguous `Pending` summary with `Open` and an explicit breakdown. At final verification it read `23 scheduled · 28 unverified`; the full hover explanation accounted for all 51 open rows.
2. Limited green to fresh, successful evidence. Loaded services with no timestamp now read `Loaded`; sources without per-run history read `Unverified`. Neither is counted complete or broken.
3. Kept source-reported misses/failures and verifiable overdue or stale signals red with specific row labels: Missed, Failed, Overdue, Stale, Timeout, or Blocked.
4. Added a visible help icon, help cursor, keyboard focus, and fixed-position explanatory tooltip to every non-green summary and row. Complete rows remain quiet.
5. Kept the `NOW` rail centered to the pixel in the scroll viewport. Automatic follow is smooth after the first positioning pass, pauses for 55 seconds after manual navigation, and becomes immediate when reduced motion is requested.
6. Stabilized interval schedules at a deterministic daily anchor and rolled interval-only latest-result sources into one coverage row. A new result can no longer rewrite earlier times or occurrence IDs.
7. Made exact per-slot evidence outrank a definition’s latest status, limited latest-only failures to the latest due slot, made disabled state outrank stale failure state, and preserved raw failure provenance.

## Final data audit

- 113 visible recurring-job rows: 49 complete, 7 intentionally skipped, 6 broken, and 51 open.
- Open accounting: 23 scheduled later, 0 currently running/active, 28 outcome-unverified, and 0 still inside the grace window.
- Unsupported green rows: 0. Every green row had a current success timestamp and at least one verified completion signal.
- Contradictory broken rows with Scheduled, Due, Loaded, or Unverified status: 0.
- Exact interval-history, blocked-status, disabled-status, raw-failure-provenance, and per-slot-precedence cases have dedicated regression tests.

## Interaction, accessibility, and runtime checks

- All 67 non-green tooltip targets passed mouse hover and keyboard focus; Escape dismissal and viewport bounds passed. No tooltip emitted `[object Object]` or internal data.
- The centered `NOW` marker measured 0 px from the scrolling viewport center. Manual scroll position held for the full 12-second interaction test across live refreshes.
- Summary and rows expose their reason through `aria-label` and `aria-describedby`; direct rowgroup children retain valid table/row semantics.
- Strict browser and visual checks passed at 1440 x 1000, 390 x 844, and 1920 x 1080 with zero console errors, page errors, failed requests, page overflow, or internal-text leaks.
- Build and regression checks passed. Repository tests passed: 402 pytest tests plus 9 projection self-tests.

## Severity review

- P0: none.
- P1: none.
- P2: none.

final result: passed
