#!/usr/bin/env python3
"""Refresh Control Tower FinOps from the JAIMES Robinhood Chain wallet.

#JAIMES: This is the canonical read-only Robinhood wallet publisher. It replaces
Solana wallet telemetry and publishes masked identity, balances, all recent
activity, and per-trade realized/unrealized P&L without exposing signer data.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

WALLET = "0xa8Fed22B1DF934370B7E9E0F611a3A894Fc257d8"
WALLET_L = WALLET.lower()
WETH = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
WETH_L = WETH.lower()
BLOCKSCOUT = "https://robinhoodchain.blockscout.com/api/v2"
EXPLORER = "https://robinhoodchain.blockscout.com"
ROOT = Path.home() / ".openclaw/workspace/mission-control"
RAW = Path.home() / ".openclaw/private/mission-control/agentic-crypto-wallet-raw.json"
OUT = ROOT / "data/agentic-crypto-wallet.json"
DASH = ROOT / "data/dashboard-data.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 JAIMES-Control-Tower/1.0",
    "Accept": "application/json",
    "Origin": "https://docs.robinhood.com/",
    "Referer": "https://docs.robinhood.com/",
}
TRADE_METHODS = {"exactinputsingle", "exactinput", "swapexacttokensfortokens", "swapexacttokensforeth", "swapexactethfortokens"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def get_json(url: str, attempts: int = 3) -> Any:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed after {attempts} attempts: {url}: {last}")


def paged(path: str, max_pages: int = 12) -> list[dict[str, Any]]:
    url = f"{BLOCKSCOUT}{path}"
    rows: list[dict[str, Any]] = []
    for _ in range(max_pages):
        payload = get_json(url)
        if not isinstance(payload, dict):
            break
        rows.extend(row for row in (payload.get("items") or []) if isinstance(row, dict))
        nxt = payload.get("next_page_params")
        if not isinstance(nxt, dict) or not nxt:
            break
        base = url.split("?", 1)[0]
        prior = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True)
        keys = set(nxt)
        query = [(k, v) for k, v in prior if k not in keys]
        query.extend((k, str(v)) for k, v in nxt.items())
        url = base + "?" + urllib.parse.urlencode(query)
    return rows


def address_of(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("hash") or value.get("address_hash") or "").lower()
    return str(value or "").lower()


def token_meta(row: dict[str, Any]) -> dict[str, Any]:
    token = row.get("token") if isinstance(row.get("token"), dict) else {}
    return {
        "address": str(token.get("address_hash") or ""),
        "name": str(token.get("name") or "Unknown token"),
        "symbol": str(token.get("symbol") or "?")[:28],
        "decimals": int(token.get("decimals") or 0),
        "type": str(token.get("type") or row.get("token_type") or ""),
        "exchange_rate": float(token.get("exchange_rate") or 0),
    }


def token_amount(row: dict[str, Any]) -> float:
    meta = token_meta(row)
    raw = row.get("value")
    if raw is None and isinstance(row.get("total"), dict):
        raw = row["total"].get("value")
        try:
            meta["decimals"] = int(row["total"].get("decimals") or meta["decimals"])
        except Exception:
            pass
    try:
        return int(raw or 0) / (10 ** int(meta["decimals"] or 0))
    except Exception:
        return 0.0


def dex_prices(addresses: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for start in range(0, len(addresses), 30):
        batch = addresses[start:start + 30]
        if not batch:
            continue
        try:
            payload = get_json("https://api.dexscreener.com/latest/dex/tokens/" + ",".join(batch))
        except Exception:
            continue
        best: dict[str, tuple[float, float]] = {}
        for pair in (payload.get("pairs") or []) if isinstance(payload, dict) else []:
            if str(pair.get("chainId") or "").lower() != "robinhood":
                continue
            base = address_of((pair.get("baseToken") or {}).get("address"))
            if not base:
                base = str((pair.get("baseToken") or {}).get("address") or "").lower()
            try:
                price = float(pair.get("priceUsd") or 0)
                liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
            except Exception:
                continue
            if price > 0 and (base not in best or liquidity > best[base][0]):
                best[base] = (liquidity, price)
        for address, (_, price) in best.items():
            prices[address] = price
    return prices


def transfer_groups(transfers: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in transfers:
        tx = str(row.get("transaction_hash") or "")
        if tx:
            groups[tx].append(row)
    return groups


def trade_events(transfers: list[dict[str, Any]], prices: dict[str, float], eth_usd: float) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for tx, rows in transfer_groups(transfers).items():
        method = str(rows[0].get("method") or "").lower()
        if method not in TRADE_METHODS:
            continue
        outgoing: list[tuple[dict[str, Any], dict[str, Any], float]] = []
        incoming: list[tuple[dict[str, Any], dict[str, Any], float]] = []
        for row in rows:
            meta = token_meta(row)
            if meta["type"] != "ERC-20":
                continue
            amount = token_amount(row)
            if amount <= 0:
                continue
            if address_of(row.get("from")) == WALLET_L:
                outgoing.append((row, meta, amount))
            if address_of(row.get("to")) == WALLET_L:
                incoming.append((row, meta, amount))
        out_weth = next((x for x in outgoing if x[1]["address"].lower() == WETH_L), None)
        in_weth = next((x for x in incoming if x[1]["address"].lower() == WETH_L), None)
        if out_weth:
            asset = next((x for x in incoming if x[1]["address"].lower() != WETH_L), None)
            side = "open"
            eth_amount = out_weth[2]
        elif in_weth:
            asset = next((x for x in outgoing if x[1]["address"].lower() != WETH_L), None)
            side = "close"
            eth_amount = in_weth[2]
        else:
            continue
        if not asset:
            continue
        row, meta, units = asset
        trades.append({
            "id": f"rh-{tx.lower()}",
            "timestamp": str(row.get("timestamp") or ""),
            "sourceAgent": "jaimes",
            "side": side,
            "chain": "robinhood",
            "asset": meta["symbol"],
            "pair": f"WETH/{meta['symbol']}",
            "action": f"{'Bought' if side == 'open' else 'Sold'} {meta['symbol']}",
            "amount": f"{eth_amount:.6f} WETH {'→' if side == 'open' else '←'} {units:,.6f} {meta['symbol']}",
            "valueUsd": round(eth_amount * eth_usd, 2),
            "status": "confirmed",
            "txHash": tx,
            "explorerLabel": f"RH tx {tx[:8]}…{tx[-6:]}",
            "explorerUrl": f"{EXPLORER}/tx/{tx}",
            "tokenAddress": meta["address"],
            "tokenUnits": units,
            "ethAmount": eth_amount,
            "tokenPriceUsd": prices.get(meta["address"].lower(), meta["exchange_rate"]),
        })
    trades.sort(key=lambda row: row.get("timestamp") or "")

    lots: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for trade in trades:
        token = str(trade["tokenAddress"]).lower()
        if trade["side"] == "open":
            lots[token].append({"remaining": float(trade["tokenUnits"]), "cost_eth": float(trade["ethAmount"]), "row": trade})
            continue
        sell_units = float(trade["tokenUnits"])
        proceeds = float(trade["ethAmount"])
        matched_cost = 0.0
        matched_units = 0.0
        while sell_units > 1e-18 and lots[token]:
            lot = lots[token][0]
            take = min(sell_units, lot["remaining"])
            unit_cost = lot["cost_eth"] / max(lot["remaining"], 1e-30)
            cost = take * unit_cost
            matched_cost += cost
            matched_units += take
            lot["remaining"] -= take
            lot["cost_eth"] -= cost
            sell_units -= take
            if lot["remaining"] <= 1e-12:
                lots[token].popleft()
        if matched_units > 0:
            allocated_proceeds = proceeds * (matched_units / float(trade["tokenUnits"]))
            pnl_eth = allocated_proceeds - matched_cost
            trade["pnlUsd"] = round(pnl_eth * eth_usd, 2)
            trade["pnl"] = f"{pnl_eth:+.6f} ETH realized"
        else:
            trade["pnl"] = "P&L n/a · transferred basis"

    for token, queue in lots.items():
        price = prices.get(token, 0)
        for lot in queue:
            row = lot["row"]
            if price > 0 and lot["remaining"] > 0:
                mark_usd = lot["remaining"] * price
                cost_usd = lot["cost_eth"] * eth_usd
                row["pnlUsd"] = round(mark_usd - cost_usd, 2)
                row["pnl"] = f"{(mark_usd - cost_usd) / max(eth_usd, 1e-9):+.6f} ETH unrealized"
            else:
                row["pnl"] = "P&L pending live mark"

    for trade in trades:
        for key in ("tokenAddress", "tokenUnits", "ethAmount", "tokenPriceUsd"):
            trade.pop(key, None)
    return trades


def activity_rows(transactions: list[dict[str, Any]], trades: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    trade_by_tx = {row.get("txHash"): row for row in trades}
    recent: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    all_activity: list[dict[str, Any]] = []
    for tx in transactions:
        tx_hash = str(tx.get("hash") or "")
        if tx_hash in trade_by_tx:
            row = dict(trade_by_tx[tx_hash])
            ledger.append(row)
            activity = {
                "timestamp": row["timestamp"], "action": row["action"], "status": row["status"],
                "chain": "robinhood", "valueSummary": row["amount"],
                "explorerLabel": row["explorerLabel"], "explorerUrl": row["explorerUrl"], "sourceAgent": "jaimes",
            }
            recent.append(activity)
            all_activity.append(activity)
            continue
        method = str(tx.get("method") or "transaction")
        method_l = method.lower()
        if "mint" in method_l:
            action = "NFT mint"
        elif method_l == "approve":
            action = "Token approval"
        elif method_l in {"deposit", "withdraw"}:
            action = "WETH wrap/unwrap"
        elif "transfer" in method_l:
            action = "Transfer"
        else:
            action = method.replace("_", " ").strip().title() or "Contract activity"
        value_eth = int(tx.get("value") or 0) / 1e18
        fee_raw = (tx.get("fee") or {}).get("value") if isinstance(tx.get("fee"), dict) else 0
        fee_eth = int(fee_raw or 0) / 1e18
        status = "confirmed" if tx.get("status") == "ok" else str(tx.get("status") or "unknown")
        amount = f"{value_eth:.6f} ETH · gas {fee_eth:.6f} ETH" if value_eth else f"gas {fee_eth:.6f} ETH"
        common = {
            "timestamp": str(tx.get("timestamp") or ""), "status": status, "chain": "robinhood",
            "explorerLabel": f"RH tx {tx_hash[:8]}…{tx_hash[-6:]}", "explorerUrl": f"{EXPLORER}/tx/{tx_hash}",
            "sourceAgent": "jaimes",
        }
        activity = {**common, "action": action, "valueSummary": amount}
        recent.append(activity)
        all_activity.append(activity)
        # Non-trade actions stay in the complete activity journal. The FinOps
        # trade card remains trade-only so its two visible rows always show P&L.
    recent.sort(key=lambda row: row.get("timestamp") or "", reverse=True)
    ledger.sort(key=lambda row: row.get("timestamp") or "", reverse=True)
    all_activity.sort(key=lambda row: row.get("timestamp") or "", reverse=True)
    return recent[:40], ledger[:80], all_activity[:200]


def main() -> int:
    errors: list[str] = []
    address = get_json(f"{BLOCKSCOUT}/addresses/{WALLET}")
    balances = get_json(f"{BLOCKSCOUT}/addresses/{WALLET}/token-balances")
    transactions = paged(f"/addresses/{WALLET}/transactions", max_pages=4)
    transfers = paged(f"/addresses/{WALLET}/token-transfers", max_pages=12)
    if not isinstance(balances, list):
        raise RuntimeError("Blockscout token balances returned an unexpected payload")

    eth_usd = float(address.get("exchange_rate") or 0)
    native_eth = int(address.get("coin_balance") or 0) / 1e18
    erc20_addresses = [token_meta(row)["address"] for row in balances if token_meta(row)["type"] == "ERC-20"]
    dex = dex_prices([address for address in erc20_addresses if address])

    tokens: list[dict[str, Any]] = []
    nfts: list[dict[str, Any]] = []
    prices: dict[str, float] = {}
    token_liquid_usd = 0.0
    for row in balances:
        meta = token_meta(row)
        amount = token_amount(row)
        address_l = meta["address"].lower()
        if meta["type"] == "ERC-20":
            price = dex.get(address_l) or meta["exchange_rate"]
            prices[address_l] = price
            value = amount * price
            if value < 0.01 and address_l != WETH_L:
                continue
            token_liquid_usd += value
            tokens.append({
                "amount": round(amount, 8), "chain": "robinhood", "classification": "core" if address_l == WETH_L else "active-trade",
                "contractMasked": f"{meta['address'][:6]}…{meta['address'][-4:]}", "name": meta["name"], "symbol": meta["symbol"],
                "priceUsd": round(price, 10), "priceSource": "DexScreener" if address_l in dex else "Blockscout",
                "source": "robinhood-blockscout-live", "valueUsd": round(value, 2),
            })
        elif meta["type"] in {"ERC-721", "ERC-1155"} and "important alert" not in meta["name"].lower():
            nfts.append({
                "chain": "robinhood", "collection": meta["name"], "symbol": meta["symbol"],
                "count": int(amount), "tokenStandard": meta["type"], "source": "Robinhood Blockscout", "confidence": "on-chain",
            })
    tokens.sort(key=lambda row: row.get("valueUsd") or 0, reverse=True)
    nfts.sort(key=lambda row: row.get("count") or 0, reverse=True)

    trades = trade_events(transfers, prices, eth_usd)
    recent_activity, ledger, all_activity = activity_rows(transactions, trades)
    ts = now()
    native_usd = native_eth * eth_usd
    liquid_usd = token_liquid_usd + native_usd
    sidecar: dict[str, Any] = {
        "updatedAt": ts,
        "status": "fresh",
        "walletMode": "read-only",
        "refreshMode": "live-robinhood-chain",
        "lastFullRefreshAt": ts,
        "wallets": {"evmMasked": f"{WALLET[:6]}…{WALLET[-4:]}", "primaryChain": "Robinhood Chain"},
        "summary": {
            "freshnessStatus": "fresh", "lastRefreshed": ts,
            "nativeLiquidUsd": round(native_usd, 2), "tokenLiquidUsd": round(token_liquid_usd, 2),
            "liquidEstimatedUsd": round(liquid_usd, 2), "nftEstimatedUsd": 0,
            "totalEstimatedUsd": round(liquid_usd, 2),
        },
        "chains": [{
            "chain": "robinhood", "chainId": 4663, "gasBalance": round(native_eth, 9), "gasSymbol": "ETH",
            "gasValueUsd": round(native_usd, 2), "estimatedGasBudgetUsd": round(native_usd, 2),
            "gasStatus": "ready" if native_eth >= 0.003 else "low", "source": "robinhood-blockscout-live",
        }],
        "tokens": tokens,
        "nfts": nfts,
        "approvals": [],
        "recentActivity": recent_activity,
        "activityLedger": all_activity,
        "tradeLedger": ledger,
        "tradingGoal": {
            "title": "Robinhood wallet ledger", "description": "Balance, all activity, and per-trade P&L refresh every five minutes.",
            "current": len(ledger), "target": max(len(ledger), 1), "unit": "trades", "status": "live", "updatedAt": ts,
        },
        "guardrails": {
            "chainAllowlist": ["Robinhood Chain"], "requiresHumanApproval": True, "simulationRequired": True,
            "swapsRequireApproval": True, "mintingRequiresApproval": True, "stakingRequiresApproval": True,
            "bridgingRequiresApproval": True, "unknownContractWritesBlocked": True,
        },
        "opportunities": [{
            "actionType": "Read-only refresh", "chain": "robinhood", "estimatedCost": "$0",
            "expectedBenefit": "Keeps balance, holdings, activity, and trade P&L current every five minutes.",
            "requiredApproval": "none", "riskLevel": "low", "simulationStatus": "live",
        }],
        "errors": errors,
    }

    raw = json.loads(RAW.read_text()) if RAW.exists() else {"note": "Private local inventory cache. Do not publish."}
    raw["addresses"] = {"evm": WALLET}
    raw["publicSidecar"] = sidecar
    raw["updatedAt"] = ts
    atomic(RAW, raw)
    atomic(OUT, sidecar)
    if DASH.exists():
        dash = json.loads(DASH.read_text())
        dash["agenticCryptoWallet"] = sidecar
        dash["lastUpdated"] = ts
        atomic(DASH, dash)
    print(json.dumps({
        "ok": True, "updatedAt": ts, "walletMasked": sidecar["wallets"]["evmMasked"],
        "liquidEstimatedUsd": sidecar["summary"]["liquidEstimatedUsd"], "tokenCount": len(tokens),
        "nftCollections": len(nfts), "activityRows": len(recent_activity), "ledgerRows": len(ledger),
        "tradeRows": sum(1 for row in ledger if row.get("side") in {"open", "close"}),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
