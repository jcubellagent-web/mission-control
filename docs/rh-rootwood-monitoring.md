# Rootwood Monitoring Runbook

- Contract: `0x8D2301C19050bA61C1bA722EFa8a339bADD554Df`
- Wallet: `0xa8Fed22B1DF934370B7E9E0F611a3A894Fc257d8`
- Inventory baseline: 50 Rootlings, IDs 15344–15353, 15414–15423, 15484–15513.
- Mint cost: 0.000027 ETH each; aggregate mint value 0.00135 ETH; observed gas 0.000080887 ETH.

## Mechanics

Rootwood is a verified ERC-721 / ERC-7420 collection with 100,000 maximum supply. Phase one mints Rootlings. After the creator calls `launch`, the contract deploys the ROOT token and seeds an internal constant-product ETH/ROOT curve. Each Rootling can then be burned through `swapToTokens(tokenId)` for 500 ROOT. Swapping is irreversible. ROOT purchases and sales charge a 1% protocol fee.

## Alerts

The monitor runs every five minutes and stays silent unless material state changes:

- owned Rootling count changes;
- ROOT launch occurs;
- mint crosses 25%, 50%, 75%, 90%, 95%, or 100%;
- observed redemption quote moves at least 20%;
- first secondary sales appear in the one-hour window.

After launch, it records the ROOT contract, spot price, and aggregate quote for converting and selling all currently owned Rootlings. It never burns or sells automatically.
