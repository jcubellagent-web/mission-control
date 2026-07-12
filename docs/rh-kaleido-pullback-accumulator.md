# KALEIDO One-Share Pullback Accumulator

Standing objective: accumulate exactly 1.0 KALEIDO, which automatically creates one mirrored Kaleido Share NFT, only during a major pullback while on-chain attention remains active.

## Tranches

| Balance target | Maximum price | Minimum drawdown |
|---|---:|---:|
| 0.25 | 0.070 ETH | 35% |
| 0.50 | 0.060 ETH | 45% |
| 0.75 | 0.052 ETH | 52% |
| 1.00 | 0.045 ETH | 58% |

## Mandatory gates

- Maximum aggregate spend: 0.06 ETH.
- Liquidity: at least $100,000.
- One-hour volume: at least $50,000.
- Five-minute volume: at least $5,000.
- One-hour trades: at least 400.
- One-hour buyers: at least 100.
- Round-trip route impact: at most 6%.
- Fresh execution candidate: at most 180 seconds old.
- Sufficient WETH balance.

The peak is ratcheted upward, so drawdowns are measured against the highest observed price. Orders use exact finite WETH allowance and a 6% minimum-output guard. The target stops permanently at 1.0 KALEIDO; the NFT is a mirror of the whole-token balance, not a separate free asset.
