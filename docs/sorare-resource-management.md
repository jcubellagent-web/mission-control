# JAIMES Sorare crafting and XP resource management

JAIMES is the sole runtime owner of Sorare inventory, lineup analytics, and
resource-management work. The workflow runs on JAIMES and must not require
JOSHeX, this laptop, screen sharing, or a local browser session to stay alive.

## Automatic read-only audit

The Sorare fast lane runs `jaimes_sorare_resource_manager.py --mode audit`
every two hours. It uses the authenticated host-local Sorare GraphQL path to:

1. paginate the complete owned Limited inventory;
2. read the current Limited XP reserve, card XP, grade, XP-to-next-grade, XP
   bonus, and cooldown;
3. compare the inventory with the prior private snapshot and flag newly crafted
   or acquired cards;
4. write a private local audit artifact with `reoptimize_required=true` when a
   new card appears.

The initial run establishes a baseline and does not misclassify the entire
inventory as newly acquired. Audit mode cannot submit lineups, spend XP, craft,
bid, offer, transfer cards, or mutate wallet state. Raw authenticated payloads
and credentials never enter shared telemetry.

## Post-craft lineup procedure

When a new card is detected, JAIMES must refresh the full inventory before the
next optimization pass. Compare the new card directly with each current lineup
candidate under the competition's actual eligibility rules. Deploy it only
when it improves the portfolio objective; never force a craft into a lineup.

Championship lineups get first claim on the strongest ceiling resources. Every
Championship lineup remains all in-season except for the strategically selected
single non-in-season card. Model outputs are ranking and portfolio signals, not
guaranteed or expected points. After an approved change, resubmit only affected
entries and then authenticate a full 10/10 card-set diff before reporting success.

## XP allocation procedure

JAIMES may generate a proposed XP plan from current Championship exposure,
card grade, exact XP gap, cooldown, schedule, role certainty, and lineup leverage.
Cheap diversified one-grade improvements can dominate expensive concentration,
but this is a decision signal rather than a fixed rule.

Every XP spend requires all of the following:

- a JSON plan listing each exact card slug and approved `xp_needed`;
- an approved `max_spend`;
- `--execute` plus the exact `APPROVED_BY_JOSH` token;
- a durable approval reference;
- authenticated preflight confirmation that every card is owned, the XP gaps
  have not drifted, no cooldown is active, and the balance covers the batch;
- authenticated post-verification that grade and XP bonus each rose by one
  level, a cooldown was created, and the reserve fell by exactly the spend.

The manager fails closed on any discrepancy. Cooldown prevents another immediate
level-up of that card; it does not prevent a lineup from being submitted again.

## Explicitly out of scope

JAIMES must not automatically craft with essence, spend XP without a newly
approved exact batch, or mutate bids, offers, transfers, cards, lineups, or
wallet state from the recurring audit. Those actions retain their existing
approval and verification controls.
