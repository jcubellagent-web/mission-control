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

## Next visual pass
Once imagegen is enabled, generate 2–3 screenshots from this spec, choose the least decorative concept, then continue with:

1. FinOps wallet/trade card anatomy alignment.
2. Brain hero primitive cleanup.
3. Today's Jobs focus-row rewrite.
4. Chip severity role cleanup.
