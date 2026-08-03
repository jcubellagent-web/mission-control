#!/usr/bin/env python3
"""JAIMES-owned Sorare Limited inventory and XP reserve manager.

Audit is read-only and safe for launchd. Apply is intentionally fail-closed: it
requires an exact JSON plan, Josh's explicit approval token, a separate approval
reference, authenticated preflight validation, and authenticated post-verification.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SORARE = Path.home() / "sorare_ml"
ARTIFACT_DIR = SORARE / "artifacts" / "resource_management"
SNAPSHOT = ARTIFACT_DIR / "limited_inventory_snapshot.json"
APPROVAL_TOKEN = "APPROVED_BY_JOSH"

CARD_QUERY = """
query ResourceCards($after: String) {
  currentUser {
    cards(sport: BASEBALL, rarities: [limited], first: 50, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        ... on BaseballCard {
          slug serialNumber ownerSince sealed seasonYear inSeasonEligible anyPositions
          xp grade xpNeededForNextGrade levelUpAppliedCount lastLeveledUpAt
          nextLevelUpAvailableAt power powerBreakdown { xpBasisPoints }
          baseballPlayer { displayName slug activeClub { name slug } anyPositions injuries { status } }
        }
      }
    }
  }
}
"""

BALANCE_QUERY = """
query ResourceBalance {
  currentUser {
    balances: inGameCurrencyBalances(
      sport: BASEBALL
      inGameCurrencies: [LIMITED_XP]
    ) { amount currency capAlmostReached }
  }
}
"""

MUTATION = """
mutation LevelUp($input: levelUpCardsWithXpCurrencyInput!, $sport: Sport!) {
  levelUpCardsWithXpCurrency(input: $input) {
    cards {
      slug xp grade xpNeededForNextGrade lastLeveledUpAt nextLevelUpAvailableAt
      power powerBreakdown { xpBasisPoints }
    }
    currentUser {
      balances: inGameCurrencyBalances(
        sport: $sport
        inGameCurrencies: [LIMITED_XP]
      ) { amount currency capAlmostReached }
    }
    errors { code message }
  }
}
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def cooldown_active(card: dict[str, Any], at: datetime | None = None) -> bool:
    available = parse_time(card.get("nextLevelUpAvailableAt"))
    return bool(available and available > (at or now_utc()))


def xp_gap(card: dict[str, Any]) -> int | None:
    needed = card.get("xpNeededForNextGrade")
    if needed is None:
        return None
    return max(0, int(needed) - int(card.get("xp") or 0))


def detect_new_slugs(previous: dict[str, Any] | None, cards: list[dict[str, Any]]) -> list[str]:
    if not previous:
        return []
    old = {str(row.get("slug")) for row in previous.get("cards", []) if row.get("slug")}
    return sorted(str(row["slug"]) for row in cards if row.get("slug") and str(row["slug"]) not in old)


def ensure_apply_authorized(execute: bool, token: str | None, approval_ref: str | None) -> None:
    if not execute:
        raise PermissionError("XP apply requires --execute")
    if token != APPROVAL_TOKEN:
        raise PermissionError("XP apply requires Josh's exact approval token")
    if not approval_ref or not approval_ref.strip():
        raise PermissionError("XP apply requires a durable approval reference")


