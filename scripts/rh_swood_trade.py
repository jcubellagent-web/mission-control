#!/usr/bin/env python3
"""SWOOD medium-term entry via official Uniswap V2 router on Robinhood.

Default is dry-run. --execute uses the approved burner signer, exact WETH input
allowance, and exact resulting SWOOD exit allowance. No unlimited approvals.
"""
from __future__ import annotations
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, "/Users/jc_agent/.hermes/scripts")
from eth_abi import encode, decode
from eth_utils import keccak, to_checksum_address
import rh_autonomous_executor as core

ROUTER = to_checksum_address("0x89e5DB8B5aA49aA85AC63f691524311AEB649eba")
WETH = to_checksum_address(core.WETH)
VIRTUAL = to_checksum_address("0xc6911796042b15d7Fa4F6CDe69e245DdCd3d9c31")
SWOOD = to_checksum_address("0xB1cB27F78B7335df8C3d8ebF0881A15BeD6BeB60")
WALLET = to_checksum_address(core.WALLET)
SIZE_WEI = int(0.003 * 1e18)
SLIPPAGE_BPS = 600
OUT = Path("/Users/jc_agent/reports/rh_swood_entry_latest.json")
POSITIONS = Path("/Users/jc_agent/reports/rh_manual_strategy_positions.json")
SEL_QUOTE = "0x" + keccak(text="getAmountsOut(uint256,address[])")[:4].hex()
SEL_SWAP = "0x" + keccak(text="swapExactTokensForTokens(uint256,uint256,address[],address,uint256)")[:4].hex()
SEL_APPROVE = "0x" + keccak(text="approve(address,uint256)")[:4].hex()
SEL_ALLOWANCE = "0x" + keccak(text="allowance(address,address)")[:4].hex()
SEL_BALANCE = "0x" + keccak(text="balanceOf(address)")[:4].hex()


def call(to: str, data: str) -> str:
    return core.selector_call(to_checksum_address(to), data, WALLET)


def quote(amount: int, path: list[str]) -> list[int]:
    data = SEL_QUOTE + encode(["uint256", "address[]"], [amount, [to_checksum_address(x) for x in path]]).hex()
    return list(decode(["uint256[]"], bytes.fromhex(call(ROUTER, data)[2:]))[0])


def allowance(token: str) -> int:
    data = SEL_ALLOWANCE + encode(["address", "address"], [WALLET, ROUTER]).hex()
    return int(decode(["uint256"], bytes.fromhex(call(token, data)[2:]))[0])


def balance(token: str) -> int:
    data = SEL_BALANCE + encode(["address"], [WALLET]).hex()
    return int(decode(["uint256"], bytes.fromhex(call(token, data)[2:]))[0])


def approve_data(amount: int) -> str:
    return SEL_APPROVE + encode(["address", "uint256"], [ROUTER, amount]).hex()


def wait_receipt(tx_hash: str, timeout: int = 180) -> dict[str, Any]:
    end = time.time() + timeout
    while time.time() < end:
        r = core.rpc("eth_getTransactionReceipt", [tx_hash])
        if r:
            if int(r.get("status", "0x0"), 16) != 1:
                raise RuntimeError(f"transaction reverted: {tx_hash}")
            return r
        time.sleep(2)
    raise TimeoutError(f"receipt timeout: {tx_hash}")


def send_tx(to: str, data: str, nonce: int, gas_limit: int | None = None) -> str:
    acct = core.account()
    if acct.address.lower() != WALLET.lower():
        raise RuntimeError("burner signer mismatch")
    gp = int(core.rpc("eth_gasPrice"), 16)
    tx0 = {"from": WALLET, "to": to_checksum_address(to), "data": data, "value": "0x0"}
    est = gas_limit or int(core.rpc("eth_estimateGas", [tx0]), 16)
    tx = {"chainId": core.CHAIN_ID, "nonce": nonce, "to": to_checksum_address(to), "value": 0, "data": data, "gas": int(est * 1.25), "gasPrice": int(gp * 1.10)}
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    raw_hex = raw.hex(); raw_hex = raw_hex if raw_hex.startswith("0x") else "0x" + raw_hex
    return core.rpc("eth_sendRawTransaction", [raw_hex])


