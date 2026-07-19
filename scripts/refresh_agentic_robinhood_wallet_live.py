#!/usr/bin/env python3
"""Refresh Control Tower FinOps from the JAIMES Robinhood Chain wallet.

#JAIMES: This is the canonical read-only Robinhood wallet publisher. It replaces
Solana wallet telemetry and publishes masked identity, balances, all recent
activity, and per-trade realized/unrealized P&L without exposing signer data.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import math
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
USDG = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
USDG_L = USDG.lower()
BLOCKSCOUT = "https://robinhoodchain.blockscout.com/api/v2"
EXPLORER = "https://robinhoodchain.blockscout.com"
DEX_CHAIN = "robinhood"
DEX_ANCHOR_RATIO_LIMIT = 3.0
DEX_CONSENSUS_RATIO_LIMIT = 3.0
DEX_MIN_UNANCHORED_LIQUIDITY_USD = 100.0
ROOT = Path.home() / ".openclaw/workspace/mission-control"
RAW = Path.home() / ".openclaw/private/mission-control/agentic-crypto-wallet-raw.json"
OUT = ROOT / "data/agentic-crypto-wallet.json"
DASH = ROOT / "data/dashboard-data.json"
REFRESH_LOCK_PATH = Path.home() / ".openclaw/private/mission-control/agentic-crypto-wallet-refresh.lock"
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


def finite_positive(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if math.isfinite(parsed) and parsed > 0 else 0.0


def finite_nonnegative(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Invalid wallet value for {field}") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise RuntimeError(f"Invalid wallet value for {field}")
    return parsed


def valid_decimals(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if 0 <= parsed <= 36 else None


def token_meta(row: dict[str, Any]) -> dict[str, Any]:
    token = row.get("token") if isinstance(row.get("token"), dict) else {}
    token_type = str(token.get("type") or row.get("token_type") or "")
    decimals = valid_decimals(token.get("decimals"))
    if decimals is None and token_type in {"ERC-721", "ERC-1155"}:
        decimals = 0
    return {
        "address": str(token.get("address_hash") or ""),
        "name": str(token.get("name") or "Unknown token"),
        "symbol": str(token.get("symbol") or "?")[:28],
        "decimals": decimals,
        "type": token_type,
        "exchange_rate": finite_positive(token.get("exchange_rate")),
    }


def token_amount(row: dict[str, Any]) -> float:
    meta = token_meta(row)
    total = row.get("total") if isinstance(row.get("total"), dict) else {}
    raw = row.get("value")
    if raw is None:
        raw = total.get("value")
    override = valid_decimals(total.get("decimals"))
    if meta["type"] == "ERC-20" and override is not None:
        meta["decimals"] = override
    if meta["decimals"] is None:
        return 0.0
    try:
        amount = int(raw or 0) / (10 ** meta["decimals"])
        return amount if math.isfinite(amount) and amount >= 0 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def pair_volume_usd(pair: dict[str, Any]) -> float:
    volume = pair.get("volume") if isinstance(pair.get("volume"), dict) else {}
    return finite_positive(volume.get("h24"))


def pair_liquidity_usd(pair: dict[str, Any]) -> float:
    liquidity = pair.get("liquidity") if isinstance(pair.get("liquidity"), dict) else {}
    return finite_positive(liquidity.get("usd"))


def select_dex_price(pairs: list[dict[str, Any]], address: str, anchor: float = 0.0) -> float:
    """Select a corroborated price instead of trusting one reported-liquidity maximum."""
    target = address.lower()
    candidates: list[dict[str, float | bool]] = []
    for pair in pairs:
        if str(pair.get("chainId") or "").lower() != DEX_CHAIN:
            continue
        base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
        quote = pair.get("quoteToken") if isinstance(pair.get("quoteToken"), dict) else {}
        if address_of(base.get("address")) != target:
            continue
        price = finite_positive(pair.get("priceUsd"))
        liquidity = pair_liquidity_usd(pair)
        if not price or not liquidity:
            continue
        candidates.append({
            "price": price,
            "liquidity": liquidity,
            "volume": pair_volume_usd(pair),
            "trustedQuote": address_of(quote.get("address")) in {WETH_L, USDG_L},
        })
    if not candidates:
        return 0.0

    anchored = finite_positive(anchor)
    if anchored:
        lower = anchored / DEX_ANCHOR_RATIO_LIMIT
        upper = anchored * DEX_ANCHOR_RATIO_LIMIT
        candidates = [row for row in candidates if lower <= float(row["price"]) <= upper]
        if not candidates:
            return 0.0

    trusted = [row for row in candidates if bool(row["trustedQuote"])]
    if trusted:
        candidates = trusted
    elif not anchored:
        return 0.0

    ordered = sorted(candidates, key=lambda row: float(row["price"]))
    clusters: list[list[dict[str, float | bool]]] = []
    for row in ordered:
        if not clusters or float(row["price"]) > float(clusters[-1][0]["price"]) * DEX_CONSENSUS_RATIO_LIMIT:
            clusters.append([row])
        else:
            clusters[-1].append(row)
    candidates = max(
        clusters,
        key=lambda cluster: (
            len(cluster),
            sum(float(row["volume"]) for row in cluster),
            sum(float(row["liquidity"]) for row in cluster),
        ),
    )
    if not anchored and len(ordered) > 1 and len(candidates) == 1:
        return 0.0
    if not anchored and len(candidates) == 1 and float(candidates[0]["liquidity"]) < DEX_MIN_UNANCHORED_LIQUIDITY_USD:
        return 0.0
    best = max(candidates, key=lambda row: (float(row["volume"]), float(row["liquidity"])))
    return float(best["price"])


def dex_prices(addresses: list[str], anchors: dict[str, float] | None = None) -> tuple[dict[str, float], set[str]]:
    """Fetch every token independently so a many-pool token cannot crowd out the rest."""
    prices: dict[str, float] = {}
    rejected: set[str] = set()
    seen: set[str] = set()
    anchors = anchors or {}
    for address in addresses:
        address_l = str(address or "").lower()
        if not address_l or address_l in seen:
            continue
        seen.add(address_l)
        try:
            payload = get_json(f"https://api.dexscreener.com/token-pairs/v1/{DEX_CHAIN}/{address}")
        except Exception:
            continue
        pairs = payload if isinstance(payload, list) else []
        price = select_dex_price(pairs, address_l, anchors.get(address_l, 0.0))
        if price:
            prices[address_l] = price
        elif any(
            str(pair.get("chainId") or "").lower() == DEX_CHAIN
            and address_of((pair.get("baseToken") or {}).get("address")) == address_l
            and finite_positive(pair.get("priceUsd"))
            and pair_liquidity_usd(pair)
            for pair in pairs
            if isinstance(pair, dict)
        ):
            rejected.add(address_l)
    return prices, rejected


def unique_token_balances(balances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse exact duplicate ERC-20 rows without conflating same-symbol contracts."""
    output: list[dict[str, Any]] = []
    seen: dict[str, float] = {}
    for row in balances:
        meta = token_meta(row)
        address = meta["address"].lower()
        if meta["type"] != "ERC-20" or not address:
            output.append(row)
            continue
        amount = token_amount(row)
        if address in seen:
            if not math.isclose(amount, seen[address], rel_tol=1e-12, abs_tol=1e-12):
                raise RuntimeError(f"Conflicting duplicate ERC-20 balance for {address[:8]}…")
            continue
        seen[address] = amount
        output.append(row)
    return output


