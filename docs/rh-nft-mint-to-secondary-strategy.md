# Robinhood NFT Mint-to-Secondary Strategy

## Discovery

- Primary feed: OpenSea Robinhood mint activity.
- Secondary feed: OpenSea Robinhood sales activity.
- Collection-only feed rows are hydrated from on-chain ERC-721 mint logs.
- Every alert is labeled Robinhood Chain.

## Mint gate

Mint at most one NFT per contract only when all gates pass:

- Mint is free or at most `$0.10`.
- Gas-inclusive total cost is at most `$0.15`.
- Public mint calldata is verified and bounded to one NFT.
- At least 12 recent mints from at least 8 collectors.
- At least 2 qualified collectors.
- Wash-risk heuristic is at most 25%.
- Supply and sellout proximity are verified.
- No repeated mint from the same contract.

## Position management

- Default hold: 0–48 hours.
- Extend to 3–7 days only with real secondary demand.
- Absolute maximum hold: 14 days.
- Record token IDs and cost basis from the mint receipt.

## Secondary exit ladder

- Prepare bid acceptance when a verified bid clears 2× basis and is at least 75% of floor.
- Prepare a near-floor listing when verified secondary demand exists; target at least 3× basis.
- At day 7, prioritize capital recycling.
- At day 14, exit at the best verified market available.

The scanner, guarded mint executor, receipt-based position ledger, and secondary exit planner are deployed. OpenSea listing/bid execution requires an authenticated Seaport order-signing/submission lane; until configured, the system emits an exact exit plan instead of fabricating a listing.