def dry_run() -> dict[str, Any]:
    forward = quote(SIZE_WEI, [WETH, VIRTUAL, SWOOD])
    reverse = quote(forward[-1], [SWOOD, VIRTUAL, WETH])
    min_out = forward[-1] * (10_000 - SLIPPAGE_BPS) // 10_000
    deadline = int(time.time()) + 300
    swap_data = SEL_SWAP + encode(["uint256", "uint256", "address[]", "address", "uint256"], [SIZE_WEI, min_out, [WETH, VIRTUAL, SWOOD], WALLET, deadline]).hex()
    gas = int(core.rpc("eth_estimateGas", [{"from": WALLET, "to": ROUTER, "data": swap_data, "value": "0x0"}]), 16) if allowance(WETH) >= SIZE_WEI else None
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "mode": "DRY_RUN",
        "symbol": "SWOOD",
        "token": SWOOD,
        "pair": "0xabc83c3F04C3dEc51CE32F8aa83bE281E1B27Dad",
        "horizon": "MEDIUM_3_TO_7_DAYS",
        "size_eth": 0.003,
        "route": "WETH->VIRTUAL->SWOOD via verified UniswapV2Router02",
        "expected_swood": forward[-1] / 1e18,
        "min_swood": min_out / 1e18,
        "reverse_eth": reverse[-1] / 1e18,
        "roundtrip_ratio": reverse[-1] / SIZE_WEI,
        "roundtrip_impact_pct": 100 * (1 - reverse[-1] / SIZE_WEI),
        "weth_allowance_exact_required": allowance(WETH) < SIZE_WEI,
        "swood_exit_allowance_exact_required": True,
        "gas_estimate_swap": gas,
        "swap_data": swap_data,
    }


def execute(plan: dict[str, Any]) -> dict[str, Any]:
    nonce = int(core.rpc("eth_getTransactionCount", [WALLET, "pending"]), 16)
    txs: dict[str, str] = {}
    if allowance(WETH) < SIZE_WEI:
        txs["approve_weth"] = send_tx(WETH, approve_data(SIZE_WEI), nonce)
        wait_receipt(txs["approve_weth"]); nonce += 1
    before = balance(SWOOD)
    txs["buy"] = send_tx(ROUTER, plan["swap_data"], nonce)
    buy_receipt = wait_receipt(txs["buy"]); nonce += 1
    after = balance(SWOOD)
    acquired = after - before
    if acquired <= 0:
        raise RuntimeError("SWOOD balance did not increase")
    if allowance(SWOOD) < acquired:
        txs["approve_exit"] = send_tx(SWOOD, approve_data(acquired), nonce)
        wait_receipt(txs["approve_exit"])
    result = {k: v for k, v in plan.items() if k != "swap_data"}
    result.update({"mode": "EXECUTED", "tx_hashes": txs, "acquired_swood": acquired / 1e18, "receipt_block": int(buy_receipt["blockNumber"], 16), "exact_exit_allowance": acquired / 1e18})
    state = {"positions": []}
    try: state = json.loads(POSITIONS.read_text())
    except Exception: pass
    positions = [p for p in state.get("positions", []) if str(p.get("token", "")).lower() != SWOOD.lower() or p.get("status") == "CLOSED"]
    positions.append({"symbol":"SWOOD","token":SWOOD,"status":"OPEN","opened_at":datetime.now(timezone.utc).isoformat(),"entry_eth":0.003,"token_units":acquired/1e18,"horizon":"MEDIUM_3_TO_7_DAYS","review_at_hours":48,"max_hold_days":7,"soft_invalidation_pct":-18,"hard_invalidation_pct":-28,"tp_levels":[{"pct":35,"trim":0.25},{"pct":80,"trim":0.25},{"pct":150,"trim":0.25}],"runner":0.25,"entry_tx":txs["buy"]})
    POSITIONS.write_text(json.dumps({"updated_at":datetime.now(timezone.utc).isoformat(),"positions":positions},indent=2))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--execute", action="store_true"); args = ap.parse_args()
    plan = dry_run()
    if plan["roundtrip_impact_pct"] > 6:
        raise RuntimeError("round-trip impact exceeds 6%")
    result = execute(plan) if args.execute else {k:v for k,v in plan.items() if k != "swap_data"}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True)); print(json.dumps(result, indent=2, sort_keys=True)); return 0

if __name__ == "__main__": raise SystemExit(main())