def validate_wallet_sidecar(sidecar: dict[str, Any]) -> None:
    summary = sidecar.get("summary") if isinstance(sidecar.get("summary"), dict) else {}
    native = finite_nonnegative(summary.get("nativeLiquidUsd"), "nativeLiquidUsd")
    token_summary = finite_nonnegative(summary.get("tokenLiquidUsd"), "tokenLiquidUsd")
    nft_summary = finite_nonnegative(summary.get("nftEstimatedUsd"), "nftEstimatedUsd")
    tokens = sidecar.get("tokens") if isinstance(sidecar.get("tokens"), list) else []
    token_total = 0.0
    for index, row in enumerate(tokens):
        if not isinstance(row, dict):
            raise RuntimeError(f"Invalid wallet token row {index}")
        finite_nonnegative(row.get("amount"), f"tokens[{index}].amount")
        finite_nonnegative(row.get("priceUsd"), f"tokens[{index}].priceUsd")
        token_total += finite_nonnegative(row.get("valueUsd"), f"tokens[{index}].valueUsd")
    liquid = finite_nonnegative(summary.get("liquidEstimatedUsd"), "liquidEstimatedUsd")
    total = finite_nonnegative(summary.get("totalEstimatedUsd"), "totalEstimatedUsd")
    rounding_tolerance = 0.005 * (len(tokens) + 1) + 1e-9
    if not math.isclose(token_summary, token_total, rel_tol=0, abs_tol=rounding_tolerance):
        raise RuntimeError("Wallet token total failed published-row reconciliation")
    if not math.isclose(liquid, native + token_summary, rel_tol=0, abs_tol=0.015000001):
        raise RuntimeError("Wallet total failed native-plus-token reconciliation")
    if not math.isclose(total, liquid + nft_summary, rel_tol=0, abs_tol=0.015000001):
        raise RuntimeError("Wallet total failed liquid-plus-NFT reconciliation")


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