def validate_plan(
    plan: dict[str, Any],
    cards: list[dict[str, Any]],
    balance: int,
    at: datetime | None = None,
) -> list[dict[str, Any]]:
    requested = plan.get("cards")
    if not isinstance(requested, list) or not requested:
        raise ValueError("Plan must contain a non-empty cards list")
    max_spend = plan.get("max_spend")
    if max_spend is None or int(max_spend) < 0:
        raise ValueError("Plan must contain non-negative max_spend")
    owned = {str(card.get("slug")): card for card in cards if card.get("slug")}
    seen: set[str] = set()
    verified: list[dict[str, Any]] = []
    for item in requested:
        slug = str(item.get("slug") or "")
        if not slug or slug in seen:
            raise ValueError(f"Missing or duplicate card slug: {slug!r}")
        seen.add(slug)
        card = owned.get(slug)
        if not card:
            raise ValueError(f"Card is not in the authenticated owned Limited inventory: {slug}")
        gap = xp_gap(card)
        expected = item.get("xp_needed")
        if gap is None:
            raise ValueError(f"Card is already max grade: {slug}")
        if int(expected if expected is not None else -1) != gap:
            raise ValueError(f"XP gap drift for {slug}: approved={expected}, live={gap}")
        if gap <= 0:
            raise ValueError(f"No positive XP gap for {slug}")
        if cooldown_active(card, at):
            raise ValueError(f"Level-up cooldown active for {slug}")
        verified.append({
            "slug": slug,
            "player": ((card.get("baseballPlayer") or {}).get("displayName")),
            "xp_needed": gap,
            "before_grade": int(card.get("grade") or 0),
            "before_xp": int(card.get("xp") or 0),
            "before_xp_basis_points": int((card.get("powerBreakdown") or {}).get("xpBasisPoints") or 0),
        })
    spend = sum(row["xp_needed"] for row in verified)
    if spend > int(max_spend):
        raise ValueError(f"Live spend {spend} exceeds approved max_spend {max_spend}")
    if spend > int(balance):
        raise ValueError(f"Insufficient Limited XP: balance={balance}, spend={spend}")
    return verified


def fetch_balance(gql: Callable[..., dict[str, Any]], headers: dict[str, str]) -> int:
    result = gql(headers, BALANCE_QUERY)
    rows = (((result.get("data") or {}).get("currentUser") or {}).get("balances") or [])
    return next((int(row.get("amount") or 0) for row in rows if row.get("currency") == "LIMITED_XP"), 0)


