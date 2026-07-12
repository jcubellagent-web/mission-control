# Robinhood NFT Landscape Monitoring

All material NFT alerts route exclusively to Telegram topic `2131` (NFT Alerts). Routine scans remain silent.

## Coverage

- Chain-wide ERC-721 transfers from Blockscout every two minutes, capturing direct contract mints before marketplace indexing.
- Separate OpenSea Robinhood mint and sale activity feeds.
- Receipt-log hydration for collection-only OpenSea rows.
- Public marketplace pricing snapshots and comparable collections.
- Owned-wallet movement, position basis, holder concentration, contract controls, wash heuristics, and smart-collector history.
- ERC-7420 launch, redemption, and bonded-token lifecycle monitoring.

## Opportunity tiers

1. **Early mint watch** — free/≤$0.10, at least 5 mints/15m, 4 unique collectors, legitimacy ≥55, wash heuristic ≤35%.
2. **Guarded mint ready** — safe public calldata, known supply and sellout proximity, ≥12 mints/15m, ≥8 collectors, ≥2 smart collectors, legitimacy ≥75, wash ≤25%, and gas-inclusive total ≤$0.15.
3. **Secondary traction** — ≥4 verified sales/15m, ≥3 collectors, legitimacy ≥60, wash ≤25%, plus floor/bid/holder analysis when available.
4. **Owned collection** — material mint, sale, launch, valuation, or inventory movement.

The early tier alerts before autonomous eligibility so fast foundational launches are visible without weakening execution safety. The strict guarded mint rules remain unchanged. No unlimited approvals are allowed. Marketplace listing submission requires an authenticated order-signing lane.
