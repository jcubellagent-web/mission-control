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
