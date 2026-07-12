# SWOOD Medium-Term Trade Card

- Token: SWOOD / Sherwood Exchange
- Contract: `0xb1cb27f78b7335df8c3d8ebf0881a15bed6beb60`
- Pair: `0xabc83c3f04c3dec51ce32f8aa83be281e1b27dad`
- Lane: Virtuals ecosystem
- Horizon: medium, 3–7 days
- Size: `0.003 WETH`
- Route: `WETH -> VIRTUAL -> SWOOD`
- Router: verified UniswapV2Router02 for the pair factory
- Entry round-trip impact at validation: approximately `1.23%`
- Slippage cap: `6%`
- Exact finite allowances only

## Management

- Soft thesis failure: `-18%` plus one-hour breakdown
- Hard invalidation: `-28%`
- TP1: trim 25% at `+35%`
- TP2: trim 25% at `+80%`
- TP3: trim 25% at `+150%`
- Final 25% is the runner
- Review after 48 hours
- Exit or promote at day 7
- Absolute ecosystem hold limit: 14 days

The dedicated monitor is read-only and alerts Crypto Alerts only when the action state changes. It does not sell silently.
