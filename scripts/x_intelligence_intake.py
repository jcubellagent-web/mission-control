#!/usr/bin/env python3
"""Offline X-intelligence intake: parse supplied evidence, score, route, dedupe.

This program deliberately has no HTTP client and never opens X. Public discovery
and primary-source lookup happen outside this process through approved web search.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
WATCHLIST = ROOT / "config" / "x-intelligence-watchlist.json"
DEFAULT_STATE = ROOT / "data" / "x-intelligence-recent.json"
X_RE = re.compile(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})/status/(\d+)", re.I)
PRIMARY_DOMAINS = {
    "github.com", "docs.github.com", "openai.com", "developers.openai.com", "help.openai.com", "anthropic.com",
    "blog.google", "developers.googleblog.com", "ollama.com", "nousresearch.com",
    "robinhood.com", "sec.gov", "www.sec.gov", "status.openai.com",
    "status.anthropic.com", "status.cloud.google.com", "mlb.com", "www.mlb.com",
    "sorare.com", "help.sorare.com", "solana.com", "base.org", "coinbase.com",
}
CRYPTO_WORDS = {"crypto", "token", "solana", "base", "robinhood chain", "contract", "wallet", "bridge", "exploit", "airdrop"}
OPS_WORDS = {"openclaw", "hermes", "codex", "gemini", "ollama", "api", "release", "model", "agent", "github"}
SORARE_WORDS = {"sorare", "mlb", "lineup", "pitcher", "fantasy", "injury", "scratch", "closer"}
BREAKING_WORDS = {"breaking", "urgent", "outage", "incident", "attack", "war", "sanctions", "emergency", "recall"}


def parse_x_url(url: str) -> dict:
    match = X_RE.fullmatch(url.strip().rstrip("/"))
    if not match:
        raise ValueError("Expected a public X status URL; profiles and logged-in URLs are not accepted")
    return {"url": url.strip(), "handle": match.group(1), "post_id": match.group(2)}


def domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def topic_for(text: str) -> tuple[int, str]:
    lowered = text.lower()
    if any(word in lowered for word in SORARE_WORDS):
        return 19, "Sorare/MLB intelligence"
    if any(word in lowered for word in CRYPTO_WORDS):
        return 20, "actionable crypto intelligence"
    if any(word in lowered for word in OPS_WORDS):
        return 17, "implementation or deep ecosystem research"
    if any(word in lowered for word in BREAKING_WORDS):
        return 56, "breaking/news intelligence"
    return 1, "topic unclear"


def confidence(source_tier: str, corroboration: list[str]) -> tuple[str, float, int]:
    primary_count = sum(1 for url in corroboration if domain(url) in PRIMARY_DOMAINS)
    base = {"primary": 0.9, "reputable": 0.72, "community": 0.5, "unknown": 0.3}.get(source_tier, 0.3)
    score = min(0.99, base + min(len(corroboration), 3) * 0.08 + min(primary_count, 2) * 0.08)
    if primary_count >= 1 and source_tier in {"primary", "reputable"}:
        return "high", score, primary_count
    if corroboration and score >= 0.58:
        return "medium", score, primary_count
    return "low", score, primary_count


def model_route(claim: str, corroboration: list[str], conflicting: bool, implementation: bool) -> tuple[str, str]:
    if implementation:
        return "Codex/OpenAI", "implementation or verified integration"
    if conflicting or len(claim) > 1200 or len(corroboration) > 5:
        return "Gemini Pro", "long, ambiguous, or conflicting evidence"
    return "Gemini Flash", "extraction, classification, deduplication, and concise summary"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def atomic_write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def intake(args: argparse.Namespace) -> dict:
    source = parse_x_url(args.url)
    claim = " ".join((args.claim or "Claim unavailable from supplied public context").split())
    evidence = list(dict.fromkeys(args.corroboration or []))
    conf, conf_score, primary_count = confidence(args.source_tier, evidence)
    topic, route_reason = topic_for(f"{claim} {source['handle']}")
    model, model_reason = model_route(claim, evidence, args.conflicting, args.implementation)
    fingerprint = hashlib.sha256(f"{source['handle'].lower()}:{source['post_id']}:{claim.lower()}".encode()).hexdigest()[:20]
    state_path = Path(args.state)
    state = load_json(state_path, {"version": 1, "items": []})
    duplicate = any(row.get("fingerprint") == fingerprint or row.get("post_id") == source["post_id"] for row in state.get("items", []))
    urgent = any(word in claim.lower() for word in BREAKING_WORDS)
    limitation = "X coverage is incomplete; the post was not independently verified."
    if primary_count:
        limitation = "X coverage may be incomplete; supplied primary evidence corroborates the core claim."
    elif evidence:
        limitation = "X coverage is incomplete; secondary corroboration exists but no supplied primary source was verified."
    recommendation = "Monitor; obtain primary confirmation before acting."
    if duplicate:
        recommendation = "No new action; duplicate of recent intelligence."
    elif conf == "high":
        recommendation = "Use the corroborated primary source; act only within the routed workflow."
    result = {
        "status": "duplicate" if duplicate else "new",
        "fingerprint": fingerprint,
        "claim": claim,
        "original_x_source": {**source, "timestamp": args.timestamp or None, "source_tier": args.source_tier},
        "corroborating_sources": evidence,
        "confidence": conf,
        "confidence_score": round(conf_score, 2),
        "primary_source_count": primary_count,
        "relevance": "high" if topic in {17, 19, 20, 56} else "low",
        "urgency": "high" if urgent else "normal",
        "topic": topic,
        "topic_reason": route_reason,
        "why_it_matters": route_reason,
        "recommended_action": recommendation,
        "model_used": model,
        "model_reason": model_reason,
        "coverage_limitation": limitation,
        "policy": {"x_scraping": False, "xai_used": False, "account_mutation": False, "incremental_api_spend_usd": 0},
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    if not args.no_write and not duplicate:
        rows = state.get("items", [])
        rows.append({"fingerprint": fingerprint, "post_id": source["post_id"], "handle": source["handle"], "topic": topic, "confidence": conf, "processed_at": result["processed_at"]})
        state["items"] = rows[-500:]
        atomic_write(state_path, state)
    return result


def render_text(result: dict) -> str:
    sources = result["corroborating_sources"]
    return "\n".join([
        f"Claim: {result['claim']}",
        f"Original X source: {result['original_x_source']['url']}",
        f"Corroborating sources: {', '.join(sources) if sources else 'none supplied'}",
        f"Confidence: {result['confidence']}",
        f"Why it matters: {result['why_it_matters']}",
        f"Recommended action: {result['recommended_action']}",
        f"Model used and why: {result['model_used']} — {result['model_reason']}",
        f"Coverage: {result['coverage_limitation']}",
        f"Route: topic {result['topic']}",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--claim", default="")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--corroboration", action="append", default=[])
    parser.add_argument("--source-tier", choices=["primary", "reputable", "community", "unknown"], default="unknown")
    parser.add_argument("--conflicting", action="store_true")
    parser.add_argument("--implementation", action="store_true")
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--silent-routine", action="store_true")
    args = parser.parse_args()
    try:
        result = intake(args)
    except ValueError as exc:
        parser.error(str(exc))
    if not (args.silent_routine and result["status"] == "duplicate"):
        print(json.dumps(result, indent=2) if args.format == "json" else render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
