# Control Tower FinOps and Live Work legibility QA

## Scope and evidence

- Source visual: `/var/folders/1w/ndj9slyx5rj80g5pn88wkp9c0000gn/T/codex-clipboard-a57cbba6-b61b-44cd-b12b-028c4cd2f03b.png`
- Implementation capture: `/private/tmp/control-tower-legibility-final-clear.png`
- Full before/after comparison: `/private/tmp/control-tower-legibility-full-comparison.png`
- Focused FinOps comparison: `/private/tmp/control-tower-legibility-finops-comparison.png`
- Browser and viewport: the existing Josh 2.0 Google Chrome kiosk at 1920 x 1080, DPR 1.
- State reviewed: live sidecars with four Live Work agents, the complete FinOps surface, Today's Jobs visible in the first viewport, and the transient black-box alert cleared by a successful verification rerun.

## Visual history

1. The source pass left about 56 px of unused space below FinOps, rendered important provider and ledger text at 6.8-9 px, reduced idle-card opacity, and forced the model ledger into cramped single-line columns.
2. The first correction filled the FinOps body and widened its wallet rail, but older high-specificity rules still overrode provider typography and opacity.
3. The final correction raised selector specificity, compacted repetitive metadata, changed the model ledger to a two-line hierarchy, and increased Live Work typography while preserving all four fixed-height rows.
4. The source and final implementation were reviewed together in both full-page and focused FinOps comparisons. The final surface materially improves hierarchy, contrast, density, and use of the available panel area without changing the approved matte-blue layout.

## Final measurements

- Page: 1920 x 1080 with zero page overflow.
- Live Work: four 1171 x 73 px cards; objective 24 px, agent name 19 px, description 12.5 px, completion text 12 px; no measured clipping.
- FinOps: 1199 x 506 px panel; 1177 x 435 px body with only the intended 9 px bottom inset.
- FinOps columns: 270 px wallet rail and 899 px model area.
- Provider cards: four 268 x 136 px cards; heading 14 px, description 11 px, metadata 10 px; idle opacity 90%; no clipping.
- Model ledger: 333 x 278 px; title 13 px, model name 11.5 px, metadata 10 px; no horizontal overflow; bounded vertical scrolling is available for the complete ledger.
- Wallet: total 27 px, section headings 11.5 px, transaction and journal rows 10 px.

## Interaction and runtime checks

- FinOps `Refresh wallet` was exercised in the actual kiosk Chrome. The button moved from enabled to disabled with a spinner and back to enabled.
- FinOps, body, wallet, and model-area geometry changed by 0 px during the interaction; page overflow remained 0 px and before/after pixels were identical.
- The public wallet publisher returned HTTP 500 because the dedicated hosts currently resolve the official Blockscout hostname through an inaccessible local DNS route. Direct execution confirmed connection refusal after three retries. This is an external data-source/DNS blocker, not a layout regression; the last successful wallet snapshot stayed visible and unchanged.
- Rendered-browser checks passed for semantics, horizontal layout, visible-text quality, and screenshots. There were no page exceptions, failed asset requests, or internal-text leaks. The only browser console entry was the expected HTTP 500 from the unavailable wallet source.
- Source regression check passed, runtime-layout self-test passed, the new permanent 1920 x 1080 legibility probe passed, and the repository suite passed 406 tests.

## Residual P2 observations

- Long wallet transaction detail strings still ellipsize inside the deliberately compact 270 px rail. Full detail remains available through the linked explorer row; a future expand/tooltip treatment could improve this without enlarging the rail.
- The smallest 9.5-10 px role and metadata text is secondary information. Primary operational text meets the distance-readability thresholds.

final result: passed
