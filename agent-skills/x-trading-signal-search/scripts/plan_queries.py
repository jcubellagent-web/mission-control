#!/usr/bin/env python3
"""Build a bounded, deterministic X UI query plan for trading-signal research."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode


EVM_ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")
BASE58_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TICKER = re.compile(r"^\$?[A-Za-z][A-Za-z0-9._-]{0,14}$")
WINDOW = re.compile(r"^(\d+)(m|h|d)$")


def clean(value: str | None, *, max_length: int = 200) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split()).strip()
    if not value or len(value) > max_length:
        raise ValueError(f"invalid value length: expected 1-{max_length} characters")
    return value


def parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_window(value: str) -> timedelta:
    match = WINDOW.fullmatch(value)
    if not match:
        raise ValueError("window must look like 15m, 6h, 1d, or 7d")
    amount = int(match.group(1))
    if amount < 1:
        raise ValueError("window must be positive")
    unit = match.group(2)
    delta = {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]
    if delta > timedelta(days=30):
        raise ValueError("window cannot exceed 30 days")
    return delta


def quote(value: str) -> str:
    return f'"{value.replace(chr(34), "").strip()}"'


def normalize_handle(value: str) -> str:
    value = clean(value, max_length=16) or ""
    value = value.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", value):
        raise ValueError(f"invalid X handle: {value!r}")
    return value


def classify(identifier: str, ticker: str | None, project: str | None) -> tuple[str, str | None]:
    if EVM_ADDRESS.fullmatch(identifier):
        return "evm_contract", ticker
    if BASE58_ADDRESS.fullmatch(identifier):
        return "base58_contract", ticker
    if ticker:
        return "ticker", ticker
    if identifier.startswith("$") and TICKER.fullmatch(identifier):
        return "ticker", identifier.lstrip("$").upper()
    if TICKER.fullmatch(identifier) and identifier.upper() == identifier:
        return "ticker", identifier.upper()
    if project or " " in identifier:
        return "project", ticker
    return "project", ticker


def search_url(query: str, mode: str) -> str:
    params = {"q": query, "src": "typed_query"}
    if mode == "latest":
        params["f"] = "live"
    return "https://x.com/search?" + urlencode(params)


def build_plan(args: argparse.Namespace) -> dict:
    identifier = clean(args.identifier) or ""
    chain = clean(args.chain, max_length=40)
    project = clean(args.project)
    ticker = clean(args.ticker, max_length=16)
    ticker = ticker.lstrip("$").upper() if ticker else None
    if ticker and not TICKER.fullmatch(ticker):
        raise ValueError(f"invalid ticker: {ticker!r}")

    kind, inferred_ticker = classify(identifier, ticker, project)
    ticker = ticker or inferred_ticker
    now = parse_now(args.now)
    delta = parse_window(args.window)
    cutoff = now - delta
    until = now.date() + timedelta(days=1)
    date_filter = f"since:{cutoff.date().isoformat()} until:{until.isoformat()}"
    exclusions = [clean(value, max_length=50) for value in args.exclude]
    exclusions = [f'-{value.replace(" ", "-")}' for value in exclusions if value]
    exclusion_filter = " ".join(exclusions)

    logical: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(purpose: str, query: str) -> None:
        query = " ".join(query.split()).strip()
        if exclusion_filter:
            query = f"{query} {exclusion_filter}"
        query = f"{query} {date_filter}"
        if query not in seen:
            seen.add(query)
            logical.append({"purpose": purpose, "query": query})

    if kind in {"evm_contract", "base58_contract"}:
        anchor = quote(identifier)
        add("exact-contract", anchor)
        if chain:
            add("contract-and-chain", f"{anchor} {quote(chain)}")
        if ticker:
            add("contract-and-ticker", f'{anchor} "${ticker}"')
    elif kind == "ticker":
        ticker = ticker or identifier.lstrip("$").upper()
        bare_anchor = f'"${ticker}"'
        disambiguators = " ".join(quote(value) for value in (project, chain) if value)
        if disambiguators:
            anchor = f"{bare_anchor} {disambiguators}"
            add("ticker-disambiguated", anchor)
        else:
            anchor = bare_anchor
            add("exact-cashtag", anchor)
    else:
        anchor = quote(project or identifier)
        add("exact-project", anchor)
        if chain:
            add("project-and-chain", f"{anchor} {quote(chain)}")
        if ticker:
            add("project-and-ticker", f'{anchor} "${ticker}"')

    add(
        "risk",
        f"{anchor} (scam OR rug OR exploit OR hack OR hacked OR honeypot OR delist OR outage OR congestion OR drain OR blacklist OR freeze)",
    )
    add(
        "catalyst",
        f"{anchor} (listing OR launch OR partnership OR unlock OR migration OR airdrop OR upgrade OR governance OR staking OR ETF OR regulatory)",
    )
    add(
        "market-narrative",
        f"{anchor} (bullish OR bearish OR buy OR sell OR whale OR liquidity OR volume OR holders OR accumulation OR distribution OR inflow OR outflow OR validator OR fees)",
    )
    for handle in args.account:
        normalized = normalize_handle(handle)
        add("named-source", f"from:{normalized} {anchor}")

    requests: list[dict[str, str]] = []

    def emit(item: dict[str, str], mode: str) -> None:
        if len(requests) >= args.max_queries:
            return
        requests.append(
            {
                "id": f"q{len(requests) + 1}",
                "purpose": item["purpose"],
                "mode": mode,
                "query": item["query"],
                "url": search_url(item["query"], mode),
            }
        )

    if args.mode == "both":
        if logical:
            emit(logical[0], "latest")
            emit(logical[0], "top")
        for item in logical[1:]:
            emit(item, "latest")
        if len(requests) < args.max_queries and len(logical) > 1:
            emit(logical[-1], "top")
    else:
        for item in logical:
            emit(item, args.mode)

    return {
        "version": 1,
        "asset": {
            "kind": kind,
            "identifier": identifier,
            "chain": chain,
            "ticker": ticker,
            "project": project,
        },
        "window": {
            "label": args.window,
            "cutoffUtc": cutoff.isoformat().replace("+00:00", "Z"),
            "collectedBeforeUtc": now.isoformat().replace("+00:00", "Z"),
            "clientTimestampFilterRequired": delta < timedelta(days=1),
        },
        "queryBudget": args.max_queries,
        "queries": requests,
        "notes": [
            "Treat ticker-only results as ambiguous until chain or contract identifiers converge.",
            "Supply --account after resolving an official or primary source handle independently.",
            "Apply cutoffUtc to returned timestamps for sub-day windows.",
            "X results are leads; corroborate material claims with primary or onchain sources.",
        ],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("identifier", help="contract address, ticker/cashtag, or project name")
    result.add_argument("--chain")
    result.add_argument("--ticker")
    result.add_argument("--project")
    result.add_argument("--account", action="append", default=[], help="preferred X source handle; repeatable")
    result.add_argument("--exclude", action="append", default=[], help="word to exclude; repeatable")
    result.add_argument("--window", default="24h")
    result.add_argument("--mode", choices=("latest", "top", "both"), default="both")
    result.add_argument("--max-queries", type=int, choices=range(1, 13), default=8)
    result.add_argument("--now", help="UTC ISO timestamp for deterministic planning")
    result.add_argument("--format", choices=("json", "lines"), default="json")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        plan = build_plan(args)
    except ValueError as exc:
        parser().error(str(exc))
    if args.format == "lines":
        for query in plan["queries"]:
            print(query["url"])
    else:
        print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
