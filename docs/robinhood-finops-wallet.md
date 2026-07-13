# Robinhood FinOps Wallet

## Scope

FinOps tracks `0xa8Fed22B1DF934370B7E9E0F611a3A894Fc257d8` on Robinhood Chain. This feed replaces the retired Solana wallet feed.

## Live publisher

- Script: `scripts/refresh_agentic_robinhood_wallet_live.py`
- Sidecar: `data/agentic-crypto-wallet.json`
- Dashboard merge: `data/dashboard-data.json -> agenticCryptoWallet`
- Explorer/API: `https://robinhoodchain.blockscout.com`
- Automatic refresh: `com.josh20.agentic-crypto-refresh`, every five minutes
- Manual refresh: FinOps **Refresh wallet** action invokes the same publisher

## Dashboard records

- **Liquid wallet**: native ETH plus fungible token holdings
- **Trade ledger**: all detected WETH/token swaps in the scanned wallet history
- **Activity journal**: up to 200 latest on-chain actions, including swaps, transfers, approvals, mints, and contract calls
- **P&L**: open positions are marked against live Blockscout prices; closes use FIFO basis when the wallet acquired the position

## Basis policy

A close whose inventory was transferred into this wallet has no defensible acquisition basis inside the tracked wallet. It is displayed as `P&L n/a · transferred basis` rather than fabricating a result. Imported cross-wallet basis can be added later when an authoritative source ledger is available.

## Verification

```bash
npm run doctor:paths
npm run check
npm run build
python3 scripts/refresh_agentic_robinhood_wallet_live.py
```

Expected publisher output includes `ok: true`, a masked wallet ending in `57d8`, current liquid value, activity row count, and trade/P&L row count.
