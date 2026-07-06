#JAIMES: imagegen backend was unavailable, so this captures the implementable Control Tower redesign direction from the approved workflow.

# Control Tower Imagegen Redesign Spec — 2026-07-06

## Source
- Live kiosk screenshot captured from Josh 2.0: `~/agent-work/control-tower-imagegen/control-tower-live.png`
- Target surface: React kiosk in `v2-react/`
- Constraint: preserve current kiosk/operator information architecture.

## Imagegen status
Attempted three image-to-image redesign generations using the live screenshot. The Hermes image generation provider is not currently usable because `FAL_KEY` is not set and no managed imagegen provider is configured in this session.

## Concept direction to use once imagegen is enabled
Use the screenshot as the source image and ask for a practical UI reference, not concept art:

1. **Operator command center**
   - Preserve: Live Work Board left, FinOps below, Flight Deck + Priority Queue center, status rails right.
   - Improve: one dominant takeaway per panel, calmer chip density, stronger section headers.
   - Avoid: SaaS landing-page styling, decorative illustration, fake charts.

2. **Bloomberg × spacecraft wall**
   - Preserve dark kiosk posture and dense data rows.
   - Use crisp typography, quiet metadata, modular panels.
   - FinOps becomes provider/wallet/route/trade cards with shared anatomy.

3. **Maximum scan speed**
   - Preserve blue/green/amber/red state language.
   - Simplify pill walls into status/source/attention/action groups.
   - Emphasize large value lockups and stable row primitives.

## Implemented first cleanup slice
Because imagegen was blocked, JAIMES implemented the low-risk structure-first slice that the generated concepts would likely demand anyway:

- Added FinOps provider card anatomy classes:
  - `finops-anatomy-card`
  - `provider-card-head`
  - `provider-card-blurb`
  - `provider-card-primary`
  - `provider-card-support`
  - `provider-card-actions`
- Structured the route strip as label/value chips.
- Removed two local `!important` declarations from provider pill/evidence white-space rules.
- Added a short `#JAIMES:` handoff note near the touched FinOps component.

## Follow-up slice implemented
After Josh approved the next pass, JAIMES tightened the wallet/trade rail:

- Changed the wallet headline from total estimated value to **liquid wallet** value.
- Marked NFT/collectible value as excluded from the headline.
- Added wallet card anatomy hooks: `wallet-card-primary`, `wallet-card-secondary`.
- Gave recent trade rows slightly stronger scan rhythm and PnL treatment.
- Rebuilt and regression-checked before deploy.

## Brain hero primitive slice
After the next approval, JAIMES tightened the Brain hero card primitive:

- Replaced a dead flight-deck column override with explicit agent-card row anatomy.
- Kept the existing operator layout, copy, and live status logic intact.
- Preserved the 164px objective readout row and 28px support note row.
- Rebuilt and regression-checked before deploy.

## Next visual pass
Once imagegen is enabled, generate 2–3 screenshots from this spec, choose the least decorative concept, then continue with:

1. Today's Jobs focus-row rewrite.
2. Chip severity role cleanup.
3. FinOps route strip de-clutter if it still feels noisy.
4. Reduce remaining late-stage `!important` overrides by area.
