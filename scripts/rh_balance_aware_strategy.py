#!/usr/bin/env python3
"""Read-only balance-aware RH strategy controller.

Builds the live account snapshot, evaluates promotion gates, ranks tactical
candidates, and emits approval-ready trade cards. It never signs or broadcasts.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RPC = "https://rpc.mainnet.chain.robinhood.com"
WALLET = "0xa8Fed22B1DF934370B7E9E0F611a3A894Fc257d8"
WETH = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
CASHCAT = "0x020bfc650a365f8bb26819deaabf3e21291018b4"
CONFIG = Path("/Users/jc_agent/.hermes/config/rh_balance_aware_strategy_v2.json")
SHADOW = Path("/Users/jc_agent/reports/rh_profitability_shadow_gate_latest.json")
SHADOW_LATEST = Path("/Users/jc_agent/reports/rh_edge_shadow_latest.json")
ARCHETYPES = Path("/Users/jc_agent/reports/rh_strategy_archetype_review_latest.json")
ENRICH = Path("/Users/jc_agent/reports/rh_signal_enrichment_latest.json")
POSITIONS = Path("/Users/jc_agent/reports/rh_autonomous_positions.json")
OUT = Path("/Users/jc_agent/reports/rh_balance_aware_strategy_latest.json")
JOURNAL = Path("/Users/jc_agent/reports/rh_strategy_decision_journal.jsonl")
HEADERS = {"User-Agent": "Mozilla/5.0 JAIMES", "Origin": "https://docs.robinhood.com/", "Referer": "https://docs.robinhood.com/"}


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def request_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def rpc(method: str, params: list[Any]) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body, headers={**HEADERS, "Content-Type": "application/json"})
    result = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result["result"]


def token_balance(token: str) -> float:
    data = "0x70a08231" + WALLET.lower().removeprefix("0x").rjust(64, "0")
    return int(rpc("eth_call", [{"to": token, "data": data}, "latest"]), 16) / 1e18


def dex_price(token: str) -> dict[str, Any]:
    pairs = (request_json(f"https://api.dexscreener.com/latest/dex/tokens/{token}").get("pairs") or [])
    exact = [p for p in pairs if str((p.get("baseToken") or {}).get("address", "")).lower() == token.lower()]
    rows = exact or pairs
    if not rows:
        return {}
    p = max(rows, key=lambda row: float((row.get("liquidity") or {}).get("usd") or 0))
    return {
        "price_usd": float(p.get("priceUsd") or 0),
        "market_cap_usd": float(p.get("marketCap") or 0),
        "liquidity_usd": float((p.get("liquidity") or {}).get("usd") or 0),
        "price_change": p.get("priceChange") or {},
        "pair": p.get("pairAddress"),
    }


def eth_price() -> float:
    return float(request_json("https://api.coinbase.com/v2/prices/ETH-USD/spot")["data"]["amount"])


def account_snapshot() -> dict[str, Any]:
    native = int(rpc("eth_getBalance", [WALLET, "latest"]), 16) / 1e18
    weth = token_balance(WETH)
    cashcat = token_balance(CASHCAT)
    eth_usd = eth_price()
    cashcat_market = dex_price(CASHCAT)
    cashcat_usd = cashcat * float(cashcat_market.get("price_usd") or 0)
    liquid = native + weth
    total = liquid * eth_usd + cashcat_usd
    reserve = float(load(CONFIG, {}).get("capital_policy", {}).get("native_gas_reserve_eth", 0.005))
    return {
        "wallet": WALLET,
        "native_eth": native,
        "weth": weth,
        "liquid_eth_weth": liquid,
        "deployable_after_reserve_eth": max(0.0, liquid - reserve),
        "cashcat_units": cashcat,
        "cashcat_market": cashcat_market,
        "eth_usd": eth_usd,
        "liquid_usd": liquid * eth_usd,
        "cashcat_usd": cashcat_usd,
        "total_usd": total,
        "cashcat_concentration_pct": (100 * cashcat_usd / total) if total else 0.0,
    }


def promotion(cfg: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    shadow = load(SHADOW, {})
    archetypes = load(ARCHETYPES, {})
    thresholds = cfg.get("promotion") or {}
    stats = shadow.get("statistical_validation") or {}
    boot = stats.get("bootstrap") or {}
    mc = stats.get("monte_carlo") or {}
    rebound = (archetypes.get("archetypes") or {}).get("MEAN_REVERSION_REBOUND") or {}
    checks = {
        "liquid_for_one_entry": account["deployable_after_reserve_eth"] >= float((cfg.get("capital_policy") or {}).get("entry_eth", 0.003)),
        "closed_trades": int(shadow.get("closed_count") or 0) >= int(thresholds.get("closed_trades_min", 30)),
        "positive_expectancy_probability": float(boot.get("probability_expectancy_positive") or 0) >= float(thresholds.get("positive_expectancy_probability_min", 0.75)),
        "profit_factor": float(shadow.get("profit_factor") or 0) >= float(thresholds.get("profit_factor_min", 1.3)),
        "p95_drawdown": float(mc.get("max_drawdown_entry_units_p95") or 999) <= float(thresholds.get("p95_drawdown_entry_units_max", 3.0)),
        "winner_concentration": float(shadow.get("top_winner_contribution") or 1) <= float(thresholds.get("top_winner_contribution_max", 0.5)),
        "rebound_sample": int(rebound.get("closed") or 0) >= int(thresholds.get("archetype_closed_min", 20)),
        "rebound_profit_factor": float(rebound.get("profit_factor") or 0) >= 1.25,
        "rebound_not_concentrated": float(rebound.get("top_winner_contribution") or 1) <= 0.5,
    }
    return {
        "ready": all(checks.values()),
        "checks": checks,
        "shadow_status": shadow.get("status"),
        "metrics": {
            "closed": shadow.get("closed_count"),
            "positive_expectancy_probability": boot.get("probability_expectancy_positive"),
            "profit_factor": shadow.get("profit_factor"),
            "p95_drawdown_entry_units": mc.get("max_drawdown_entry_units_p95"),
            "top_winner_contribution": shadow.get("top_winner_contribution"),
            "rebound_closed": rebound.get("closed"),
            "rebound_profit_factor": rebound.get("profit_factor"),
        },
    }


def clean_open_positions() -> dict[str, Any]:
    positions = load(POSITIONS, {"open": {}})
    actual = {k: v for k, v in (positions.get("open") or {}).items() if str(v.get("status") or "OPEN").upper() == "OPEN" and int(v.get("balance_raw") or 1) > 0}
    stale = [k for k in (positions.get("open") or {}) if k not in actual]
    return {"actual_open_count": len(actual), "actual_open": actual, "stale_rows": stale}


def rank_candidates(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots = list((load(SHADOW_LATEST, {}).get("snapshots") or []))
    positions = load(POSITIONS, {"closed": []})
    history: dict[str, list[float]] = {}
    for closed in positions.get("closed") or []:
        token = str(closed.get("token") or "").lower()
        if token:
            history.setdefault(token, []).append(float(closed.get("last_pnl_pct") or 0))
    ranked = []
    for row in snapshots:
        token = str(row.get("token") or "").lower()
        blockers = list(row.get("blockers") or [])
        prior = history.get(token, [])
        avg_prior = sum(prior) / len(prior) if prior else None
        if prior and avg_prior is not None and avg_prior <= -15:
            blockers.append("historical_negative_edge")
        checks = row.get("qualification_checks") or {}
        missing_checks = [k for k, passed in checks.items() if not passed]
        mcap = float(row.get("mcap_usd") or 0)
        edge = float(row.get("edge_score_raw") or 0)
        percentile = float(row.get("edge_percentile") or 0)
        settled = bool(row.get("settled_rebound"))
        route = 0.0
        for level in row.get("depth_ladder") or []:
            if abs(float(level.get("entry_eth") or 0) - 0.003) < 1e-9:
                route = float(level.get("reverse_ratio") or 0)
        shape = settled and 50_000 <= mcap <= 1_500_000 and percentile >= .90 and edge >= 90 and route >= .94
        qualified = bool(row.get("qualified")) and not blockers and shape
        ranked.append({
            "token": token,
            "symbol": row.get("symbol"),
            "lane": str(row.get("lane") or ("C_SETTLED_REBOUND" if settled else "WATCH_ONLY")),
            "edge_score": edge,
            "edge_percentile": percentile,
            "mcap_usd": mcap,
            "reverse_ratio_003": route,
            "net_eth_5m": row.get("net_eth_5m"),
            "sells_5m": row.get("sells_5m"),
            "eligible_shape": shape,
            "qualified_now": qualified,
            "missing_checks": missing_checks,
            "blockers": blockers,
            "prior_attempts": len(prior),
            "prior_avg_pnl_pct": avg_prior,
            "entry_eth": float((cfg.get("capital_policy") or {}).get("entry_eth", 0.003)),
            "slippage_assumption_pct": 6.0,
            "invalidation": "-22% hard or reclaim/base failure",
            "status": "SHADOW_READY" if qualified else ("NEAR_READY" if shape and not blockers else "WATCH_ONLY"),
        })
    ranked.sort(key=lambda x: (x["qualified_now"], x["eligible_shape"], not x["blockers"], x["edge_percentile"], x["edge_score"]), reverse=True)
    return ranked[:5]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=["json", "summary"], default="json")
    ap.add_argument("--journal", action="store_true")
    args = ap.parse_args()
    cfg = load(CONFIG, {})
    account = account_snapshot()
    gate = promotion(cfg, account)
    positions = clean_open_positions()
    candidates = rank_candidates(cfg)
    result = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "strategy_id": cfg.get("strategy_id"),
        "mode": "SHADOW_CANARY",
        "account": account,
        "positions": positions,
        "promotion": gate,
        "live_entry_authorized": False,
        "authorization_reason": "manual activation required after every statistical gate passes" if gate["ready"] else "statistical promotion gate failed",
        "inventory_action": "MONITOR_CASHCAT_NO_ADD_NO_AUTOMATIC_SELL",
        "candidates": candidates,
        "policy": {"signing": False, "broadcast": False, "incremental_api_spend_usd": 0},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True))
    if args.journal:
        with JOURNAL.open("a") as f:
            f.write(json.dumps({"at": result["as_of"], "decision": result["authorization_reason"], "top_candidate": candidates[0] if candidates else None}, sort_keys=True) + "\n")
    if args.format == "summary":
        print(f"{result['mode']} | ${account['total_usd']:.2f} total | {account['liquid_eth_weth']:.6f} ETH/WETH liquid | gate={'PASS' if gate['ready'] else 'FAIL'}")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
