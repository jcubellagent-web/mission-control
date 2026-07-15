#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "rh_nft_intelligence.py"


def load_module():
    spec = importlib.util.spec_from_file_location("rh_nft_intelligence_test_target", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chainwide_mint_discovery() -> None:
    module = load_module()
    now = "2026-07-12T22:59:28.000000Z"
    module.time.time = lambda: 1783897268
    row = {
        "from": {"hash": module.ZERO},
        "to": {"hash": "0x" + "1" * 40},
        "timestamp": now,
        "transaction_hash": "0x" + "2" * 64,
        "token": {
            "address_hash": "0x" + "3" * 40,
            "name": "Foundational Free Mint",
            "symbol": "FFM",
        },
        "total": {"token_id": "7"},
    }
    module.getj = lambda *args, **kwargs: {"items": [row], "next_page_params": None}
    module.tx_detail = lambda tx_hash: {"value": "0x0", "from": "0x" + "4" * 40}

    output = module.chainwide_mint_activity()

    assert len(output) == 1
    assert output[0]["type"] == "MINT"
    assert output[0]["price_eth"] == 0
    assert output[0]["collection"] == "Foundational Free Mint"
    assert output[0]["token_id"] == "7"