def refresh_wallet() -> int:
    errors: list[str] = []
    pricing_warnings: list[str] = []
    address = get_json(f"{BLOCKSCOUT}/addresses/{WALLET}")
    balances = get_json(f"{BLOCKSCOUT}/addresses/{WALLET}/token-balances")
    transactions = paged(f"/addresses/{WALLET}/transactions", max_pages=4)
    transfers = paged(f"/addresses/{WALLET}/token-transfers", max_pages=12)
    if not isinstance(balances, list):
        raise RuntimeError("Blockscout token balances returned an unexpected payload")

    eth_usd = finite_positive(address.get("exchange_rate"))
    native_eth = int(address.get("coin_balance") or 0) / 1e18
    balances = unique_token_balances(balances)
    erc20_addresses = [token_meta(row)["address"] for row in balances if token_meta(row)["type"] == "ERC-20"]
    anchors = {
        token_meta(row)["address"].lower(): token_meta(row)["exchange_rate"]
        for row in balances
        if token_meta(row)["type"] == "ERC-20" and token_meta(row)["address"]
    }
    dex, rejected_prices = dex_prices([address for address in erc20_addresses if address], anchors)
    if rejected_prices:
        pricing_warnings.append(f"{len(rejected_prices)} token price source disagreement(s); affected rows withheld")

    tokens: list[dict[str, Any]] = []
    nfts: list[dict[str, Any]] = []
    prices: dict[str, float] = {}
    token_liquid_usd = 0.0
    for row in balances:
        meta = token_meta(row)
        amount = token_amount(row)
        address_l = meta["address"].lower()
        if meta["type"] == "ERC-20":
            price = finite_positive(dex.get(address_l))
            if not price and address_l not in rejected_prices:
                price = meta["exchange_rate"]
            prices[address_l] = price
            value = amount * price
            if not math.isfinite(value) or value < 0:
                continue
            if value < 0.01 and address_l != WETH_L:
                continue
            token_liquid_usd += value
            tokens.append({
                "amount": round(amount, 8), "chain": "robinhood", "classification": "core" if address_l == WETH_L else "active-trade",
                "contractMasked": f"{meta['address'][:6]}…{meta['address'][-4:]}", "name": meta["name"], "symbol": meta["symbol"],
                "priceUsd": round(price, 10), "priceSource": "DexScreener screened" if address_l in dex else "Blockscout",
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
        "status": "attention" if errors else "fresh",
        "walletMode": "read-only",
        "refreshMode": "live-robinhood-chain",
        "lastFullRefreshAt": ts,
        "wallets": {"evmMasked": f"{WALLET[:6]}…{WALLET[-4:]}", "primaryChain": "Robinhood Chain"},
        "summary": {
            "freshnessStatus": "attention" if errors else "fresh", "lastRefreshed": ts,
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
        "pricingWarnings": pricing_warnings,
    }

    validate_wallet_sidecar(sidecar)

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


def main() -> int:
    REFRESH_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = REFRESH_LOCK_PATH.open("a+", encoding="utf-8")
    os.chmod(REFRESH_LOCK_PATH, 0o600)
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        print(json.dumps({"ok": True, "status": "already-running"}, sort_keys=True))
        return 0
    try:
        return refresh_wallet()
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
