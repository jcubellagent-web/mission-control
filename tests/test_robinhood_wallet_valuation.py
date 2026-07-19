from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "refresh_agentic_robinhood_wallet_live.py"
SPEC = importlib.util.spec_from_file_location("robinhood_wallet_valuation", SCRIPT)
assert SPEC and SPEC.loader
wallet = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wallet
SPEC.loader.exec_module(wallet)


def pair(price: float, liquidity: float, volume: float, *, base: str = "0xtoken", quote: str | None = None) -> dict:
    return {
        "chainId": "robinhood",
        "baseToken": {"address": base},
        "quoteToken": {"address": quote or wallet.WETH},
        "priceUsd": str(price),
        "liquidity": {"usd": liquidity},
        "volume": {"h24": volume},
    }


def balance(address: str, amount: int, *, symbol: str = "TOKEN", decimals: object = 0) -> dict:
    return {
        "value": str(amount),
        "token": {
            "address_hash": address,
            "name": symbol,
            "symbol": symbol,
            "decimals": decimals,
            "type": "ERC-20",
            "exchange_rate": "0",
        },
    }


def test_price_selection_rejects_high_liquidity_outlier() -> None:
    pairs = [
        pair(0.0214, 1_041_475, 4_000_000),
        pair(0.02133, 205_522, 700_000, quote=wallet.USDG),
        pair(0.02267, 222_660, 500_000, quote=wallet.USDG),
        pair(19.35, 4_090_968, 251, quote=wallet.USDG),
    ]

    selected = wallet.select_dex_price(pairs, "0xtoken", anchor=0.02368751)

    assert math.isclose(selected, 0.0214)


def test_price_selection_requires_exact_base_address() -> None:
    pairs = [pair(100, 1_000_000, 1_000_000, base="0xother"), pair(0.2, 50_000, 2_000)]

    assert wallet.select_dex_price(pairs, "0xtoken", anchor=0.19) == 0.2


def test_unanchored_single_trusted_pair_is_allowed_when_liquid() -> None:
    assert wallet.select_dex_price([pair(0.0002196, 44_859, 10_000)], "0xtoken") == 0.0002196


def test_unanchored_two_pool_disagreement_fails_closed() -> None:
    assert wallet.select_dex_price(
        [pair(0.02, 50_000, 10_000), pair(19.35, 4_000_000, 250)],
        "0xtoken",
    ) == 0.0


def test_dex_prices_fetches_each_token_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str) -> list[dict]:
        calls.append(url)
        address = url.rsplit("/", 1)[-1]
        return [pair(0.1 if address.lower() == "0xa" else 0.2, 10_000, 1_000, base=address)]

    monkeypatch.setattr(wallet, "get_json", fake_get)

    prices, rejected = wallet.dex_prices(["0xA", "0xB", "0xa"])
    assert prices == {"0xa": 0.1, "0xb": 0.2}
    assert rejected == set()
    assert len(calls) == 2


def test_dex_prices_marks_unresolved_disagreement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wallet,
        "get_json",
        lambda _url: [pair(0.02, 50_000, 10_000), pair(19.35, 4_000_000, 250)],
    )

    prices, rejected = wallet.dex_prices(["0xtoken"])

    assert prices == {}
    assert rejected == {"0xtoken"}


def test_duplicate_contract_collapses_but_same_symbol_contracts_remain() -> None:
    rows = [balance("0xA", 10, symbol="DUP"), balance("0xa", 10, symbol="DUP"), balance("0xB", 20, symbol="DUP")]

    unique = wallet.unique_token_balances(rows)

    assert len(unique) == 2
    assert {wallet.token_meta(row)["address"].lower() for row in unique} == {"0xa", "0xb"}


def test_conflicting_duplicate_contract_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="Conflicting duplicate"):
        wallet.unique_token_balances([balance("0xA", 10), balance("0xa", 11)])


@pytest.mark.parametrize("decimals", [None, -1, 37, "bad"])
def test_invalid_decimals_are_not_treated_as_whole_tokens(decimals: object) -> None:
    assert wallet.token_amount(balance("0xA", 10**18, decimals=decimals)) == 0.0


def test_nft_without_decimals_keeps_whole_item_count() -> None:
    row = {"value": "3", "token": {"address_hash": "0xNFT", "type": "ERC-721", "decimals": None}}

    assert wallet.token_amount(row) == 3.0


@pytest.mark.parametrize("token_type", ["ERC-721", "ERC-1155"])
def test_nft_total_decimals_do_not_scale_item_count(token_type: str) -> None:
    row = {
        "total": {"value": "3", "decimals": 18},
        "token": {"address_hash": "0xNFT", "type": token_type, "decimals": None},
    }

    assert wallet.token_amount(row) == 3.0


def test_sidecar_total_must_reconcile() -> None:
    valid = {
        "summary": {
            "nativeLiquidUsd": 5.0,
            "tokenLiquidUsd": 3.0,
            "liquidEstimatedUsd": 8.0,
            "nftEstimatedUsd": 0.0,
            "totalEstimatedUsd": 8.0,
        },
        "tokens": [
            {"amount": 1.0, "priceUsd": 1.0, "valueUsd": 1.0},
            {"amount": 1.0, "priceUsd": 2.0, "valueUsd": 2.0},
        ],
    }
    wallet.validate_wallet_sidecar(valid)

    invalid = {
        "summary": {
            "nativeLiquidUsd": 5.0,
            "tokenLiquidUsd": 3.0,
            "liquidEstimatedUsd": 80.0,
            "nftEstimatedUsd": 0.0,
            "totalEstimatedUsd": 80.0,
        },
        "tokens": [{"amount": 1.0, "priceUsd": 3.0, "valueUsd": 3.0}],
    }
    with pytest.raises(RuntimeError, match="reconciliation"):
        wallet.validate_wallet_sidecar(invalid)


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf")])
def test_sidecar_rejects_invalid_published_values(bad: float) -> None:
    sidecar = {
        "summary": {
            "nativeLiquidUsd": 0.0,
            "tokenLiquidUsd": 0.0,
            "liquidEstimatedUsd": 0.0,
            "nftEstimatedUsd": 0.0,
            "totalEstimatedUsd": 0.0,
        },
        "tokens": [{"amount": 1.0, "priceUsd": 1.0, "valueUsd": bad}],
    }

    with pytest.raises(RuntimeError, match="Invalid wallet value"):
        wallet.validate_wallet_sidecar(sidecar)


def test_total_estimated_usd_must_reconcile() -> None:
    sidecar = {
        "summary": {
            "nativeLiquidUsd": 0.0,
            "tokenLiquidUsd": 0.0,
            "liquidEstimatedUsd": 0.0,
            "nftEstimatedUsd": 0.0,
            "totalEstimatedUsd": 999_999.0,
        },
        "tokens": [],
    }

    with pytest.raises(RuntimeError, match="liquid-plus-NFT"):
        wallet.validate_wallet_sidecar(sidecar)