def fetch_cards(gql: Callable[..., dict[str, Any]], headers: dict[str, str]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        result = gql(headers, CARD_QUERY, {"after": after})
        payload = (((result.get("data") or {}).get("currentUser") or {}).get("cards") or {})
        cards.extend(row for row in payload.get("nodes") or [] if row and row.get("slug"))
        page = payload.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return cards
        after = page.get("endCursor")


def safe_card(card: dict[str, Any]) -> dict[str, Any]:
    player = card.get("baseballPlayer") or {}
    return {
        "slug": card.get("slug"),
        "player": player.get("displayName"),
        "owner_since": card.get("ownerSince"),
        "season_year": card.get("seasonYear"),
        "positions": card.get("anyPositions") or [],
        "grade": int(card.get("grade") or 0),
        "xp": int(card.get("xp") or 0),
        "xp_gap": xp_gap(card),
        "xp_bonus_basis_points": int((card.get("powerBreakdown") or {}).get("xpBasisPoints") or 0),
        "cooldown": cooldown_active(card),
        "next_level_available_at": card.get("nextLevelUpAvailableAt"),
    }


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n")


def audit(headers: dict[str, str], gql: Callable[..., dict[str, Any]], out: Path) -> dict[str, Any]:
    cards = fetch_cards(gql, headers)
    balance = fetch_balance(gql, headers)
    previous = read_json(SNAPSHOT)
    new_slugs = detect_new_slugs(previous, cards)
    by_slug = {str(card["slug"]): card for card in cards}
    new_cards = [safe_card(by_slug[slug]) for slug in new_slugs]
    eligible = [safe_card(card) for card in cards if xp_gap(card) not in (None, 0) and not cooldown_active(card)]
    eligible.sort(key=lambda row: (int(row["xp_gap"] or 10**12), -int(row["grade"])))
    payload = {
        "observed_at": now_iso(),
        "mode": "read-only",
        "limited_card_count": len(cards),
        "limited_xp_balance": balance,
        "new_card_count": len(new_cards),
        "new_cards": new_cards,
        "reoptimize_required": bool(new_cards),
        "eligible_one_level_count": len(eligible),
        "lowest_xp_gaps": eligible[:25],
        "guardrails": {
            "xp_mutated": False,
            "lineups_mutated": False,
            "bids_offers_wallet_mutated": False,
        },
    }
    snapshot = {
        "observed_at": payload["observed_at"],
        "cards": [{"slug": card.get("slug"), "owner_since": card.get("ownerSince")} for card in cards],
    }
    write_json(SNAPSHOT, snapshot)
    write_json(out, payload)
    return payload


def apply_plan(
    headers: dict[str, str],
    gql: Callable[..., dict[str, Any]],
    plan: dict[str, Any],
    approval_ref: str,
    out: Path,
) -> dict[str, Any]:
    cards = fetch_cards(gql, headers)
    before_balance = fetch_balance(gql, headers)
    verified = validate_plan(plan, cards, before_balance)
    spend = sum(row["xp_needed"] for row in verified)
    variables = {
        "input": {"levelUpCards": [
            {"cardSlug": row["slug"], "xpNeeded": row["xp_needed"]} for row in verified
        ]},
        "sport": "BASEBALL",
    }
    result = gql(headers, MUTATION, variables)
    mutation_payload = ((result.get("data") or {}).get("levelUpCardsWithXpCurrency") or {})
    if mutation_payload.get("errors"):
        raise RuntimeError(f"Sorare level-up errors: {mutation_payload['errors']}")

    after_cards = {str(card["slug"]): card for card in fetch_cards(gql, headers)}
    after_balance = fetch_balance(gql, headers)
    checks: list[dict[str, Any]] = []
    for before in verified:
        after = after_cards.get(before["slug"]) or {}
        check = {
            **before,
            "after_grade": int(after.get("grade") or 0),
            "after_xp": int(after.get("xp") or 0),
            "after_xp_basis_points": int((after.get("powerBreakdown") or {}).get("xpBasisPoints") or 0),
            "next_level_available_at": after.get("nextLevelUpAvailableAt"),
        }
        check["verified"] = (
            check["after_grade"] == check["before_grade"] + 1
            and check["after_xp"] == check["before_xp"] + check["xp_needed"]
            and check["after_xp_basis_points"] == check["before_xp_basis_points"] + 100
            and bool(check["next_level_available_at"])
        )
        checks.append(check)
    if after_balance != before_balance - spend or not all(row["verified"] for row in checks):
        raise RuntimeError("Authenticated XP post-verification failed")
    receipt = {
        "created_at": now_iso(),
        "approval_ref": approval_ref,
        "balance_before": before_balance,
        "spent": spend,
        "balance_after": after_balance,
        "cards_requested": len(checks),
        "cards_verified": sum(bool(row["verified"]) for row in checks),
        "verification": checks,
    }
    write_json(out, receipt)
    return receipt


def load_ml_bot() -> Any:
    sys.path.insert(0, str(SORARE))
    import ml_bot  # type: ignore
    return ml_bot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["audit", "apply"], default="audit")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approve-token")
    parser.add_argument("--approval-ref")
    args = parser.parse_args()

    logging.getLogger().setLevel(logging.ERROR)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    if args.mode == "audit":
        ml_bot = load_ml_bot()
        _, headers = ml_bot.authenticate()
        out = args.out or ARTIFACT_DIR / "latest.json"
        result = audit(headers, ml_bot.gql, out)
        print(json.dumps({
            "status": "audited",
            "limited_card_count": result["limited_card_count"],
            "limited_xp_balance": result["limited_xp_balance"],
            "new_card_count": result["new_card_count"],
            "reoptimize_required": result["reoptimize_required"],
            "artifact": str(out),
        }, indent=2))
        return 0

    ensure_apply_authorized(args.execute, args.approve_token, args.approval_ref)
    if not args.plan:
        raise ValueError("XP apply requires --plan")
    plan = read_json(args.plan)
    if not plan:
        raise ValueError("XP plan is missing or invalid JSON")
    ml_bot = load_ml_bot()
    _, headers = ml_bot.authenticate()
    out = args.out or ARTIFACT_DIR / f"xp-apply-{now_utc().strftime('%Y%m%dT%H%M%SZ')}.json"
    receipt = apply_plan(headers, ml_bot.gql, plan, str(args.approval_ref), out)
    print(json.dumps({
        "status": "verified",
        "spent": receipt["spent"],
        "balance_after": receipt["balance_after"],
        "cards_verified": receipt["cards_verified"],
        "artifact": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
