#!/usr/bin/env python3
"""Refresh the dashboard-safe agentic wallet sidecar.

Reads the private local inventory cache and writes only its sanitized
`publicSidecar` payload to data/agentic-crypto-wallet.json. This script never
prints or publishes raw wallet addresses, keys, calldata, cookies, or secrets.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "agentic-crypto-wallet.json"
PRIVATE_RAW = Path.home() / ".openclaw" / "private" / "mission-control" / "agentic-crypto-wallet-raw.json"

ALLOWED_KEYS = {
    "updatedAt", "status", "walletMode", "refreshMode", "wallets", "summary",
    "chains", "tokens", "nfts", "approvals", "recentActivity", "opportunities",
    "baseMcp", "guardrails", "errors", "lastFullRefreshAt",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def placeholder(reason: str) -> dict[str, Any]:
    now = utc_now()
    return {
        "updatedAt": now,
        "status": "ready",
        "walletMode": "read-only",
        "refreshMode": "local-placeholder",
        "wallets": {"evmMasked": "not-connected", "solanaMasked": "not-connected"},
        "summary": {
            "totalEstimatedUsd": None,
            "liquidEstimatedUsd": None,
            "nftEstimatedUsd": None,
            "lastRefreshed": now,
            "freshnessStatus": "not-connected",
        },
        "chains": [],
        "tokens": [],
        "nfts": [],
        "approvals": [],
        "recentActivity": [],
        "opportunities": [],
        "baseMcp": {
            "status": "not-connected",
            "mode": "proposal-only",
            "accountConnection": "not-connected",
            "lastChecked": now,
            "summary": "Read-only wallet source is not connected on this host.",
        },
        "guardrails": {
            "chainAllowlist": ["base", "ethereum", "solana"],
            "requiresHumanApproval": True,
        },
        "errors": [reason],
    }


def load_public_sidecar() -> dict[str, Any]:
    if not PRIVATE_RAW.exists():
        return placeholder("Private wallet inventory cache not found; read-only view remains disconnected.")
    raw = json.loads(PRIVATE_RAW.read_text())
    public = raw.get("publicSidecar")
    if not isinstance(public, dict):
        return placeholder("Private wallet inventory cache has no public sidecar.")
    clean = {key: public[key] for key in ALLOWED_KEYS if key in public}
    clean.setdefault("updatedAt", public.get("updatedAt") or raw.get("updatedAt") or utc_now())
    clean.setdefault("status", "fresh")
    clean.setdefault("walletMode", "read-only")
    clean.setdefault("refreshMode", "lightweight")
    clean.setdefault("errors", [])
    return clean


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="auto", choices=["auto", "lightweight"])
    args = parser.parse_args()
    payload = load_public_sidecar()
    payload["refreshMode"] = "lightweight" if args.mode == "lightweight" else payload.get("refreshMode", "lightweight")
    atomic_write_json(OUT, payload)
    summary = payload.get("summary") or {}
    print(json.dumps({
        "ok": True,
        "status": payload.get("status"),
        "walletMode": payload.get("walletMode"),
        "totalEstimatedUsd": summary.get("totalEstimatedUsd"),
        "tokenCount": len(payload.get("tokens") or []),
        "chainCount": len(payload.get("chains") or []),
        "out": str(OUT),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
