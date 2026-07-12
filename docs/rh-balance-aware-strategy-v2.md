# RH Balance-Aware Tactical Strategy v2

## Objective

Maximize Robinhood-ecosystem upside without counting illiquid inventory as deployable cash, revenge trading after losses, or promoting an unvalidated strategy to live signing.

## Account baseline — 2026-07-12

- Burner: `0xa8Fed22B1DF934370B7E9E0F611a3A894Fc257d8`
- Native ETH: `0.00690677`
- WETH: `0.01716416`
- Liquid ETH/WETH: `0.02407093`
- CashCat: `950.42173927`
- CashCat full-route quote: `0.08977384 WETH`
- Total mark: approximately `$208`
- CashCat concentration: approximately `79%`

CashCat inventory is excluded from new-entry buying power. The strategy never adds to CashCat and never automatically sells it. It produces review/trim cards only.

## Capital posture

- Mode: `SHADOW_CANARY`
- Native gas reserve: `0.005 ETH`
- Entry size: `0.003 ETH`
- Maximum simultaneous new positions: `2`
- Maximum new exposure: `0.006 ETH`
- No averaging down
- No size increase after losses

## Tactical lanes

1. **Settled rebound** — first priority
   - `$50K-$1.5M` market cap
   - 90th-percentile edge or better
   - 90+ holistic score and 85%+ evidence coverage
   - Two independent confirmations
   - Real sellers and positive net flow
   - 0.94+ executable round-trip ratio

2. **Winner continuation** — second priority
   - `$300K-$1.5M` market cap
   - Reclaim rather than vertical chase
   - 92nd-percentile edge or better
   - 0.95+ executable round-trip ratio

## Promotion gate

Live-entry authorization remains false until all conditions pass:

- At least 30 shadow closes
- Positive-expectancy probability at least 75%
- Profit factor at least 1.30
- P95 drawdown no more than 3 entry units
- Top-winner contribution no more than 50%
- Settled-rebound sample at least 20 closes
- Settled-rebound PF at least 1.25
- Manual activation approval

## Learning loop

Every trade and meaningful no-trade is journaled. The learner labels mistakes by cause—late entry, weak route, stop slippage, stale signal, theme overfit, premature exit, or thesis decay. Challenger strategies remain shadow-only. Automatic changes may tighten gates but never loosen them or increase size.

## Deployment

- Controller: `scripts/rh_balance_aware_strategy.py`
- Policy: `config/rh_balance_aware_strategy_v2.json`
- Operational report: `~/reports/rh_balance_aware_strategy_latest.json`
- Decision journal: `~/reports/rh_strategy_decision_journal.jsonl`
- Local scheduler: every five minutes, no agent, local delivery
- Existing executor has a fail-closed `balance_strategy_not_authorized` gate

The controller is read-only and cannot sign or broadcast.
