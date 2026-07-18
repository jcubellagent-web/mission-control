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
