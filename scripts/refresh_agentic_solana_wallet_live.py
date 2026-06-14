#!/usr/bin/env python3
"""Live-refresh the dashboard-safe Agentic Wallet sidecar with current Solana holdings.

Preserves non-Solana cached data from the private raw sidecar and replaces Solana
balances/prices with live RPC + Dexscreener/Jupiter data. Never prints secrets.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

ADDR = "J3ABbrV1LFqo3QwckcjqCu3TzeSMiLJr8NQrMDuiMRA"
RPC = "https://api.mainnet-beta.solana.com"
ROOT = Path.home() / ".openclaw/workspace/mission-control"
RAW = Path.home() / ".openclaw/private/mission-control/agentic-crypto-wallet-raw.json"
OUT = ROOT / "data/agentic-crypto-wallet.json"
DASH = ROOT / "data/dashboard-data.json"
PROGRAMS = {
    "spl": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "token2022": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}
ACTIVE_TRADE_MINTS = {
    "BcHEaaTCvycPwwsJ9yQTXdHP9X2gCLkznDbZ8VySpump",  # Jotchua
    "Cm6fNnMk7NfzStP9CZpsQA2v3jjzbcYGAxdJySmHpump",  # Buttcoin
    "B7a9wpdcdSt44Y9iHqrMYo9yuntqtEV25irTS5YRpump",  # xWorld
    "SPCXwBHVrKpRqMRawL3NNvt1sXP2Yf3edwRbta53N69",  # SPCX69
    "2dJniDEAGCG7zWKseCkyrML3W23WLjDf1CGxpNv3pump",  # TURTLE
    "3WjLscH2JsXLEFJZRA9z8ti8yRGxWGKbqymPd7UicRth",  # WOC
    "5E2woTdd2Gc4BpfE4yDPC4rTEJCo3fijhveDxhaZpump",  # uAPE
}
ALLOWED = {
    "updatedAt", "status", "walletMode", "refreshMode", "wallets", "summary",
    "chains", "tokens", "nfts", "approvals", "recentActivity", "opportunities",
    "baseMcp", "guardrails", "errors", "lastFullRefreshAt",
}
HEADERS = {"User-Agent": "JAIMES-control-tower-wallet-refresh/1.0", "Accept": "application/json"}


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


def rpc(method: str, params: list[Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        RPC,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "JAIMES/1.0"},
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def get_json(url: str, timeout: int = 25) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def mask(s: str) -> str:
    return s[:4] + "..." + s[-4:]


def sol_price_usd() -> float:
    try:
        q = get_json(
            "https://lite-api.jup.ag/swap/v1/quote?"
            "inputMint=So11111111111111111111111111111111111111112&"
            "outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&"
            "amount=1000000000&slippageBps=50",
            timeout=20,
        )
        return int(q["outAmount"]) / 1_000_000
    except Exception:
        return 68.94


def live_solana_rows() -> tuple[float, list[dict[str, Any]]]:
    sol_bal = rpc("getBalance", [ADDR])["result"]["value"] / 1_000_000_000
    rows: list[dict[str, Any]] = []
    for program in PROGRAMS.values():
        res = rpc("getTokenAccountsByOwner", [ADDR, {"programId": program}, {"encoding": "jsonParsed"}])
        for x in res.get("result", {}).get("value", []):
            info = x["account"]["data"]["parsed"]["info"]
            amt = info["tokenAmount"]
            bal = float(amt.get("uiAmount") or 0)
            if bal > 0:
                rows.append({"mint": info["mint"], "amount": bal})
    return sol_bal, rows


def dex_best_pairs(mints: list[str]) -> dict[str, dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for i in range(0, len(mints), 30):
        try:
            pairs += get_json("https://api.dexscreener.com/latest/dex/tokens/" + ",".join(mints[i:i + 30])).get("pairs") or []
        except Exception:
            pass
    best: dict[str, dict[str, Any]] = {}
    for p in pairs:
        base = (p.get("baseToken") or {}).get("address")
        liq = (p.get("liquidity") or {}).get("usd") or 0
        if base and (base not in best or liq > ((best[base].get("liquidity") or {}).get("usd") or 0)):
            best[base] = p
    return best


def main() -> None:
    if not RAW.exists():
        raise SystemExit("private raw wallet sidecar missing")
    sol_bal, rows = live_solana_rows()
    price_sol = sol_price_usd()
    best = dex_best_pairs([r["mint"] for r in rows])

    sol_tokens: list[dict[str, Any]] = [{
        "amount": round(sol_bal, 6),
        "chain": "solana",
        "classification": "core",
        "name": "Solana",
        "priceSource": "Jupiter",
        "source": "solana-rpc-live",
        "symbol": "SOL",
        "valueUsd": round(sol_bal * price_sol, 2),
    }]
    for r in rows:
        p = best.get(r["mint"])
        price = float(p["priceUsd"]) if p and p.get("priceUsd") else None
        value = round((price or 0) * r["amount"], 2)
        if value <= 0 and r["amount"] < 0.0001:
            continue
        base = (p.get("baseToken") or {}) if p else {}
        cls = "active-trade" if r["mint"] in ACTIVE_TRADE_MINTS else ("dust" if value < 1 else "useful")
        sol_tokens.append({
            "amount": round(r["amount"], 6),
            "chain": "solana",
            "classification": cls,
            "mintMasked": mask(r["mint"]),
            "name": base.get("name") or "Unknown Solana token",
            "priceSource": "DexScreener" if price else "unpriced",
            "source": "solana-rpc-live",
            "symbol": base.get("symbol") or mask(r["mint"]),
            "valueUsd": value,
        })

    raw = json.loads(RAW.read_text())
    side = raw.get("publicSidecar", {})
    non_sol = [t for t in side.get("tokens", []) if str(t.get("chain", "")).lower() != "solana"]
    side["tokens"] = sorted(non_sol + sol_tokens, key=lambda t: t.get("valueUsd") or 0, reverse=True)
    chains = [c for c in side.get("chains", []) if str(c.get("chain", "")).lower() != "solana"]
    chains.append({
        "chain": "solana",
        "estimatedGasBudgetUsd": round(sol_bal * price_sol, 2),
        "gasBalance": round(sol_bal, 6),
        "gasStatus": "ready" if sol_bal >= 0.05 else "low",
        "gasSymbol": "SOL",
        "gasValueUsd": round(sol_bal * price_sol, 2),
    })
    side["chains"] = chains

    nft = (side.get("summary") or {}).get("nftEstimatedUsd") or 0
    liquid = sum(t.get("valueUsd") or 0 for t in side["tokens"])
    ts = now()
    side.update({
        "updatedAt": ts,
        "status": "fresh",
        "walletMode": "read-only",
        "refreshMode": "live-solana-plus-private-cache",
        "lastFullRefreshAt": ts,
        "wallets": {"evmMasked": "0xf1CC...B596", "solanaMasked": "J3AB...iMRA"},
        "summary": {
            "freshnessStatus": "fresh",
            "lastRefreshed": ts,
            "liquidEstimatedUsd": round(liquid, 2),
            "nftEstimatedUsd": round(nft, 2),
            "totalEstimatedUsd": round(liquid + nft, 2),
        },
    })
    raw["publicSidecar"] = side
    raw["updatedAt"] = ts
    atomic(RAW, raw)

    out = {k: side[k] for k in ALLOWED if k in side}
    atomic(OUT, out)
    if DASH.exists():
        dash = json.loads(DASH.read_text())
        dash["agenticCryptoWallet"] = out
        atomic(DASH, dash)
    print(json.dumps({
        "ok": True,
        "updatedAt": ts,
        "solBalance": round(sol_bal, 9),
        "liquidEstimatedUsd": round(liquid, 2),
        "totalEstimatedUsd": round(liquid + nft, 2),
        "tokenCount": len(side["tokens"]),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
